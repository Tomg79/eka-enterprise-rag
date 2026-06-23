import logging
import os
import re
import json
import traceback
import asyncio
import nest_asyncio
from sqlalchemy import create_engine, text

import qdrant_client
from qdrant_client.http.models import Filter, FieldCondition, MatchAny

from llama_index.core import VectorStoreIndex, Settings
from llama_index.core.tools import FunctionTool
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.vector_stores.qdrant import QdrantVectorStore

from llama_index.core.agent.workflow import ReActAgent

from config import (
    QDRANT_STORAGE_PATH, QDRANT_URL, QDRANT_COLLECTION_NAME, EMBEDDING_MODEL_NAME,
    EMBEDDING_DEVICE, OLLAMA_MODEL, OLLAMA_BASE_URL, OLLAMA_REQUEST_TIMEOUT,
    OLLAMA_CONTEXT_WINDOW, SIMILARITY_TOP_K,
    POLICY_FILE, USERS_FILE, AUDIT_LOG_FILE,
)
from policy import PolicyEngine, Principal, DENY_ALL_TAG
from audit import AuditLogger
from connectors.sql import GenericSQLConnector, load_sql_sources
from config import SQL_SOURCES_FILE, BASE_DIR as PROJECT_ROOT

nest_asyncio.apply()

logger = logging.getLogger(__name__)

# --- PFADE ZU GLOBALCORP ---
BASE_DIR = "data"
SAP_DB_PATH = os.path.join(BASE_DIR, "sap_legacy.db")
API_DIR = os.path.join(BASE_DIR, "api_salesforce")

# Wie viele CRM-Datensaetze maximal in eine Tool-Antwort, wenn keine konkrete
# Kunden-ID in der Frage steht (Kontext-Schutz).
_SALESFORCE_BULK_CAP = 25


# System-Prompt fuer den ReAct-Agenten: erzwingt, dass NUR mit Tool-Ausgaben
# geantwortet wird (Anti-Halluzination).
AGENT_SYSTEM_PROMPT = (
    "You are a secure enterprise assistant. You may ONLY use information returned "
    "by your tools. Never invent or infer facts that are not present in a tool output. "
    "If the tools do not return enough authorized information, reply exactly: "
    "\"I don't have sufficient authorized information to answer this question.\" "
    "Always cite the source document name(s) that a fact came from. "
    "Permission filtering already happened inside the tools; treat any tool result "
    "as the complete set of data you are allowed to see. "
    "For questions about business records (customers, orders, suppliers, prices, "
    "salaries, invoices, materials, tickets, contracts, etc.), you MUST FIRST call "
    "sql_list_tables with a relevant keyword to find authorized tables, THEN call "
    "sql_lookup or sql_filter to read them. Use document search only for unstructured "
    "text. Only answer that information is unavailable AFTER you have tried the "
    "relevant sql_* tools and they returned nothing authorized."
)


def _dms_filter_for_groups(allowed_groups: list) -> Filter:
    """Baut den Qdrant-Payload-Filter auf dem Feld 'acl_groups' (Phase 2: aus der
    Datei-ACL gelesen). Leere Gruppenliste -> Filter, der auf NICHTS matcht
    (fail-closed), statt versehentlich alles freizugeben."""
    groups = list(allowed_groups) if allowed_groups else [DENY_ALL_TAG]
    return Filter(must=[FieldCondition(key="acl_groups", match=MatchAny(any=groups))])


# --- RAG QUERY ENGINE KLASSE ---
class RAGQueryEngine:
    def __init__(self):
        logger.info("Starte den Enterprise Workflow-Agenten (Async-Mode)...")
        self.embed_model = HuggingFaceEmbedding(model_name=EMBEDDING_MODEL_NAME, device=EMBEDDING_DEVICE)
        Settings.embed_model = self.embed_model

        self.llm = Ollama(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL, request_timeout=3600.0, context_window=OLLAMA_CONTEXT_WINDOW)
        Settings.llm = self.llm

        # Phase 6: Server-Modus via QDRANT_URL (Docker/On-Prem), sonst lokal file-based.
        if QDRANT_URL:
            self.qdrant_client = qdrant_client.QdrantClient(url=QDRANT_URL)
        else:
            self.qdrant_client = qdrant_client.QdrantClient(path=QDRANT_STORAGE_PATH)
        self.vector_store = QdrantVectorStore(client=self.qdrant_client, collection_name=QDRANT_COLLECTION_NAME, flat_metadata=True)
        self.index = VectorStoreIndex.from_vector_store(vector_store=self.vector_store)
        # Phase 4: generischer, deklarativer SQL-Connector (ersetzt die hartkodierte
        # SAP-Engine). Quellen/Tabellen/Spalten stehen in data/sql_sources.json;
        # Identifier nur aus der Whitelist, Werte parametrisiert (siehe connectors/sql.py).
        # base_dir = Projektwurzel (NICHT das lokale 'data'), damit relative
        # sqlite-Pfade wie 'sqlite:///data/sap_legacy.db' korrekt aufgeloest werden.
        self.sql = GenericSQLConnector(load_sql_sources(SQL_SOURCES_FILE), base_dir=PROJECT_ROOT)

        # Phase 1: zentraler Permission-Layer + Audit-Log.
        self.policy = PolicyEngine(POLICY_FILE, USERS_FILE)
        self.audit = AuditLogger(AUDIT_LOG_FILE)

        # Pro-Anfrage-Zustand (Quellen + Audit-Events der Tools).
        self._last_vector_sources = []
        self._audit_events = []

    # ------------------------------------------------------------------
    def list_users(self) -> list:
        """Fuer die UI: [(user_id, display), ...]."""
        return self.policy.list_users()

    def _record_event(self, tool: str, decision: str, detail: str = "", sources=None):
        self._audit_events.append({
            "tool": tool,
            "decision": decision,
            "detail": detail,
            "sources": sources or [],
        })

    # ------------------------------------------------------------------
    def query(self, query_text: str, user_id: str) -> dict:
        # Pro-Anfrage-Zustand zuruecksetzen.
        self._last_vector_sources = []
        self._audit_events = []

        # Principal IMMER aufloesen (auch fuer den Audit-Eintrag im Fehlerfall).
        principal = self.policy.get_principal(user_id)

        try:
            tools = []

            # 1. SharePoint/DMS Tool (Qdrant) -- SYNCHRONES Retrieval.
            #
            # Der Workflow-ReActAgent ruft Tools ueber 'await tool.acall()' auf. Ein
            # QueryEngineTool wuerde 'vector_store.aquery()' ausloesen (braucht aclient,
            # beim lokalen file-basierten Qdrant wegen Storage-Lock nicht moeglich).
            # Loesung: FunctionTool mit SYNCHRONER Retrieval-Funktion -> der aquery-Pfad
            # wird nie betreten. RBAC-Filter via vector_store_kwargs -> vector_store.query().
            allowed_groups = self.policy.allowed_groups(principal)
            rbac_filter = _dms_filter_for_groups(allowed_groups)
            retriever = self.index.as_retriever(
                similarity_top_k=SIMILARITY_TOP_K,
                vector_store_kwargs={"qdrant_filters": rbac_filter},
            )

            def search_sharepoint_dms(query: str) -> str:
                """Searches unstructured text: documents, meeting notes, HR warnings, guidelines."""
                if not allowed_groups:
                    self._record_event("sharepoint_dms_search", "denied", "no DMS tags for principal")
                    return "ACCESS DENIED: not authorized for any document category."
                nodes = retriever.retrieve(query)
                if not nodes:
                    self._record_event("sharepoint_dms_search", "allowed",
                                       f"groups={allowed_groups}; 0 hits")
                    return "No authorized documents were found for this query."
                blocks = []
                used = []
                for nws in nodes:
                    meta = nws.node.metadata or {}
                    file_name = meta.get("file_name", "unknown")
                    snippet = nws.node.get_content() or ""
                    self._last_vector_sources.append({
                        "file_name": file_name,
                        "source_type": meta.get("source_type", "document"),
                        "text_snippet": snippet[:300],
                        "score": getattr(nws, "score", None),
                    })
                    used.append(file_name)
                    blocks.append("[Source: " + file_name + "]\n" + snippet)
                self._record_event("sharepoint_dms_search", "allowed",
                                   f"groups={allowed_groups}", sources=used)
                return "\n\n---\n\n".join(blocks)

            tools.append(FunctionTool.from_defaults(
                fn=search_sharepoint_dms,
                name="sharepoint_dms_search",
                description="Use this to search for unstructured text, meeting notes, HR warnings, and rumors."
            ))

            # 2. Salesforce Tool (API) -- Scope ueber PolicyEngine.
            def fetch_salesforce_data(query: str) -> str:
                """Reads Salesforce CRM records. Mention a customer id like CUST-999 to look it up."""
                mode, scope_ids = self.policy.salesforce_scope(principal)
                if mode == "none":
                    self._record_event("salesforce_api_client", "denied", "no CRM scope")
                    return "ACCESS DENIED: Role not authorized for Salesforce CRM."

                requested = sorted(set(re.findall(r"CUST-\d+", (query or "").upper())))
                if requested:
                    allowed = [c for c in requested if self.policy.can_access_customer(principal, c)]
                    denied = [c for c in requested if c not in allowed]
                else:
                    # Keine konkrete ID -> in-scope-Kunden (gedeckelt).
                    if mode == "all":
                        all_ids = sorted(
                            os.path.splitext(f)[0]
                            for f in os.listdir(API_DIR) if f.endswith(".json")
                        )
                    else:
                        all_ids = sorted(scope_ids)
                    allowed = all_ids[:_SALESFORCE_BULK_CAP]
                    denied = []

                results = []
                for cust_id in allowed:
                    path = os.path.join(API_DIR, f"{cust_id}.json")
                    if os.path.exists(path):
                        with open(path, "r", encoding="utf-8") as f:
                            results.append(f.read())

                self._record_event(
                    "salesforce_api_client",
                    "allowed" if allowed else "denied",
                    f"requested={requested or '*'}; returned={allowed}; denied={denied}",
                    sources=[f"salesforce:{c}" for c in allowed],
                )
                if not results:
                    if denied:
                        return f"ACCESS DENIED for customer(s): {', '.join(denied)}."
                    return "No customer records found in your authorized scope."
                note = ""
                if denied:
                    note = f"\n(NOTE: access denied for {', '.join(denied)})"
                return "Salesforce JSON Results:\n" + "\n".join(results) + note

            tools.append(FunctionTool.from_defaults(
                fn=fetch_salesforce_data,
                name="salesforce_api_client",
                description="Use this to find account manager IDs for customer IDs like CUST-999."
            ))

            # 3. SQL-Tools (generischer Connector) -- SKALIERBAR: statt eines Tools
            #    pro Tabelle (sprengt bei hunderten Tabellen den LLM-Kontext) gibt es
            #    DREI feste, parametrisierte Tools. Tabellenname kommt als Argument,
            #    Whitelist + RBAC werden INNEN geprueft. Nur autorisierte Tabellen sind
            #    ueberhaupt sicht-/erreichbar (fail-closed), Policy-Recheck pro Aufruf.
            authorized = {sp.qualified: sp for sp in self.sql.specs()
                          if self.policy.can_access_sql_table(principal, sp.rbac_id)}

            def _resolve_spec(table: str):
                t = (table or "").strip()
                sp = authorized.get(t)
                if sp is None:  # auch Kurzname (ohne Quelle) zulassen, wenn eindeutig
                    cand = [q for q in authorized if q.split(".")[-1] == t]
                    if len(cand) == 1:
                        sp = authorized[cand[0]]
                return sp

            def sql_list_tables(keyword: str = "") -> str:
                """List/search the database tables you are allowed to query. Pass a keyword
                to filter by name (e.g. 'lieferant', 'gehalt', 'kunden'). CALL THIS FIRST to
                discover tables, then use sql_lookup/sql_filter with an exact table name."""
                kw = (keyword or "").strip().lower()
                names = sorted(q for q in authorized if kw in q.lower()) if kw else sorted(authorized)
                prefix = ""
                if kw and not names:
                    # Kein Treffer (z.B. englisches Stichwort vs. deutsche Tabellennamen)
                    # -> ALLE erlaubten Tabellen zeigen, statt "gibt es nicht".
                    names = sorted(authorized)
                    prefix = (f"(Kein Treffer fuer '{keyword}'. Hier ALLE Tabellen, die du "
                              f"abfragen darfst -- waehle die passende:)\n")
                if not names:
                    self._record_event("sql_list_tables", "allowed", "no authorized tables")
                    return "You have no authorized tables."
                shown = names[:40]
                lines = [f"{q} (key={authorized[q].lookup_column}; cols={', '.join(authorized[q].select_columns)})"
                         for q in shown]
                more = f"\n(+{len(names)-len(shown)} weitere -- Stichwort verfeinern)" if len(names) > len(shown) else ""
                self._record_event("sql_list_tables", "allowed", f"kw={keyword!r}; {len(names)} hits")
                return prefix + "Authorized tables:\n" + "\n".join(lines) + more

            def sql_lookup(table: str, value: str) -> str:
                """Look up a single record by its key. Args: table (exact qualified name from
                sql_list_tables, e.g. 'lieferanten_2022_0015.preise'), value (the key)."""
                sp = _resolve_spec(table)
                if sp is None:
                    self._record_event("sql_lookup", "denied", f"{table!r} unknown/not authorized")
                    return (f"ACCESS DENIED or unknown table: {table!r}. "
                            f"Call sql_list_tables to see allowed tables.")
                status, payload = self.sql.lookup(sp.qualified, value)
                if status == "bad_value":
                    self._record_event("sql_lookup", "rejected", f"{sp.qualified}: bad value {value!r}")
                    return payload
                norm = sp.norm(value)
                if status in ("no_table", None):
                    self._record_event("sql_lookup", "denied", f"{sp.qualified} not available")
                    return "Requested table is not available."
                if status == "not_found":
                    self._record_event("sql_lookup", "allowed", f"{sp.qualified} {norm}: not found")
                    return f"No record found in {sp.qualified} for {value!r}."
                self._record_event("sql_lookup", "allowed", f"{sp.qualified} {norm}",
                                   sources=[f"{sp.qualified}:{norm}"])
                return self.sql.format_row(sp.qualified, payload)

            def sql_filter(table: str, column: str = "", value: str = "", limit: int = 10) -> str:
                """List rows of a table. Leave column EMPTY to list the first rows of the table
                (use this for 'what is in table X / list customers' questions); or pass
                column+value to filter. Args: table (qualified name from sql_list_tables),
                column (optional), value (optional)."""
                sp = _resolve_spec(table)
                if sp is None:
                    self._record_event("sql_filter", "denied", f"{table!r} unknown/not authorized")
                    return f"ACCESS DENIED or unknown table: {table!r}. Use sql_list_tables."
                if not (column or "").strip():
                    status, rows = self.sql.sample_rows(sp.qualified, limit)
                else:
                    status, rows = self.sql.filter_rows(sp.qualified, column, value, limit=limit)
                if status == "bad_column":
                    self._record_event("sql_filter", "rejected", f"{sp.qualified}: bad column {column!r}")
                    return rows
                if status in ("no_table", None):
                    self._record_event("sql_filter", "denied", f"{sp.qualified} not available")
                    return "Requested table is not available."
                self._record_event("sql_filter", "allowed",
                                   f"{sp.qualified} {column}={value!r}: {len(rows)} rows",
                                   sources=[f"{sp.qualified}:{column}={value}"])
                if not rows:
                    return f"No rows in {sp.qualified} where {column} = {value!r}."
                return "\n".join(self.sql.format_row(sp.qualified, r) for r in rows)

            if authorized:
                tools.append(FunctionTool.from_defaults(
                    fn=sql_list_tables, name="sql_list_tables",
                    description=("List/search database tables the current user may query "
                                 "(optional keyword filter). Call FIRST to find table names.")))
                tools.append(FunctionTool.from_defaults(
                    fn=sql_lookup, name="sql_lookup",
                    description=("Look up one record by key in a table (exact qualified name "
                                 "from sql_list_tables). Args: table, value.")))
                tools.append(FunctionTool.from_defaults(
                    fn=sql_filter, name="sql_filter",
                    description=("List/sample rows of a table. Leave column empty to list the "
                                 "first rows (e.g. which customers/orders exist); or filter by "
                                 "column=value. Args: table, column(optional), value(optional).")))

            # DER WORKFLOW AGENT (ASYNC)
            agent = ReActAgent(
                tools=tools,
                llm=self.llm,
                system_prompt=AGENT_SYSTEM_PROMPT,
                verbose=True,
                timeout=3600.0
            )

            async def _run_agent_async():
                return await agent.run(query_text)

            response = asyncio.run(_run_agent_async())
            answer = str(response)

            # DMS-Quellen fuer die UI (dedupe nach Dateiname).
            sources = []
            seen = set()
            for src in self._last_vector_sources:
                if src["file_name"] not in seen:
                    seen.add(src["file_name"])
                    sources.append(src)
            if not sources:
                sources = [{
                    "file_name": "-",
                    "source_type": "Multi-Hop Workflow",
                    "text_snippet": "Keine DMS-Dokumente in dieser Antwort verwendet "
                                    "(ggf. nur SAP-/Salesforce-Tool oder keine Treffer).",
                }]

            self._write_audit(principal, query_text, answer, error=None)

            return {
                "response": answer,
                "sources": sources,
                "user": principal.user_id,
                "display": principal.display,
                "roles": sorted(principal.roles),
            }

        except Exception as e:
            logger.error("Query fehlgeschlagen: %s\n%s", e, traceback.format_exc())
            self._write_audit(principal, query_text, answer_preview="", error=str(e))
            return {
                "response": "Die Anfrage konnte nicht beantwortet werden "
                            "(interner Fehler). Details stehen im Server-Log.",
                "sources": [{
                    "file_name": "System",
                    "source_type": "Error Log",
                    "text_snippet": str(e)[:200],
                }],
                "user": principal.user_id,
                "display": principal.display,
                "roles": sorted(principal.roles),
            }

    # ------------------------------------------------------------------
    def _write_audit(self, principal: Principal, query_text: str,
                     answer_preview: str = "", error=None):
        self.audit.log({
            "user": principal.user_id,
            "display": principal.display,
            "roles": sorted(principal.roles),
            "query": query_text,
            "tool_events": list(self._audit_events),
            "answer_preview": (answer_preview or "")[:300],
            "error": error,
        })

    def close(self):
        """Gibt den Qdrant-Client (und damit den Datei-Lock) frei. Wichtig fuer den
        datei-basierten Modus, bevor ein Ingest einen neuen Client oeffnet."""
        try:
            self.qdrant_client.close()
        except Exception:
            pass

    def known_groups(self) -> list:
        return self.policy.all_groups()

    def add_meeting_document(self, file_name, text, acl_groups, user_id="system") -> int:
        """Spielt ein freigegebenes Entscheidungsdokument INKREMENTELL in den Index ein
        (nutzt den bereits offenen Qdrant-Client -> kein Lock-Konflikt). Kein Voll-Reindex."""
        from llama_index.core import Document
        from llama_index.core.node_parser import SentenceSplitter
        from config import CHUNK_SIZE, CHUNK_OVERLAP
        splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
        doc = Document(text=text, metadata={"file_name": file_name, "source_type": "meeting"})
        nodes = splitter.get_nodes_from_documents([doc])
        for n in nodes:
            n.metadata["acl_groups"] = list(acl_groups)
            n.metadata["source_type"] = "meeting"
            n.metadata["file_name"] = file_name
            n.excluded_llm_metadata_keys = ["acl_groups", "source_type"]
            n.excluded_embed_metadata_keys = ["acl_groups", "source_type"]
        self.index.insert_nodes(nodes)
        self.audit.log({
            "user": user_id, "display": user_id, "roles": [],
            "query": f"[MEETING-FREIGABE] {file_name}",
            "tool_events": [{"tool": "meeting_ingest", "decision": "approved",
                             "detail": f"groups={list(acl_groups)}", "sources": [file_name]}],
            "answer_preview": text[:300], "error": None,
        })
        return len(nodes)


def check_ollama_connection() -> bool:
    import urllib.request
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            models = [m.get("name", "") for m in json.loads(resp.read().decode()).get("models", [])]
            return any(OLLAMA_MODEL in m for m in models)
    except Exception:
        return False
