@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: run_orchestrator_v0.21.bat PROJECT.pdw [POLL_SECONDS]
  exit /b 2
)
set POLL_SECONDS=%~2
if "%POLL_SECONDS%"=="" set POLL_SECONDS=60
if not exist ".venv\Scripts\python.exe" py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements_v0.21.txt
.venv\Scripts\python.exe workbench_headless_v0.21.py --project "%~1" --daemon --poll-seconds %POLL_SECONDS%
exit /b %ERRORLEVEL%
