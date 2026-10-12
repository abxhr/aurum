@echo off
cd /d "%~dp0"
if not exist ".venv" ( python -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-background.ps1"
pause
