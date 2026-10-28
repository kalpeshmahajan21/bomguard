@echo off
REM One-click start for BOMGuard on Windows
if not exist .venv (
  python -m venv .venv
  call .venv\Scripts\activate
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate
)
python -m uvicorn app.main:app --port 8000
