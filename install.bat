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
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\make_shortcuts.ps1" -ProjectDir "%~dp0"
if errorlevel 1 (
    echo.
    echo Could not create the shortcuts.
    pause
    exit /b 1
)

choice /C YN /N /M "Start Citizen Assistant automatically with Windows? [Y/N] "
if errorlevel 2 goto skip_startup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\make_shortcuts.ps1" -ProjectDir "%~dp0" -Startup
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

