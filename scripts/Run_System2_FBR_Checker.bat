@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo  SYSTEM 2 - Female FBR Verification Checker (female only)
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo Install Python 3.9+ from python.org and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist "name_female.txt" (
    echo [WARNING] name_female.txt is missing in this folder.
    echo Put the FEMALE applicant table next to this .bat file and try again.
    echo.
)

REM Optional: automatic login without typing in the browser.
REM Remove the REM below and fill in your real SLBFE agency credentials:
REM set SLBFE_USER=your_agency_username
REM set SLBFE_PASS=your_agency_password

REM Any arguments you pass are forwarded to the Python script, e.g.:
REM   Run_System2_FBR_Checker.bat --only 1-3
REM   Run_System2_FBR_Checker.bat --headless
python "System2_FBR_Checker.py" %*

echo.
echo ============================================================
echo  Finished. Outputs are saved under the output\ folder:
echo  fbr_report_latest.xlsx, fbr_report_latest.csv,
echo  fbr_summary.json, fbr_screenshots\
echo ============================================================
pause
