@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\bootstrap_windows.ps1" %*
set "result=%errorlevel%"
if not "%result%"=="0" echo Installation failed. Read the message above and retry.
pause
exit /b %result%
