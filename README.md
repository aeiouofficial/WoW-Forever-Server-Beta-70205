# WoW Forever Server — Beta 1.60.1.70205

Deployment automation for the local **AnoCore / ForeverCore** WoW private-server installation.

> This repository contains the **reviewed startup, shutdown and health-monitoring scripts**, not the complete TrinityCore source tree, client, compiled binaries, databases or private configuration. Do not publish `secrets/`, `mariadb-data/`, `client-login.local.json`, TLS private keys, runtime logs or character data.

## Supported local entrypoints

| Operation | Command |
| --- | --- |
| Start all server services | `D:\AnoCore-Server\Start-ForeverServer.cmd` |
| Start game + server | `D:\AnoCore-Server\Start-AnoWoW.cmd` |
| Check server only | `powershell.exe -NoProfile -File D:\AnoCore-Server\tools\Test-ForeverServerStack.ps1 -Json` |
| Intentional server shutdown | `D:\AnoCore-Server\Stop-ForeverServer.cmd` |

Both start entrypoints use `Start-ForeverServer-Local.ps1` through `Start-AnoCore-NoState.cmd`. A server is not reported **ready** unless all seven local TCP listeners are bound to the expected processes:

| Dependency | Port(s) |
| --- | --- |
| MariaDB / account and world databases | 3307 |
| Local CRL HTTP service | 8087 |
| Battle.net authentication and REST | 1120 / 8082 |
| Worldserver | 8085 |
| TLS authentication and REST bridge | 1119 / 8081 |

## Reliability guarantees and boundaries

- The startup routine checks service readiness, process ownership and occupied-port conflicts. It is idempotent, protected by a named process mutex and fails with a nonzero exit code rather than silently accepting a partially initialized stack.
- A single-instance watchdog checks endpoints periodically while running and attempts to restore missing components. Failure and recovery events are recorded under `D:\AnoCore-Server\logs\stack-supervisor.jsonl`.
- An **intentional** `Stop-ForeverServer.cmd` writes a workspace-local stop marker so the watchdog does not automatically undo shutdown.
- `TEMP`, `TMP` and `TMPDIR` used by these scripts point into the `D:\AnoCore-Server\scratch\tmp` workspace.
- An OS reboot, terminated watchdog, missing certificates, permission errors or unavailable dependencies can still interrupt service. The monitor is session-bound and **does not install a scheduled task or Windows service**.
- Network listener checks **do not** prove client first-login reliability, gameplay, vendor transactions or trainer windows; these require separate in-game tests.

See [STACK_RUNBOOK.md](STACK_RUNBOOK.md) for deployment, validation and rollback steps.
