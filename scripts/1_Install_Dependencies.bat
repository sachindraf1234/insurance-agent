@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo  SLBFE Automation - one-time dependency setup
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo Install Python 3.9 or newer from https://www.python.org/downloads/
    echo and TICK "Add python.exe to PATH" during installation, then run this file again.
    echo.
    pause
    exit /b 1
)

python --version
echo.
echo [1/3] Upgrading pip...
python -m pip install --upgrade pip
echo.
echo [2/3] Installing Python packages (playwright, openpyxl)...
python -m pip install playwright openpyxl
echo.
echo [3/3] Installing the Chromium browser used by Playwright...
python -m playwright install chromium
REM (on Windows no extra system libraries are needed)

echo.
echo ============================================================
echo  Setup finished. You can now run:
echo    Run_System1_Training_Checker.bat
echo    Run_System2_FBR_Checker.bat
echo ============================================================
pause
