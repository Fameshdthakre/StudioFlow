@echo off
cd /d "%~dp0"
echo ==========================================
echo Starting StudioFlow Local Server...
echo URL: http://127.0.0.1:8000
echo Press Ctrl+C to stop.
echo ==========================================
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
) else (
    python -m uvicorn app:app --host 127.0.0.1 --port 8000
)
pause

