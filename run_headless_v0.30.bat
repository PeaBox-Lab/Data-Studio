@echo off
setlocal
cd /d "%~dp0"
if "%~2"=="" echo Usage: run_headless_v0.30.bat PROJECT.pdw JOB_ID & exit /b 2
python workbench_headless_v0.30.py --project "%~1" --job-id "%~2"
