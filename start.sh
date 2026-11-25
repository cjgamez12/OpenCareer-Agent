#!/bin/zsh
# Start the OpenCareer Agent dashboard and open it in your browser.
# Usage:  ./start.sh
cd "$(dirname "$0")" || exit 1
source .venv/bin/activate
(sleep 2 && open http://127.0.0.1:8000) &
PYTHONPATH=src exec uvicorn opencareer.web:app --reload
