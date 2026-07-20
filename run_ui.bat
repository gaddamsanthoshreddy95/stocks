@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Run setup_windows.bat first.
    pause
    exit /b 1
)

call ".venv\Scripts\python.exe" -m streamlit run ui_app.py
if errorlevel 1 pause
