#!/usr/bin/env python3
"""Controlled local world-auth helper for the current WoW Forever build; never targets the official client.

Build-neutral: the exact client executable hash comes from the caller (--client-sha256, the
launcher's client profile), not from this file. The group-8 certificate key it replaces lives in
CASC FDID 7725530, which has been byte-identical across the builds seen so far (69977, 70009,
70058); the helper therefore keeps working across a client update as long as that bundle and
the runtime behaviour stay the same, which must be re-verified live per build.

Measured 2026-09-29 (build 70009, helper and client logs correlated): the persistent
certificate-key copies appear in already committed heap regions around the client's first
world connection, and no new copies appear on later logins in the same process. A
single-threaded full pass took about 2.3 s; the pass is split over worker threads and its
duration is logged so the timing stays visible in every session. The worldserver option
Forever.EncryptedModeDelayMs gives this helper time before the first check.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path

LEGACY = Path(__file__).with_name("watch-wow-world-auth-key.py")
spec = importlib.util.spec_from_file_location("forever_key_common", LEGACY)
if spec is None or spec.loader is None:
    raise RuntimeError("common local-client watcher unavailable")
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)

SCAN_WORKERS = 4
SUMMARY_SECONDS = 60.0


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest().upper()


def require_local_client(path: Path, expected_path: Path, client_sha256: str) -> Path:
    resolved = path.resolve(strict=True)
    if resolved.name.lower() != "wowb-foreverlocal.exe":
        raise RuntimeError("the helper requires the separate ForeverLocal executable")
    if "_classic_beta_" in (part.lower() for part in resolved.parts):
        raise RuntimeError("the helper refuses the official reference installation")
    if resolved != expected_path.resolve(strict=True):
        raise RuntimeError("the helper is restricted to the selected separate local client")
    if digest(resolved) != client_sha256.upper():
        raise RuntimeError("executable SHA-256 does not match the current client profile")
    return resolved


def process_image(handle: int) -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    length = wintypes.DWORD(len(buffer))
    if not common.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buffer.value)


REGION_LIMIT = 8 * 1024 * 1024


def patch_copies_fast(handle: int, stock: bytes, replacement: bytes,
                      regions: list[tuple[int, int]]) -> list[int]:
    """Patch exact key copies in the given private writable regions.

    Same result as common.patch_69977_beta_private_copies, but each region is read with one
    ReadProcessMemory call into a reused buffer and searched in place. The chunked reader makes
    several full copies of every megabyte while holding the interpreter lock, which made one pass
    over the client take seconds; the first world sign-in only succeeds when the copies are
    replaced before the server's encrypted-mode challenge arrives.
    """
    patched: list[int] = []
    storage = bytearray(REGION_LIMIT)
    buffer = (ctypes.c_char * REGION_LIMIT).from_buffer(storage)
    count = ctypes.c_size_t()
    chunked: list[tuple[int, int]] = []
    for base, size in sorted(regions, key=lambda r: r[1]):
        if size > REGION_LIMIT:
            continue
        if not common.kernel32.ReadProcessMemory(
            handle, ctypes.c_void_p(base), buffer, size, ctypes.byref(count)
        ) or count.value != size:
            # Pages were freed or protected after the region list was taken: the chunked reader
            # scans the readable start of such a region.
            chunked.append((base, size))
            continue
        start = 0
        while (found := storage.find(stock, start, size)) >= 0:
            address = base + found
            if common.read_memory(handle, address, len(stock)) == stock:
                common.write_writable_memory(handle, address, replacement)
                if common.read_memory(handle, address, len(replacement)) != replacement:
                    raise RuntimeError(f"key copy verification failed at 0x{address:X}")
                patched.append(address)
            start = found + 1
    if chunked:
        patched.extend(common.patch_69977_beta_private_copies(
            handle, stock, replacement, candidate_regions=chunked))
    return patched


def region_of(address: int, regions: list[tuple[int, int]]) -> tuple[int, int] | None:
    for base, size in regions:
        if base <= address < base + size:
            return base, size
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-sha256", required=True,
                        help="exact SHA-256 of the current build's client executable (from the client profile)")
    parser.add_argument("--build", default="current", help="build label for logs and the session record")
    parser.add_argument("--profile-check", type=Path)
    parser.add_argument("--local-client", type=Path, required=True)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--armed-file", type=Path)
    parser.add_argument("--scan-seconds", type=float, default=0.1,
                        help="pause between full private-memory scans")
    parser.add_argument("--scan-workers", type=int, default=SCAN_WORKERS,
                        help="threads reading the client's private regions in one pass")
    args = parser.parse_args()
    client_sha256 = args.client_sha256.upper()
    if len(client_sha256) != 64:
        parser.error("--client-sha256 must be a 64-character SHA-256")

    if args.profile_check:
        require_local_client(args.profile_check, args.local_client, client_sha256)
        print(f"PASS: exact controlled client executable ({args.build})", flush=True)
        return 0
    if not args.pid or not args.output or not args.armed_file:
        parser.error("--pid, --output and --armed-file are required")
    if not 0.01 <= args.scan_seconds <= 10:
        parser.error("--scan-seconds must be 0.01..10")
    if not 1 <= args.scan_workers <= 16:
        parser.error("--scan-workers must be 1..16")

    access = (common.PROCESS_QUERY_INFORMATION | common.PROCESS_VM_READ |
              common.PROCESS_VM_WRITE | common.PROCESS_VM_OPERATION)
    handle = common.kernel32.OpenProcess(access, False, args.pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    result = {"build": args.build, "pid": args.pid, "mode": "local-group8-certificate-probe",
              "clientSha256": client_sha256, "patchedPrivateCopies": 0,
              "officialRegionGroup": 8, "scanWorkers": args.scan_workers,
              "fullPasses": 0, "fullPassMsMin": None, "fullPassMsMax": None, "fullPassMsLast": None}
    stop_fast = threading.Event()
    fast_thread: threading.Thread | None = None
    pool = ThreadPoolExecutor(max_workers=args.scan_workers, thread_name_prefix="world-auth-key-scan")
    label = f"{args.build} world-auth"
    try:
        require_local_client(process_image(handle), args.local_client, client_sha256)
        seen: set[int] = set()
        seen_lock = threading.Lock()
        fast_error: list[Exception] = []
        stock = common.FOREVER_69977_BETA_WORLD_PUBLIC_KEY
        replacement = common.LOCAL_PUBLIC_KEY

        def record(addresses: list[int], source: str, regions: list[tuple[int, int]] | None = None,
                   pass_ms: float | None = None) -> None:
            with seen_lock:
                for address in addresses:
                    if address not in seen:
                        seen.add(address)
                        region = region_of(address, regions) if regions else None
                        where = f" region_size=0x{region[1]:X}" if region else ""
                        timing = f" pass_ms={pass_ms:.0f}" if pass_ms is not None else ""
                        common.log_patch_event(
                            f"{label} {source} private certificate-key copy updated at 0x{address:X}{where}{timing}"
                        )
                result["patchedPrivateCopies"] = len(seen)

        def full_pass() -> tuple[list[int], list[tuple[int, int]], float]:
            # Regions are read in parallel; ReadProcessMemory releases the GIL, so
            # several workers cut the wall-clock time of one pass.
            started = time.monotonic()
            regions = [r for r in common.private_writable_regions(handle) if r[1] <= REGION_LIMIT]
            regions.sort(key=lambda r: r[1])
            buckets: list[list[tuple[int, int]]] = [[] for _ in range(args.scan_workers)]
            for index, region in enumerate(regions):
                buckets[index % args.scan_workers].append(region)
            patched: list[int] = []
            for found in pool.map(lambda bucket: patch_copies_fast(
                    handle, stock, replacement, bucket) if bucket else [], buckets):
                patched.extend(found)
            elapsed_ms = (time.monotonic() - started) * 1000.0
            result["fullPasses"] += 1
            result["fullPassMsLast"] = round(elapsed_ms)
            result["fullPassMsMin"] = round(min(elapsed_ms, result["fullPassMsMin"] or elapsed_ms))
            result["fullPassMsMax"] = round(max(elapsed_ms, result["fullPassMsMax"] or elapsed_ms))
            return patched, regions, elapsed_ms

        # Finish one complete pass before the launcher reports ready. During
        # the handshake a verifier copy can be created and consumed between
        # two full scans. Prioritize newly committed writable regions and
        # rescan those for five seconds because a region may be populated
        # after allocation. The parallel full pass remains the catch-all.
        patched, regions, pass_ms = full_pass()
        record(patched, "startup", regions, pass_ms)
        observed_regions = set(common.private_writable_regions(handle))

        def watch_fast_copies() -> None:
            try:
                recent_regions: dict[tuple[int, int], float] = {}
                while not stop_fast.is_set() and common.process_is_alive(handle):
                    now = time.monotonic()
                    current_regions = set(common.private_writable_regions(handle))
                    for region in current_regions - observed_regions:
                        if region[1] <= 8 * 1024 * 1024:
                            recent_regions[region] = now
                    observed_regions.update(current_regions)
                    for region, first_seen in list(recent_regions.items()):
                        if region not in current_regions or now - first_seen > 5.0:
                            recent_regions.pop(region, None)
                    if recent_regions:
                        candidates = list(recent_regions)
                        record(common.patch_69977_beta_private_copies(
                            handle, stock, replacement, candidate_regions=candidates
                        ), "new-region", candidates)
                    stop_fast.wait(0.01)
            except Exception as exc:
                fast_error.append(exc)
                stop_fast.set()

        fast_thread = threading.Thread(target=watch_fast_copies, name="world-auth-key-copies", daemon=True)
        fast_thread.start()
        common.publish_armed(args.armed_file, args.pid, f"{args.build}-local-group8-probe")
        common.log_patch_event(
            f"{label} watcher armed pid={args.pid} workers={args.scan_workers} startup_pass_ms={pass_ms:.0f}"
        )
        next_summary = time.monotonic() + SUMMARY_SECONDS
        while common.process_is_alive(handle):
            if fast_error:
                raise RuntimeError(f"fast verifier scan failed: {fast_error[0]}")
            patched, regions, pass_ms = full_pass()
            record(patched, "full", regions, pass_ms)
            if time.monotonic() >= next_summary:
                # One line per minute keeps the pass duration visible without flooding the log.
                common.log_patch_event(
                    f"{label} scan summary passes={result['fullPasses']} pass_ms last={result['fullPassMsLast']}"
                    f" min={result['fullPassMsMin']} max={result['fullPassMsMax']} regions={len(regions)}"
                )
                next_summary = time.monotonic() + SUMMARY_SECONDS
            stop_fast.wait(args.scan_seconds)
        result["outcome"] = "client-exited"
    finally:
        stop_fast.set()
        pool.shutdown(wait=False, cancel_futures=True)
        if fast_thread is not None:
            fast_thread.join(timeout=2)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        common.kernel32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
