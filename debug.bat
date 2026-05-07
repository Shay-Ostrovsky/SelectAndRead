@echo off
cd /d "%~dp0"

set PYTHON=venv\Scripts\python.exe

if exist "python_path.txt" (
    set /p PYTHON=<python_path.txt
    set PYTHON=%PYTHON:pythonw.exe=python.exe%
)

if not exist "%PYTHON%" (
    echo Python executable not found: %PYTHON%
    echo Run setup.bat to create a venv, or link_env.bat to use an existing environment.
    pause
    exit /b 1
)

"%PYTHON%" main.py
pause
