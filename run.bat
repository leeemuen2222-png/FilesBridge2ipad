@echo off
title FilesBridge2ipad Launcher
cd /d "%~dp0"

echo ==========================================
echo FilesBridge2ipad
echo ==========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH.
    echo Please install Python and enable "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

echo Python:
python --version
echo.

echo Checking dependencies...
python -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo Installing PySide6...
    python -m pip install PySide6
    if errorlevel 1 goto :error
)

python -c "import bleak" >nul 2>nul
if errorlevel 1 (
    echo Installing bleak...
    python -m pip install bleak
    if errorlevel 1 goto :error
)

echo.
echo Starting FilesBridge2ipad...
echo.

python main.py

if errorlevel 1 goto :error

exit /b 0

:error
echo.
echo ==========================================
echo [ERROR] FilesBridge2ipad failed to start.
echo ==========================================
echo.
echo The error message is shown above.
echo Please copy or screenshot it and send it to me.
echo.
pause
exit /b 1
