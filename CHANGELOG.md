# Changelog

## 2026-10-04 — Rollback checkpoint for verified login/world entry

- Created the private snapshot
  `D:\AnoCore-Server\backups\working-login-enter-20261004-195637`.
- Snapshot includes the runtime world/BNet binaries and configs, TLS bridge,
  launcher files, credentials file, and transactional dumps of auth,
  characters, world and hotfix databases.
- Recorded repository commit
  `5543229453a08c7785fc422ba0269e2bf5586d98` in the snapshot manifest.
- Manual login is the only verified path. Automatic login remains explicitly
  unverified and is not described as fixed.
- No gameplay source or runtime binary was deployed in this checkpoint.

### Handoff boundary

Verified: manual login, realm/ruleset selection, character enumeration, world
entry and loading the world. Unverified: automatic login, quest windows and
quest completion, gossip/NPC interactions, vendor inventory and purchases,
multi-hit combat, and pathfinding. Continue from the rollback snapshot if a
future gameplay experiment regresses login or world entry.

## 2026-10-04 — Correct local client login autofill

- Added a targeted, window-only login helper for the prepared ForeverLocal client.
- The helper reads the ignored local credential file and pastes `admin@local.test` exactly, avoiding the prior `ADMIN` truncation at the `@` character.
- The password remains outside Git and is never written to launcher logs.

## 2026-10-04 — `fix/bnet-tls-proxy`

### Recorded and synchronized

- Documented the current live boundary: login, realm/ruleset selection,
  character enumeration and Coldridge Valley world entry are working.
- Recorded the unresolved interaction path: the live log contains NPC
  selection but no gossip, quest-giver or vendor request after selection.
- Added `sql/2026_10_04_classic_creature_levels.sql`, an idempotent migration
  that populated 10,650 legacy creature level mappings required by the Classic
  stat-scaling path. A restarted worldserver loaded all 10,650 rows.
- Kept the idempotent creature difficulty and legacy gossip migrations,
  database validators, addon API fixes and configuration template changes
  traceable on this branch.

### Verification boundary

- Database creature-wiring validator: passed with all five checks at zero,
  including `missingClassicLevels=0`.
- Migration idempotence: second live application inserted 0 rows.
- Live startup: quests and vendors load, but user-visible quest/vendor
  interaction is not accepted as verified.
- Live multi-hit combat and pathfinding are not accepted as verified.
- Runtime binaries, mutable databases, client files, certificates and logs
  remain local and are not committed.
