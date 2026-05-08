@echo off
if exist "%~dp0SelectAndRead.exe" (
    start "" "%~dp0SelectAndRead.exe"
) else (
    wscript "%~dp0_launch.vbs"
)
