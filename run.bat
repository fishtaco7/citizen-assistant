@echo off
rem Launch the overlay without a console window.
setlocal
cd /d "%~dp0"

set "PY=pythonw"
if exist "C:\scov\Scripts\pythonw.exe" set "PY=C:\scov\Scripts\pythonw.exe"
if exist "%~dp0.venv\Scripts\pythonw.exe" set "PY=%~dp0.venv\Scripts\pythonw.exe"

if /i "%PY%"=="pythonw" (
    where pythonw >nul 2>nul
    if errorlevel 1 (
        echo Python was not found on this PC.
        echo.
        echo Run setup.bat once, or install Python 3.11+ from python.org
        echo and tick "Add python.exe to PATH" during setup.
        echo.
        pause
        exit /b 1
    )
)

start "" "%PY%" -m app %*
endlocal
