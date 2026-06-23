#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# EKA Enterprise-RAG -- Linux/macOS-Installer (Wrapper um Docker Compose)
# Ausfuehren:  bash deploy/install.sh
# ──────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

echo "== EKA Enterprise-RAG: Installation =="

if ! docker version >/dev/null 2>&1; then
    echo "FEHLER: Docker ist nicht installiert oder laeuft nicht." >&2
    echo "Installation: https://docs.docker.com/engine/install/" >&2
    exit 1
fi

if [ ! -f .env ]; then
    cp .env.example .env
    echo "'.env' aus Vorlage erstellt -- bei Bedarf anpassen (Modell, S3)."
fi

MODEL="$(grep -E '^\s*OLLAMA_MODEL\s*=' .env | head -n1 | cut -d= -f2- | xargs || true)"
MODEL="${MODEL:-qwen2.5:14b}"

echo "Baue und starte Container (erstes Mal dauert) ..."
docker compose up -d --build

echo "Lade lokales LLM '${MODEL}' (einmalig, mehrere GB) ..."
docker compose exec ollama ollama pull "${MODEL}"

echo ""
echo "Fertig. Oberflaeche: http://localhost:8501"
echo "Logs:    docker compose logs -f app"
echo "Stoppen: docker compose down"
