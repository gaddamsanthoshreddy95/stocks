@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment missing. Run setup_windows.bat first.
    exit /b 1
)
".venv\Scripts\python.exe" scripts\run_futures_workspace.py schedule
