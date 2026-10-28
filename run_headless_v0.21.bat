@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: run_headless_v0.21.bat PROJECT.pdw JOB_ID
  exit /b 2
)
if "%~2"=="" (
  echo Usage: run_headless_v0.21.bat PROJECT.pdw JOB_ID
  exit /b 2
)
if not exist ".venv\Scripts\python.exe" py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements_v0.21.txt
.venv\Scripts\python.exe workbench_headless_v0.21.py --project "%~1" --job-id "%~2"
exit /b %ERRORLEVEL%
