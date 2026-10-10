@echo off
setlocal
cd /d "%~dp0"

if exist ".venv" if not exist ".venv\Scripts\python.exe" (
    echo Preserving a virtual environment copied from another operating system.
    powershell -NoProfile -Command "$backup = '.venv_backup_' + [guid]::NewGuid().ToString('N'); Rename-Item -LiteralPath '.venv' -NewName $backup"
    if errorlevel 1 goto :error
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" --version >nul 2>&1
    if errorlevel 1 (
        echo Existing virtual environment cannot run on this computer. Preserving it as a backup.
        powershell -NoProfile -Command "$backup = '.venv_backup_' + [guid]::NewGuid().ToString('N'); Rename-Item -LiteralPath '.venv' -NewName $backup"
        if errorlevel 1 goto :error
    )
)

if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 goto :error
)

call ".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Setup complete. Copy .env.example to .env and add your Kite credentials.
echo Then double-click run_ui.bat.
exit /b 0

:error
echo.
echo Setup failed. Review the error above.
exit /b 1
