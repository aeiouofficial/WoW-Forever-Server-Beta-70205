[CmdletBinding()]
param([switch]$Quiet, [switch]$Json)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$env:TEMP = Join-Path $root 'scratch\tmp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$results = @()
$expectations = @(
    @{Name='MariaDB'; Port=3307; Exe=(Join-Path $root 'mariadb\bin\mysqld.exe')},
    @{Name='CRL'; Port=8087; Exe=(Join-Path $root 'python\python.exe')},
    @{Name='BattleNet'; Port=1120; Exe=(Join-Path $root 'server\bnetserver.exe')},
    @{Name='BattleNetREST'; Port=8082; Exe=(Join-Path $root 'server\bnetserver.exe')},
    @{Name='Worldserver'; Port=8085; Exe=(Join-Path $root 'server\worldserver.exe')},
    @{Name='BridgeBattleNet'; Port=1119; Exe=(Join-Path $root 'bridge\ForeverTlsBridge.exe')},
    @{Name='BridgeREST'; Port=8081; Exe=(Join-Path $root 'bridge\ForeverTlsBridge.exe')}
)
function Get-OwnerPath([int]$ProcId) {
    $path = (Get-CimInstance Win32_Process -Filter "ProcessId=$ProcId" -ErrorAction SilentlyContinue).ExecutablePath
    if ($path) { return $path }
    $service = Get-CimInstance Win32_Service -Filter "Name = 'ForeverCoreMariaDB'" -ErrorAction SilentlyContinue
    $expected = Join-Path $root 'mariadb\bin\mysqld.exe'
    if ($service -and $service.State -eq 'Running' -and [int]$service.ProcessId -eq $ProcId -and
        $service.PathName.StartsWith('"' + $expected + '"', [StringComparison]::OrdinalIgnoreCase)) {
        return $expected
    }
    return $null
}

try {
    $listening = @(Get-NetTCPConnection -State Listen -ErrorAction Stop)
    $processLookup = @{}
    foreach ($expected in $expectations) {
        $onPort = @($listening | Where-Object { $_.LocalPort -eq $expected.Port -and $_.LocalAddress -in @('127.0.0.1','0.0.0.0','::','::1') })
        $matching = @()
        foreach ($entry in $onPort) {
            $id = [int]$entry.OwningProcess
            if (-not $processLookup.ContainsKey($id)) {
                $processLookup[$id] = Get-OwnerPath $id
            }
            if ($processLookup[$id] -and $processLookup[$id] -ieq $expected.Exe) { $matching += $id }
        }
        $results += [pscustomobject]@{
            component = $expected.Name
            port = $expected.Port
            status = if ($matching.Count -gt 0) { 'PASS' } elseif ($onPort.Count -gt 0) { 'WRONG_OWNER' } else { 'MISSING' }
            pid = if ($matching.Count -gt 0) { $matching[0] } else { $null }
        }
    }
} catch {
    if (-not $Quiet) { Write-Error $_.Exception.Message }
    exit 2
}
$passed = (@($results | Where-Object status -eq 'PASS').Count -eq $expectations.Count)
if ($Json) {
    [ordered]@{ ready=$passed; checkedAtUtc=[datetime]::UtcNow.ToString('o'); endpoints=$results } | ConvertTo-Json -Depth 5
} elseif (-not $Quiet) {
    $results | Format-Table -AutoSize
    Write-Output ("Stack readiness: " + @($results | Where-Object status -eq 'PASS').Count + "/" + $expectations.Count)
}
if ($passed) { exit 0 } else { exit 1 }
