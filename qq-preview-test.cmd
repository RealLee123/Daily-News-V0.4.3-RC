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

echo [Daily News V0.4.3] QQ Preview Test
echo This command reads existing Events only.
echo Gemini calls: 0. New Embeddings: 0. Formal delivery writes: 0.
echo.
".venv\Scripts\python.exe" -m daily_news qq-preview-test
if errorlevel 1 echo Preview failed. Check the messages above.

:END
echo.
pause
endlocal
