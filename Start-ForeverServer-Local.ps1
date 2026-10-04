[CmdletBinding()]
param([ValidateRange(30, 900)][int]$WorldReadyTimeoutSeconds = 600)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$server = Join-Path $root 'server'
$bridge = Join-Path $root 'bridge'

function Test-Listener([int]$Port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalAddress '127.0.0.1' -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Wait-Listener([int]$Port, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Listener $Port) { return }
        Start-Sleep -Seconds 2
    }
    throw "Listener 127.0.0.1:$Port did not become ready within $Seconds seconds."
}

function Start-LocalComponent([string]$Name, [string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory, [int]$Port, [int]$TimeoutSeconds) {
    if (Test-Listener $Port) {
        Write-Output "$Name already listening on $Port."
        return
    }

    $existing = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath -ieq $Executable
    })
    if ($existing.Count -gt 0) {
        Write-Output "$Name is already running; waiting for port $Port."
        Wait-Listener $Port $TimeoutSeconds
        return
    }

    Write-Output "Starting $Name..."
    Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -WindowStyle Normal | Out-Null
    Wait-Listener $Port $TimeoutSeconds
}

try {
    $service = Get-Service -Name 'ForeverServer Database' -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne 'Running') {
        Start-Service -Name 'ForeverServer Database'
    }

    if (-not (Test-Listener 8087)) {
        Start-Process -FilePath (Join-Path $root 'python\python.exe') -ArgumentList @('-I', '-m', 'http.server', '8087', '--bind', '127.0.0.1', '--directory', (Join-Path $root 'tls\public')) -WorkingDirectory $root -WindowStyle Normal | Out-Null
    }

    Start-LocalComponent 'bnetserver' (Join-Path $server 'bnetserver.exe') @('-c', (Join-Path $server 'bnetserver.conf')) $server 1120 90
    Start-LocalComponent 'worldserver' (Join-Path $server 'worldserver.exe') @('-c', (Join-Path $server 'worldserver.conf')) $server 8085 $WorldReadyTimeoutSeconds
    Start-LocalComponent 'TLS bridge' (Join-Path $bridge 'ForeverTlsBridge.exe') @(
        '--certificate-thumbprint', 'FA0C400BF678DC7A87F4F061331B19D1FE42E55C',
        '--listen-address', '127.0.0.1', '--bnet-listen', '1119', '--bnet-target', '1120',
        '--bnet-target-tls', 'true', '--bnet-proxy-protocol', 'false',
        '--rest-listen', '8081', '--rest-target', '8082', '--rest-target-tls', 'true'
    ) $bridge 1119 30
    Wait-Listener 8081 30
    Write-Output 'Local AnoCore server is ready.'
    exit 0
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
