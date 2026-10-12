#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then echo "→ Creating virtual environment..."; python3 -m venv .venv; fi
source .venv/bin/activate
echo "→ Installing dependencies..."
pip install -q -r requirements.txt
echo "→ Starting Aurum → http://localhost:5000"
python backend/app.py
