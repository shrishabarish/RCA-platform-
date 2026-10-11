@echo off
echo ========================================================
echo   Starting Autonomous Cloud RCA Platform...
echo ========================================================
echo.
echo Launching FastAPI Backend on http://127.0.0.1:8000 ...
start http://127.0.0.1:8000
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
pause
