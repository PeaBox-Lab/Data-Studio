@echo off
setlocal
cd /d "%~dp0"
if not exist .venv python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements_v0.38.txt
.venv\Scripts\python.exe data_studio_v0.38.py
