[CmdletBinding()]
param(
    [string]$ConfigPath,
    [string]$MysqlPath
)
$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent $PSScriptRoot
if (-not $ConfigPath) { $ConfigPath = Join-Path $workspaceRoot 'server/worldserver.conf' }
if (-not $MysqlPath) { $MysqlPath = Join-Path $workspaceRoot 'mariadb/bin/mysql.exe' }
$configText = Get-Content -LiteralPath $ConfigPath -Raw
$match = [regex]::Match($configText, '(?m)^WorldDatabaseInfo\s*=\s*"([^"]+)"')
$parts = $match.Groups[1].Value.Split(';')
if ($parts.Count -ne 5) { throw 'Cannot parse WorldDatabaseInfo' }
$previousPassword = $env:MYSQL_PWD
try {
    $env:MYSQL_PWD = $parts[3]
    $sql = 'SELECT COUNT(*) FROM creature_template ct WHERE ct.gossip_menu_id>0 AND EXISTS(SELECT 1 FROM gossip_menu gm WHERE gm.MenuID=ct.gossip_menu_id) AND NOT EXISTS(SELECT 1 FROM creature_template_gossip cg WHERE cg.CreatureID=ct.entry AND cg.MenuID=ct.gossip_menu_id);'
    $result = & $MysqlPath --host=$($parts[0]) --port=$($parts[1]) --user=$($parts[2]) --database=$($parts[4]) --batch --skip-column-names --execute=$sql
    if ($LASTEXITCODE -ne 0) { throw "Gossip database check failed, exit $LASTEXITCODE" }
    $missing = [int]($result | Select-Object -Last 1)
    [ordered]@{check='legacy-gossip-mappings'; missingMappings=$missing; result=$(if($missing){'failed'}else{'passed'})} | ConvertTo-Json -Compress
    if ($missing) { exit 1 }
    exit 0
} finally {
    $env:MYSQL_PWD = $previousPassword
}
