@echo off
title Autonomous Cloud RCA Platform (CloudLens)
cd /d "%~dp0"

echo ========================================================
echo   Starting Autonomous Cloud RCA Platform (CloudLens)...
echo ========================================================
echo.

set PYTHON_CMD=python
if exist "venv\Scripts\python.exe" (
    set PYTHON_CMD="venv\Scripts\python.exe"
    echo Using Virtual Environment: venv\Scripts\python.exe
) else (
    echo Using System Python
)

echo.
echo Launching Cockpit Dashboard in browser...
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:8000"

echo.
echo ========================================================
echo   Platform live at: http://localhost:8000
echo   API / Swagger at: http://localhost:8000/docs
echo   Press Ctrl+C to stop.
echo ========================================================
echo.
%PYTHON_CMD% -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload

pause

