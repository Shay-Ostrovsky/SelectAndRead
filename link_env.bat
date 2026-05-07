@echo off
cd /d "%~dp0"

echo ====================================
echo  SelectAndRead  --  Link Environment
echo ====================================
echo.
echo Enter the full path to the pythonw.exe (or python.exe)
echo from the environment you want to use.
echo.
echo Examples:
echo   C:\Users\you\anaconda3\envs\my-env\pythonw.exe
echo   C:\Users\you\AppData\Local\Programs\Python\Python311\pythonw.exe
echo.
set /p PYPATH="Path: "

if "%PYPATH%"=="" (
    echo Cancelled.
    pause
    exit /b 0
)

if not exist "%PYPATH%" (
    echo.
    echo ERROR: File not found: %PYPATH%
    pause
    exit /b 1
)

echo %PYPATH%> python_path.txt

echo.
echo Linked! The app will now use:
echo   %PYPATH%
echo.
echo Run run.bat or create_shortcut.ps1 to launch.
pause
