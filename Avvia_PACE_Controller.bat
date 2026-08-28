@echo off
setlocal
cd /d "%~dp0"

fltmc >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0PACE_Controller.ps1"
if errorlevel 1 (
    echo.
    echo The program closed with an error.
    echo See PACE_controller_log.txt in this folder.
    pause
)
endlocal
