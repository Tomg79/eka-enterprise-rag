# EKA Quickstart (Windows): von Null zum Test.
#   powershell -ExecutionPolicy Bypass -File quickstart.ps1
# Optional eigener Datenordner:  ... quickstart.ps1 -DataRoot D:\Firma
param([string]$DataRoot = "", [string]$Password = "Demo1234!")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
$argsList = @("quickstart.py", "--password", $Password)
if ($DataRoot -ne "") { $argsList += @("--data-root", $DataRoot) }
python @argsList
