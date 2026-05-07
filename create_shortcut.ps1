# Creates a "SelectAndRead" shortcut on the Desktop.
# Run once: right-click -> "Run with PowerShell"

$appDir  = $PSScriptRoot
$runBat  = Join-Path $appDir "run.bat"
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "SelectAndRead.lnk"

if (-not (Test-Path $runBat)) {
    Write-Host "ERROR: run.bat not found in $appDir" -ForegroundColor Red
    pause
    exit 1
}

$shell                     = New-Object -ComObject WScript.Shell
$shortcut                  = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath       = $env:ComSpec
$shortcut.Arguments        = "/c `"$runBat`""
$shortcut.WorkingDirectory = $appDir
$shortcut.WindowStyle      = 7
$shortcut.Description      = "SelectAndRead"
$iconIco = Join-Path $appDir "icon.ico"
if (Test-Path $iconIco) {
    $shortcut.IconLocation = $iconIco
} else {
    $shortcut.IconLocation = "$env:ComSpec,0"
}
$shortcut.Save()

Write-Host "Shortcut created at: $lnkPath" -ForegroundColor Green
pause
