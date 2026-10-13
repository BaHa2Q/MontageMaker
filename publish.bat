@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==== Montage Maker - publish an update ====
for /f "usebackq tokens=2 delims='" %%v in (`findstr /b VERSION ui\updater.py`) do set CURRENT=%%v
echo Current version: %CURRENT%
set "NEWVER="
set /p NEWVER=New version (e.g. 4.4), or Enter to keep %CURRENT%:
if "%NEWVER%"=="" set NEWVER=%CURRENT%
set "NOTES="
set /p NOTES=What changed:
powershell -NoProfile -ExecutionPolicy Bypass -File publish_update.ps1 -Version "%NEWVER%" -Notes "%NOTES%"
if errorlevel 1 (
  echo.
  echo Publishing failed. Read the message above.
) else (
  echo.
  echo Done: version %NEWVER% is published.
)
pause
