[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$ConfigPath,
    [Parameter(Mandatory)] [string]$BackupPath,
    [string]$PortalHost = 'eu.actual.bgs.test'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Client configuration is missing: $ConfigPath"
}

$content = [IO.File]::ReadAllText($ConfigPath)
$pattern = '(?im)^SET\s+portal\s+"[^"]*"\s*$'
$replacement = 'SET portal "{0}"' -f $PortalHost

if ($content -notmatch $pattern) {
    throw "Client configuration has no SET portal entry: $ConfigPath"
}

$current = [regex]::Match($content, $pattern).Value.Trim()
if ($current -eq $replacement) {
    Write-Output "PASS: client portal is $PortalHost"
    exit 0
}

$backupParent = Split-Path -Parent $BackupPath
New-Item -ItemType Directory -Path $backupParent -Force | Out-Null
if (-not (Test-Path -LiteralPath $BackupPath)) {
    Copy-Item -LiteralPath $ConfigPath -Destination $BackupPath
}

$updated = [regex]::Replace($content, $pattern, $replacement, 1)
[IO.File]::WriteAllText($ConfigPath, $updated, [Text.UTF8Encoding]::new($false))
Write-Output "UPDATED: client portal changed from '$current' to '$replacement'"
