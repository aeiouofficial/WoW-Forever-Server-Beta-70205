[CmdletBinding()]
param([ValidateRange(15, 3600)][int]$IntervalSeconds = 30)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$env:TEMP = Join-Path $root 'scratch\tmp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$stopFile = Join-Path $root 'scratch\stack-supervisor.stop'
$logFile = Join-Path $root 'logs\stack-supervisor.jsonl'
$script:traceId = [guid]::NewGuid().ToString('N')
$mutex = [System.Threading.Mutex]::new($false, 'Local\AnoCoreForeverStackWatch')
$locked = $false

function Write-WatchEvent([string]$Severity, [string]$Result, [string]$Message) {
    $payload = [ordered]@{
        timestampUtc = [datetime]::UtcNow.ToString('o')
        severity = $Severity
        traceId = $script:traceId
        component = 'stack-watchdog'
        result = $Result
        message = $Message
    }
    Add-Content -LiteralPath $logFile -Value ($payload | ConvertTo-Json -Compress)
}

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

function Test-StackReady {
    $expected = @(
        @{ Port=3307; Path=(Join-Path $root 'mariadb\bin\mysqld.exe') },
        @{ Port=8087; Path=(Join-Path $root 'python\python.exe') },
        @{ Port=1120; Path=(Join-Path $root 'server\bnetserver.exe') },
        @{ Port=8082; Path=(Join-Path $root 'server\bnetserver.exe') },
        @{ Port=8085; Path=(Join-Path $root 'server\worldserver.exe') },
        @{ Port=1119; Path=(Join-Path $root 'bridge\ForeverTlsBridge.exe') },
        @{ Port=8081; Path=(Join-Path $root 'bridge\ForeverTlsBridge.exe') }
    )
    $connections = @(Get-NetTCPConnection -State Listen -ErrorAction Stop)
    $ids = @{}
    foreach ($item in $expected) {
        $owners = @($connections | Where-Object {
            $_.LocalPort -eq $item.Port -and $_.LocalAddress -in @('127.0.0.1','0.0.0.0','::','::1')
        })
        if ($owners.Count -eq 0) { return $false }
        $found = $false
        foreach ($listener in $owners) {
            $processId = [int]$listener.OwningProcess
            if (-not $ids.ContainsKey($processId)) {
                $ids[$processId] = Get-OwnerPath $processId
            }
            if ($ids[$processId] -and $ids[$processId] -ieq $item.Path) { $found = $true }
        }
        if (-not $found) { return $false }
    }
    return $true
}

try {
    if (Test-Path -LiteralPath $stopFile) { exit 0 }
    try { $locked = $mutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { exit 0 }
    Write-WatchEvent 'Info' 'started' ('Supervisor active at ' + $IntervalSeconds + 's interval.')
    while (-not (Test-Path -LiteralPath $stopFile)) {
        try {
            if (-not (Test-StackReady)) {
                Write-WatchEvent 'Warning' 'degraded' 'Missing or mismatched stack endpoint; attempting guarded recovery.'
                & (Join-Path $root 'Start-ForeverServer-Local.ps1') -NoWatchdog
                if ($LASTEXITCODE -ne 0 -or -not (Test-StackReady)) {
                    Write-WatchEvent 'Critical' 'failed' 'Recovery failed; will retry on the next interval.'
                } else {
                    Write-WatchEvent 'Info' 'recovered' 'Full 7/7 stack restored.'
                }
            }
        } catch {
            Write-WatchEvent 'Critical' 'exception' $_.Exception.Message
        }
        for ($i = 0; $i -lt $IntervalSeconds; $i++) {
            if (Test-Path -LiteralPath $stopFile) { break }
            Start-Sleep -Seconds 1
        }
    }
    Write-WatchEvent 'Info' 'stopped' 'Intentional shutdown marker detected.'
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
