# Changelog

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
