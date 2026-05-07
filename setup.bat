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

call venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet

echo.
echo Do you have an NVIDIA GPU and want faster processing?
echo (If unsure, choose N -- the app works fine on CPU)
echo.
set /p GPU_CHOICE="Use GPU? (Y/N): "

if /i "%GPU_CHOICE%"=="Y" (
    echo.
    echo Installing PyTorch with CUDA 12.1 support...
    echo This is a large download (~2.5 GB) -- please wait.
    echo.
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
    if errorlevel 1 (
        echo.
        echo WARNING: CUDA PyTorch install failed. Falling back to CPU version.
        echo Make sure your NVIDIA drivers are up to date, or choose N next time.
        echo.
        pip install torch torchvision torchaudio
    )
) else (
    echo.
    echo Installing PyTorch ^(CPU^)...
    pip install torch torchvision torchaudio
)

if errorlevel 1 (
    echo.
    echo ERROR: PyTorch installation failed.
    pause
    exit /b 1
)

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
