@echo off
REM ===========================================================================
REM  Quran Video Generator - Surah Al-Fatihah
REM  One-time setup for Windows. Double-click this file, or run it from a
REM  terminal inside VS Code.
REM ===========================================================================
setlocal

cd /d "%~dp0"

echo.
echo  ==========================================
echo   Quran Video Generator - setup
echo  ==========================================
echo.

REM --- 1. Find Python -------------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo  [ERROR] Python was not found.
    echo.
    echo  Install Python 3.11 or newer from https://www.python.org/downloads/
    echo  During installation, tick "Add python.exe to PATH".
    echo  Then run setup.bat again.
    echo.
    pause
    exit /b 1
)

echo  [1/4] Using Python:
%PY% --version
echo.

REM --- 2. Virtual environment ----------------------------------------------
if exist ".venv\Scripts\python.exe" (
    echo  [2/4] Virtual environment already exists - reusing .venv
) else (
    echo  [2/4] Creating the virtual environment in .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo  [ERROR] Could not create the virtual environment.
        pause
        exit /b 1
    )
)
echo.

set "VENV_PY=.venv\Scripts\python.exe"

REM --- 3. Dependencies ------------------------------------------------------
echo  [3/4] Installing dependencies (this can take a few minutes) ...
"%VENV_PY%" -m pip install --upgrade pip --quiet
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  [ERROR] Installing the dependencies failed.
    echo  Check your internet connection and run setup.bat again.
    pause
    exit /b 1
)
echo.

REM --- 4. Fonts and Quran text ---------------------------------------------
echo  [4/4] Downloading the SIL OFL fonts and the verified Quran text ...
"%VENV_PY%" run.py fonts
if not exist "data\al_fatihah.json" (
    "%VENV_PY%" run.py fetch-text
) else (
    echo  data\al_fatihah.json already exists - leaving it alone.
)
echo.

echo  ==========================================
echo   Setup finished.
echo  ==========================================
echo.
echo  Next steps:
echo.
echo    1. Copy your Arabic recitation into assets\audio\recitation\
echo         001.mp3  002.mp3  003.mp3  004.mp3  005.mp3  006.mp3  007.mp3
echo    2. Copy your Urdu narration into assets\audio\urdu\
echo         001.mp3  002.mp3  003.mp3  004.mp3  005.mp3  006.mp3  007.mp3
echo    3. Fill in the "reciter:" block in config.yaml
echo.
echo  Then run, in this order:
echo.
echo     .venv\Scripts\python run.py validate
echo     .venv\Scripts\python run.py preview
echo     .venv\Scripts\python run.py render
echo.
echo  To check the layout before you have any audio:
echo.
echo     .venv\Scripts\python run.py preview --no-audio
echo.
pause
endlocal
