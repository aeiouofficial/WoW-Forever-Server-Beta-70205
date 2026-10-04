#!/usr/bin/env python3
"""Watch the separate local WoW client and patch its build-specific world-auth key.

Launch this watcher when starting the local client, wait until it is armed,
then log in. Keep the session watcher running through the world handshake.
The signed executable remains unchanged on disk. The build-69913 and
build-69977 paths use their own verified profiles; never reuse a key or RVA
for a future build without exact-build evidence. Do not target the official
reference client.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import re
import socket
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path


PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
MEM_MAPPED = 0x40000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
PAGE_EXECUTE_READWRITE = 0x40
WRITABLE_PROTECTIONS = {0x04, 0x08, 0x40, 0x80}


def log_patch_event(message: str) -> None:
    """Correlate verifier writes with world logs without changing patch timing."""
    wall_time = datetime.now().astimezone().isoformat(timespec="milliseconds")
    print(f"{wall_time} monotonic_ns={time.monotonic_ns()} {message}", flush=True)

# Exact file-offset -> RVA translation for the researched 69913 PE:
# file offset 0x48C6D90 in .rdata maps to image RVA 0x48C8790.
FOREVER_69913_ED25519_RVA = 0x48C8790
# The adjacent ASCII marker at file offset 0x47DF380 says WardenUpdateKey.
# This is NOT the 69977 world-auth verifier certificate/key.
FOREVER_69977_WARDEN_KEY_RVA = 0x47E0790
FOREVER_69977_CLIENT_SHA256 = "E9D20AACF83381F96364DDA4E3B6F39573A3A960E428A6A40391D58257853D72"
FOREVER_69977_PORTAL_SUFFIX_RVA = 0x49C7778
FOREVER_69977_STOCK_PORTAL_SUFFIX = b".actual.battle.net"
FOREVER_69977_LOCAL_PORTAL_SUFFIX = b".actual.bgs.test\0\0"
FOREVER_69977_WARDEN_KEY = bytes.fromhex(
    "15D618BD7DB577BD9A8D45769C59E4FC"
    "78694051CB224345592AF7136D796C99"
)
FOREVER_69977_BETA_WORLD_PUBLIC_KEY = bytes.fromhex(
    "1FD6DD8FA0EC30D39E3F72E755B8A045"
    "BDE0F70449DC71008B767C2EAA89FB9F"
)

# Runtime evidence from build 69913: the character-enum handler at RVA
# 0x236D060 checks the byte at RVA 0x5F301C7 before posting its glue event and
# then explicitly clears that byte.  It is therefore a one-shot pending-request
# latch, not a state that may be held true.  Arm it once when Hermes observes a
# fresh CMSG_ENUM_CHARACTERS and let the client clear it after the response.
# Do not patch the handler code page: the client's integrity monitor rejects
# executable-page changes.
FOREVER_69913_ENUM_UI_GATE_RVA = 0x5F301C7

# Character-enum consumer state recovered from the 69913 runtime.  The parser
# stores its completed result at handler+0xC90.  The consumer only takes that
# result when these four writable data-state gates have the values observed
# after the missing lifecycle transition.  Publish them once per non-null
# pending result; never touch executable code or vtables.
FOREVER_69913_ENUM_HANDLER_GLOBAL_RVA = 0x5F3B8B8
FOREVER_69913_ENUM_PENDING_OFFSET = 0xC90
FOREVER_69913_ENUM_CONSUMER_GATES = (
    (0x57C5F49, 0),
    (0x60BBFAB, 1),
    (0x5F33A54, 1),
    (0x57744CE, 0),
)

# Exact 69977 local-client character-screen callback state. The authenticated
# official enum was consumed with (closed, ready, blocked) = (0, 1, 0). In a
# controlled local run the result remained at handler+0xC90 with (1, 0, 1);
# setting these three writable bytes only when a result is pending made the
# client consume it and display all five characters. These are build-pinned
# client lifecycle bytes, not world packet fields or executable code.
FOREVER_69977_ENUM_HANDLER_GLOBAL_RVA = 0x5E524C8
FOREVER_69977_ENUM_PENDING_OFFSET = 0xC90
FOREVER_69977_ENUM_SESSION_STATE_RVA = 0x5E85678
FOREVER_69977_ENUM_STATE2_READY_RVA = 0x5E4A43C
FOREVER_69977_ENUM_CONSUMER_GATES = (
    (0x56DCE59, 0),
    (0x5FD2BDB, 1),
    (0x568B4CE, 0),
)

STOCK_EU_KEY = bytes.fromhex(
    "A6B858485748BF38BE193517AB90F8DE"
    "169EFF0989EA9360DB346A378B0FFE15"
)
LOCAL_PUBLIC_KEY = bytes.fromhex(
    "02596F0D0C061A8B30745988FD72C59E"
    "29EC367FB0F341F28E0F08D037BAFC69"
)
EU_REGION_ENTRY_PATTERN = (3).to_bytes(4, "little") + STOCK_EU_KEY + b"\x01"

TRIGGER = re.compile(rb"WORLD_AUTH_PATCH_WINDOW_OPEN")
SUCCESS = re.compile(rb"CMSG_ENTER_ENCRYPTED_MODE_ACK received")
FAILURE = re.compile(rb"Client disconnected with reason ([0-9]+)")


class MemoryBasicInformation(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class ModuleInfo(ctypes.Structure):
    _fields_ = [
        ("BaseOfDll", ctypes.c_void_p),
        ("SizeOfImage", wintypes.DWORD),
        ("EntryPoint", ctypes.c_void_p),
    ]


class TcpOwnerPidRow(ctypes.Structure):
    _fields_ = [
        ("state", wintypes.DWORD), ("local_addr", wintypes.DWORD),
        ("local_port", wintypes.DWORD), ("remote_addr", wintypes.DWORD),
        ("remote_port", wintypes.DWORD), ("owner_pid", wintypes.DWORD),
    ]


iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
iphlpapi.GetExtendedTcpTable.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
    wintypes.ULONG, ctypes.c_int, wintypes.ULONG,
]
iphlpapi.GetExtendedTcpTable.restype = wintypes.DWORD


def has_process_tcp_target(pid: int, port: int) -> bool:
    """Read-only Windows IPv4 TCP owner table; ports are network byte order."""
    # The owner table can grow between the sizing and data calls as sockets
    # open during login. Windows may report either 122 or 87 for that race.
    # Re-size instead of terminating the handshake watcher.
    for _ in range(4):
        size = wintypes.DWORD()
        status = iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 5, 0)
        if status not in (0, 87, 122):
            raise OSError(status, "GetExtendedTcpTable sizing")
        if not size.value:
            return False
        buffer = ctypes.create_string_buffer(size.value + 4096)
        size = wintypes.DWORD(len(buffer))
        status = iphlpapi.GetExtendedTcpTable(buffer, ctypes.byref(size), False, 2, 5, 0)
        if status in (87, 122):
            continue
        if status:
            raise OSError(status, "GetExtendedTcpTable")
        break
    else:
        return False
    count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
    offset = ctypes.sizeof(wintypes.DWORD)
    row_size = ctypes.sizeof(TcpOwnerPidRow)
    for index in range(count):
        row = TcpOwnerPidRow.from_buffer_copy(buffer, offset + index * row_size)
        if row.owner_pid == pid and socket.ntohs(row.remote_port & 0xFFFF) == port:
            return True
    return False


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(MemoryBasicInformation),
    ctypes.c_size_t,
]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.WriteProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.WriteProcessMemory.restype = wintypes.BOOL
kernel32.VirtualProtectEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_size_t,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.VirtualProtectEx.restype = wintypes.BOOL
kernel32.FlushInstructionCache.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_size_t,
]
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.K32EnumProcessModules.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.K32EnumProcessModules.restype = wintypes.BOOL
kernel32.K32GetModuleInformation.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(ModuleInfo),
    wintypes.DWORD,
]
kernel32.K32GetModuleInformation.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

STILL_ACTIVE = 259


def read_memory(handle: int, address: int, size: int) -> bytes:
    buffer = ctypes.create_string_buffer(size)
    count = ctypes.c_size_t()
    if not kernel32.ReadProcessMemory(
        handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(count)
    ):
        return b""
    return buffer.raw[: count.value]


def verify_69977_client(handle: int, allow_reference: bool = False) -> Path:
    path_buffer = ctypes.create_unicode_buffer(32768)
    length = wintypes.DWORD(len(path_buffer))
    if not kernel32.QueryFullProcessImageNameW(
        handle, 0, path_buffer, ctypes.byref(length)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    executable = Path(path_buffer.value)
    local = executable.name.lower() == "wowb-foreverlocal.exe"
    reference = executable.name.lower() == "wowb.exe" and "_classic_beta_" in str(executable).lower()
    if not local and not (allow_reference and reference):
        raise RuntimeError("69977 watcher requires the separate local client; official client is read-only inspection only")
    digest = hashlib.sha256()
    with executable.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest().upper() != FOREVER_69977_CLIENT_SHA256:
        raise RuntimeError("69977 local client hash does not match the researched build")
    return executable


def write_memory(handle: int, address: int, payload: bytes) -> None:
    old_protect = wintypes.DWORD()
    if not kernel32.VirtualProtectEx(
        handle,
        ctypes.c_void_p(address),
        len(payload),
        PAGE_EXECUTE_READWRITE,
        ctypes.byref(old_protect),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        source = ctypes.create_string_buffer(payload)
        written = ctypes.c_size_t()
        if not kernel32.WriteProcessMemory(
            handle,
            ctypes.c_void_p(address),
            source,
            len(payload),
            ctypes.byref(written),
        ) or written.value != len(payload):
            raise ctypes.WinError(ctypes.get_last_error())
        kernel32.FlushInstructionCache(handle, ctypes.c_void_p(address), len(payload))
    finally:
        ignored = wintypes.DWORD()
        if not kernel32.VirtualProtectEx(
            handle,
            ctypes.c_void_p(address),
            len(payload),
            old_protect.value,
            ctypes.byref(ignored),
        ):
            # Capture immediately: subsequent Windows calls can overwrite it.
            error = ctypes.get_last_error()
            raise OSError(
                error,
                f"Failed to restore memory protection at 0x{address:X} "
                f"(size={len(payload)}, protection=0x{old_protect.value:X}, "
                f"Windows error={error}); write cannot be reported as successful",
            )


def write_writable_memory(handle: int, address: int, payload: bytes) -> None:
    """Write a validated writable mapped page without changing its protection."""
    info = MemoryBasicInformation()
    if not kernel32.VirtualQueryEx(
        handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if (int(info.Protect) & 0xFF) not in WRITABLE_PROTECTIONS:
        raise RuntimeError(f"mapped source at 0x{address:X} is no longer writable")
    source = ctypes.create_string_buffer(payload)
    written = ctypes.c_size_t()
    if not kernel32.WriteProcessMemory(
        handle, ctypes.c_void_p(address), source, len(payload), ctypes.byref(written)
    ) or written.value != len(payload):
        raise ctypes.WinError(ctypes.get_last_error())


def patch_69977_beta_private_copies(
    handle: int, stock: bytes, replacement: bytes,
    max_region_bytes: int = 8 * 1024 * 1024,
    candidate_regions: list[tuple[int, int]] | None = None
) -> list[int]:
    """Patch only exact beta-cert key copies in private writable client memory."""
    patched: list[int] = []
    regions = [r for r in (candidate_regions if candidate_regions is not None
                           else private_writable_regions(handle)) if r[1] <= max_region_bytes]
    regions.sort(key=lambda r: r[1])
    for base, size in regions:
        offset = 0
        overlap = b""
        while offset < size:
            chunk = read_memory(handle, base + offset, min(size - offset, 1024 * 1024))
            if not chunk:
                break
            joined = overlap + chunk
            start = 0
            while (found := joined.find(stock, start)) >= 0:
                address = base + offset - len(overlap) + found
                if read_memory(handle, address, len(stock)) == stock:
                    write_writable_memory(handle, address, replacement)
                    if read_memory(handle, address, len(replacement)) != replacement:
                        raise RuntimeError(f"69977 beta key verification failed at 0x{address:X}")
                    patched.append(address)
                start = found + 1
            overlap = joined[-(len(stock) - 1):]
            offset += len(chunk)
    return patched


def find_all(handle: int, pattern: bytes) -> list[int]:
    hits: list[int] = []
    address = 0
    ceiling = 0x0000800000000000
    info = MemoryBasicInformation()
    while address < ceiling:
        if not kernel32.VirtualQueryEx(
            handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)
        ):
            break
        base = int(info.BaseAddress or 0)
        size = int(info.RegionSize)
        next_address = base + max(size, 0x1000)
        readable = (
            info.State == MEM_COMMIT
            and not (info.Protect & PAGE_GUARD)
            and (info.Protect & 0xFF) != PAGE_NOACCESS
        )
        if readable:
            offset = 0
            overlap = b""
            while offset < size:
                chunk_size = min(8 * 1024 * 1024, size - offset)
                chunk = read_memory(handle, base + offset, chunk_size)
                if not chunk:
                    break
                joined = overlap + chunk
                start = 0
                while True:
                    found = joined.find(pattern, start)
                    if found < 0:
                        break
                    hit = base + offset - len(overlap) + found
                    if hit not in hits:
                        hits.append(hit)
                    start = found + 1
                overlap = joined[-(len(pattern) - 1) :]
                offset += len(chunk)
        if next_address <= address:
            break
        address = next_address
    return hits


def private_writable_regions(handle: int) -> list[tuple[int, int]]:
    """Return committed private writable regions that can hold the key vector."""
    regions: list[tuple[int, int]] = []
    address = 0
    ceiling = 0x0000800000000000
    info = MemoryBasicInformation()
    while address < ceiling:
        if not kernel32.VirtualQueryEx(
            handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)
        ):
            break
        base = int(info.BaseAddress or 0)
        size = int(info.RegionSize)
        next_address = base + max(size, 0x1000)
        protection = int(info.Protect) & 0xFF
        if (
            info.State == MEM_COMMIT
            and int(info.Type) == MEM_PRIVATE
            and protection in WRITABLE_PROTECTIONS
            and not (info.Protect & PAGE_GUARD)
            and size <= 32 * 1024 * 1024
        ):
            regions.append((base, size))
        if next_address <= address:
            break
        address = next_address
    return regions


def find_eu_region_key_entries(
    handle: int, regions: list[tuple[int, int]] | None = None
) -> list[int]:
    """Find only region-3 entries in the client's 40-byte regional key vector."""
    hits: list[int] = []
    pattern = EU_REGION_ENTRY_PATTERN
    if regions is None:
        regions = private_writable_regions(handle)
    for base, size in regions:
        offset = 0
        overlap = b""
        while offset < size:
            chunk_size = min(1 * 1024 * 1024, size - offset)
            chunk = read_memory(handle, base + offset, chunk_size)
            if not chunk:
                break
            joined = overlap + chunk
            start = 0
            while True:
                found = joined.find(pattern, start)
                if found < 0:
                    break
                entry_address = base + offset - len(overlap) + found
                key_address = entry_address + 4
                if key_address not in hits:
                    hits.append(key_address)
                start = found + 1
            overlap = joined[-(len(pattern) - 1) :]
            offset += len(chunk)
    return hits


def find_private_key_copies(handle: int, regions: list[tuple[int, int]]) -> list[int]:
    """Read-only exact-key search in writable private client memory."""
    hits: list[int] = []
    for base, size in regions:
        offset = 0
        overlap = b""
        while offset < size:
            chunk = read_memory(handle, base + offset, min(1024 * 1024, size - offset))
            if not chunk:
                break
            data = overlap + chunk
            start = 0
            while (found := data.find(STOCK_EU_KEY, start)) >= 0:
                address = base + offset - len(overlap) + found
                if address not in hits:
                    hits.append(address)
                start = found + 1
            overlap = data[-(len(STOCK_EU_KEY) - 1):]
            offset += len(chunk)
    return hits


def mapped_key_sources(handle: int, key_rva: int) -> list[tuple[int, int]]:
    """Find exact-key copies at the same RVA in all mapped image allocations."""
    hits: list[tuple[int, int]] = []
    seen: set[int] = set()
    address = 0
    info = MemoryBasicInformation()
    while address < 0x0000800000000000:
        if not kernel32.VirtualQueryEx(
            handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)
        ):
            break
        base = int(info.BaseAddress or 0)
        next_address = base + max(int(info.RegionSize), 0x1000)
        allocation = int(info.AllocationBase or 0)
        if info.State == MEM_COMMIT and info.Type == MEM_MAPPED and allocation not in seen:
            seen.add(allocation)
            candidate = allocation + key_rva
            if read_memory(handle, candidate, len(STOCK_EU_KEY)) == STOCK_EU_KEY:
                key_info = MemoryBasicInformation()
                if kernel32.VirtualQueryEx(
                    handle, ctypes.c_void_p(candidate), ctypes.byref(key_info), ctypes.sizeof(key_info)
                ):
                    hits.append((candidate, int(key_info.Protect) & 0xFF))
        if next_address <= address:
            break
        address = next_address
    return hits


def patch_eu_region_key_entries(
    handle: int,
    regions: list[tuple[int, int]] | None = None,
    stop_after: int | None = 1,
) -> tuple[list[int], list[int]]:
    """Find and patch the region-3 source entry before it creates copies.

    Build 69913 stores regional verification keys as 40-byte records:
    uint32 region, 32-byte public key, uint8 enabled, followed by padding.
    Matching the complete record identifies the persistent EU source table and
    avoids treating an arbitrary number of short-lived raw-key copies as a
    readiness condition.
    """
    hits: list[int] = []
    patched: list[int] = []
    pattern = EU_REGION_ENTRY_PATTERN
    if regions is None:
        regions = private_writable_regions(handle)
    regions.sort(key=regional_key_scan_priority)
    for base, size in regions:
        offset = 0
        overlap = b""
        while offset < size:
            chunk_size = min(1 * 1024 * 1024, size - offset)
            chunk = read_memory(handle, base + offset, chunk_size)
            if not chunk:
                break
            joined = overlap + chunk
            start = 0
            while True:
                found = joined.find(pattern, start)
                if found < 0:
                    break
                entry_address = base + offset - len(overlap) + found
                key_address = entry_address + 4
                if key_address not in hits:
                    hits.append(key_address)
                    if read_memory(handle, key_address, len(STOCK_EU_KEY)) == STOCK_EU_KEY:
                        write_memory(handle, key_address, LOCAL_PUBLIC_KEY)
                        if read_memory(handle, key_address, len(LOCAL_PUBLIC_KEY)) != LOCAL_PUBLIC_KEY:
                            raise RuntimeError(f"verification failed at 0x{key_address:X}")
                        patched.append(key_address)
                        if stop_after is not None and len(patched) >= stop_after:
                            return hits, patched
                start = found + 1
            overlap = joined[-(len(pattern) - 1) :]
            offset += len(chunk)
    return hits, patched


def regional_key_scan_priority(region: tuple[int, int]) -> tuple[int, int]:
    """Order regions from the two measured 69913 table placements."""
    _, size = region
    if 2 * 1024 * 1024 <= size < 4 * 1024 * 1024:
        return (0, size)
    if 1 * 1024 * 1024 <= size < 8 * 1024 * 1024:
        return (1, size)
    if size < 1 * 1024 * 1024:
        return (2, size)
    return (3, size)


def patch_stock_key_copies_in_small_private(
    handle: int, stop_after: int | None = None
) -> tuple[list[int], list[int]]:
    """Find and immediately patch loaded EU-key copies measured in 69913.

    Three pre-verification copies and the short-lived verification copy were
    all observed in private writable regions no larger than 4.8 MiB.  Scanning
    only regions below 8 MiB avoids the 1-2 GiB texture/game heaps.  The
    verification copy can disappear before a complete process scan returns,
    so patch each exact match in the same chunk where it was observed.
    """
    hits: list[int] = []
    patched: list[int] = []
    regions = [
        region for region in private_writable_regions(handle)
        if region[1] <= 8 * 1024 * 1024
    ]
    regions.sort(key=lambda region: region[1])
    return patch_stock_key_copies_in_regions(handle, regions, stop_after)


def patch_stock_key_copies_in_regions(
    handle: int,
    regions: list[tuple[int, int]],
    stop_after: int | None = None,
) -> tuple[list[int], list[int]]:
    """Patch exact stock-key copies in an already selected region set."""
    hits: list[int] = []
    patched: list[int] = []
    for base, size in regions:
        offset = 0
        overlap = b""
        while offset < size:
            chunk_size = min(1 * 1024 * 1024, size - offset)
            chunk = read_memory(handle, base + offset, chunk_size)
            if not chunk:
                break
            joined = overlap + chunk
            start = 0
            while True:
                found = joined.find(STOCK_EU_KEY, start)
                if found < 0:
                    break
                address = base + offset - len(overlap) + found
                if address not in hits:
                    hits.append(address)
                    if read_memory(handle, address, len(STOCK_EU_KEY)) == STOCK_EU_KEY:
                        write_memory(handle, address, LOCAL_PUBLIC_KEY)
                        if read_memory(handle, address, len(LOCAL_PUBLIC_KEY)) != LOCAL_PUBLIC_KEY:
                            raise RuntimeError(f"verification failed at 0x{address:X}")
                        patched.append(address)
                        if stop_after is not None and len(patched) >= stop_after:
                            return hits, patched
                start = found + 1
            overlap = joined[-(len(STOCK_EU_KEY) - 1) :]
            offset += len(chunk)
    return hits, patched


def patch_stock_key_copies_in_tiny_private(
    handle: int, stock: bytes = STOCK_EU_KEY, replacement: bytes = LOCAL_PUBLIC_KEY
) -> list[int]:
    """Patch short-lived verifier copies before the client can consume them.

    A failed 69913 login captured the verifier key in a newly allocated 0x2000
    MEM_PRIVATE/PAGE_READWRITE region.  The ordinary scan reached that region
    2.1 seconds after its preceding pass, which was already too late and the
    client disconnected with reason 24.  This deliberately narrow scan reads
    only writable private regions up to 64 KiB so it can run continuously
    without walking the multi-megabyte game heaps.
    """
    patched: list[int] = []
    for base, size in private_writable_regions(handle):
        if size > 64 * 1024:
            continue
        chunk = read_memory(handle, base, size)
        start = 0
        while chunk:
            found = chunk.find(stock, start)
            if found < 0:
                break
            address = base + found
            if read_memory(handle, address, len(stock)) == stock:
                write_memory(handle, address, replacement)
                if read_memory(handle, address, len(replacement)) != replacement:
                    raise RuntimeError(f"verification failed at 0x{address:X}")
                patched.append(address)
            start = found + 1
    return patched


def publish_armed(path: Path | None, pid: int, mode: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"armed": True, "pid": pid, "mode": mode}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def patch_addresses(handle: int, addresses: list[int]) -> list[int]:
    patched: list[int] = []
    try:
        for address in addresses:
            if read_memory(handle, address, len(STOCK_EU_KEY)) != STOCK_EU_KEY:
                continue
            write_memory(handle, address, LOCAL_PUBLIC_KEY)
            if read_memory(handle, address, len(LOCAL_PUBLIC_KEY)) != LOCAL_PUBLIC_KEY:
                raise RuntimeError(f"verification failed at 0x{address:X}")
            patched.append(address)
    except Exception:
        restore_addresses(handle, patched)
        raise
    return patched


def restore_addresses(handle: int, addresses: list[int]) -> int:
    restored = 0
    for address in addresses:
        current = read_memory(handle, address, len(LOCAL_PUBLIC_KEY))
        if current == LOCAL_PUBLIC_KEY:
            write_memory(handle, address, STOCK_EU_KEY)
            restored += 1
    return restored


def process_is_alive(handle: int) -> bool:
    exit_code = wintypes.DWORD()
    return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == STILL_ACTIVE


def main_module_base(handle: int) -> int:
    modules = (ctypes.c_void_p * 1)()
    needed = wintypes.DWORD()
    if not kernel32.K32EnumProcessModules(
        handle, modules, ctypes.sizeof(modules), ctypes.byref(needed)
    ) or not modules[0]:
        raise ctypes.WinError(ctypes.get_last_error())
    info = ModuleInfo()
    if not kernel32.K32GetModuleInformation(
        handle, modules[0], ctypes.byref(info), ctypes.sizeof(info)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(info.BaseOfDll or 0)


def describe_address(handle: int, address: int, module_base: int) -> str:
    info = MemoryBasicInformation()
    if not kernel32.VirtualQueryEx(
        handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)
    ):
        return f"0x{address:X}:query-failed"
    base = int(info.BaseAddress or 0)
    allocation_base = int(info.AllocationBase or 0)
    return (
        f"0x{address:X}(module+0x{address - module_base:X},"
        f"region=0x{base:X},allocation=0x{allocation_base:X},"
        f"size=0x{int(info.RegionSize):X},type=0x{int(info.Type):X},"
        f"protect=0x{int(info.Protect):X})"
    )


def read_new_lines(stream, position: int, encoding: str) -> tuple[int, bytes]:
    stream.seek(position)
    data = stream.read()
    if encoding == "utf-16-le" and data:
        # PowerShell 5.1 Tee-Object creates UTF-16LE transcript files.  The
        # previous byte regex silently missed every server trigger because of
        # the NUL byte between each ASCII character.
        data = data[: len(data) - (len(data) % 2)]
        data = data.decode("utf-16-le", errors="ignore").encode("utf-8")
    return stream.tell(), data


def main() -> int:
    global STOCK_EU_KEY, EU_REGION_ENTRY_PATTERN
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=int, choices=(69913, 69977), required=True)
    parser.add_argument("--profile-check", type=Path, help="offline exact-build client check")
    parser.add_argument("--inspect", action="store_true", help="read-only key placement check")
    parser.add_argument("--inspect-reference", action="store_true", help="allow read-only official client inspection")
    parser.add_argument("--inspect-live-seconds", type=float, default=0.0)
    parser.add_argument("--inspect-all", action="store_true", help="read-only search across all mapped memory")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--hermes-log", type=Path)
    parser.add_argument("--trigger-log", type=Path, help="local auth log for a transient 69977 handshake experiment")
    parser.add_argument("--trigger-port", type=int, help="watch this client's REST connection before world join")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--ready-file",
        type=Path,
        help="atomically publish session readiness while continuing to hold the patch",
    )
    parser.add_argument(
        "--armed-file",
        type=Path,
        help="atomically publish that exact-RVA fast monitoring is active",
    )
    parser.add_argument("--scan-wait-seconds", type=float, default=60.0)
    parser.add_argument("--wait-seconds", type=float, default=600.0)
    parser.add_argument("--patch-seconds", type=float, default=6.0)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument(
        "--tiny-monitor",
        action="store_true",
        help="run only the high-frequency <=64 KiB verifier-copy monitor",
    )
    parser.add_argument(
        "--regional-monitor",
        action="store_true",
        help="run only the build-69913 region-3 source-table monitor",
    )
    parser.add_argument(
        "--enum-ui-monitor",
        action="store_true",
        help="run only the high-frequency character-enum UI data-gate monitor",
    )
    parser.add_argument(
        "--enum-consumer-monitor",
        action="store_true",
        help="publish the proven 69913 character-enum consumer state once per pending result",
    )
    parser.add_argument(
        "--session",
        action="store_true",
        help="patch every loaded copy, verify two zero-stock scans, and keep the patch until WoW exits",
    )
    parser.add_argument(
        "--stabilize-seconds",
        type=float,
        default=30.0,
        help="minimum client-load time before two zero-stock scans may mark a session ready",
    )
    args = parser.parse_args()

    if args.profile_check is not None:
        if args.build != 69977:
            raise RuntimeError("offline profile check requires build 69977")
        if args.profile_check.name.lower() not in ("wowb.exe", "wowb-foreverlocal.exe"):
            raise RuntimeError("unexpected client executable name")
        digest = hashlib.sha256()
        with args.profile_check.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
            source.seek(0x47DF390)
            key = source.read(32)
        if digest.hexdigest().upper() != FOREVER_69977_CLIENT_SHA256 or key != FOREVER_69977_WARDEN_KEY:
            raise RuntimeError("69977 executable hash or WardenUpdateKey bytes do not match")
        print("PASS: exact 69977 executable hash and WardenUpdateKey bytes", flush=True)
        return 0

    if args.pid is None or args.output is None:
        parser.error("--pid and --output are required for process monitoring")

    access = (
        PROCESS_QUERY_INFORMATION
        | PROCESS_VM_OPERATION
        | PROCESS_VM_READ
        | PROCESS_VM_WRITE
    )
    handle = kernel32.OpenProcess(access, False, args.pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    if args.build == 69977:
        verify_69977_client(handle, allow_reference=args.inspect and args.inspect_reference)
        STOCK_EU_KEY = FOREVER_69977_BETA_WORLD_PUBLIC_KEY
        EU_REGION_ENTRY_PATTERN = (8).to_bytes(4, "little") + STOCK_EU_KEY + b"\x01"
        if args.enum_ui_monitor or args.enum_consumer_monitor:
            raise RuntimeError("69913 character-enum offsets are not validated for 69977")
        if not (args.inspect or args.session):
            raise RuntimeError("69977 supports only inspected, exact-key session mode")
    elif args.inspect:
        raise RuntimeError("read-only placement inspection is only defined for 69977")

    mode = (
        "tiny-monitor"
        if args.tiny_monitor
        else (
            "regional-monitor"
            if args.regional_monitor
            else (
                "enum-ui-monitor"
                if args.enum_ui_monitor
                else (
                    "enum-consumer-monitor"
                    if args.enum_consumer_monitor
                    else ("session" if args.session else ("smoke" if args.smoke else "watch"))
                )
            )
        )
    )
    result: dict[str, object] = {"pid": args.pid, "mode": mode}
    patched: list[int] = []
    fast_stop: threading.Event | None = None
    fast_thread: threading.Thread | None = None
    tiny_process: subprocess.Popen | None = None
    regional_process: subprocess.Popen | None = None
    enum_ui_process: subprocess.Popen | None = None
    enum_consumer_process: subprocess.Popen | None = None
    try:
        if args.inspect:
            module_base = main_module_base(handle)
            pinned_address = module_base + FOREVER_69977_WARDEN_KEY_RVA
            warden_marker = read_memory(handle, pinned_address, len(FOREVER_69977_WARDEN_KEY))
            regions = private_writable_regions(handle)
            regional = find_eu_region_key_entries(handle, regions)
            private_keys = find_private_key_copies(handle, regions)
            all_keys = find_all(handle, STOCK_EU_KEY) if args.inspect_all else []
            observed_tiny: set[int] = set()
            if args.inspect_live_seconds > 0:
                deadline = time.monotonic() + min(args.inspect_live_seconds, 60.0)
                while process_is_alive(handle) and time.monotonic() < deadline:
                    tiny_regions = [r for r in private_writable_regions(handle) if r[1] <= 65536]
                    observed_tiny.update(find_private_key_copies(handle, tiny_regions))
                    time.sleep(0.002)
            result.update({
                "build": 69977,
                "moduleBase": f"0x{module_base:X}",
                "wardenKeyRva": f"0x{FOREVER_69977_WARDEN_KEY_RVA:X}",
                "wardenMarkerMatches": warden_marker == FOREVER_69977_WARDEN_KEY,
                "betaWorldPublicKey": STOCK_EU_KEY.hex(),
                "privateRegionCount": len(regions),
                "regionalEntryCount": len(regional),
                "privateKeyCopyCount": len(private_keys),
                "privateKeyCopies": [f"0x{address:X}" for address in private_keys[:16]],
                "allBetaKeyCopies": [describe_address(handle, address, module_base) for address in all_keys[:32]],
                "observedTinyKeyCopies": [f"0x{address:X}" for address in sorted(observed_tiny)[:16]],
                "outcome": "read-only-inspection",
            })
            print(json.dumps(result, indent=2), flush=True)
            return 0

        if args.build == 69977:
            # The exact 69977 group-8 beta certificate supplies this key.
            # WardenUpdateKey in the executable is unrelated and must never
            # be touched. Only client-owned private writable copies qualify.
            if args.trigger_port is None or args.trigger_log is not None:
                raise RuntimeError("69977 session requires --trigger-port")
            if not 1 <= args.trigger_port <= 65535:
                raise RuntimeError("invalid 69977 trigger port")
            result.update({"build": 69977, "regionGroup": 8,
                           "stockPublicKey": STOCK_EU_KEY.hex()})
            module_base = main_module_base(handle)
            # The launcher already configures the separate client's portal in
            # WTF/Config.wtf and routes that hostname through its managed hosts
            # entries. Do not mutate the image's read-only portal string: the
            # 69977 client produced Security Crash 0x103 after that write.
            portal_address = module_base + FOREVER_69977_PORTAL_SUFFIX_RVA
            stock_portal = read_memory(handle, portal_address, len(FOREVER_69977_STOCK_PORTAL_SUFFIX))
            portal_deadline = time.monotonic() + 5.0
            while stock_portal != FOREVER_69977_STOCK_PORTAL_SUFFIX and time.monotonic() < portal_deadline:
                time.sleep(0.05)
                stock_portal = read_memory(handle, portal_address, len(FOREVER_69977_STOCK_PORTAL_SUFFIX))
            if stock_portal != FOREVER_69977_STOCK_PORTAL_SUFFIX:
                raise RuntimeError("69977 portal suffix is not the unchanged exact-build value")
            client_path = verify_69977_client(handle)
            config_path = client_path.parent / "WTF" / "Config.wtf"
            config_text = config_path.read_text(encoding="utf-8-sig", errors="replace") if config_path.is_file() else ""
            portal_settings = re.findall(r'^\s*SET\s+portal\s+"([^"]+)"\s*$', config_text, re.IGNORECASE | re.MULTILINE)
            if len(portal_settings) != 1 or not re.fullmatch(r'(?:us|eu|kr|tw|cn|test)\.actual\.bgs\.test(?::1119)?', portal_settings[0], re.IGNORECASE):
                raise RuntimeError(f"69977 requires one Forever portal setting in {config_path}; client memory was not changed")
            print(f"69977 portal image unchanged; using Config.wtf portal {portal_settings[0]}", flush=True)
            handler_global = module_base + FOREVER_69977_ENUM_HANDLER_GLOBAL_RVA
            publish_armed(args.armed_file, args.pid, mode)
            print(f"69977 beta-key watcher armed pid={args.pid} trigger_port={args.trigger_port}", flush=True)
            port_seen = False
            windows = 0
            last_published_pending = 0
            enum_publications = 0

            def patch_available_beta_copies() -> None:
                # The group-8 verifier can allocate another writable copy well
                # after the REST login window. Keep this exact-key check alive
                # for the entire process rather than assuming a 12-second
                # login window covers the later world encryption handshake.
                newly_patched = patch_69977_beta_private_copies(
                    handle, STOCK_EU_KEY, LOCAL_PUBLIC_KEY
                )
                for address in newly_patched:
                    if address not in patched:
                        patched.append(address)
                        print(f"69977 beta verifier patched=0x{address:X}", flush=True)

            def publish_enum_if_pending() -> None:
                # Never manufacture client-owned DB2/UI readiness.
                def waiting(reason):
                    # Log transitions, not every 5 ms poll. No addresses or keys.
                    if result.get("enumWaitReason") != reason:
                        result["enumWaitReason"] = reason
                        print(f"69977 enum waiting: {reason}", flush=True)

                handler_bytes = read_memory(handle, handler_global, 8)
                handler = int.from_bytes(handler_bytes, "little") if len(handler_bytes) == 8 else 0
                if not handler:
                    waiting("handler unavailable")
                    return
                pending = read_memory(handle, handler + FOREVER_69977_ENUM_PENDING_OFFSET, 8)
                if len(pending) != 8 or not int.from_bytes(pending, "little"):
                    waiting("no pending character list")
                    return
                if read_memory(handle, module_base + FOREVER_69977_ENUM_SESSION_STATE_RVA, 4) != bytes((2, 0, 0, 0)):
                    waiting("session state")
                    return
                if read_memory(handle, module_base + FOREVER_69977_ENUM_STATE2_READY_RVA, 1) != bytes((1,)):
                    waiting("state-2 UI readiness")
                    return
                if read_memory(handle, module_base + 0x56DCE59, 1) != bytes((0,)):
                    waiting("UI closed flag")
                    return
                if read_memory(handle, module_base + 0x5FD2BDB, 1) != bytes((1,)):
                    waiting("UI ready flag")
                    return
                # Exact 69977 ChrCustomizationOption object and IsLoaded byte,
                # recovered from the 13:31:56 crash and DB2 reader at 657360.
                table = module_base + 0x5FF0A70
                meta_bytes = read_memory(handle, table + 8, 8)
                meta = int.from_bytes(meta_bytes, "little") if len(meta_bytes) == 8 else 0
                if not meta or read_memory(handle, meta + 8, 4) != (3384247).to_bytes(4, "little"):
                    waiting("customization table identity")
                    return
                if read_memory(handle, meta + 0x64, 4) != (0xDCC2A86E).to_bytes(4, "little"):
                    waiting("customization table layout")
                    return
                if read_memory(handle, table + 0x20D, 1) != bytes((1,)):
                    waiting("customization table not loaded")
                    return
                block = module_base + 0x568B4CE
                if read_memory(handle, block, 1) == bytes((1,)):
                    write_memory(handle, block, bytes((0,)))
                    if read_memory(handle, block, 1) != bytes((0,)):
                        raise RuntimeError("69977 loaded enum release did not verify")
                    result["enumPublications"] = result.get("enumPublications", 0) + 1
                    result["enumWaitReason"] = "released"
                    print("69977 enum released after client readiness and loaded customization DB2", flush=True)
                elif read_memory(handle, block, 1) != bytes((0,)):
                    waiting("enum block unreadable or invalid")

            next_background_scan = time.monotonic()
            while process_is_alive(handle):
                publish_enum_if_pending()
                now = time.monotonic()
                if now >= next_background_scan:
                    patch_available_beta_copies()
                    next_background_scan = time.monotonic() + 2.0
                connected = has_process_tcp_target(args.pid, args.trigger_port)
                triggered = connected and not port_seen
                port_seen = connected
                if not triggered:
                    time.sleep(0.005)
                    continue
                windows += 1
                print(f"69977 login window open count={windows}", flush=True)
                deadline = time.monotonic() + max(args.patch_seconds, 12.0)
                while process_is_alive(handle) and time.monotonic() < deadline:
                    patch_available_beta_copies()
                    publish_enum_if_pending()
                    time.sleep(0.01)
            result["windows"] = windows
            result.setdefault("enumPublications", 0)
            result["patchedPrivateCopies"] = len(patched)
            result["outcome"] = "process-exited"
            return 0
        if args.enum_consumer_monitor:
            module_base = main_module_base(handle)
            handler_global = module_base + FOREVER_69913_ENUM_HANDLER_GLOBAL_RVA
            enum_latch = module_base + FOREVER_69913_ENUM_UI_GATE_RVA
            publish_armed(args.armed_file, args.pid, mode)
            published_for_pending = False
            publish_count = 0
            latch_arm_count = 0

            def arm_enum_latch(reason: str) -> None:
                nonlocal latch_arm_count
                write_memory(handle, enum_latch, b"\x01")
                if read_memory(handle, enum_latch, 1) != b"\x01":
                    raise RuntimeError("character-enum request latch verification failed")
                latch_arm_count += 1
                print(f"enum request latch armed reason={reason}", flush=True)

            # This watcher is started with the client, before the first enum
            # request.  The handler consumes and clears this one-shot latch.
            arm_enum_latch("monitor-start")
            while process_is_alive(handle):
                handler_bytes = read_memory(handle, handler_global, 8)
                handler = int.from_bytes(handler_bytes, "little") if len(handler_bytes) == 8 else 0
                pending = 0
                if handler:
                    pending_bytes = read_memory(
                        handle, handler + FOREVER_69913_ENUM_PENDING_OFFSET, 8
                    )
                    if len(pending_bytes) == 8:
                        pending = int.from_bytes(pending_bytes, "little")

                if pending and not published_for_pending:
                    before = {}
                    for rva, value in FOREVER_69913_ENUM_CONSUMER_GATES:
                        address = module_base + rva
                        current = read_memory(handle, address, 1)
                        before[f"0x{rva:X}"] = current[0] if current else None
                        write_memory(handle, address, bytes((value,)))
                        if read_memory(handle, address, 1) != bytes((value,)):
                            raise RuntimeError(
                                f"character-enum gate verification failed at RVA 0x{rva:X}"
                            )
                    published_for_pending = True
                    publish_count += 1
                    print(
                        f"enum consumer state published pending=0x{pending:X} before={before}",
                        flush=True,
                    )
                elif not pending and published_for_pending:
                    published_for_pending = False
                    arm_enum_latch("previous-result-consumed")

                time.sleep(0.002)

            result["publishCount"] = publish_count
            result["latchArmCount"] = latch_arm_count
            result["outcome"] = "process-exited"
            return 0

        if args.enum_ui_monitor:
            module_base = main_module_base(handle)
            enum_ui_gate_address = module_base + FOREVER_69913_ENUM_UI_GATE_RVA
            publish_armed(args.armed_file, args.pid, mode)
            write_count = 0
            transient_failures = 0
            if args.hermes_log is None:
                raise RuntimeError("--enum-ui-monitor requires --hermes-log")
            log_position = args.hermes_log.stat().st_size if args.hermes_log.exists() else 0
            while process_is_alive(handle):
                if args.hermes_log.exists():
                    with args.hermes_log.open("rb") as source:
                        source.seek(log_position)
                        appended = source.read()
                        log_position = source.tell()
                    enum_requests = appended.count(b"CMSG_ENUM_CHARACTERS received")
                    if enum_requests:
                        try:
                            write_memory(handle, enum_ui_gate_address, b"\x01")
                            if read_memory(handle, enum_ui_gate_address, 1) == b"\x01":
                                write_count += 1
                                print(
                                    f"enum UI pending latch armed for {enum_requests} observed request(s)",
                                    flush=True,
                                )
                        except OSError:
                            transient_failures += 1
                time.sleep(0.002)
            result["writeCount"] = write_count
            result["transientFailures"] = transient_failures
            result["outcome"] = "process-exited"
            return 0

        if args.regional_monitor:
            module_base = main_module_base(handle)
            publish_armed(args.armed_file, args.pid, mode)
            scan_count = 0
            observed_regions = set(private_writable_regions(handle))
            last_full_rescan = 0.0
            while process_is_alive(handle):
                scan_count += 1
                for address in tuple(patched):
                    current = read_memory(handle, address, len(STOCK_EU_KEY))
                    if current == STOCK_EU_KEY:
                        write_memory(handle, address, LOCAL_PUBLIC_KEY)
                        print(
                            "regional-process repatched "
                            + describe_address(handle, address, module_base),
                            flush=True,
                        )

                if not patched:
                    current_regions = set(private_writable_regions(handle))
                    candidate_regions = [
                        region
                        for region in current_regions - observed_regions
                        if region[1] <= 8 * 1024 * 1024
                    ]
                    observed_regions.update(current_regions)

                    # A table may be populated shortly after its allocation.
                    # Periodically rescan the bounded candidate class, while
                    # prioritizing newly committed regions on every pass.
                    now = time.monotonic()
                    if not candidate_regions and now - last_full_rescan >= 0.25:
                        candidate_regions = [
                            region
                            for region in current_regions
                            if region[1] <= 8 * 1024 * 1024
                        ]
                        last_full_rescan = now

                    _, newly_patched_regional = patch_eu_region_key_entries(
                        handle, candidate_regions, stop_after=1
                    )
                    _, newly_patched_copies = patch_stock_key_copies_in_regions(
                        handle, candidate_regions, stop_after=None
                    )
                    newly_patched = newly_patched_regional + newly_patched_copies
                    for address in newly_patched:
                        if address not in patched:
                            patched.append(address)
                            log_patch_event(
                                "regional-process patched "
                                + describe_address(handle, address, module_base)
                            )
                    if newly_patched_regional and args.ready_file is not None:
                        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
                        ready_payload = {
                            "ready": True,
                            "pid": args.pid,
                            "mode": mode,
                            "regionalSource": True,
                            "address": f"0x{newly_patched_regional[0]:X}",
                            "scanCount": scan_count,
                        }
                        temporary_ready_file = args.ready_file.with_suffix(
                            args.ready_file.suffix + ".tmp"
                        )
                        temporary_ready_file.write_text(
                            json.dumps(ready_payload, indent=2), encoding="utf-8"
                        )
                        temporary_ready_file.replace(args.ready_file)

                time.sleep(0.001 if patched else 0.01)
            result["scanCount"] = scan_count
            result["outcome"] = "process-exited"
            return 0

        if args.tiny_monitor:
            module_base = main_module_base(handle)
            pinned_address = module_base + FOREVER_69913_ED25519_RVA
            publish_armed(args.armed_file, args.pid, mode)
            scan_count = 0
            while process_is_alive(handle):
                scan_count += 1
                # The client was measured rewriting an already discovered
                # verifier address back to the stock key repeatedly.  Poll
                # proven addresses directly before doing any region walk.
                for address in tuple(patched):
                    if read_memory(handle, address, len(STOCK_EU_KEY)) == STOCK_EU_KEY:
                        write_memory(handle, address, LOCAL_PUBLIC_KEY)
                        print(
                            "tiny-process repatched "
                            + describe_address(handle, address, module_base),
                            flush=True,
                        )
                current = read_memory(handle, pinned_address, len(STOCK_EU_KEY))
                if current == STOCK_EU_KEY:
                    write_memory(handle, pinned_address, LOCAL_PUBLIC_KEY)
                    if read_memory(handle, pinned_address, len(LOCAL_PUBLIC_KEY)) == LOCAL_PUBLIC_KEY:
                        if pinned_address not in patched:
                            patched.append(pinned_address)
                            log_patch_event(
                                "tiny-process patched "
                                + describe_address(handle, pinned_address, module_base)
                            )
                newly_patched = patch_stock_key_copies_in_tiny_private(handle)
                for address in newly_patched:
                    if address not in patched:
                        patched.append(address)
                        log_patch_event(
                            "tiny-process patched "
                            + describe_address(handle, address, module_base)
                        )
                time.sleep(0.001)
            result["scanCount"] = scan_count
            result["outcome"] = "process-exited"
            return 0

        if args.session:
            tiny_output = args.output.with_suffix(args.output.suffix + ".tiny.json")
            tiny_armed = args.output.with_suffix(args.output.suffix + ".tiny.armed.json")
            regional_output = args.output.with_suffix(args.output.suffix + ".regional.json")
            regional_armed = args.output.with_suffix(args.output.suffix + ".regional.armed.json")
            regional_ready = args.output.with_suffix(args.output.suffix + ".regional.ready.json")
            enum_ui_output = args.output.with_suffix(args.output.suffix + ".enum-ui.json")
            enum_ui_armed = args.output.with_suffix(args.output.suffix + ".enum-ui.armed.json")
            enum_consumer_output = args.output.with_suffix(
                args.output.suffix + ".enum-consumer.json"
            )
            enum_consumer_armed = args.output.with_suffix(
                args.output.suffix + ".enum-consumer.armed.json"
            )
            tiny_output.unlink(missing_ok=True)
            tiny_armed.unlink(missing_ok=True)
            regional_output.unlink(missing_ok=True)
            regional_armed.unlink(missing_ok=True)
            regional_ready.unlink(missing_ok=True)
            enum_ui_output.unlink(missing_ok=True)
            enum_ui_armed.unlink(missing_ok=True)
            enum_consumer_output.unlink(missing_ok=True)
            enum_consumer_armed.unlink(missing_ok=True)
            tiny_process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--pid",
                    str(args.pid),
                    "--output",
                    str(tiny_output),
                    "--armed-file",
                    str(tiny_armed),
                    "--tiny-monitor",
                ]
            )
            regional_process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--pid",
                    str(args.pid),
                    "--output",
                    str(regional_output),
                    "--armed-file",
                    str(regional_armed),
                    "--ready-file",
                    str(regional_ready),
                    "--regional-monitor",
                ]
            )
            enum_consumer_process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--pid",
                    str(args.pid),
                    "--output",
                    str(enum_consumer_output),
                    "--armed-file",
                    str(enum_consumer_armed),
                    "--enum-consumer-monitor",
                ]
            )
            # Do not alter the client's character-enum pending latch during a
            # normal session.  The last known working character-screen commit
            # did not run this monitor, and the exact handler already owns and
            # clears the latch.  Keep --enum-ui-monitor as an explicit
            # diagnostic mode only.
            enum_ui_process = None
            tiny_deadline = time.monotonic() + 2.0
            while (
                not tiny_armed.exists()
                or not regional_armed.exists()
                or not enum_consumer_armed.exists()
            ):
                if tiny_process.poll() is not None:
                    raise RuntimeError(
                        f"tiny verifier monitor exited early with code {tiny_process.returncode}"
                    )
                if regional_process.poll() is not None:
                    raise RuntimeError(
                        f"regional source monitor exited early with code {regional_process.returncode}"
                    )
                if enum_consumer_process.poll() is not None:
                    raise RuntimeError(
                        "character-enum consumer monitor exited early with code "
                        f"{enum_consumer_process.returncode}"
                    )
                if time.monotonic() >= tiny_deadline:
                    raise RuntimeError("key monitors did not arm within 2 seconds")
                time.sleep(0.01)

        publish_armed(args.armed_file, args.pid, mode)
        baseline_regions = set(private_writable_regions(handle)) if mode == "watch" else set()
        if args.session:
            patched_lock = threading.Lock()
            fast_stop = threading.Event()
            module_base = main_module_base(handle)
            pinned_address = module_base + FOREVER_69913_ED25519_RVA
            enum_ui_gate_address = module_base + FOREVER_69913_ENUM_UI_GATE_RVA
            regional_source_seen = False

            def remember_patched(addresses: list[int]) -> None:
                with patched_lock:
                    for address in addresses:
                        if address not in patched:
                            patched.append(address)
                            log_patch_event(
                                "session patched "
                                + describe_address(handle, address, module_base)
                            )

            def fast_verifier_monitor() -> None:
                while not fast_stop.is_set() and process_is_alive(handle):
                    with patched_lock:
                        known_addresses = tuple(patched)
                    for address in known_addresses:
                        if read_memory(handle, address, len(STOCK_EU_KEY)) == STOCK_EU_KEY:
                            write_memory(handle, address, LOCAL_PUBLIC_KEY)
                            if read_memory(handle, address, len(LOCAL_PUBLIC_KEY)) == LOCAL_PUBLIC_KEY:
                                print(
                                    "fast-known repatched "
                                    + describe_address(handle, address, module_base),
                                    flush=True,
                                )
                    current = read_memory(handle, pinned_address, len(STOCK_EU_KEY))
                    if current == STOCK_EU_KEY:
                        write_memory(handle, pinned_address, LOCAL_PUBLIC_KEY)
                        if read_memory(handle, pinned_address, len(LOCAL_PUBLIC_KEY)) == LOCAL_PUBLIC_KEY:
                            remember_patched([pinned_address])
                            print(
                                f"fast-rva patched=0x{pinned_address:X} "
                                f"module_base=0x{module_base:X} rva=0x{FOREVER_69913_ED25519_RVA:X}",
                                flush=True,
                            )
                    # The verifier copy measured after the last reason-24
                    # disconnect occupied only one 0x2000 private RW region.
                    # Poll this small class separately; a complete heap scan
                    # is too slow to win the verification race reliably.
                    tiny_patched = patch_stock_key_copies_in_tiny_private(handle)
                    if tiny_patched:
                        remember_patched(tiny_patched)
                        for address in tiny_patched:
                            log_patch_event(
                                "fast-tiny patched "
                                + describe_address(handle, address, module_base)
                            )
                    fast_stop.wait(0.001)

            fast_thread = threading.Thread(
                target=fast_verifier_monitor,
                name="forever-69913-ed25519-verifier",
                daemon=True,
            )
            fast_thread.start()
            if args.armed_file is not None:
                args.armed_file.parent.mkdir(parents=True, exist_ok=True)
                armed_payload = {
                    "armed": True,
                    "pid": args.pid,
                    "mode": mode,
                    "moduleBase": f"0x{module_base:X}",
                    "ed25519Rva": f"0x{FOREVER_69913_ED25519_RVA:X}",
                    "pinnedAddress": f"0x{pinned_address:X}",
                    "characterEnumUiGateRva": f"0x{FOREVER_69913_ENUM_UI_GATE_RVA:X}",
                }
                temporary_armed_file = args.armed_file.with_suffix(
                    args.armed_file.suffix + ".tmp"
                )
                temporary_armed_file.write_text(
                    json.dumps(armed_payload, indent=2), encoding="utf-8"
                )
                temporary_armed_file.replace(args.armed_file)

            zero_stock_scans = 0
            scan_count = 0
            stabilization_started = time.monotonic()
            while process_is_alive(handle):
                scan_count += 1
                if regional_ready.exists():
                    regional_source_seen = True
                regional_addresses, newly_patched_regional = patch_eu_region_key_entries(
                    handle, stop_after=1
                )
                if regional_addresses:
                    regional_source_seen = True
                if newly_patched_regional:
                    remember_patched(newly_patched_regional)
                    for address in newly_patched_regional:
                        print(
                            "regional-key-entry patched "
                            + describe_address(handle, address, module_base),
                            flush=True,
                        )
                stock_addresses, newly_patched = patch_stock_key_copies_in_small_private(
                    handle, stop_after=4
                )
                remember_patched(newly_patched)
                with patched_lock:
                    patched_count = len(patched)
                stabilization_elapsed = time.monotonic() - stabilization_started
                # The exact region-3 source record is the readiness invariant.
                # Raw copies are derived from it and can vary in number and
                # lifetime, so they are monitored but never counted as proof.
                if (
                    stock_addresses
                    or not regional_source_seen
                ):
                    zero_stock_scans = 0
                else:
                    zero_stock_scans += 1
                print(
                    f"session scan={scan_count} stock={len(stock_addresses)} "
                    f"regional_source={int(regional_source_seen)} "
                    f"patched_total={patched_count} elapsed={stabilization_elapsed:.1f}s "
                    f"zero_scans={zero_stock_scans}/2",
                    flush=True,
                )
                if patched_count and zero_stock_scans >= 2:
                    break
                time.sleep(1.0)

            if not process_is_alive(handle):
                result["outcome"] = "process-exited-during-stabilization"
                return 0

            result["scanCount"] = scan_count
            result["verifiedZeroStockScans"] = zero_stock_scans
            with patched_lock:
                patched_count = len(patched)
            print(
                f"ready pid={args.pid} stock_key_copies=0 patched={patched_count} session_lifetime=true",
                flush=True,
            )
            if args.ready_file is not None:
                args.ready_file.parent.mkdir(parents=True, exist_ok=True)
                ready_payload = {
                    "ready": True,
                    "pid": args.pid,
                    "patched": patched_count,
                    "scanCount": scan_count,
                    "verifiedZeroStockScans": zero_stock_scans,
                }
                temporary_ready_file = args.ready_file.with_suffix(
                    args.ready_file.suffix + ".tmp"
                )
                temporary_ready_file.write_text(
                    json.dumps(ready_payload, indent=2), encoding="utf-8"
                )
                temporary_ready_file.replace(args.ready_file)
            # Loaded verification copies are not session-stable.  The client
            # creates more copies on every login/reconnect, so keep applying
            # the exact replacement for the lifetime of the process instead
            # of merely holding the addresses found during startup.
            lifetime_scan_count = 0
            while process_is_alive(handle):
                lifetime_scan_count += 1
                regional_addresses, newly_patched_regional = patch_eu_region_key_entries(
                    handle, stop_after=1
                )
                if newly_patched_regional:
                    remember_patched(newly_patched_regional)
                    for address in newly_patched_regional:
                        print(
                            "regional-key-entry repatched "
                            + describe_address(handle, address, module_base),
                            flush=True,
                        )
                stock_addresses, newly_patched = patch_stock_key_copies_in_small_private(
                    handle, stop_after=4
                )
                remember_patched(newly_patched)
                if regional_addresses or newly_patched_regional or stock_addresses or newly_patched:
                    with patched_lock:
                        lifetime_patched_count = len(patched)
                    print(
                        f"session lifetime-scan={lifetime_scan_count} "
                        f"regional={len(regional_addresses)} "
                        f"stock={len(stock_addresses)} "
                        f"newly_patched={len(newly_patched)} "
                        f"patched_total={lifetime_patched_count}",
                        flush=True,
                    )
                time.sleep(0.05)
            fast_stop.set()
            fast_thread.join(timeout=2.0)
            result["outcome"] = "process-exited"
            return 0

        if args.smoke:
            scan_deadline = time.monotonic() + args.scan_wait_seconds
            addresses: list[int] = []
            while not addresses:
                addresses = find_all(handle, STOCK_EU_KEY)
                if addresses or time.monotonic() >= scan_deadline:
                    break
                time.sleep(0.25)
            if not addresses:
                raise RuntimeError(
                    "stock EU Ed25519 key was not found in client memory before the scan timeout"
                )
            result["addresses"] = [f"0x{address:X}" for address in addresses]
            print(f"ready pid={args.pid} stock_key_copies={len(addresses)}", flush=True)
            patched = patch_addresses(handle, addresses)
            print(f"smoke patched={len(patched)}", flush=True)
            time.sleep(args.patch_seconds)
            result["outcome"] = "smoke"
        else:
            if args.hermes_log is None:
                raise ValueError("--hermes-log is required without --smoke")
            with args.hermes_log.open("rb") as log:
                marker = log.read(2)
                log_encoding = "utf-16-le" if marker == b"\xff\xfe" else "bytes"
                position = log.seek(0, 2)
                print(f"armed pid={args.pid} log_encoding={log_encoding}", flush=True)
                deadline = time.monotonic() + args.wait_seconds
                while time.monotonic() < deadline:
                    position, data = read_new_lines(log, position, log_encoding)
                    if TRIGGER.search(data):
                        result["triggered"] = True
                        break
                    time.sleep(0.01)
                else:
                    result["outcome"] = "wait-timeout"
                    return 3

                # Build 69913 creates additional writable trust-key copies while
                # the encrypted-mode handshake is already in progress.  A
                # one-shot scan can observe only the first copy and then fail
                # verification; successful captures patched three or more.
                # Keep scanning for the whole server window and patch every new
                # exact stock-key copy as soon as it appears.
                scan_started = time.monotonic()
                scan_deadline = scan_started + args.scan_wait_seconds
                patch_deadline = time.monotonic() + args.patch_seconds
                outcome = "patch-timeout"
                disconnect_reason = None
                all_addresses: list[int] = []
                scan_count = 0
                while time.monotonic() < patch_deadline:
                    scan_count += 1
                    addresses, newly_patched = patch_stock_key_copies_in_small_private(
                        handle, stop_after=4
                    )
                    for address in newly_patched:
                        if address not in patched:
                            patched.append(address)
                        if address not in all_addresses:
                            all_addresses.append(address)
                    scan_elapsed = time.monotonic() - scan_started
                    print(
                        f"window-scan={scan_count} stock={len(addresses)} "
                        f"newly_patched={len(newly_patched)} patched_total={len(patched)} "
                        f"elapsed_ms={scan_elapsed * 1000:.1f}",
                        flush=True,
                    )
                    position, data = read_new_lines(log, position, log_encoding)
                    if SUCCESS.search(data):
                        outcome = "encrypted-mode-ack"
                        break
                    failure = FAILURE.search(data)
                    if failure:
                        disconnect_reason = int(failure.group(1))
                        outcome = "disconnect"
                        break
                    if not patched and time.monotonic() >= scan_deadline:
                        raise RuntimeError(
                            "EU region-3 trust-table entry was not found during the server patch window"
                        )
                    time.sleep(0.005)
                scan_elapsed = time.monotonic() - scan_started
                result["addresses"] = [f"0x{address:X}" for address in all_addresses]
                result["baselineRegionCount"] = len(baseline_regions)
                result["scanCount"] = scan_count
                result["scanElapsedMs"] = round(scan_elapsed * 1000, 1)
                result["outcome"] = outcome
                if disconnect_reason is not None:
                    result["disconnectReason"] = disconnect_reason
                print(f"outcome={outcome}", flush=True)
    finally:
        if fast_stop is not None:
            fast_stop.set()
        if fast_thread is not None:
            fast_thread.join(timeout=2.0)
        if tiny_process is not None:
            try:
                tiny_process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                # The helper is intentionally allowed to outlive a parent
                # failure while WoW still runs, so it cannot leave the next
                # handshake unprotected.
                pass
        if regional_process is not None:
            try:
                regional_process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                # Like the tiny verifier monitor, keep the source monitor
                # alive if the parent fails while WoW is still running.
                pass
        if enum_ui_process is not None:
            try:
                enum_ui_process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                # Keep the process-local data-gate monitor alive with WoW if
                # the parent monitor fails.
                pass
        if enum_consumer_process is not None:
            try:
                enum_consumer_process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                # Keep the process-local consumer monitor alive with WoW if
                # the parent session monitor fails.
                pass
        if args.build == 69977:
            restored = 0
            for address in patched:
                if read_memory(handle, address, len(LOCAL_PUBLIC_KEY)) == LOCAL_PUBLIC_KEY:
                    write_writable_memory(handle, address, STOCK_EU_KEY)
                    restored += 1
        else:
            restored = restore_addresses(handle, patched)
        result["patched"] = len(patched)
        result["restored"] = restored
        kernel32.CloseHandle(handle)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        if patched:
            print(f"restored={restored}", flush=True)

    return 0


def publish_process_event(path, event):
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(event, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)

def monitor_process_main():
    parser = argparse.ArgumentParser(description="Read-only process identity and lifetime; no patching")
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--build', type=int, choices=[69977], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--armed-file', type=Path)
    parser.add_argument('--ready-file', type=Path)
    parser.add_argument('--seconds', type=float, default=180)
    args = parser.parse_args(sys.argv[2:])
    if args.pid <= 0 or not 0 < args.seconds <= 86400:
        parser.error('PID must be positive; duration must be in (0, 86400].')
    paths = [p.resolve() for p in (args.output, args.armed_file, args.ready_file) if p]
    if len(paths) != len(set(paths)) or any(p.exists() for p in paths):
        parser.error('Use distinct, new output paths; stale readiness files are refused.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # Query identity and wait for exit only; no VM read/write/operation rights.
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, args.pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    start = time.monotonic()
    event = {'pid': args.pid, 'build': args.build, 'mode': 'read-only-process-monitor',
             'scope': 'Process identity/lifetime only; no memory readiness or login validation.'}
    try:
        if kernel.WaitForSingleObject(handle, 0) != 258:
            raise RuntimeError('Target process is not running.')
        publish_process_event(args.armed_file, {**event, 'state': 'armed'})
        print('ARMED: monitoring the selected PID', flush=True)
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        exe = Path(buffer.value)
        if exe.name.lower() not in ('wowb.exe', 'wowb-foreverlocal.exe'):
            raise RuntimeError('Selected process is not a recognized WoW executable.')
        with exe.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest().upper()
        event.update(executable=exe.name, fileSha256=digest)
        if digest != FOREVER_69977_CLIENT_SHA256:
            raise RuntimeError('Executable hash does not match the researched 69977 build.')
        if kernel.WaitForSingleObject(handle, 0) != 258:
            raise RuntimeError('Client exited during identity verification.')
        publish_process_event(args.ready_file, {**event, 'state': 'ready-process-identity-verified'})
        print('READY: process identity verified; login readiness is NOT established', flush=True)
        remaining = max(0, args.seconds - (time.monotonic() - start))
        result = kernel.WaitForSingleObject(handle, int(remaining * 1000))
        if result == 258:
            event['state'] = 'observation-ended-process-running'
        elif result == 0:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            event.update(state='process-exited', exitCode=code.value)
        else:
            raise ctypes.WinError(ctypes.get_last_error())
    except Exception as error:
        event.update(state='error', error=str(error))
        raise
    finally:
        event['elapsedSeconds'] = time.monotonic() - start
        try:
            publish_process_event(args.output, event)
        finally:
            kernel.CloseHandle(handle)
    print(json.dumps(event), flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(monitor_process_main() if sys.argv[1:2] == ["--monitor-process"] else main())
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"error: {exc}", flush=True)
        raise SystemExit(1)
