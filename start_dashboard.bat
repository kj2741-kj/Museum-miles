@echo off
setlocal
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

for /f "tokens=5" %%p in ('netstat -aon ^| findstr :8675 ^| findstr LISTENING') do (
    taskkill /F /PID %%p >nul 2>&1
)

C:\Users\Kartavya\anaconda3\python.exe -m streamlit run app.py --server.port 8675
pause
