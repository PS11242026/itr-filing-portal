@echo off
rem Launch the ITR Filing Portal (local, port 8511).
rem Kills any stale portal servers first: Windows allows several processes
rem to bind the same port, and stale servers keep serving OLD code.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No .venv found — run setup_portal.bat first.
    pause
    exit /b 1
)

echo Stopping any running portal servers on port 8511...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8511 ^| findstr LISTENING') do (
    taskkill /F /PID %%p >nul 2>&1
)

echo Starting the portal at http://localhost:8511 ...
".venv\Scripts\python.exe" -m streamlit run app.py
pause
