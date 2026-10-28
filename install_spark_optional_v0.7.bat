@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements_spark_optional_v0.7.txt
echo PySpark installed. Java 17 must also be available through JAVA_HOME.
pause
endlocal
