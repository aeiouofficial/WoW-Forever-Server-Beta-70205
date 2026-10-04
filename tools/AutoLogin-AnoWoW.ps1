[CmdletBinding()]
param(
    [Parameter(Mandatory)] [int] $ClientPid,
    [Parameter(Mandatory)] [string] $CredentialsPath,
    [int] $TimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -IgnoreWarnings @'
using System;
using System.Runtime.InteropServices;
public static class AnoWoWWindow {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
'@

if (-not (Test-Path -LiteralPath $CredentialsPath -PathType Leaf)) { throw "Credentials file is missing: $CredentialsPath" }
$credentials = Get-Content -LiteralPath $CredentialsPath -Raw | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace($credentials.email) -or [string]::IsNullOrWhiteSpace($credentials.password)) { throw 'Credentials file must contain non-empty email and password fields.' }

$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
$process = $null
$window = [IntPtr]::Zero
while ([DateTime]::UtcNow -lt $deadline) {
    $process = Get-Process -Id $ClientPid -ErrorAction SilentlyContinue
    if (-not $process) { throw "WoW client exited before login fields were available (pid $ClientPid)." }
    $process.Refresh()
    if ($process.MainWindowHandle -ne [IntPtr]::Zero) { $window = $process.MainWindowHandle; break }
    Start-Sleep -Milliseconds 250
}
if ($window -eq [IntPtr]::Zero) { throw "WoW client window did not become available within $TimeoutSeconds seconds." }

[AnoWoWWindow]::ShowWindow($window, 9) | Out-Null
[AnoWoWWindow]::SetForegroundWindow($window) | Out-Null
$rect = New-Object AnoWoWWindow+RECT
if (-not [AnoWoWWindow]::GetWindowRect($window, [ref]$rect)) { throw 'Could not read the WoW client window bounds.' }
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -lt 800 -or $height -lt 500) { throw "Unexpected WoW client window size ${width}x${height}." }

# Forever's login form is custom-rendered, so it has no standard edit controls.
$emailX = [int]($rect.Left + ($width * 0.41))
$emailY = [int]($rect.Top + ($height * 0.46))
[System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point($emailX, $emailY)
[AnoWoWWindow]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
[AnoWoWWindow]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 150
[System.Windows.Forms.SendKeys]::SendWait('^a')
[System.Windows.Forms.Clipboard]::SetText([string]$credentials.email)
[System.Windows.Forms.SendKeys]::SendWait('^v')
[System.Windows.Forms.SendKeys]::SendWait('{TAB}')
[System.Windows.Forms.SendKeys]::SendWait('^a')
[System.Windows.Forms.Clipboard]::SetText([string]$credentials.password)
[System.Windows.Forms.SendKeys]::SendWait('^v')
[System.Windows.Forms.SendKeys]::SendWait('{TAB 2}')
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
Write-Output ('AUTOLOGIN_SUBMITTED email={0} clientPid={1}' -f $credentials.email, $ClientPid)
