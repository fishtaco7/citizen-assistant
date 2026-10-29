@echo off
rem One-time setup on a new PC: create a short-path venv and install the deps.
rem PySide6 ships very deep paths, so the venv lives in C:\scov, not in OneDrive.
setlocal
cd /d "%~dp0"

set "PAUSE_AT_END=1"
if /i "%~1"=="--no-pause" set "PAUSE_AT_END="

set "VENV=C:\scov"
set "BASE="
for %%C in ("py -3.13" "py -3" "python" "python3") do (
    if not defined BASE (
        %%~C -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "BASE=%%~C"
    )
)

if not defined BASE (
    echo Python 3.11+ not found.
    echo Install Python 3.13 ^(64-bit^) from python.org and run this again.
    echo The Microsoft Store build works too, but it redirects %%APPDATA%% ^(see README^).
    pause
    exit /b 1
)

echo Using: %BASE%
if not exist "%VENV%\Scripts\python.exe" (
    echo Creating virtual environment in %VENV% ...
    %BASE% -m venv "%VENV%"
    if errorlevel 1 (
        echo Could not create %VENV%. Create one manually somewhere short, e.g. %%LOCALAPPDATA%%\scov
        pause
        exit /b 1
    )
)

"%VENV%\Scripts\python.exe" -m pip install --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Dependency install failed.
    pause
    exit /b 1
)

echo Precompiling Python modules for a faster first start ...
"%VENV%\Scripts\python.exe" -m compileall -q app tools
if errorlevel 1 echo Precompile warning: the app will still start, but may be slower.

echo.
echo Setup complete. Start the overlay with run.bat or run-hidden.vbs
echo Optional check: "%VENV%\Scripts\python.exe" -m tools.selftest
if defined PAUSE_AT_END pause
endlocal
