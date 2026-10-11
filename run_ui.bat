@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    call setup_windows.bat
    if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" --version >nul 2>&1
if errorlevel 1 (
    call setup_windows.bat
    if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    call setup_windows.bat
    if errorlevel 1 exit /b 1
)

if /I "%~1"=="--recover-stopped" (
    ".venv\Scripts\python.exe" scripts\run_futures_workspace.py recover-job --worker-stopped
    if errorlevel 1 (
        pause
        exit /b 1
    )
)

call ".venv\Scripts\python.exe" scripts\start_ui.py --server.fileWatcherType none --runner.magicEnabled false
if errorlevel 1 pause
