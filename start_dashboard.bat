@echo off
setlocal
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo Python not found on PATH. Install Python 3.11+ from python.org
    echo ^(check "Add python.exe to PATH" during install^), then re-run this file.
    echo Or run setup.bat first, which checks this and installs dependencies.
    pause
    exit /b 1
)

for /f "tokens=5" %%p in ('netstat -aon ^| findstr :8675 ^| findstr LISTENING') do (
    taskkill /F /PID %%p >nul 2>&1
)

python -m streamlit run app.py --server.port 8675
pause
