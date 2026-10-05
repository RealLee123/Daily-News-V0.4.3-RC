@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo [qq-smoke] started. Now send "hello" to your bot in QQ.
echo.
".venv\Scripts\python.exe" -m daily_news qq-smoke
pause
