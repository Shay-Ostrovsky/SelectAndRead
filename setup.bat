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
echo This installs CUDA-enabled wheels for BOTH speech (PyTorch with CUDA
echo 12.1) and OCR (onnxruntime-gpu). Either model can still be toggled
echo CPU/GPU later in the app -- this just decides whether the GPU option
echo is available at all.
echo (If unsure, choose N -- the app works fine on CPU.)
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
    set GPU_CHOICE=N
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

if /i "%GPU_CHOICE%"=="Y" (
    echo.
    echo Swapping onnxruntime CPU for onnxruntime-gpu so OCR can use CUDA...
    echo (~250 MB from PyPI -- fast.^)
    pip uninstall -y onnxruntime onnxruntime-gpu >nul
    pip install onnxruntime-gpu
    if errorlevel 1 (
        echo.
        echo WARNING: onnxruntime-gpu install failed -- OCR will run on CPU.
        echo Make sure your NVIDIA drivers are up to date, or choose N next time.
        echo Reinstalling the CPU runtime so the app still works...
        pip install onnxruntime >nul
    )
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
echo Building SelectAndRead.exe launcher...
set CSC=
if exist "%SystemRoot%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" set CSC=%SystemRoot%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
if "%CSC%"=="" if exist "%SystemRoot%\Microsoft.NET\Framework\v4.0.30319\csc.exe" set CSC=%SystemRoot%\Microsoft.NET\Framework\v4.0.30319\csc.exe

if not "%CSC%"=="" (
    if exist "icon.ico" (
        "%CSC%" /nologo /out:SelectAndRead.exe /target:winexe /win32icon:icon.ico /reference:System.Windows.Forms.dll /reference:System.Drawing.dll _launcher.cs >nul
    ) else (
        "%CSC%" /nologo /out:SelectAndRead.exe /target:winexe /reference:System.Windows.Forms.dll /reference:System.Drawing.dll _launcher.cs >nul
    )
    if exist "SelectAndRead.exe" (
        echo Launcher built.
    ) else (
        echo WARNING: Launcher build failed -- run.bat will use the VBS fallback.
    )
) else (
    echo WARNING: .NET compiler not found -- run.bat will use the VBS fallback.
)

echo.
echo ====================================
echo  Setup complete!
echo.
if /i "%GPU_CHOICE%"=="Y" (
    echo  GPU mode installed. Enable it in the app
    echo  using the "Use GPU - TTS" / "Use GPU - OCR" checkboxes.
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
