@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" (
  echo Run setup.cmd first.
  goto END
)
if not exist ".env" (
  echo Missing .env. Run setup.cmd first.
  goto END
)

echo [Daily News V0.4.3] Incremental news run
echo The program selects morning/evening edition by Asia/Shanghai time.
echo.
".venv\Scripts\python.exe" -m daily_news run-news
if errorlevel 1 echo Run failed. Check the messages above.

:END
echo.
pause
endlocal
