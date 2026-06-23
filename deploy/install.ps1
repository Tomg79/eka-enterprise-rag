# ──────────────────────────────────────────────────────────────
# EKA Enterprise-RAG -- Windows-Installer (Wrapper um Docker Compose)
# Ausfuehren:  powershell -ExecutionPolicy Bypass -File deploy\install.ps1
# ──────────────────────────────────────────────────────────────
$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "== EKA Enterprise-RAG: Installation ==" -ForegroundColor Cyan

# 1) Docker vorhanden?
try { docker version | Out-Null }
catch {
    Write-Host "Docker Desktop ist nicht installiert oder laeuft nicht." -ForegroundColor Red
    Write-Host "Bitte Docker Desktop installieren/starten: https://www.docker.com/products/docker-desktop/"
    exit 1
}

# 2) .env anlegen, falls nicht vorhanden
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "'.env' aus Vorlage erstellt -- bei Bedarf anpassen (Modell, S3)." -ForegroundColor Yellow
}

# 3) Modellname aus .env lesen (Default qwen2.5:14b)
$model = "qwen2.5:14b"
$line = Select-String -Path ".env" -Pattern '^\s*OLLAMA_MODEL\s*=\s*(.+)\s*$' | Select-Object -First 1
if ($line) { $model = $line.Matches[0].Groups[1].Value.Trim() }

# 4) Stack bauen + starten
Write-Host "Baue und starte Container (das kann beim ersten Mal dauern) ..." -ForegroundColor Cyan
docker compose up -d --build

# 5) LLM in den Ollama-Dienst laden
Write-Host "Lade lokales LLM '$model' (einmalig, mehrere GB) ..." -ForegroundColor Cyan
docker compose exec ollama ollama pull $model

Write-Host ""
Write-Host "Fertig. Oberflaeche: http://localhost:8501" -ForegroundColor Green
Write-Host "Logs:   docker compose logs -f app"
Write-Host "Stoppen: docker compose down"
