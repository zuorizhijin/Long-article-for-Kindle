@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting Kindle Article Sync Server...
python server.py %*
if errorlevel 1 (
    echo Server exited with error code %errorlevel%.
    pause
)
