"""
config.py — Centralized configuration for the Enterprise RAG prototype.

All paths, model names, chunking parameters, and RBAC role definitions
live here so that every module imports from a single source of truth.
"""

import os

# ──────────────────────────────────────────────
# Path Configuration
# ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Input data directories
DATA_DOCS_DIR = os.path.join(BASE_DIR, "data", "docs")
DATA_MEETINGS_DIR = os.path.join(BASE_DIR, "data", "meetings")

# Persistent Qdrant storage (local filesystem, no server needed)
QDRANT_STORAGE_PATH = os.path.join(BASE_DIR, "storage", "qdrant")

# Permissions mapping file
PERMISSIONS_FILE = os.path.join(BASE_DIR, "data", "permissions.json")

# ──────────────────────────────────────────────
# Qdrant Configuration
# ──────────────────────────────────────────────
QDRANT_COLLECTION_NAME = "enterprise_rag"

# ──────────────────────────────────────────────
# Embedding Model (runs 100% locally via HuggingFace)
# ──────────────────────────────────────────────
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"
EMBEDDING_DIMENSION = 1024          # Output dimension of bge-m3
EMBEDDING_DEVICE = "cpu"            # CPU to avoid VRAM contention with Ollama

# ──────────────────────────────────────────────
# LLM Configuration (Ollama — local inference)
# ──────────────────────────────────────────────
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:32b")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_REQUEST_TIMEOUT = 12000.0      # Generous timeout for CPU-heavy inference
OLLAMA_CONTEXT_WINDOW = 8192        # Llama 3 8B context window

# ──────────────────────────────────────────────
# Whisper Configuration (faster-whisper, local)
# ──────────────────────────────────────────────
WHISPER_MODEL_SIZE = "base"         # Options: tiny, base, small, medium, large-v3
WHISPER_DEVICE = "cpu"              # CPU for Windows compatibility (ROCm = Linux only)
WHISPER_COMPUTE_TYPE = "int8"       # int8 quantization for memory efficiency

# ──────────────────────────────────────────────
# Chunking Parameters
# ──────────────────────────────────────────────
CHUNK_SIZE = 1024                   # Characters per chunk
CHUNK_OVERLAP = 200                 # Overlap to preserve cross-chunk context

# ──────────────────────────────────────────────
# Retrieval Settings
# ──────────────────────────────────────────────
SIMILARITY_TOP_K = 5                # Number of chunks to retrieve per query

# ──────────────────────────────────────────────
# RBAC Role Hierarchy
# ──────────────────────────────────────────────
# Each key is a UI-selectable role. Its value is the list of document
# permission tags that role is authorized to access.
# "All" is the baseline — every role can see "All"-tagged documents.
ROLE_HIERARCHY = {
    "Intern":    ["All"],
    "Engineer":  ["Engineer", "All"],
    "Manager":   ["Manager", "Engineer", "All"],
    "HR":        ["HR", "All"],
    "CEO":       ["CEO", "HR", "Manager", "Engineer", "All"],
}

# Ordered list for the UI dropdown
AVAILABLE_ROLES = ["Intern", "Engineer", "Manager", "HR", "CEO"]

# ──────────────────────────────────────────────
# Prompt Template (anti-hallucination)
# ──────────────────────────────────────────────
RAG_PROMPT_TEMPLATE = """\
You are a secure enterprise assistant. Answer the question ONLY using the \
provided context below. Follow these rules strictly:

1. If the context does not contain enough information, say: \
"I don't have sufficient authorized information to answer this question."
2. Do NOT make up or infer facts beyond what is explicitly stated in the context.
3. Cite your sources by referencing the document name or meeting file and timestamp.
4. Keep your answer concise and professional.

Context:
---------
{context_str}
---------

Question: {query_str}

Answer:"""

# ──────────────────────────────────────────────
# Phase 1: Permission-Layer v2 + Audit-Log
# ──────────────────────────────────────────────
# Quelle der Wahrheit fuer Rollen/Rechte und User->Rollen-Zuordnung.
POLICY_FILE = os.path.join(BASE_DIR, "data", "policy.json")
USERS_FILE = os.path.join(BASE_DIR, "data", "users.json")
# Append-only Audit-Log (JSONL), bleibt lokal.
AUDIT_LOG_FILE = os.path.join(BASE_DIR, "data", "audit", "audit_log.jsonl")

# ──────────────────────────────────────────────
# Phase 2: Filesystem-Connector / ACL-Leser
# ──────────────────────────────────────────────
# "composite" (Default): echte per-Datei-ACL via Sidecar (<datei>.acl.json) gewinnt,
#   sonst Fallback auf Dateinamen-Praefix (Abwaertskompatibilitaet).
# Alternativen: "sidecar", "prefix", "windows" (echte NTFS-ACLs, nur auf Windows).
ACL_READER = "composite"

# ──────────────────────────────────────────────
# Phase 3: Meeting-Pipeline
# ──────────────────────────────────────────────
MEETINGS_PENDING_DIR = os.path.join(BASE_DIR, "data", "meetings_pending")
MEETINGS_TRANSCRIPTS_DIR = os.path.join(BASE_DIR, "data", "meetings_transcripts")

# ──────────────────────────────────────────────
# Phase 4: Generischer SQL-Connector
# ──────────────────────────────────────────────
# Deklarative Whitelist: Quellen (beliebige Connection-Strings), Tabellen, Spalten,
# Lookup-/Filter-Spalten. Tabellen-/Spaltennamen kommen NUR von hier; Werte werden
# parametrisiert gebunden. Siehe connectors/sql.py.
SQL_SOURCES_FILE = os.path.join(BASE_DIR, "data", "sql_sources.json")

# ──────────────────────────────────────────────
# Phase 5: Cloud-Storage-Connector (S3-kompatibel)
# ──────────────────────────────────────────────
# Zugangsdaten kommen AUSSCHLIESSLICH aus der Umgebung (AWS_ACCESS_KEY_ID/SECRET,
# AWS_PROFILE, IAM-Rolle) -- NIE hier eintragen. endpoint_url -> On-Prem-S3 (MinIO),
# damit keine Daten zu AWS gehen.
S3_ENABLED = os.environ.get("S3_ENABLED", "false").lower() == "true"
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_PREFIX = os.environ.get("S3_PREFIX", "")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL") or None   # z.B. http://minio:9000
S3_REGION = os.environ.get("S3_REGION") or None
# ACL-Leser fuer S3-Objekte: "composite" (Sidecar gewinnt, sonst Prefix) | "sidecar" | "prefix"
S3_ACL_READER = os.environ.get("S3_ACL_READER", "composite")

# ──────────────────────────────────────────────
# Phase 6: Deployment -- Qdrant Server-Modus (optional, nicht-brechend)
# ──────────────────────────────────────────────
# Default = lokaler, file-basierter Qdrant (QDRANT_STORAGE_PATH, unveraendert).
# Ist QDRANT_URL gesetzt (z.B. im Docker-Compose: http://qdrant:6333), wird der
# Server-Modus verwendet. So laeuft derselbe Code lokal wie im Container.
QDRANT_URL = os.environ.get("QDRANT_URL") or None

# ──────────────────────────────────────────────
# Phase 8: Authentifizierung (Login statt User-Dropdown)
# ──────────────────────────────────────────────
# Default AUS, damit bestehende Demos/Tests unveraendert laufen. In Produktion auf
# 'true' setzen -> die App verlangt Login; die user_id kommt dann aus der Session,
# nicht mehr aus dem Sidebar-Dropdown. Autorisierung bleibt in der PolicyEngine.
AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "false").lower() == "true"
AUTH_USERS_FILE = os.path.join(BASE_DIR, "data", "auth_users.json")
AUTH_SECRET_FILE = os.path.join(BASE_DIR, "data", ".auth_secret")
AUTH_SESSION_TTL_MIN = int(os.environ.get("AUTH_SESSION_TTL_MIN", "480"))  # 8h

# ──────────────────────────────────────────────
# Onboarding: Multi-Quellen-Ingest-Manifest
# ──────────────────────────────────────────────
# Vom Bootstrap geschrieben (aus landscape.json). Listet alle Dateifreigaben +
# Object-Stores, die indexiert werden sollen. Fehlt die Datei -> Fallback auf
# DATA_DOCS_DIR (bestehendes Verhalten).
INGEST_SOURCES_FILE = os.path.join(BASE_DIR, "data", "ingest_sources.json")
