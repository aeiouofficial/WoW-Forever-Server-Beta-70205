[CmdletBinding()]
param(
    [ValidateRange(10, 600)][int]$WorldShutdownTimeoutSeconds = 120,
    # Kill worldserver if it does not exit in time (unsaved progress is lost).
    [switch]$Force,
    # Also stop the MariaDB service (normally left running).
    [switch]$Database
)
. (Join-Path $PSScriptRoot 'ForeverServer.Common.ps1')

# Tell the watchdog an intentional shutdown is in progress, so it will not
# immediately resurrect stopped services.
$supervisorStopMarker = Join-Path $Root 'scratch\stack-supervisor.stop'
[IO.File]::WriteAllText($supervisorStopMarker, [datetime]::UtcNow.ToString('o'))


function Send-CtrlC([int]$ProcessId) {
    # A separate hidden process attaches to the server console and sends
    # Ctrl+C; worldserver treats it as a normal shutdown and saves players.
    $script = @"
Add-Type -Namespace Forever -Name Console -MemberDefinition '
[DllImport("kernel32.dll")] public static extern bool FreeConsole();
[DllImport("kernel32.dll")] public static extern bool AttachConsole(uint id);
[DllImport("kernel32.dll")] public static extern bool SetConsoleCtrlHandler(System.IntPtr handler, bool add);
[DllImport("kernel32.dll")] public static extern bool GenerateConsoleCtrlEvent(uint ctrlEvent, uint group);'
[void][Forever.Console]::FreeConsole()
if (-not [Forever.Console]::AttachConsole($ProcessId)) { exit 2 }
[void][Forever.Console]::SetConsoleCtrlHandler([IntPtr]::Zero, `$true)
if (-not [Forever.Console]::GenerateConsoleCtrlEvent(0, 0)) { exit 3 }
Start-Sleep -Seconds 2
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    $helper = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList @('-NoProfile', '-NonInteractive', '-EncodedCommand', $encoded) `
        -WindowStyle Hidden -PassThru -Wait
    $helper.ExitCode -eq 0
}

foreach ($component in @(
        @{ Name = 'TLS bridge'; Exe = $BridgeExe; Marker = '' },
        @{ Name = 'CRL endpoint'; Exe = $PythonExe; Marker = 'http.server' })) {
    foreach ($process in (Get-ForeverProcess $component.Exe $component.Marker)) {
        Write-Step "Stopping $($component.Name) (PID $($process.ProcessId))"
        Stop-Process -Id $process.ProcessId -Force
    }
}

foreach ($process in (Get-ForeverProcess $WorldExe)) {
    Write-Step "Shutting down worldserver (PID $($process.ProcessId)), saving characters"
    [void](Send-CtrlC $process.ProcessId)
    $exited = $true
    try { Wait-Process -Id $process.ProcessId -Timeout $WorldShutdownTimeoutSeconds -ErrorAction Stop } catch { $exited = -not (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) }
    if (-not $exited) {
        if (-not $Force) { throw "worldserver is still running. Type 'server shutdown 10' in its window, or rerun with -Force." }
        Write-Warning 'worldserver did not exit in time; killing it.'
        Stop-Process -Id $process.ProcessId -Force
    }
}

foreach ($process in (Get-ForeverProcess $BnetExe)) {
    Write-Step "Stopping bnetserver (PID $($process.ProcessId))"
    Stop-Process -Id $process.ProcessId -Force
}

if ($Database -and (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue).Status -eq 'Running') {
    Write-Step 'Stopping MariaDB'
    Stop-Service -Name $ServiceName
}
Write-Host "$BrandName stopped." -ForegroundColor Green
