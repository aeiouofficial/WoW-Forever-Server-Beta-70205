[CmdletBinding()]
param([ValidateRange(30, 1800)][int]$WorldReadyTimeoutSeconds = 600)
# Starts every component in its own visible console window. Keep the Windows
# session signed in (disconnect Remote Desktop instead of signing out).
. (Join-Path $PSScriptRoot 'ForeverServer.Common.ps1')
$state = Get-ForeverState
$me = "$env:USERDOMAIN\$env:USERNAME"
if (-not (Test-Path -LiteralPath ("Cert:\CurrentUser\My\" + $state.leafThumbprint))) {
    throw "The server certificate is not installed for $me. Run Install-ForeverServer.cmd as this user (installed by $($state.installedBy))."
}

function Start-Component([string]$Title, [string]$Exe, [string[]]$Arguments, [string]$WorkingDirectory, [string]$Marker = '') {
    if (Get-ForeverProcess $Exe $Marker) { Write-Host "$Title is already running."; return $null }
    Write-Step "Starting $Title"
    # cmd hosts the console so the window keeps a readable title.
    $inner = '"' + $Exe + '" ' + (($Arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' ')
    Start-Process -FilePath $env:ComSpec -ArgumentList @('/c', "title $BrandName - $Title && $inner") -WorkingDirectory $WorkingDirectory -WindowStyle Normal -PassThru
}

if ((Get-Service -Name $ServiceName).Status -ne 'Running') { Write-Step 'Starting MariaDB'; Start-Service -Name $ServiceName }
if (-not (Wait-ForeverPort $DatabasePort 60)) { throw 'MariaDB is not listening on 127.0.0.1:3307.' }

# A local installation (127.0.0.1) accepts connections from this PC only.
$hostIp = $null
$isLocal = [Net.IPAddress]::TryParse([string]$state.publicHost, [ref]$hostIp) -and [Net.IPAddress]::IsLoopback($hostIp)
$listenAddress = if ($isLocal) { '127.0.0.1' } else { '0.0.0.0' }

$crlDir = Join-Path $Root 'tls\public'
Start-Component 'CRL (8087)' $PythonExe @('-I', '-m', 'http.server', '8087', '--bind', $listenAddress, '--directory', $crlDir) $Root 'http.server' | Out-Null

$serverDir = Join-Path $Root 'server'
Start-Component 'bnetserver' $BnetExe @('-c', (Join-Path $serverDir 'bnetserver.conf')) $serverDir | Out-Null
if (-not (Wait-ForeverPort 1120 90 { [bool](Get-ForeverProcess $BnetExe) })) { throw 'bnetserver did not open TCP 1120. See logs\auth-current.log.' }

Start-Component 'worldserver' $WorldExe @('-c', (Join-Path $serverDir 'worldserver.conf')) $serverDir | Out-Null
Write-Step "Waiting for worldserver to load (up to $WorldReadyTimeoutSeconds s)"
if (-not (Wait-ForeverPort 8085 $WorldReadyTimeoutSeconds { [bool](Get-ForeverProcess $WorldExe) })) {
    throw 'worldserver did not open TCP 8085. See logs\Server.log and logs\DBErrors.log.'
}

Start-Component 'TLS bridge (1119/8081)' $BridgeExe @('--certificate-thumbprint', $state.leafThumbprint, '--listen-address', $listenAddress,
    '--bnet-listen', '1119', '--bnet-target', '1120', '--bnet-target-tls', 'true', '--bnet-proxy-protocol', 'false',
    '--rest-listen', '8081', '--rest-target', '8082', '--rest-target-tls', 'true') (Join-Path $Root 'bridge') | Out-Null
if (-not (Wait-ForeverPort 1119 30 { [bool](Get-ForeverProcess $BridgeExe) })) { throw 'TLS bridge did not open TCP 1119. Check its window.' }

Write-Host ''
Write-Host "$BrandName is running for $($state.publicHost)." -ForegroundColor Green
Write-Host 'Create accounts in the worldserver window:  bnetaccount create <email> <password>'
Write-Host 'Stop cleanly with Stop-ForeverServer.cmd.'
Write-Brand
