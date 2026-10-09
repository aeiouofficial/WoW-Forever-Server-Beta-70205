@echo off
setlocal EnableExtensions
title AnoWoW BetaForever - Local Server Client

set "SERVER_ROOT=%~dp0"
if "%SERVER_ROOT:~-1%"=="\" set "SERVER_ROOT=%SERVER_ROOT:~0,-1%"
set "CLIENT_ROOT=H:\World of Warcraft\BetaForever"
set "LOCAL_CLIENT=%CLIENT_ROOT%\WowB-ForeverLocal.exe"
set "CLIENT_STARTER=%SERVER_ROOT%\tools\Start-AnoWoWClient.ps1"
set "PORTAL_ENFORCER=%SERVER_ROOT%\tools\Ensure-AnoWoWPortal.ps1"
set "CONFIG_BACKUP=%SERVER_ROOT%\backups\launcher-fix-20261003\Config.wtf.before-cert-host-fix"
set "HOSTS=%SystemRoot%\System32\drivers\etc\hosts"
set "CONFIG=%CLIENT_ROOT%\WTF\Config.wtf"

rem The launcher and local server can run without elevation. Only the optional
rem hosts-file repair needs administrator rights; do not block the client on UAC.
set "HAS_ADMIN=1"
net session >nul 2>&1
if errorlevel 1 set "HAS_ADMIN=0"

if not exist "%LOCAL_CLIENT%" (
    echo ERROR: prepared local client is missing: %LOCAL_CLIENT%
    exit /b 1
)

if not exist "%CONFIG%" (
    echo ERROR: client configuration is missing: %CONFIG%
    exit /b 1
)

if not exist "%CLIENT_STARTER%" (
    echo ERROR: client helper starter is missing: %CLIENT_STARTER%
    exit /b 1
)

if not exist "%PORTAL_ENFORCER%" (
    echo ERROR: client portal enforcer is missing: %PORTAL_ENFORCER%
    exit /b 1
)

echo [1/5] Ensuring local authentication routes...
if "%HAS_ADMIN%"=="1" (
    if not exist "%SERVER_ROOT%\backups" mkdir "%SERVER_ROOT%\backups" >nul 2>&1
    if not exist "%SERVER_ROOT%\backups\hosts.before" (
        copy /Y "%HOSTS%" "%SERVER_ROOT%\backups\hosts.before" >nul
        if errorlevel 1 echo WARNING: could not create the workspace-local hosts backup.
    )
    attrib -r "%HOSTS%" >nul 2>&1
    for %%R in (eu us kr tw cn) do (
        call :EnsureHost "%%R.actual.bgs.test"
        call :EnsureHost "%%R.actual.battle.net"
    )
    ipconfig /flushdns >nul 2>&1
) else (
    echo       WARNING: not elevated; skipping hosts-file repair. The prepared client/helper will continue; run elevated later if host routes are required.
)

echo [2/5] Verifying and repairing the complete 7-endpoint server stack...
call "%SERVER_ROOT%\Start-AnoCore-NoState.cmd"
if errorlevel 1 (
    echo ERROR: full-stack verification or recovery failed. See logs\stack-supervisor.jsonl.
    exit /b 1
)
call :WaitForRequiredPorts 90
if errorlevel 1 (
    echo ERROR: server sockets did not become ready: 3307, 8087, 1119, 1120, 8081, 8082, 8085
    exit /b 1
)

echo [3/5] Closing direct client instances...
taskkill /f /im WowB.exe >nul 2>&1
taskkill /f /im WowB-ForeverLocal.exe >nul 2>&1

echo [4/5] Verifying the client is configured for the local portal...
call powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PORTAL_ENFORCER%" -ConfigPath "%CONFIG%" -BackupPath "%CONFIG_BACKUP%"
if errorlevel 1 (
    echo ERROR: could not configure the TLS hostname in Config.wtf.
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$a=Resolve-DnsName -Name 'eu.actual.bgs.test' -Type A -ErrorAction SilentlyContinue; if($a.IPAddress -contains '127.0.0.1'){exit 0}; exit 1"
if errorlevel 1 (
    echo ERROR: eu.actual.bgs.test does not resolve to 127.0.0.1. Run this starter elevated once to repair hosts.
    exit /b 1
)

echo [5/5] Starting the prepared local client and its connection helper...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%CLIENT_STARTER%"
if errorlevel 1 (
    echo ERROR: could not start the prepared local client/helper.
    exit /b 1
)

echo       Prepared local client and guarded connection helper started successfully.
exit /b 0

:EnsureHost
findstr /I /C:"127.0.0.1 %~1" "%HOSTS%" >nul 2>&1
if errorlevel 1 >>"%HOSTS%" echo 127.0.0.1 %~1
exit /b 0

:AllRequiredPortsReady
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ports=@(3307,8087,1119,1120,8081,8082,8085); $listen=Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue; if (@($ports | Where-Object { $listen.LocalPort -notcontains $_ }).Count -eq 0) { exit 0 }; exit 1"
if errorlevel 1 exit /b 1
exit /b 0

:WaitForRequiredPorts
set "WAIT_SECONDS=%~1"
for /L %%N in (1,1,%WAIT_SECONDS%) do (
    call :AllRequiredPortsReady
    if not errorlevel 1 exit /b 0
    timeout /t 1 /nobreak >nul
)
exit /b 1
