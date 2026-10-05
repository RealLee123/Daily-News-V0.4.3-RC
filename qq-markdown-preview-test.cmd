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

echo [Daily News V0.4.3+] QQ Markdown Digest Preview
echo Reads existing Events from the configured database and sends a Markdown preview.
echo Gemini calls: 0. Embeddings: 0. Database writes: 0.
echo.
".venv\Scripts\python.exe" -m daily_news qq-markdown-preview-test
if errorlevel 1 echo Markdown Preview failed. Check the QQ API error above.

:END
echo.
pause
endlocal
