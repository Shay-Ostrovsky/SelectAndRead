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
    echo kokoro requires Python 3.10, 3.11, or 3.12.
    echo Please install Python 3.12 from https://python.org
    echo Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)
echo Python version OK.
echo.

if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Could not create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo Virtual environment already exists, skipping creation.
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
echo  Downloading AI models...
echo  This may take several minutes
echo  depending on your internet speed.
echo  (~400 MB total -- please wait)
echo ====================================
echo.
python -c "import easyocr; easyocr.Reader(['en'], verbose=True)"
if errorlevel 1 (
    echo.
    echo WARNING: EasyOCR model download failed. It will retry on first launch.
)
python -c "from kokoro import KPipeline; KPipeline(lang_code='a', repo_id='hexgrad/Kokoro-82M')"
if errorlevel 1 (
    echo.
    echo WARNING: Kokoro model download failed. It will retry on first launch.
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
echo    - Run create_shortcut.ps1 once
echo      to add a Desktop shortcut.
echo ====================================
pause
