@echo off
setlocal
set "PYTHONUTF8=1"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo First double-click 1_install.cmd.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\install_local_skill.py" --copy
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\configure_harness_mcp.py"
if errorlevel 1 goto failed
echo Connected. Restart or refresh your Codex MCP connection.
pause
exit /b 0
:failed
echo Existing settings were preserved. Read the conflict message above.
pause
exit /b 1
