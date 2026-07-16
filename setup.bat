@echo off
REM Double-click entry point for setup.ps1 -- .ps1 files don't run directly
REM on double-click by default on Windows, so this wraps it.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
pause
