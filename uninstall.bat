@echo off
rem Remove the desktop / Start menu / startup shortcuts.
rem The app folder, the C:\scov environment and your settings are kept.
setlocal
cd /d "%~dp0"

echo Removing Citizen Assistant shortcuts ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\make_shortcuts.ps1" -ProjectDir "%~dp0" -Remove
echo.
echo Shortcuts removed.
echo The app folder, C:\scov and %%APPDATA%%\Citizen Assistant were left untouched.
echo Delete the app folder to remove the overlay completely.
echo.
pause
endlocal

