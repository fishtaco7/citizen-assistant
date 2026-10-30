@echo off
rem One-click install: dependencies, shortcuts, optional startup entry.
rem After this the overlay runs without any console window.
setlocal
cd /d "%~dp0"

echo.
echo  Citizen Assistant - install
echo  --------------------
echo.

if not exist "app\main.py" (
    echo Run install.bat from inside the citizen-assistant folder.
    pause
    exit /b 1
)

call "%~dp0setup.bat" --no-pause
if errorlevel 1 (
    echo.
    echo Dependency setup failed - nothing was installed.
    pause
    exit /b 1
)

echo.
echo Creating shortcuts ...
rem No -ProjectDir: $PSScriptRoot already points at tools\, so the script finds
rem the app folder itself. Passing "%~dp0" ends in a backslash ("C:\path\"),
rem and the \" is read as an escaped quote, which arrives as an illegal path.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\make_shortcuts.ps1"
if errorlevel 1 (
    echo.
    echo Could not create the shortcuts.
    pause
    exit /b 1
)

echo.
echo  Installed folder: %~dp0
set "PY=C:\scov\Scripts\python.exe"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" "%PY%" -m app --version
echo  The desktop and Start menu shortcuts now launch this folder.

choice /C YN /N /M "Start Citizen Assistant automatically with Windows? [Y/N] "
if errorlevel 2 goto skip_startup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\make_shortcuts.ps1" -Startup
if errorlevel 1 echo Warning: could not create the startup shortcut.
:skip_startup

echo.
echo Done. Use the "Citizen Assistant" shortcut on the desktop or in the Start menu.
choice /C YN /N /M "Start it now, without a console window? [Y/N] "
if errorlevel 2 goto finish
start "" wscript.exe "%~dp0run-hidden.vbs"
:finish

echo.
pause
endlocal

