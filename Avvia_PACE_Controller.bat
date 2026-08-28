@echo off
setlocal
cd /d "%~dp0"

fltmc >nul 2>&1
if errorlevel 1 (
    echo Richiesta dei privilegi di amministratore...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0PACE_Controller.ps1"
if errorlevel 1 (
    echo.
    echo Il programma si e chiuso con un errore.
    echo Consulta PACE_controller_log.txt nella stessa cartella.
    pause
)
endlocal
