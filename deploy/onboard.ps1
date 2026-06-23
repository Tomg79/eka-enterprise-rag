# Komfort: Onboarding + Start in einem Schritt.
#   powershell -ExecutionPolicy Bypass -File deploy\onboard.ps1 C:\pfad\zu\daten
param([Parameter(Mandatory=$true)][string]$Root)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $here
Write-Host "== 1/2: Onboarding (Discovery + Config) ==" -ForegroundColor Cyan
python onboarding\bootstrap.py --root $Root --apply
Write-Host "== 2/2: Stack starten ==" -ForegroundColor Cyan
powershell -ExecutionPolicy Bypass -File deploy\install.ps1
