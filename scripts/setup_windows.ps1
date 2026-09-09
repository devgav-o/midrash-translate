# One-time setup on the classroom Windows machine.
# Run in PowerShell from the project folder:
#     powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1

$ErrorActionPreference = "Stop"

Write-Host "`n[1/4] Checking Python..." -ForegroundColor Cyan
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Host "Python not found. Install Python 3.10+ from python.org and re-run." -ForegroundColor Red
    Write-Host "Tick 'Add Python to PATH' in the installer." -ForegroundColor Red
    exit 1
}
& $py.Source --version

Write-Host "`n[2/4] Creating virtual environment..." -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { & $py.Source -m venv .venv }

Write-Host "`n[3/4] Installing dependencies (this downloads ~2GB for PyTorch)..." -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host "`n[4/4] Downloading translation models for offline use..." -ForegroundColor Cyan
& .\.venv\Scripts\python.exe scripts\prefetch_models.py

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host "Start it during class with:  .\.venv\Scripts\python.exe run.py"
