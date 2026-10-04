[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$serverRoot = Split-Path -Parent $PSScriptRoot
$clientRoot = 'H:\World of Warcraft\BetaForever'
$localClient = Join-Path $clientRoot 'WowB-ForeverLocal.exe'
$python = Join-Path $serverRoot 'player-launcher-kit\runtime\python.exe'
$helper = Join-Path $serverRoot 'player-launcher-kit\watch-wow-world-auth-key-current.py'
$logRoot = Join-Path $serverRoot 'logs'
$correlationId = [guid]::NewGuid().ToString('N')
$startedUtc = [datetime]::UtcNow.ToString('o')
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$helperOutput = Join-Path $logRoot "client-world-auth-$stamp-$correlationId.json"
$armedFile = Join-Path $logRoot "client-world-auth-$stamp-$correlationId.armed"
$helperStdout = Join-Path $logRoot "client-world-auth-$stamp-$correlationId.stdout.log"
$helperStderr = Join-Path $logRoot "client-world-auth-$stamp-$correlationId.stderr.log"
$eventLog = Join-Path $logRoot 'client-launcher.log'

function Write-LaunchEvent {
    param(
        [Parameter(Mandatory)] [string] $Result,
        [Parameter(Mandatory)] [string] $Severity,
        [Parameter(Mandatory)] [string] $Message,
        [hashtable] $Details = @{}
    )

    $record = [ordered]@{
        timestampUtc = [datetime]::UtcNow.ToString('o')
        severity = $Severity
        correlationId = $correlationId
        component = 'Start-AnoWoWClient'
        affectedEntity = 'local AnoWoW client'
        result = $Result
        message = $Message
        details = $Details
    }
    Add-Content -LiteralPath $eventLog -Value (($record | ConvertTo-Json -Compress -Depth 5))
}

try {
    foreach ($path in @($localClient, $python, $helper)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required client-launch component is missing: $path"
        }
    }

    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $staleHelpers = @(
        Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -like '*watch-wow-world-auth-key-current.py*' -and
                $_.CommandLine -like '*WowB-ForeverLocal.exe*'
            }
    )
    foreach ($stale in $staleHelpers) {
        Stop-Process -Id ([int]$stale.ProcessId) -Force -ErrorAction SilentlyContinue
    }
    if ($staleHelpers.Count -gt 0) {
        Write-LaunchEvent -Result 'cleaned' -Severity 'Warning' -Message 'Stopped stale guarded helper processes before starting a new client session.' -Details @{
            processIds = @($staleHelpers | ForEach-Object { [int]$_.ProcessId })
        }
    }

    $sha256 = [Security.Cryptography.SHA256]::Create()
    $clientStream = [IO.File]::OpenRead($localClient)
    try {
        $clientSha256 = ([BitConverter]::ToString($sha256.ComputeHash($clientStream)) -replace '-', '').ToUpperInvariant()
    } finally {
        $clientStream.Dispose()
        $sha256.Dispose()
    }

    $profileCheck = @(& $python $helper '--client-sha256' $clientSha256 '--build' '70205' '--local-client' $localClient '--profile-check' $localClient 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "The guarded world-auth helper rejected the selected client: $($profileCheck -join ' ')"
    }

    Write-LaunchEvent -Result 'validated' -Severity 'Info' -Message 'Validated the separate ForeverLocal executable before launch.' -Details @{
        clientPath = $localClient
        clientSha256 = $clientSha256
        build = '1.60.1.70205'
    }

    $clientProcess = Start-Process -FilePath $localClient -WorkingDirectory $clientRoot -PassThru
    Start-Sleep -Milliseconds 750
    $runningClient = Get-Process -Id $clientProcess.Id -ErrorAction SilentlyContinue
    if (-not $runningClient) {
        throw "The ForeverLocal client exited before the world-auth helper could attach (pid $($clientProcess.Id))."
    }

    $helperArguments = '"{0}" --client-sha256 {1} --build 70205 --local-client "{2}" --pid {3} --output "{4}" --armed-file "{5}" --scan-seconds 0.1 --scan-workers 4' -f `
        $helper, $clientSha256, $localClient, $clientProcess.Id, $helperOutput, $armedFile
    $helperProcess = Start-Process -FilePath $python -ArgumentList $helperArguments -WorkingDirectory (Split-Path -Parent $helper) -WindowStyle Hidden -RedirectStandardOutput $helperStdout -RedirectStandardError $helperStderr -PassThru
    $armingDeadline = [datetime]::UtcNow.AddSeconds(60)
    while (-not (Test-Path -LiteralPath $armedFile)) {
        if (-not (Get-Process -Id $clientProcess.Id -ErrorAction SilentlyContinue)) {
            throw 'Client exited while waiting for helper readiness.'
        }
        if (-not (Get-Process -Id $helperProcess.Id -ErrorAction SilentlyContinue)) { break }
        if ([datetime]::UtcNow -ge $armingDeadline) {
            throw 'World-auth helper did not finish its initial scan within 60 seconds.'
        }
        Start-Sleep -Milliseconds 100
    }
    if (-not (Test-Path -LiteralPath $armedFile)) {
        $stderr = if (Test-Path -LiteralPath $helperStderr) { Get-Content -LiteralPath $helperStderr -Raw } else { 'no helper stderr was captured' }
        Stop-Process -Id $clientProcess.Id -Force -ErrorAction SilentlyContinue
        throw "The world-auth helper exited before arming: $stderr"
    }

    Write-LaunchEvent -Result 'started' -Severity 'Info' -Message 'Started the separate client and attached the guarded world-auth helper.' -Details @{
        clientPid = $clientProcess.Id
        helperPid = $helperProcess.Id
        helperOutput = $helperOutput
        armedFile = $armedFile
        helperStdout = $helperStdout
        helperStderr = $helperStderr
        hostsRepair = 'optional; starter reports when not elevated'
    }
    Write-Output "CLIENT_PID=$($clientProcess.Id)"
    Write-Output "HELPER_PID=$($helperProcess.Id)"
    Write-Output "HELPER_OUTPUT=$helperOutput"
    Write-Output "CORRELATION_ID=$correlationId"
    exit 0
}
catch {
    if ($clientProcess) { Stop-Process -Id $clientProcess.Id -Force -ErrorAction SilentlyContinue }
    if ($helperProcess) { Stop-Process -Id $helperProcess.Id -Force -ErrorAction SilentlyContinue }
    try {
        Write-LaunchEvent -Result 'failed' -Severity 'Critical' -Message $_.Exception.Message -Details @{
            clientPath = $localClient
            followUp = 'Inspect logs/client-launcher.log and the helper JSON output.'
        }
    } catch {
        # Preserve the original launch failure if logging itself is unavailable.
    }
    Write-Error $_.Exception.Message
    exit 1
}
