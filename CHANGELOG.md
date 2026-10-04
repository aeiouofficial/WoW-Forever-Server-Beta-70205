# Changelog

## 2026-10-04 — `fix/bnet-tls-proxy`

### Recorded and synchronized

- Documented the current live boundary: login, realm/ruleset selection,
  character enumeration and Coldridge Valley world entry are working.
- Recorded the unresolved interaction path: the live log contains NPC
  selection but no gossip, quest-giver or vendor request after selection.
- Recorded the confirmed creature-stat gap: startup loads zero rows from
  `creature_classic_level`, so legacy creatures still use the core's retail
  expected-stat fallback until that compatibility layer is implemented and
  tested.
- Kept the idempotent creature difficulty and legacy gossip migrations,
  database validators, addon API fixes and configuration template changes
  traceable on this branch.

### Verification boundary

- Database creature-wiring validator: passed with all four checks at zero.
- Live startup: quests and vendors load, but user-visible quest/vendor
  interaction is not accepted as verified.
- Live multi-hit combat and pathfinding are not accepted as verified.
- Runtime binaries, mutable databases, client files, certificates and logs
  remain local and are not committed.
