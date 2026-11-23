@echo off
setlocal
set "PYTHONUTF8=1"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo First run 1_install.cmd.
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\harness_cli.py" %*
exit /b %errorlevel%
