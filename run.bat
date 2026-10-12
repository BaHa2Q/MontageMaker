@echo off
cd /d "%~dp0"
if not exist "venv\Scripts\pythonw.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
venv\Scripts\python.exe -c "from PySide6.QtMultimedia import QMediaPlayer; import cv2, scipy" >nul 2>nul
if errorlevel 1 (
  echo The new editor needs updated libraries. Run setup.bat again.
  pause
  exit /b 1
)
start "" "venv\Scripts\pythonw.exe" "app.py"
