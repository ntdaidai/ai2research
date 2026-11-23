@echo off
setlocal
set "PYTHONUTF8=1"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo First double-click 1_install.cmd.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\run_workbench.py" %*
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%
