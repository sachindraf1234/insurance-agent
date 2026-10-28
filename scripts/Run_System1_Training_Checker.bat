@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo  SYSTEM 1 - Pre Departure Training Checker (all applicants)
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo Install Python 3.9+ from python.org and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist "name.txt" (
    echo [WARNING] name.txt is missing in this folder.
    echo Put the applicant table ^(name.txt^) next to this .bat file and try again.
    echo.
)

REM Optional: automatic login without typing in the browser.
REM Remove the REM below and fill in your real SLBFE agency credentials:
REM set SLBFE_USER=your_agency_username
REM set SLBFE_PASS=your_agency_password

REM Any arguments you pass are forwarded to the Python script, e.g.:
REM   Run_System1_Training_Checker.bat --only 1-3
REM   Run_System1_Training_Checker.bat --recheck-all
python "System1_Training_Checker.py" %*

echo.
echo ============================================================
echo  Finished. Outputs are saved in this same folder:
echo  progress.json, results_summary.csv, not_trained.csv,
echo  not_trained.txt, results_detail.csv, screenshots.zip
echo ============================================================
pause
