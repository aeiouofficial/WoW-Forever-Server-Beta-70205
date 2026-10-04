[CmdletBinding()]
param([string]$ConfigPath,[string]$MysqlPath)
$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent $PSScriptRoot
if (-not $ConfigPath) { $ConfigPath = Join-Path $workspaceRoot 'server/worldserver.conf' }
if (-not $MysqlPath) { $MysqlPath = Join-Path $workspaceRoot 'mariadb/bin/mysql.exe' }
$configText = Get-Content -LiteralPath $ConfigPath -Raw
$parts = [regex]::Match($configText, '(?m)^WorldDatabaseInfo\s*=\s*"([^"]+)"').Groups[1].Value.Split(';')
if ($parts.Count -ne 5) { throw 'Cannot parse WorldDatabaseInfo' }
$previousPassword = $env:MYSQL_PWD
try {
    $env:MYSQL_PWD = $parts[3]
    $sql = @'
SELECT 'missingDifficultyRows',COUNT(*) FROM creature_template ct
 WHERE NOT EXISTS(SELECT 1 FROM creature_template_difficulty d WHERE d.Entry=ct.entry AND d.DifficultyID=0)
UNION ALL SELECT 'invalidHealthMultipliers',COUNT(*) FROM creature_template_difficulty WHERE HealthModifier<=0
UNION ALL SELECT 'missingVanillaLevelStats',COUNT(*) FROM creature_template ct
 WHERE ct.minlevel BETWEEN 1 AND 63 AND NOT EXISTS
 (SELECT 1 FROM creature_classlevelstats s WHERE s.level=ct.minlevel AND s.class=ct.unit_class AND s.basehp0>0)
UNION ALL SELECT 'missingClassicLevels',COUNT(*) FROM creature_template ct
 WHERE ct.minlevel BETWEEN 1 AND 63 AND ct.maxlevel BETWEEN ct.minlevel AND 63
   AND NOT EXISTS
   (SELECT 1 FROM creature_classic_level cl WHERE cl.entry=ct.entry)
UNION ALL SELECT 'invalidVendorFlags',COUNT(*) FROM creature_template ct
 WHERE EXISTS(SELECT 1 FROM npc_vendor v WHERE v.entry=ct.entry) AND (ct.npcflag & 128)=0;
'@
    $rows = & $MysqlPath --host=$($parts[0]) --port=$($parts[1]) --user=$($parts[2]) --database=$($parts[4]) --batch --skip-column-names --execute=$sql
    if ($LASTEXITCODE -ne 0) { throw "Creature wiring query failed, exit $LASTEXITCODE" }
    $checks = [ordered]@{}
    foreach ($row in $rows) {
        $columns = $row -split "`t"
        if ($columns.Count -ne 2 -or $columns[1] -notmatch '^\d+$') { throw 'Unexpected database check output' }
        $checks[$columns[0]] = [int]$columns[1]
    }
    if ($checks.Count -ne 5) { throw 'Incomplete database checks' }
    $failed = @($checks.Values | Where-Object { $_ -ne 0 }).Count -gt 0
    [ordered]@{ check='creature-database-wiring'; checks=$checks; result=$(if($failed){'failed'}else{'passed'}) } | ConvertTo-Json -Depth 3 -Compress
    if ($failed) { exit 1 }
    exit 0
} finally { $env:MYSQL_PWD = $previousPassword }
