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
    call :select_python
    if errorlevel 1 goto :error
    call :create_venv
    if errorlevel 1 goto :error
)

call ".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Setup complete. If .env is missing, copy .env.example to .env and add your Kite credentials.
echo Then double-click run_ui.bat.
exit /b 0

:error
echo.
echo Setup failed. Review the error above.
exit /b 1

:select_python
rem STOCKS_PYTHON may contain an explicit interpreter path, without surrounding quotes.
set "STOCKS_PYTHON_ARGS="
if defined STOCKS_PYTHON (
    "%STOCKS_PYTHON%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo STOCKS_PYTHON must point to a working Python 3.12 or newer executable.
        exit /b 1
    )
    exit /b 0
)
py -3.12 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "STOCKS_PYTHON=py"
    set "STOCKS_PYTHON_ARGS=-3.12"
    exit /b 0
)
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "STOCKS_PYTHON=py"
    set "STOCKS_PYTHON_ARGS=-3"
    exit /b 0
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "STOCKS_PYTHON=python"
    exit /b 0
)
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "STOCKS_PYTHON=python3"
    exit /b 0
)
echo No working Python 3.12 or newer was found.
echo Install Python with Add Python to PATH enabled, then reopen your terminal.
echo Or set STOCKS_PYTHON to the full path of an installed python.exe.
exit /b 1

:create_venv
"%STOCKS_PYTHON%" %STOCKS_PYTHON_ARGS% -m venv .venv
exit /b %errorlevel%
