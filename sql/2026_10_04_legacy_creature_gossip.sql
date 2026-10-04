-- Preserve existing mappings; import only legacy menu IDs that actually exist.
-- Safe to rerun: primary-key matches are excluded rather than overwritten.
START TRANSACTION;
INSERT INTO creature_template_gossip (CreatureID, MenuID, VerifiedBuild)
SELECT ct.entry, ct.gossip_menu_id, ct.VerifiedBuild
FROM creature_template ct
WHERE ct.gossip_menu_id > 0
  AND EXISTS (SELECT 1 FROM gossip_menu gm WHERE gm.MenuID = ct.gossip_menu_id)
  AND NOT EXISTS (
    SELECT 1 FROM creature_template_gossip cg
    WHERE cg.CreatureID = ct.entry AND cg.MenuID = ct.gossip_menu_id
  );
SELECT ROW_COUNT() AS inserted_mappings;
COMMIT;
