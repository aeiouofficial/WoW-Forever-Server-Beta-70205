@echo off
setlocal
cd /d "%~dp0"

rem Delegate startup to the local idempotent bootstrap. It does not require
rem secrets\install-state.json and waits for listeners instead of launching duplicates.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-ForeverServer-Local.ps1" -WorldReadyTimeoutSeconds 600
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
