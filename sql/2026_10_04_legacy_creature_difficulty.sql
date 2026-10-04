-- Restore existing legacy template values to the table read by this core.
-- Preserve explicitly configured difficulty rows; no invented stats or loot IDs.
START TRANSACTION;
INSERT INTO creature_template_difficulty
 (Entry, DifficultyID, HealthScalingExpansion, HealthModifier, ManaModifier,
  ArmorModifier, DamageModifier, CreatureDifficultyID, TypeFlags, TypeFlags2,
  LootID, PickPocketLootID, SkinLootID, GoldMin, GoldMax, VerifiedBuild)
SELECT ct.entry, 0, ct.HealthScalingExpansion, ct.HealthModifier * ct.HealthModifierExtra,
 ct.ManaModifier * ct.ManaModifierExtra, ct.ArmorModifier, ct.DamageModifier,
 ct.CreatureDifficultyID, ct.type_flags, ct.type_flags2, ct.lootid,
 ct.pickpocketloot, ct.skinloot, ct.mingold, ct.maxgold, ct.VerifiedBuild
FROM creature_template ct
WHERE NOT EXISTS (SELECT 1 FROM creature_template_difficulty d
                  WHERE d.Entry=ct.entry AND d.DifficultyID=0);
SELECT ROW_COUNT() AS insertedMappings;
COMMIT;
