# Creates a "SelectAndRead" shortcut on the Desktop.
# Run once: right-click -> "Run with PowerShell"

$appDir  = $PSScriptRoot
$vbsFile = Join-Path $appDir "_launch.vbs"
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "SelectAndRead.lnk"
$wscript = "$env:SystemRoot\System32\wscript.exe"

if (-not (Test-Path $vbsFile)) {
    Write-Host "ERROR: _launch.vbs not found in $appDir" -ForegroundColor Red
    pause
    exit 1
}

$shell                     = New-Object -ComObject WScript.Shell
$shortcut                  = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath       = $wscript
$shortcut.Arguments        = "//B //Nologo `"$vbsFile`""
$shortcut.WorkingDirectory = $appDir
$shortcut.Description      = "SelectAndRead"
$iconIco = Join-Path $appDir "icon.ico"
if (Test-Path $iconIco) {
    $shortcut.IconLocation = $iconIco
} else {
    $shortcut.IconLocation = "$wscript,0"
}
$shortcut.Save()

Write-Host "Shortcut created at: $lnkPath" -ForegroundColor Green
pause
