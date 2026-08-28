param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt

$OutputName = "PACE-Controller-v$Version-Windows-x86_64"
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --uac-admin `
    --name $OutputName `
    --icon assets/pace-controller.ico `
    --paths src `
    --hidden-import serial `
    --hidden-import serial.tools.list_ports `
    pace_controller_launcher.py

$Executable = Join-Path $ProjectRoot "dist\$OutputName.exe"
if (-not (Test-Path $Executable)) {
    throw "Executable was not generated: $Executable"
}

Write-Host "Built $Executable"
