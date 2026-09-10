@echo off
title FilesBridge2ipad
cd /d "%~dp0"

echo ==========================================
echo FilesBridge2ipad
echo ==========================================
echo.

set "PYEXE="

where py >nul 2>nul
if not errorlevel 1 (
    set "PYEXE=py -3"
)

if not defined PYEXE (
    where python >nul 2>nul
    if not errorlevel 1 (
        set "PYEXE=python"
    )
)

if not defined PYEXE (
    echo [ERROR] Python was not found.
    echo.
    echo Please install Python from python.org
    echo and enable "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

echo Using:
%PYEXE% --version
echo.

echo Checking Tkinter...
%PYEXE% -c "import tkinter; print('Tkinter OK')"
if errorlevel 1 (
    echo.
    echo [ERROR] Tkinter is unavailable in this Python installation.
    echo.
    echo Please reinstall Python from python.org
    echo and keep Tcl/Tk selected during installation.
    echo.
    pause
    exit /b 1
)

echo.
echo Starting FilesBridge2ipad...
echo Right-click the orb to exit.
echo.

%PYEXE% main.py

if errorlevel 1 (
    echo.
    echo ==========================================
    echo [ERROR] Program stopped unexpectedly.
    echo ==========================================
    echo.
    pause
)
