-- Forever 1.60.1 / build 70205
-- Populate the classic level range used by Creature::ApplyLevelScaling().
-- Idempotent: existing entries are preserved and never overwritten.

START TRANSACTION;

INSERT INTO creature_classic_level (entry, level_min, level_max)
SELECT ct.entry, ct.minlevel, ct.maxlevel
FROM creature_template ct
WHERE ct.minlevel BETWEEN 1 AND 63
  AND ct.maxlevel BETWEEN ct.minlevel AND 63
  AND NOT EXISTS
      (SELECT 1
       FROM creature_classic_level cl
       WHERE cl.entry = ct.entry);

SELECT ROW_COUNT() AS inserted_classic_creature_levels;

COMMIT;
