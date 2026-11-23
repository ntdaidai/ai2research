@echo off
setlocal
set "PYTHONUTF8=1"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo First double-click 1_install.cmd.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\verify_install.py" --full
set "result=%errorlevel%"
echo The report is in .local\install-check.json.
pause
exit /b %result%
