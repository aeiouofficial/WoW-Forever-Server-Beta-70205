[CmdletBinding()]
param(
    [string]$WorkspaceRoot
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($WorkspaceRoot)) {
    $WorkspaceRoot = Split-Path -Parent $PSScriptRoot
}
$starterPath = Join-Path $WorkspaceRoot 'Start-AnoWoW.cmd'
$shortcutPath = Join-Path $env:USERPROFILE 'Desktop\Play AnoWoW.lnk'
$starter = Get-Content -Raw -LiteralPath $starterPath
$clientStarterPath = Join-Path $WorkspaceRoot 'tools\Start-AnoWoWClient.ps1'
$clientStarter = Get-Content -Raw -LiteralPath $clientStarterPath
$failures = [System.Collections.Generic.List[string]]::new()

if ($starter -notmatch '(?im)WowB-ForeverLocal\.exe') {
    $failures.Add('starter does not select the prepared WowB-ForeverLocal.exe')
}

if ($starter -notmatch '(?im)Start-AnoWoWClient\.ps1') {
    $failures.Add('starter does not invoke the guarded local-client helper flow')
}

if ($clientStarter -notmatch '(?im)watch-wow-world-auth-key-current\.py') {
    $failures.Add('client starter does not attach the guarded world-auth helper')
}

if ($clientStarter -notmatch '(?im)--profile-check') {
    $failures.Add('client starter does not validate the selected local executable before launch')
}

if ($starter -notmatch '(?im)Start-AnoCore-NoState\.cmd') {
    $failures.Add('starter does not invoke the workspace server bootstrap')
}

if ($starter -match '(?is)net session.*?if errorlevel 1\s*\(.*?Start-Process.*?RunAs') {
    $failures.Add('starter blocks launcher startup behind unconditional self-elevation')
}

if ($starter -notmatch '(?im)Test-NetConnection|Get-NetTCPConnection') {
    $failures.Add('starter has no listener-readiness check')
}

foreach ($suffix in @('actual.bgs.test', 'actual.battle.net')) {
    if ($starter -notmatch [regex]::Escape($suffix)) {
        $failures.Add("starter does not configure $suffix host routes")
    }
}

if (-not (Test-Path -LiteralPath $shortcutPath)) {
    $failures.Add("desktop shortcut is missing: $shortcutPath")
} else {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $expectedTarget = Join-Path $WorkspaceRoot 'Start-AnoWoW.cmd'
    if ([IO.Path]::GetFullPath($shortcut.TargetPath) -ne [IO.Path]::GetFullPath($expectedTarget)) {
        $failures.Add("desktop shortcut target is '$($shortcut.TargetPath)', expected '$expectedTarget'")
    }
}

$requiredPorts = 1119, 1120, 8081, 8082, 8085
foreach ($port in $requiredPorts) {
    $listening = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if (-not $listening) {
        $failures.Add("required local listener is down: $port")
    }
}

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Error $_ }
    exit 1
}

Write-Output "PASS: AnoWoW prepared-client/helper flow and local listeners are configured ($($requiredPorts -join ', '))"
