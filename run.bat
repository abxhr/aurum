@echo off
cd /d "%~dp0"
if not exist ".venv" ( echo Creating environment... & python -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
echo Starting Aurum - open http://localhost:5000
python backend\app.py
