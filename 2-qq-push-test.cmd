@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo [qq-push-test] do NOT send anything. Wait for the bot to push by itself.
echo.
".venv\Scripts\python.exe" -m daily_news qq-push-test
pause
