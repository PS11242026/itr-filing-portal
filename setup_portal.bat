@echo off
rem One-time setup on a fresh machine: create the venv and install deps.
cd /d "%~dp0"

where python >nul 2>&1 || (
    echo Python 3.12+ is required — install from python.org first.
    pause & exit /b 1
)
if not exist ".venv" python -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo.
echo Setup complete. Optional checks:
echo   .venv\Scripts\python -m pip install -r requirements-dev.txt
echo   .venv\Scripts\python -m pytest
echo Then launch with run_portal.bat
pause
