"""
ingest.py — Module A: Data Ingestion & Transcription

Handles two pipelines:
  1. PDF/Document ingestion from ./data/docs
  2. Audio meeting transcription from ./data/meetings (via faster-whisper)

Both pipelines assign RBAC role metadata from permissions.json and store
embedded chunks in the local Qdrant vector database.
"""

import json
import os
import logging
from typing import Optional

import qdrant_client
from llama_index.core import (
    VectorStoreIndex,
    StorageContext,
    SimpleDirectoryReader,
    Document,
)
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode
from llama_index.core import Settings
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore

from config import (
    DATA_DOCS_DIR,
    DATA_MEETINGS_DIR,
    QDRANT_STORAGE_PATH,
    QDRANT_COLLECTION_NAME,
    EMBEDDING_MODEL_NAME,
    EMBEDDING_DIMENSION,
    EMBEDDING_DEVICE,
    PERMISSIONS_FILE,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    WHISPER_MODEL_SIZE,
    WHISPER_DEVICE,
    WHISPER_COMPUTE_TYPE,
)

from config import (
    ACL_READER, QDRANT_URL, INGEST_SOURCES_FILE,
    S3_ENABLED, S3_BUCKET, S3_PREFIX, S3_ENDPOINT_URL, S3_REGION, S3_ACL_READER,
)
from connectors import (
    FilesystemConnector, get_acl_reader, S3Connector, get_s3_acl_reader,
    ApiDumpConnector,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Shared Utilities
# ──────────────────────────────────────────────

def _load_permissions() -> dict:
    """Load the file-to-role permission mapping from permissions.json.
    
    Returns a dict with 'default_role' and 'files' keys.
    Files not listed in the mapping receive the default role.
    """
    if os.path.exists(PERMISSIONS_FILE):
        with open(PERMISSIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    # Fallback: everything is public
    logger.warning("permissions.json not found — all documents get role=['All']")
    return {"default_role": ["All"], "files": {}}


def _get_roles_for_file(filename: str, permissions: dict = None) -> list[str]:
    """
    Simuliert einen SharePoint-Crawler, der die ACL (Access Control List)
    direkt aus den Metadaten/Dateinamen ausliest.
    """
    name_upper = filename.upper()
    if name_upper.startswith("PUBLIC_"):
        return ["PUBLIC"]
    elif name_upper.startswith("SALES_"):
        return ["SALES"]
    elif name_upper.startswith("HR_CONFIDENTIAL_"):
        return ["HR_CONFIDENTIAL"]
    else:
        return ["PUBLIC"] # Fallback


def _get_embed_model() -> HuggingFaceEmbedding:
    """Initialize the local HuggingFace embedding model.
    
    Uses BAAI/bge-m3 on CPU to avoid VRAM contention with Ollama.
    The model is cached locally after first download — fully offline thereafter.
    """
    return HuggingFaceEmbedding(
        model_name=EMBEDDING_MODEL_NAME,
        device=EMBEDDING_DEVICE,
    )


def _make_qdrant_client():
    """Server-Modus, wenn QDRANT_URL gesetzt ist (Docker/On-Prem-Server), sonst der
    lokale, file-basierte Client (Default, unveraendert). Gleicher Code lokal wie im
    Container."""
    if QDRANT_URL:
        return qdrant_client.QdrantClient(url=QDRANT_URL)
    return qdrant_client.QdrantClient(path=QDRANT_STORAGE_PATH)


def _get_qdrant_vector_store() -> QdrantVectorStore:
    """Create a Qdrant vector store (local file-based oder Server via QDRANT_URL)."""
    client = _make_qdrant_client()
    return QdrantVectorStore(
        client=client,
        collection_name=QDRANT_COLLECTION_NAME,
        # flat_metadata=True ensures payload keys like 'role' are stored
        # at the top level, making FieldCondition(key="role") work directly
        # without needing nested "metadata.role" paths.
        flat_metadata=True,
    )


# ──────────────────────────────────────────────
# Pipeline 1: Document Ingestion
# ──────────────────────────────────────────────

def ingest_documents(progress_callback=None) -> dict:
    """Phase 2: Liest Dokumente ueber den FilesystemConnector und taggt jeden Chunk
    mit den aus der Datei-ACL gelesenen Gruppen (payload 'acl_groups'). Ersetzt die
    fruehere Dateinamen-Praefix-Simulation; der ACL-Leser ist via config.ACL_READER
    waehlbar (composite|sidecar|prefix|windows).
    """
    if not os.path.exists(DATA_DOCS_DIR) or not os.listdir(DATA_DOCS_DIR):
        logger.warning(f"No documents found in {DATA_DOCS_DIR}")
        return {"documents": 0, "chunks": 0}

    if progress_callback:
        progress_callback("Lese Dokumente + ACLs ...", 0.1)

    reader = get_acl_reader(ACL_READER)
    connector = FilesystemConnector(DATA_DOCS_DIR, reader, recursive=True)
    splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)

    n_docs, all_nodes = _nodes_from_connector(connector, splitter)

    logger.info(f"Connector '{connector.name}' lieferte {n_docs} Dokument(e), {len(all_nodes)} Chunks")
    if progress_callback:
        progress_callback(f"Embedde {len(all_nodes)} Chunks ...", 0.5)

    embed_model = _get_embed_model()
    Settings.embed_model = embed_model
    # Datei-basierter Qdrant sperrt den Ordner exklusiv pro Client -> Client hier
    # explizit anlegen und nach dem Schreiben SCHLIESSEN (Lock freigeben).
    client = _make_qdrant_client()
    try:
        vector_store = QdrantVectorStore(client=client, collection_name=QDRANT_COLLECTION_NAME, flat_metadata=True)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        VectorStoreIndex(nodes=all_nodes, storage_context=storage_context, show_progress=True)
    finally:
        try:
            client.close()
        except Exception:
            pass

    if progress_callback:
        progress_callback("Document ingestion complete!", 1.0)
    stats = {"documents": n_docs, "chunks": len(all_nodes)}
    logger.info(f"Ingestion complete: {stats}")
    return stats


def _nodes_from_connector(connector, splitter):
    """Baut LlamaIndex-Nodes aus EINEM Connector (Filesystem/S3). Jeder Chunk traegt
    die acl_groups AUS DER QUELLE (Connector). Fail-closed: Objekte ohne Gruppen werden
    indexiert, sind aber fuer niemanden sichtbar. Returns (n_docs, nodes)."""
    all_nodes = []
    n_docs = 0
    for cdoc in connector.iter_documents():
        n_docs += 1
        file_name = cdoc.metadata.get("file_name", cdoc.doc_id)
        source_type = cdoc.metadata.get("source_type", "document")
        doc = Document(text=cdoc.text,
                       metadata={"file_name": file_name, "source_type": source_type})
        nodes = splitter.get_nodes_from_documents([doc])
        for node in nodes:
            node.metadata["acl_groups"] = list(cdoc.acl_groups)
            node.metadata["source_type"] = source_type
            node.metadata["file_name"] = file_name
            node.excluded_llm_metadata_keys = ["acl_groups", "source_type"]
            node.excluded_embed_metadata_keys = ["acl_groups", "source_type"]
        all_nodes.extend(nodes)
    return n_docs, all_nodes


def ingest_s3_documents(progress_callback=None) -> dict:
    """Phase 5: liest Text-Objekte aus einem S3-kompatiblen Bucket und taggt jeden Chunk
    mit den PER-OBJEKT-ACL gelesenen Gruppen (payload 'acl_groups'). Additiv zum
    Filesystem-Ingest; schreibt in dieselbe Qdrant-Collection. Aktiv nur, wenn
    S3_ENABLED gesetzt ist und ein Bucket konfiguriert wurde (sonst fail-closed/Skip)."""
    if not S3_ENABLED:
        logger.info("S3-Ingest uebersprungen (S3_ENABLED=false).")
        return {"documents": 0, "chunks": 0, "skipped": "disabled"}
    if not S3_BUCKET:
        logger.error("S3_ENABLED, aber S3_BUCKET fehlt -> Abbruch (fail-closed).")
        return {"documents": 0, "chunks": 0, "error": "no_bucket"}

    if progress_callback:
        progress_callback("Lese S3-Objekte + ACLs ...", 0.1)

    reader = get_s3_acl_reader(S3_ACL_READER)
    connector = S3Connector(bucket=S3_BUCKET, prefix=S3_PREFIX, acl_reader=reader,
                            endpoint_url=S3_ENDPOINT_URL, region=S3_REGION)
    splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    n_docs, all_nodes = _nodes_from_connector(connector, splitter)

    if not all_nodes:
        logger.warning("S3: keine Text-Objekte unter s3://%s/%s", S3_BUCKET, S3_PREFIX)
        return {"documents": 0, "chunks": 0}

    logger.info("Connector 's3' lieferte %d Objekt(e), %d Chunks", n_docs, len(all_nodes))
    if progress_callback:
        progress_callback(f"Embedde {len(all_nodes)} S3-Chunks ...", 0.5)

    embed_model = _get_embed_model()
    Settings.embed_model = embed_model
    # Datei-basierter Qdrant sperrt den Ordner exklusiv pro Client -> Client hier
    # explizit anlegen und nach dem Schreiben SCHLIESSEN (Lock freigeben).
    client = _make_qdrant_client()
    try:
        vector_store = QdrantVectorStore(client=client, collection_name=QDRANT_COLLECTION_NAME, flat_metadata=True)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        VectorStoreIndex(nodes=all_nodes, storage_context=storage_context, show_progress=True)
    finally:
        try:
            client.close()
        except Exception:
            pass

    if progress_callback:
        progress_callback("S3 ingestion complete!", 1.0)
    stats = {"documents": n_docs, "chunks": len(all_nodes)}
    logger.info("S3 ingestion complete: %s", stats)
    return stats


def _load_ingest_sources():
    """Liest data/ingest_sources.json (vom Bootstrap aus der Landscape erzeugt). Fehlt
    die Datei oder ist sie leer/defekt -> None (Aufrufer faellt auf DATA_DOCS_DIR zurueck)."""
    try:
        with open(INGEST_SOURCES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _connectors_from_manifest(spec):
    """Baut die Connector-Liste [(label, connector), ...] aus dem Ingest-Manifest.
    Dateifreigaben -> FilesystemConnector (mit deren ACL-Leser); Object-Stores ->
    S3Connector (echtes S3 via endpoint_url) bzw. FilesystemConnector auf dem lokalen
    Seed (Demo)."""
    out = []
    for fs in (spec.get("file_shares") or []):
        path = fs.get("path")
        if path and os.path.isdir(path):
            out.append((f"share:{fs.get('name', path)}",
                        FilesystemConnector(path, get_acl_reader(fs.get("acl_reader", "composite")))))
        else:
            logger.warning("INGEST: Freigabe-Pfad fehlt/ungueltig: %s", path)
    for ob in (spec.get("object_stores") or []):
        if ob.get("endpoint_url") and ob.get("bucket"):
            out.append((f"s3:{ob.get('name', ob['bucket'])}",
                        S3Connector(bucket=ob["bucket"], prefix=ob.get("prefix", ""),
                                    acl_reader=get_s3_acl_reader(ob.get("acl_reader", "composite")),
                                    endpoint_url=ob["endpoint_url"], region=ob.get("region"))))
        elif ob.get("local_seed_path") and os.path.isdir(ob["local_seed_path"]):
            # Demo/Seed: lokalen Bucket-Ordner wie eine Dateifreigabe indexieren.
            out.append((f"seed:{ob.get('name', 'bucket')}",
                        FilesystemConnector(ob["local_seed_path"],
                                            get_acl_reader(ob.get("acl_reader", "composite")))))
    for a in (spec.get("apis") or []):
        if a.get("kind") == "api_dump" and a.get("path") and os.path.isdir(a["path"]):
            out.append((f"api:{a.get('name', 'api')}", ApiDumpConnector(a["path"])))
    return out


def ingest_all(progress_callback=None) -> dict:
    """Indexiert ALLE im Manifest (data/ingest_sources.json) gelisteten Dokumentquellen
    -- Dateifreigaben + Object-Stores -- in EINEN Qdrant-Index, mit per-Quelle korrektem
    ACL-Tagging. Ohne Manifest faellt es auf DATA_DOCS_DIR zurueck (bestehendes Verhalten).
    Embedding/Store passieren einmal am Ende; der Qdrant-Client wird sauber geschlossen
    (Lock-Freigabe)."""
    spec = _load_ingest_sources()
    if spec:
        connectors = _connectors_from_manifest(spec)
    else:
        connectors = [("docs", FilesystemConnector(DATA_DOCS_DIR, get_acl_reader(ACL_READER)))]
    if not connectors:
        logger.warning("INGEST: keine Quellen -> nichts zu tun.")
        return {"sources": 0, "documents": 0, "chunks": 0}

    splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    all_nodes = []
    n_docs = 0
    for i, (label, conn) in enumerate(connectors):
        if progress_callback:
            progress_callback(f"Lese Quelle '{label}' ({i+1}/{len(connectors)}) ...",
                              0.1 + 0.4 * i / max(1, len(connectors)))
        nd, nodes = _nodes_from_connector(conn, splitter)
        n_docs += nd
        all_nodes.extend(nodes)
        logger.info("INGEST: Quelle '%s' -> %d Dok, %d Chunks", label, nd, len(nodes))

    if not all_nodes:
        return {"sources": len(connectors), "documents": 0, "chunks": 0}

    if progress_callback:
        progress_callback(f"Embedde {len(all_nodes)} Chunks aus {len(connectors)} Quelle(n) ...", 0.6)
    embed_model = _get_embed_model()
    Settings.embed_model = embed_model
    client = _make_qdrant_client()
    try:
        # Frischer Index: bestehende Collection erst loeschen -> kein Vermischen alter/neuer
        # Daten, kein manuelles Loeschen von storage/qdrant noetig.
        try:
            client.delete_collection(QDRANT_COLLECTION_NAME)
        except Exception:
            pass
        vector_store = QdrantVectorStore(client=client, collection_name=QDRANT_COLLECTION_NAME, flat_metadata=True)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        VectorStoreIndex(nodes=all_nodes, storage_context=storage_context, show_progress=True)
    finally:
        try:
            client.close()
        except Exception:
            pass

    if progress_callback:
        progress_callback("Ingestion abgeschlossen!", 1.0)
    stats = {"sources": len(connectors), "documents": n_docs, "chunks": len(all_nodes)}
    logger.info("INGEST (all): %s", stats)
    return stats


def _format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS format for source citation."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def transcribe_meetings(progress_callback: Optional[callable] = None) -> dict:
    """Transcribe meeting audio files and ingest into Qdrant.
    
    Process:
        1. Scan ./data/meetings for .mp3/.wav files
        2. Transcribe each file using local faster-whisper (CPU, int8)
        3. Create TextNodes from transcript segments with timestamp metadata
        4. Chunk long segments and attach RBAC role metadata
        5. Embed and store in Qdrant alongside document vectors
    
    Args:
        progress_callback: Optional callable(status_text, progress_fraction)
    
    Returns:
        Dict with transcription stats: {'meetings': int, 'chunks': int}
    """
    # Lazy import — only load Whisper when actually transcribing
    # This saves ~500MB of RAM when not using the audio pipeline
    from faster_whisper import WhisperModel

    if not os.path.exists(DATA_MEETINGS_DIR) or not os.listdir(DATA_MEETINGS_DIR):
        logger.warning(f"No meeting files found in {DATA_MEETINGS_DIR}")
        return {"meetings": 0, "chunks": 0}

    # Collect audio files
    audio_extensions = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}
    audio_files = [
        f for f in os.listdir(DATA_MEETINGS_DIR)
        if os.path.splitext(f)[1].lower() in audio_extensions
    ]

    if not audio_files:
        logger.warning("No supported audio files found")
        return {"meetings": 0, "chunks": 0}

    permissions = _load_permissions()

    if progress_callback:
        progress_callback("Loading Whisper model...", 0.1)

    # ── Step 1: Initialize faster-whisper ──
    # Using CPU + int8 quantization for memory efficiency on 32GB systems
    # On Linux with ROCm, change device to "cuda" for GPU acceleration
    whisper_model = WhisperModel(
        WHISPER_MODEL_SIZE,
        device=WHISPER_DEVICE,
        compute_type=WHISPER_COMPUTE_TYPE,
    )

    splitter = SentenceSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )

    all_nodes = []
    total_files = len(audio_files)

    for idx, audio_file in enumerate(audio_files):
        file_path = os.path.join(DATA_MEETINGS_DIR, audio_file)
        roles = _get_roles_for_file(audio_file, permissions)

        if progress_callback:
            progress_callback(
                f"Transcribing ({idx + 1}/{total_files}): {audio_file}",
                0.1 + (0.6 * idx / total_files),
            )

        logger.info(f"Transcribing: {audio_file}")

        # ── Step 2: Transcribe with faster-whisper ──
        # Returns an iterator of segments with timing information
        segments, info = whisper_model.transcribe(
            file_path,
            beam_size=5,                # Beam search for better accuracy
            language=None,              # Auto-detect language (multilingual support via bge-m3)
            vad_filter=True,            # Voice Activity Detection to skip silence
        )

        # ── Step 3: Build TextNodes from transcript segments ──
        # Group segments into meaningful chunks rather than single sentences
        segment_buffer = []
        buffer_start = 0.0
        buffer_end = 0.0
        buffer_text = ""

        for segment in segments:
            if not buffer_text:
                buffer_start = segment.start

            buffer_text += segment.text + " "
            buffer_end = segment.end

            # Flush buffer when we accumulate enough text for a meaningful chunk
            if len(buffer_text) >= CHUNK_SIZE // 2:
                node = TextNode(
                    text=buffer_text.strip(),
                    metadata={
                        "role": roles,
                        "source_type": "meeting",
                        "file_name": audio_file,
                        "timestamp_start": _format_timestamp(buffer_start),
                        "timestamp_end": _format_timestamp(buffer_end),
                        "language": info.language,
                    },
                    excluded_llm_metadata_keys=["role", "source_type"],
                    excluded_embed_metadata_keys=["role", "source_type"],
                )
                segment_buffer.append(node)
                buffer_text = ""

        # Flush remaining buffer
        if buffer_text.strip():
            node = TextNode(
                text=buffer_text.strip(),
                metadata={
                    "role": roles,
                    "source_type": "meeting",
                    "file_name": audio_file,
                    "timestamp_start": _format_timestamp(buffer_start),
                    "timestamp_end": _format_timestamp(buffer_end),
                    "language": info.language if info else "unknown",
                },
                excluded_llm_metadata_keys=["role", "source_type"],
                excluded_embed_metadata_keys=["role", "source_type"],
            )
            segment_buffer.append(node)

        # Apply sentence splitter to any nodes that are still too long
        final_nodes = []
        for node in segment_buffer:
            if len(node.text) > CHUNK_SIZE:
                sub_nodes = splitter.get_nodes_from_documents(
                    [Document(text=node.text, metadata=node.metadata)]
                )
                for sn in sub_nodes:
                    sn.excluded_llm_metadata_keys = ["role", "source_type"]
                    sn.excluded_embed_metadata_keys = ["role", "source_type"]
                final_nodes.extend(sub_nodes)
            else:
                final_nodes.append(node)

        all_nodes.extend(final_nodes)
        logger.info(
            f"  → {audio_file}: {len(final_nodes)} chunks, "
            f"language={info.language}, duration={info.duration:.1f}s"
        )

    if progress_callback:
        progress_callback(f"Embedding {len(all_nodes)} meeting chunks...", 0.75)

    # ── Step 4: Embed and store in Qdrant ──
    embed_model = _get_embed_model()
    Settings.embed_model = embed_model

    client = _make_qdrant_client()
    try:
        vector_store = QdrantVectorStore(client=client, collection_name=QDRANT_COLLECTION_NAME, flat_metadata=True)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        VectorStoreIndex(nodes=all_nodes, storage_context=storage_context, show_progress=True)
    finally:
        try:
            client.close()
        except Exception:
            pass

    # Free Whisper model memory immediately
    del whisper_model

    if progress_callback:
        progress_callback("Meeting transcription complete!", 1.0)

    stats = {"meetings": total_files, "chunks": len(all_nodes)}
    logger.info(f"Transcription complete: {stats}")
    return stats


# ──────────────────────────────────────────────
# Collection Info (for UI status display)
# ──────────────────────────────────────────────

def get_collection_stats() -> dict:
    """Get Qdrant collection statistics for the UI status panel.
    
    Returns:
        Dict with 'vectors_count' and 'status' or error info
    """
    try:
        client = _make_qdrant_client()
        try:
            info = client.get_collection(QDRANT_COLLECTION_NAME)
            return {
                "vectors_count": info.vectors_count,
                "points_count": info.points_count,
                "status": str(info.status),
            }
        finally:
            try:
                client.close()
            except Exception:
                pass
    except Exception:
        return {"vectors_count": 0, "points_count": 0, "status": "not_initialized"}
