# syntax=docker/dockerfile:1
# ──────────────────────────────────────────────────────────────
# EKA Enterprise-RAG -- App-Image (Streamlit + LlamaIndex)
# Backend-Dienste (Qdrant, Ollama) laufen als eigene Compose-Services.
# ──────────────────────────────────────────────────────────────
FROM python:3.11-slim

# ffmpeg fuer faster-whisper (Audio), build-essential fuer einige Wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Erst Requirements (Layer-Caching), dann Code.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# bge-m3-Embeddings + Qdrant-Daten persistieren via Volumes (siehe compose).
ENV HF_HOME=/app/.hf_cache \
    PYTHONUNBUFFERED=1

EXPOSE 8501

# Healthcheck auf den Streamlit-Port.
HEALTHCHECK --interval=30s --timeout=5s --retries=5 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
