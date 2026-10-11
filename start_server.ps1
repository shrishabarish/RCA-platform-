Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  Starting Autonomous Cloud RCA Platform..." -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Opening Dashboard at http://127.0.0.1:8000 ..." -ForegroundColor Green
Start-Process "http://127.0.0.1:8000"
Write-Host "Starting Uvicorn Backend..." -ForegroundColor Yellow
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
