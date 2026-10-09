@echo off
setlocal EnableExtensions
cd /d "%~dp0"
rem Canonical local server start: validates ALL seven endpoints and starts watchdog.
call "%~dp0Start-AnoCore-NoState.cmd" %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" echo ERROR: AnoCore stack startup failed. See logs\stack-supervisor.jsonl
endlocal & exit /b %RESULT%
