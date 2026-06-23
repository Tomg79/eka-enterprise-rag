# Baut die EKA-.exe mit PyInstaller (Windows). Im Projekt-Root ausfuehren:
#   powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))

Write-Host "== EKA: .exe bauen ==" -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

# sauberer Build
if (Test-Path build) { Remove-Item build -Recurse -Force }
if (Test-Path dist)  { Remove-Item dist  -Recurse -Force }

pyinstaller packaging\eka.spec --noconfirm

Write-Host ""
Write-Host "Fertig. Ergebnis: dist\EKA\EKA.exe" -ForegroundColor Green
Write-Host "Onboarding+Start:  dist\EKA\EKA.exe --data-root D:\Firma"
Write-Host "Nur starten:       dist\EKA\EKA.exe"
