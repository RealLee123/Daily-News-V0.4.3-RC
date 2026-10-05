@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv py -3.12 -m venv .venv
call .venv\Scripts\activate
python -m pip install -e .
python -m daily_news qq-smoke
pause
