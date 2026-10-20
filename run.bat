@echo off
title Autonomous Cloud RCA Platform (CloudLens)
cd /d "%~dp0"

echo ========================================================
echo   Starting Autonomous Cloud RCA Platform (CloudLens)...
echo ========================================================
echo.

:: 1. Set environment variables
set ALLOW_PRODUCTION=1
set PORT=8000
set HOST=0.0.0.0

:: 2. Activate virtual environment so all sub-processes and reloader have dotenv
if exist "%~dp0venv\Scripts\activate.bat" (
    echo Activating Virtual Environment...
    call "%~dp0venv\Scripts\activate.bat"
)

:: 3. Launch the browser directly (single window, no second terminal)
echo.
echo Launching Cockpit Dashboard at http://localhost:8000 ...
start http://localhost:8000

:: 4. Start the Uvicorn server in this window
echo.
echo ========================================================
echo   Platform running at: http://localhost:8000
echo   API / Swagger at:    http://localhost:8000/docs
echo   Press Ctrl+C to stop.
echo ========================================================
echo.
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload

pause
