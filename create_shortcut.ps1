# Creates a "SelectAndRead" shortcut on the Desktop.
# Run once: right-click -> "Run with PowerShell"

$appDir   = $PSScriptRoot
$mainFile = Join-Path $appDir "main.py"
$desktop  = [Environment]::GetFolderPath("Desktop")
$lnkPath  = Join-Path $desktop "SelectAndRead.lnk"

# Use linked environment if available, otherwise fall back to venv
$configFile = Join-Path $appDir "python_path.txt"
if (Test-Path $configFile) {
    $pythonw = (Get-Content $configFile -Raw).Trim()
} else {
    $pythonw = Join-Path $appDir "venv\Scripts\pythonw.exe"
}

if (-not (Test-Path $pythonw)) {
    Write-Host "ERROR: Python executable not found: $pythonw" -ForegroundColor Red
    Write-Host "Run setup.bat to create a venv, or link_env.bat to use an existing environment." -ForegroundColor Yellow
    pause
    exit 1
}

# Convert icon.png -> icon.ico automatically if icon.png exists
$iconPng = Join-Path $appDir "icon.png"
$iconIco = Join-Path $appDir "icon.ico"
if (Test-Path $iconPng) {
    $python = $pythonw -replace "pythonw\.exe","python.exe"
    & $python -c "
from PIL import Image
src = r'$iconPng'
out = r'$iconIco'
img = Image.open(src).convert('RGBA')
sizes = [256,128,64,48,32,16]
frames = [img.resize((s,s), Image.LANCZOS) for s in sizes]
frames[0].save(out, format='ICO', sizes=[(s,s) for s in sizes], append_images=frames[1:])
"
}

$shell                     = New-Object -ComObject WScript.Shell
$shortcut                  = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath       = $pythonw
$shortcut.Arguments        = "`"$mainFile`""
$shortcut.WorkingDirectory = $appDir
$shortcut.Description      = "SelectAndRead"
if (Test-Path $iconIco) {
    $shortcut.IconLocation = $iconIco
} else {
    $shortcut.IconLocation = "$pythonw,0"
}
$shortcut.Save()

Write-Host "Shortcut created at: $lnkPath" -ForegroundColor Green
Write-Host "Using Python: $pythonw" -ForegroundColor Cyan
pause
