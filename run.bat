@echo off
cd /d "%~dp0"

set PYTHONW=venv\Scripts\pythonw.exe

if exist "python_path.txt" (
    set /p PYTHONW=<python_path.txt
)

if not exist "%PYTHONW%" (
    echo Python executable not found: %PYTHONW%
    echo Run setup.bat to create a venv, or link_env.bat to use an existing environment.
    pause
    exit /b 1
)

start "" "%PYTHONW%" main.py
