@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo [Daily News V0.4.3] Setup
echo.

where py >nul 2>&1
if errorlevel 1 (
  echo Python launcher was not found. Install Python 3.12 first.
  goto FAILED
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python 3.12 virtual environment...
  py -3.12 -m venv .venv
  if errorlevel 1 goto FAILED
)

echo Installing project and verification dependencies...
".venv\Scripts\python.exe" -m pip install -e ".[dev]"
if errorlevel 1 goto FAILED

if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  echo Created .env from .env.example.
)

".venv\Scripts\python.exe" -c "from daily_news.config import Settings; s=Settings(); print('Gemini key:', 'configured' if s.gemini_api_key else 'missing'); print('QQ credentials:', 'configured' if s.qq_app_id and s.qq_app_secret else 'missing'); print('Database:', s.database_url)"
echo.
echo Setup finished. Fill missing values in .env, then use run-news.cmd.
goto END

:FAILED
echo.
echo Setup failed. Read the message above.

:END
echo.
pause
endlocal
