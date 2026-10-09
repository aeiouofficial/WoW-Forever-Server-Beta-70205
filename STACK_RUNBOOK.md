# AnoCore local server stack — Operations & recovery

## Paths and scope

- Local deployment root: `D:\AnoCore-Server`
- Prepared client: `H:\World of Warcraft\BetaForever\WowB-ForeverLocal.exe`
- GitHub repository: `aeiouofficial/WoW-Forever-Server-Beta-70205`
- State, credentials, databases and executable binaries are **local only**, never synchronized to the public repo.
- Scripts use workspace-local temporary files under `scratch\tmp`; do not redirect these paths to C:.

## Why bnetserver is required

The 70205 client still uses local Battle.net-compatible authentication and realm/session tickets. `bnetserver.exe` is the private implementation, not a connection to Blizzard's real login infrastructure. It must start before the worldserver and the TLS bridge.

## Canonical boot sequence

1. Set workspace-local temporary directories and take the named bootstrap mutex.
2. If all seven listeners belong to their expected executables, skip service restarts.
3. Otherwise ensure MariaDB is running and accepting connections on 3307; identify its service-owned PID if `ExecutablePath` is unavailable to non-elevated Win32 queries.
4. Start/await the CRL endpoint 8087.
5. Start/await bnetserver 1120 and REST 8082.
6. Start/await worldserver 8085, allowing slow world initialization.
7. Verify the current-user TLS certificate; start/await the bridge 1119 and 8081.
8. Re-validate the complete set (including process ownership) and fail if any component is missing or hijacked.
9. Launch the single-instance watchdog. It checks periodically and invokes the same repair bootstrap on degradation.

The game launcher always invokes the server bootstrap before touching the client. Startup failures stop before the client is launched.

## Verification

```powershell
& 'D:\AnoCore-Server\tools\Test-ForeverServerStack.ps1' -Json
& 'D:\AnoCore-Server\Start-ForeverServer-Local.ps1'
& 'D:\AnoCore-Server\tools\Test-ForeverServerStack.ps1' -Json
Get-Content 'D:\AnoCore-Server\logs\stack-supervisor.jsonl' -Tail 20
```

Expected: all seven entries PASS, bootstrap exit code 0; no duplicate bnetserver/worldserver/bridge instances.

### Simulated dependency failure

With no player online, stop **only** the CRL HTTP process or TLS bridge as a bounded test, watch the log for `degraded` then `recovered`, and confirm all seven listeners PASS. Never force-kill worldserver with players online just to test a watchdog.

### Intentional shutdown

Use `Stop-ForeverServer.cmd` to write `scratch\stack-supervisor.stop` and shut down the stack. Next `Start-ForeverServer.cmd` clears the stop marker once full readiness succeeds. MariaDB is left up unless explicitly asked to stop it.

### Recovery and rollback

Before this change, files were backed up to `backups\startup-stack-hardening-20261009`. If the new bootstrap fails, inspect `logs\stack-supervisor.jsonl` before restoring the old entrypoints. Do not roll back or overwrite server binaries, auth configuration, existing characters or quest/vendor databases as part of a bootstrap-script rollback.

## Known boundaries

- Windows power loss, OS restart, closed user sessions and third-party intervention can stop a session-bound watchdog. Persistent OS-managed autostart would require explicit setup; this repository does not silently install OS-wide services/tasks.
- TCP readiness proves the expected processes own the expected ports; it is not equivalent to full application-protocol or gameplay verification.
- The intermittent **first autologin attempt** and remaining **trainer/vendor interaction checks** are tracked separately. These scripts do not change or claim to fix them.
- Existing SmartAI/DBErrors warnings are not resolved by successful infrastructure startup.

## Local-only validation performed on 2026-10-09

A running system reported all seven expected listeners bound by the intended executables, with MariaDB confirmed via Windows service PID (the service process does not expose its executable path to all sessions). **Fault-injection PASS (2026-10-09):** The dedicated local CRL service (PID 29964) was deliberately terminated after confirming that it belonged to the workspace Python HTTP server. The watchdog remained running as exactly one process at a 30-second interval. Evidence in `logs\stack-supervisor.jsonl`:
- 20:35:18 UTC: watchdog started (`traceId=418892b23e4a4bb5a380e847e23deb86`).
- 20:36:25 UTC: loss of one listener detected (`degraded`).
- 20:36:32 UTC: CRL endpoint restart initiated.
- 20:36:33 UTC: CRL port 8087 reopened.
- 20:36:53 UTC: complete 7/7 stack restored (`recovered`), with MariaDB, Battle.net, worldserver and TLS bridge retained.

**GitHub sync PASS:** `e61713bf78556ffb948371beca3456465bcba9e4` fetched to `D:\AnoCore-Server\git-sync` and compared against the live workspace: nine versioned deployment and operations files MATCH (normalized line endings); working tree clean. The first-attempt client autologin and full NPC UI are independent, unresolved gameplay checks.
