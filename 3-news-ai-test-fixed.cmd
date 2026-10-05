@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo [Daily News V0.4.2] Official Sources News AI Test
echo Project default text model: gemini-3.5-flash-lite
echo This test will NOT send QQ messages.
echo Run this file a second time to verify embedding cache hits.
echo.

if exist ".venv\Scripts\python.exe" goto CHECKSDK

echo Creating Python 3.12 virtual environment...
py -3.12 -m venv .venv
if errorlevel 1 goto FAILED

echo Installing project dependencies...
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto FAILED

:CHECKSDK
".venv\Scripts\python.exe" -c "from google import genai" >nul 2>&1
if not errorlevel 1 goto CHECKENV

echo Installing/updating Gemini dependencies...
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto FAILED

:CHECKENV
if exist ".env" goto RUNTEST
copy /Y ".env.example" ".env" >nul
echo Created .env. Fill GEMINI_API_KEY and run this file again.
goto END

:RUNTEST
echo Running real-news AI test...
".venv\Scripts\python.exe" -m daily_news news-ai-test
if errorlevel 1 goto FAILED
goto END

:FAILED
echo.
echo Test failed. Read the error messages above.

:END
echo.
pause
endlocal
