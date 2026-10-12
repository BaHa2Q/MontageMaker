@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Montage Maker Studio - Setup
echo.
echo ===== Montage Maker Studio setup / update =====
echo Close Montage Maker before updating.
echo.
py -3.12 -c "import venv" >nul 2>nul
if not errorlevel 1 (
  py -3.12 setup.py
  goto finish
)
python -c "import sys, venv; assert sys.version_info >= (3,10)" >nul 2>nul
if not errorlevel 1 (
  python setup.py
  goto finish
)
echo Installing Python 3.12 ...
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements --silent
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" setup.py
  goto finish
)
echo Python installation failed. Install Python 3.12 from python.org and retry.
pause
exit /b 1
:finish
if errorlevel 1 (
  echo.
  echo Setup failed. Read the error above and retry.
  pause
  exit /b 1
)
echo.
echo Done! Double-click run.bat.
pause
