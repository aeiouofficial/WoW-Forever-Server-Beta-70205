# Current fix branch

Branch: `fix/bnet-tls-proxy`

The local Forever server uses a TLS bridge on `127.0.0.1:1119` and forwards
to BNet on `127.0.0.1:1120`. The bridge must re-encrypt the BNet connection
and must not prepend a PROXY header to the TLS stream:

```text
--bnet-target-tls true
--bnet-proxy-protocol false
```

The previous combination sent a PROXY header before TLS and produced
`SSL Handshake failed wrong version number`, resulting in client error
`BLZ51901016`.

Validation performed locally:

- `tools/Test-AnoWoWLauncher.ps1`: exit `0`
- PowerShell parse check for `Start-ForeverServer.ps1`: passed
- listeners `1119`, `1120`, `8081`, `8082`, `8085`: active
- local bootstrap start: exit `0`; client and world-auth helper started

The local no-state bootstrap is intentionally independent of
`secrets\install-state.json`. It reuses listeners that are already active and
waits for the Worldserver listener, preventing duplicate starts and the
resulting `Could not bind ... 8085` failure.

World join also requires the encrypted-mode region group used by the local
client certificate helper. The runtime config and `worldserver.conf.template`
set `Network.EnterEncryptedModeRegionGroup = 8`; the ForeverCore patch defaults
both initial and continued world sessions to group 8 and logs the emitted
value. Group 8 alone did not fix the identity check: the local certificate
helper raced the encrypted-mode packet, and the existing delay setting was
not implemented in the upstream core.

## Handshake fix, 2026-10-04

Use `patches/forever-runtime-fixes.patch` against upstream commit
`1655eb7831547c0de70b85e32fc0222193dfa0f0`. It includes the region-group
change and the scale fix. It replaces the two earlier partial exports.
For CRLF checkouts, use `git apply --ignore-space-change`; the reverse check
against the locally modified upstream checkout passed with exit 0.

The patch schedules the initial and continued world identity packets on the
socket update loop, without sleeping on a network thread. Delay defaults to
zero for upstream compatibility; the local template sets 5000 ms. Invalid
values outside 0..10000 ms are logged and use zero delay.

`tools/Start-AnoWoWClient.ps1` waits up to 60 seconds for the helper's initial
scan, cleans up on failure, and scans with four workers and a 100 ms pause
instead of a five-second pause and one worker. The runtime helper and the
separate controlled client are existing local dependencies, not included here.

Validation:

- Debug worldserver build: exit 0.
- Core regression tests: CTest exit 0, 2 tests with 15 behavior assertions.
- Launcher PowerShell parse: exit 0.
- Runtime deployment SHA256:
  `E879B69D103A12A28112D3A7CA426B9BC1E16F73CDBCE5361340E01785FF8E88`
  for the live-tested scale-fix build. Latest source build also guards socket
  close cancellation with a mutex and moves normalization warnings to the
  startup logger; hash
  `6D4E455DF50F8F9749DA497679DE083CBC11ED08C98CAE4B9466C8C381A50189`.
- Fresh live login: authenticated account, configured 5000 ms scheduling,
  region group 8, `CMSG_ENTER_ENCRYPTED_MODE_ACK`, then `CMSG_ENUM_CHARACTERS`.
- Desktop screenshot confirms the actual character-creation UI (not an error
  or merely a running process). Helper initial scan took 19 ms.

Character creation, instance socket handshake and world entry were observed.
The first world session crashed after 48 seconds with model scale 0.0. The
database has 8256 zero-scale creature templates, and the upstream loader passed
them unchanged to clients. The new loader normalizes nonfinite, negative,
zero and epsilon-sized scales to 1.0 without mutating the database; valid
positive scales are preserved. Regression test covers seven scale cases.

After this fix, a second session reached Coldridge Valley and continued
processing movement/combat for several minutes. A captured actual client
window confirms world UI. This is NOT complete gameplay acceptance: the user
reports nonfunctional quests and vendors. New nonfatal client assertions
include `Expected 581 bytes, but received 567 bytes`; packet compatibility
remains under investigation. Quest/vendor DB data exists (4254 quests, 13544
vendor rows), but no successful NPC interaction is verified.

The live server also reports unmapped
client opcode `0x4401B0`, which upstream currently skips rather than disconnects.
Do not treat these findings as a complete gameplay or main-merge acceptance.

## Collision and navigation data

Runtime VMAPs were mixed/obsolete: 7885 of 10144 checked files were not
`VMAP_4.E`. The complete existing extraction was installed after backing up
the entire old directory to
`backups/launcher-fix-20261003/vmaps-before-format-fix-20261004`.
Post-install validation: 8052 checked files, 0 invalid, exit 0.
`tools/Test-ForeverVMaps.ps1` reproduces this format gate.

Navigation remains open: `tools/Test-ForeverMMaps.ps1` found all 3150 runtime
MMAP files incompatible (exit 1). The Release generator initially failed
because Release Boost dependencies were missing; rebuilding those and
reconfiguring CMake produced a successful Release generator build (exit 0).
Generation is running into `external/mmaps-repair-70205/mmaps`, separate from
runtime data. Do not install until generation exits 0 and the MMAP gate passes.
The exact generator command is:

```powershell
external/build-forever-70205-tools/bin/Release/mmaps_generator.exe --input data --output external/mmaps-repair-70205 --threads 2
```

Runtime binaries, database contents, extracted Blizzard client data, private
configs/certificates and personal logs are intentionally excluded from Git.
The source patch preserves the preexisting game-event integer-width fixes.

## Addon error, 2026-10-04

`addon-fixes/Versions.lua` is the corrected live addon module. Canonical source
is `D:/agent player friend/Addons/MasterOfAgentsBridge/Versions.lua`; the
selected client's copy is under `H:/World of Warcraft/BetaForever`.
Both were backed up and changed at the build-info conversion: parenthesizing
`select(4, GetBuildInfo())` limits Lua's multiple returns to one argument and
prevents the trailing `Beta` string becoming the `tonumber` base argument.

The real module reproduced the reported error before the fix. Afterward,
`tests/TestAddonVersions.lua` passed four fixture scenarios against BOTH the
canonical and live copies (exit 0): extra Forever return, ordinary Mainline,
string interface number, and missing interface fallback. The tests execute
the full real module under Lua 5.1 with controlled WoW API boundaries.
Reproduce with `lua tests/TestAddonVersions.lua addon-fixes/Versions.lua`.
Actual in-game `/reload` and full bridge telemetry remain unverified.

Reproduce the isolated regression tests:

```powershell
cmake -S tests -B tests/build -G "Visual Studio 17 2022" -A x64
cmake --build tests/build --config Debug
ctest --test-dir tests/build -C Debug --output-on-failure
```
