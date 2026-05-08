@echo off
setlocal
cd /d "%~dp0"

echo ====================================
echo  SelectAndRead  --  First-time Setup
echo ====================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Install Python 3.10 or newer from https://python.org
    echo Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Found Python %PYVER%
echo.

python -c "import sys; exit(0 if (3,10) <= sys.version_info < (3,13) else 1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python %PYVER% is not supported.
    echo Please install Python 3.12 from https://python.org
    echo Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)
echo Python version OK.
echo.

set NEED_VENV=0
if not exist "venv\Scripts\python.exe" set NEED_VENV=1
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe --version >nul 2>&1
    if errorlevel 1 set NEED_VENV=1
)

if "%NEED_VENV%"=="1" (
    if exist "venv" (
        echo Existing venv is broken or points to a missing Python -- recreating...
        rmdir /s /q venv
    )
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Could not create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo Virtual environment already exists and is valid.
)

echo.
echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Upgrading pip...
python -m pip install --upgrade pip --quiet

echo.
echo Do you have an NVIDIA GPU and want faster processing?
echo (If unsure, choose N -- the app works fine on CPU)
echo.
set /p GPU_CHOICE="Use GPU? (Y/N): "

if /i "%GPU_CHOICE%"=="Y" goto install_gpu
goto install_cpu

:install_gpu
echo.
echo Installing PyTorch with CUDA 12.1 support...
echo This is a large download (~2.5 GB) -- please wait.
echo.
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
if errorlevel 1 (
    echo.
    echo WARNING: CUDA install failed. Falling back to CPU version.
    echo Make sure your NVIDIA drivers are up to date, or choose N next time.
    echo.
    goto install_cpu
)
goto install_deps

:install_cpu
echo.
echo Installing PyTorch (CPU)...
pip install torch torchvision torchaudio
if errorlevel 1 (
    echo.
    echo ERROR: PyTorch installation failed.
    pause
    exit /b 1
)

:install_deps
echo.
echo Installing remaining dependencies...
echo.
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo ====================================
echo  Downloading AI models + voices...
echo  (~400 MB -- this may take several
echo  minutes depending on your internet.
echo  Please wait, do not close this.
echo ====================================
echo.
python _download_models.py
if errorlevel 1 (
    echo.
    echo ERROR: Model download failed. Check your internet connection and run setup again.
    pause
    exit /b 1
)

echo.
echo ====================================
echo  Setup complete!
echo.
if /i "%GPU_CHOICE%"=="Y" (
    echo  GPU mode installed. Enable it in the app
    echo  using the "Use GPU" checkbox.
) else (
    echo  CPU mode installed.
)
echo.
echo  To launch the app:
echo    - Double-click run.bat, OR
echo    - Double-click create_shortcut.bat
echo      to add a Desktop shortcut.
echo ====================================
pause
