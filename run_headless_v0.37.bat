@echo off
setlocal
cd /d "%~dp0"
if "%~2"=="" echo Usage: run_headless_v0.37.bat PROJECT.pdw JOB_ID & exit /b 2
python data_studio_headless_v0.37.py --project "%~1" --job-id "%~2"
