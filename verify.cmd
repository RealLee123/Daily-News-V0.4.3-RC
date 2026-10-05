@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" (
  echo Run setup.cmd first.
  goto END
)

echo [Daily News V0.4.3] Verification
echo.
".venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto FAILED
echo.
".venv\Scripts\python.exe" -m daily_news db-audit
if errorlevel 1 goto FAILED
echo.
echo Verification passed.
goto END

:FAILED
echo.
echo Verification failed. Read the messages above.

:END
echo.
pause
endlocal
