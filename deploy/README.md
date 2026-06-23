# EKA Enterprise-RAG -- Deployment (Phase 6)

On-Premise-Bundle: drei Container in einem privaten Netz. Nur der App-Port `8501`
ist nach aussen offen; **Qdrant und Ollama sind ausschliesslich intern erreichbar**,
das LLM laeuft lokal -- es verlassen keine Daten den Host.

## Voraussetzungen
- Docker Desktop (Windows/macOS) bzw. Docker Engine + Compose-Plugin (Linux)
- Genug Plattenplatz fuer das LLM (qwen2.5:14b ~9 GB) und die Embeddings (bge-m3)
- Empfohlen: GPU fuer Ollama (sonst laeuft die Inferenz CPU-seitig, langsamer)

## Schnellstart
**Windows:**
```powershell
powershell -ExecutionPolicy Bypass -File deploy\install.ps1
```
**Linux/macOS:**
```bash
bash deploy/install.sh
```
Danach: http://localhost:8501

Das Skript prueft Docker, legt `deploy/.env` aus der Vorlage an, baut/startet den
Stack und laedt das LLM in den Ollama-Dienst.

## Manuell
```bash
cd deploy
cp .env.example .env          # anpassen (Modell, optional S3)
docker compose up -d --build
docker compose exec ollama ollama pull qwen2.5:14b
```

## Dienste
| Dienst | Image | Port | Sichtbarkeit |
|---|---|---|---|
| app (Streamlit) | lokal gebaut (`deploy/Dockerfile`) | 8501 | Host |
| qdrant | `qdrant/qdrant` | 6333 | nur intern |
| ollama | `ollama/ollama` | 11434 | nur intern |

Die App spricht die Dienste ueber Compose-DNS an (`QDRANT_URL=http://qdrant:6333`,
`OLLAMA_BASE_URL=http://ollama:11434`). Lokal ohne Docker bleibt alles beim
file-basierten Qdrant -- **derselbe Code, nur ueber Env umgeschaltet** (`QDRANT_URL`).

## Persistenz (Named Volumes)
- `qdrant_storage` -- Vektor-Index
- `ollama_models` -- geladene LLMs
- `hf_cache` -- bge-m3-Embeddings
- `../data` (bind mount) -- Audit-Log, Dokumente, Meeting-Freigaben

## Daten indexieren
Erst-Ingest (Filesystem + optional S3) ueber die App-Oberflaeche bzw. das
Ingest-Skript anstossen. Fuer S3 in `.env` `S3_ENABLED=true` + Bucket/Endpoint setzen;
Credentials nur als Env (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`), nie ins Image.

## Updaten / Stoppen
```bash
docker compose pull && docker compose up -d --build   # aktualisieren
docker compose logs -f app                            # Logs
docker compose down                                   # stoppen (Volumes bleiben)
docker compose down -v                                # inkl. Daten loeschen (Vorsicht!)
```

## Sicherheitshinweise
- `deploy/.env` enthaelt ggf. Secrets -> **nicht committen** (in `.gitignore`).
- Standardmaessig sind Qdrant/Ollama nicht von aussen erreichbar. Wer Ports oeffnet,
  muss selbst absichern (Reverse-Proxy/Auth/TLS).
- Vor Produktivbetrieb: `../SECURITY.md` und die Security-Tests (`tests/`) durchgehen.
