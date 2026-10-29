@echo off
rem Launch with a console so tracebacks are visible.
setlocal
cd /d "%~dp0"

set "PY=python"
if exist "C:\scov\Scripts\python.exe" set "PY=C:\scov\Scripts\python.exe"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

"%PY%" -m app %*
if errorlevel 1 pause
endlocal
