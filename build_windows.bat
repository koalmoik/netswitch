@echo off
REM Builds dist\NetSwitch.exe (single file, asks for admin rights on start).
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3 -m venv .venv || python -m venv .venv || goto :error
)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e .[dev] || goto :error
.venv\Scripts\python.exe -m pytest -q || goto :error
.venv\Scripts\pyinstaller.exe --noconfirm --onefile --windowed --uac-admin ^
    --name NetSwitch run_netswitch.py || goto :error
echo.
echo Done: dist\NetSwitch.exe
exit /b 0
:error
echo Build failed.
exit /b 1
