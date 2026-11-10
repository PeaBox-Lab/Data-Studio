@echo off
setlocal
cd /d "%~dp0\.."
if not exist .venv python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe legacy\data_studio_v0.38.py
