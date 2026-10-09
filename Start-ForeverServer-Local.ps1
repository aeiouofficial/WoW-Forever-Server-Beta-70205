[CmdletBinding()]
param(
    [ValidateRange(30, 900)][int]$WorldReadyTimeoutSeconds = 600,
    [switch]$NoWatchdog
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$env:TEMP = Join-Path $root 'scratch\tmp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$logDirectory = Join-Path $root 'logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$traceId = [guid]::NewGuid().ToString('N')
$server = Join-Path $root 'server'
$pythonExe = Join-Path $root 'python\python.exe'
$bnetExe = Join-Path $server 'bnetserver.exe'
$worldExe = Join-Path $server 'worldserver.exe'
$bridgeExe = Join-Path $root 'bridge\ForeverTlsBridge.exe'
$databaseExe = Join-Path $root 'mariadb\bin\mysqld.exe'
$stopMarker = Join-Path $root 'scratch\stack-supervisor.stop'
$watcher = Join-Path $root 'tools\Watch-ForeverServerStack.ps1'
$mutex = [System.Threading.Mutex]::new($false, 'Local\AnoCoreForeverStackStart')
$lockAcquired = $false

function Write-StackEvent([string]$Severity, [string]$Result, [string]$Message) {
    $item = [ordered]@{
        timestampUtc = [datetime]::UtcNow.ToString('o')
        severity = $Severity
        traceId = $traceId
        component = 'server-stack-bootstrap'
        result = $Result
        message = $Message
    }
    Add-Content -LiteralPath (Join-Path $logDirectory 'stack-supervisor.jsonl') -Value ($item | ConvertTo-Json -Compress)
    Write-Output "[$Severity] $Message"
}

function Get-OwnerPath([int]$ProcId) {
    $path = (Get-CimInstance Win32_Process -Filter "ProcessId=$ProcId" -ErrorAction SilentlyContinue).ExecutablePath
    if ($path) { return $path }
    $service = Get-CimInstance Win32_Service -Filter "Name = 'ForeverCoreMariaDB'" -ErrorAction SilentlyContinue
    if ($service -and $service.State -eq 'Running' -and [int]$service.ProcessId -eq $ProcId -and
        $service.PathName.StartsWith('"' + $databaseExe + '"', [StringComparison]::OrdinalIgnoreCase)) {
        return $databaseExe
    }
    return $null
}

function Get-Listener([int]$Port) {
    @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @('127.0.0.1','0.0.0.0','::','::1') })
}

function Get-ExecutableProcesses([string]$Executable) {
    @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath -ieq $Executable
    })
}

function Assert-PortOwnership([string]$Name, [string]$Executable, [int[]]$Ports) {
    foreach ($port in $Ports) {
        foreach ($listener in @(Get-Listener $port)) {
            $actual = Get-OwnerPath ([int]$listener.OwningProcess)
            if (-not $actual -or $actual -ine $Executable) {
                throw "$Name port $port belongs to an unexpected executable (PID $($listener.OwningProcess)); refusing to treat it as ready."
            }
        }
    }
}

function Test-ComponentReady([string]$Executable, [int[]]$Ports) {
    foreach ($port in $Ports) {
        $matches = @(Get-Listener $port | Where-Object {
            $processId = $_.OwningProcess
            $actual = Get-OwnerPath $processId
            $actual -and $actual -ieq $Executable
        })
        if ($matches.Count -eq 0) { return $false }
    }
    return $true
}

function Ensure-Component(
    [string]$Name, [string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory,
    [int[]]$Ports, [int]$Timeout
) {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "$Name executable missing: $Executable"
    }
    Assert-PortOwnership $Name $Executable $Ports
    if (Test-ComponentReady $Executable $Ports) {
        Write-StackEvent 'Info' 'ready' "$Name already ready on ports $($Ports -join ',')."
        return
    }
    $processes = @(Get-ExecutableProcesses $Executable)
    if ($processes.Count -gt 1) {
        throw "Multiple $Name processes detected; refusing to spawn another instance."
    }
    if ($processes.Count -eq 0) {
        Write-StackEvent 'Info' 'starting' "Starting $Name."
        [void](Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru)
    } else {
        Write-StackEvent 'Warning' 'waiting' "$Name PID $($processes[0].ProcessId) is alive but not all listeners are ready."
    }
    $deadline = (Get-Date).AddSeconds($Timeout)
    do {
        Assert-PortOwnership $Name $Executable $Ports
        if (Test-ComponentReady $Executable $Ports) {
            Write-StackEvent 'Info' 'ready' "$Name ready on ports $($Ports -join ',')."
            return
        }
        if (@(Get-ExecutableProcesses $Executable).Count -eq 0) {
            throw "$Name exited before becoming ready."
        }
        Start-Sleep -Milliseconds 750
    } while ((Get-Date) -lt $deadline)
    throw "$Name failed readiness (ports $($Ports -join ',')) after $Timeout seconds."
}

try {
    try {
        $lockAcquired = $mutex.WaitOne([timespan]::FromMinutes(3))
    } catch [System.Threading.AbandonedMutexException] {
        $lockAcquired = $true
    }
    if (-not $lockAcquired) { throw 'Another server bootstrap holds the startup lock for over 3 minutes.' }
    Write-StackEvent 'Info' 'begin' 'Checking all required services and seven local listeners.'
    & (Join-Path $root 'tools\Test-ForeverServerStack.ps1') -Quiet
    $preflightHealthy = ($LASTEXITCODE -eq 0)
    if ($preflightHealthy) { Write-StackEvent 'Info' 'ready' 'All seven listeners passed preflight; repair skipped.' }
    if (-not $preflightHealthy) {
    $service = Get-Service -Name 'ForeverCoreMariaDB' -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne 'Running') {
        Write-StackEvent 'Warning' 'starting' 'Starting ForeverCoreMariaDB service.'
        Start-Service -Name 'ForeverCoreMariaDB'
    }
    Assert-PortOwnership 'MariaDB' $databaseExe @(3307)
    $dbDeadline = (Get-Date).AddSeconds(45)
    while (-not (Test-ComponentReady $databaseExe @(3307)) -and (Get-Date) -lt $dbDeadline) {
        Start-Sleep -Milliseconds 750
    }
    if (-not (Test-ComponentReady $databaseExe @(3307))) { throw 'MariaDB on 3307 is not ready.' }
    Write-StackEvent 'Info' 'ready' 'MariaDB ready on port 3307.'

    Ensure-Component 'CRL endpoint' $pythonExe @('-I','-m','http.server','8087','--bind','127.0.0.1','--directory',(Join-Path $root 'tls\public')) $root @(8087) 20
    Ensure-Component 'bnetserver' $bnetExe @('-c',(Join-Path $server 'bnetserver.conf')) $server @(1120,8082) 90
    Ensure-Component 'worldserver' $worldExe @('-c',(Join-Path $server 'worldserver.conf')) $server @(8085) $WorldReadyTimeoutSeconds

    $thumbprint = 'FA0C400BF678DC7A87F4F061331B19D1FE42E55C'
    if (-not (Test-Path -LiteralPath ("Cert:\CurrentUser\My\" + $thumbprint))) {
        throw 'TLS bridge certificate is missing from the current user certificate store.'
    }
    Ensure-Component 'TLS bridge' $bridgeExe @(
        '--certificate-thumbprint',$thumbprint,'--listen-address','127.0.0.1',
        '--bnet-listen','1119','--bnet-target','1120','--bnet-target-tls','true',
        '--bnet-proxy-protocol','false','--rest-listen','8081','--rest-target','8082','--rest-target-tls','true'
    ) (Join-Path $root 'bridge') @(1119,8081) 30

    foreach ($entry in @(
        @{ N='MariaDB'; E=$databaseExe; P=@(3307) },
        @{ N='CRL'; E=$pythonExe; P=@(8087) },
        @{ N='bnetserver'; E=$bnetExe; P=@(1120,8082) },
        @{ N='worldserver'; E=$worldExe; P=@(8085) },
        @{ N='TLS bridge'; E=$bridgeExe; P=@(1119,8081) }
    )) {
        Assert-PortOwnership $entry.N $entry.E $entry.P
        if (-not (Test-ComponentReady $entry.E $entry.P)) { throw "Final readiness failed for $($entry.N)." }
    }

    }
    if (-not $NoWatchdog) {
        Remove-Item -LiteralPath $stopMarker -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path -LiteralPath $watcher -PathType Leaf)) {
            throw "Watchdog script missing: $watcher"
        }
        # The watcher uses its own named mutex; redundant launches terminate immediately.
        # Avoid detecting the current PowerShell command line as an existing watcher.
        $watcherArguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $watcher + '"'
        [void](Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $watcherArguments -WorkingDirectory $root -WindowStyle Hidden -PassThru)
        Write-StackEvent 'Info' 'started' 'Background watchdog launch requested (single-instance mutex).'
    }
    Write-StackEvent 'Info' 'complete' 'Local AnoCore server is ready (7/7 ports, verified process owners).'
    exit 0
} catch {
    Write-StackEvent 'Critical' 'failed' $_.Exception.Message
    exit 1
} finally {
    if ($lockAcquired) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
