"""
app.py -- Streamlit Frontend
Tabs: Chat (Frage->Antwort), Audit-Log, Meetings (Transkript->Freigabe->RAG).
Zugriffspruefung zentral in der PolicyEngine (policy.py / query.py).
"""

import os
import time
import logging

import streamlit as st

from config import (
    POLICY_FILE, USERS_FILE, AUDIT_LOG_FILE,
    MEETINGS_PENDING_DIR, MEETINGS_TRANSCRIPTS_DIR, DATA_DOCS_DIR,
    AUTH_ENABLED, AUTH_USERS_FILE, AUTH_SECRET_FILE, AUTH_SESSION_TTL_MIN,
)
from policy import PolicyEngine
from auth import AuthManager, LocalBackend, load_or_create_secret
from audit import AuditLogger
from meetings import OllamaMeetingExtractor, PendingStore, persist_approved_document
from query import RAGQueryEngine, check_ollama_connection
from ingest import ingest_documents, ingest_all, transcribe_meetings, get_collection_stats

# Leichte Helfer (ohne das schwere Embedding-Modell) fuer UI + Audit + Meetings.
policy = PolicyEngine(POLICY_FILE, USERS_FILE)
audit_reader = AuditLogger(AUDIT_LOG_FILE)
pending_store = PendingStore(MEETINGS_PENDING_DIR)

# Phase 8: Authentifizierung. Default AUS (config.AUTH_ENABLED) -> bestehendes Verhalten.
auth_manager = AuthManager(
    LocalBackend(AUTH_USERS_FILE),
    load_or_create_secret(AUTH_SECRET_FILE),
    ttl_seconds=AUTH_SESSION_TTL_MIN * 60,
)


def _authenticated_user_id():
    if not AUTH_ENABLED:
        return None
    tok = st.session_state.get("auth_token")
    return auth_manager.user_from_token(tok) if tok else None

st.set_page_config(
    page_title="Enterprise RAG -- Secure Knowledge Assistant",
    page_icon="🔒",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    .stApp { font-family: 'Inter', sans-serif; }
    .main-header {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
        padding: 1.5rem 2rem; border-radius: 12px; margin-bottom: 1.5rem;
        border: 1px solid rgba(255,255,255,0.08); box-shadow: 0 4px 20px rgba(0,0,0,0.3);
    }
    .main-header h1 { color: #e2e8f0; font-weight: 700; font-size: 1.6rem; margin: 0; letter-spacing: -0.02em; }
    .main-header p { color: #94a3b8; font-size: 0.85rem; margin: 0.3rem 0 0 0; }
    .role-badge {
        display: inline-block; padding: 0.25rem 0.7rem; border-radius: 20px;
        font-size: 0.72rem; font-weight: 600; letter-spacing: 0.03em;
        text-transform: uppercase; margin: 0.15rem 0.2rem 0.15rem 0; color: white;
    }
    .role-exec  { background: linear-gradient(135deg, #7c3aed, #a855f7); }
    .role-hr    { background: linear-gradient(135deg, #0891b2, #22d3ee); }
    .role-sales { background: linear-gradient(135deg, #2563eb, #60a5fa); }
    .role-base  { background: linear-gradient(135deg, #6b7280, #9ca3af); }
    .role-project_phoenix { background: linear-gradient(135deg, #ea580c, #f59e0b); }
    .source-card {
        background: rgba(30,41,59,0.7); border: 1px solid rgba(148,163,184,0.15);
        border-radius: 8px; padding: 0.8rem 1rem; margin: 0.4rem 0; font-size: 0.82rem;
    }
    .source-card .source-label { font-weight: 600; color: #94a3b8; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; }
    .source-card .source-file { color: #e2e8f0; font-weight: 500; }
    .source-card .source-text { color: #cbd5e1; font-size: 0.8rem; margin-top: 0.3rem; line-height: 1.5; }
    .status-online { color: #34d399; } .status-offline { color: #f87171; }
    .sidebar-section {
        font-weight: 600; color: #94a3b8; font-size: 0.72rem; text-transform: uppercase;
        letter-spacing: 0.08em; margin: 1.2rem 0 0.5rem 0; padding-bottom: 0.3rem;
        border-bottom: 1px solid rgba(148,163,184,0.15);
    }
    .audit-allowed { color: #34d399; font-weight: 600; }
    .audit-denied  { color: #f87171; font-weight: 600; }
    .audit-other   { color: #fbbf24; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "query_engine" not in st.session_state:
    st.session_state.query_engine = None
if "engine_error" not in st.session_state:
    st.session_state.engine_error = None


def role_badges_html(roles) -> str:
    if not roles:
        return '<span class="role-badge role-base">keine Rolle</span>'
    out = []
    for r in roles:
        css = f"role-{r}" if r in ("exec", "hr", "sales", "base", "project_phoenix") else "role-base"
        out.append(f'<span class="role-badge {css}">{r}</span>')
    return "".join(out)


def format_source_card(source: dict) -> str:
    file_name = source.get("file_name", "Unknown")
    snippet = source.get("text_snippet", "")
    score = source.get("score", None)
    score_text = f" · Relevance: {score:.2%}" if isinstance(score, (int, float)) else ""
    return f"""
    <div class="source-card">
        <div class="source-label">Document{score_text}</div>
        <div class="source-file">📄 {file_name}</div>
        <div class="source-text">{snippet}</div>
    </div>
    """


def initialize_query_engine():
    if st.session_state.query_engine is None:
        try:
            st.session_state.query_engine = RAGQueryEngine()
            st.session_state.engine_error = None
        except Exception as e:
            st.session_state.engine_error = str(e)
            logging.error(f"Failed to initialize query engine: {e}")


# ── Login-Gate (nur wenn AUTH_ENABLED) ──
if AUTH_ENABLED and not _authenticated_user_id():
    st.markdown('''
    <div class="main-header"><h1>🔒 Enterprise Knowledge Assistant</h1>
    <p>Bitte anmelden</p></div>''', unsafe_allow_html=True)
    with st.form("login_form"):
        _uid = st.text_input("Benutzerkennung")
        _pw = st.text_input("Passwort", type="password")
        _ok = st.form_submit_button("Anmelden")
    if _ok:
        _tok = auth_manager.login((_uid or "").strip(), _pw or "")
        if _tok:
            st.session_state["auth_token"] = _tok
            st.rerun()
        else:
            st.error("Anmeldung fehlgeschlagen. Bitte Kennung/Passwort pruefen.")
    st.stop()


# ── Setup-Assistent: zeigt sich, bis eingerichtet ist (oder per Knopf erzwungen) ──
import wizard as _wizard
if (_wizard.needs_setup() or st.session_state.get("force_setup")) and not st.session_state.get("skip_setup"):
    _wizard.render_setup_wizard()
    st.stop()


# ── Sidebar ──
with st.sidebar:
    st.markdown("### 🔒 Enterprise RAG")
    st.markdown("---")
    st.markdown('<div class="sidebar-section">Access Control</div>', unsafe_allow_html=True)

    if AUTH_ENABLED:
        selected_user_id = _authenticated_user_id()
        _disp = dict(policy.list_users()).get(selected_user_id, selected_user_id)
        st.success(f"Angemeldet: {_disp}")
        if st.button("Abmelden", use_container_width=True):
            st.session_state.pop("auth_token", None)
            st.rerun()
    else:
        users = policy.list_users()
        if users:
            display_to_id = {disp: uid for uid, disp in users}
            selected_display = st.selectbox(
                "Current User", list(display_to_id.keys()), index=0,
                help="Simuliert den angemeldeten Mitarbeiter (nur ohne Login). "
                     "Rechte = Vereinigung seiner Rollen.",
            )
            selected_user_id = display_to_id[selected_display]
        else:
            st.error("Keine User in users.json gefunden.")
            selected_user_id = ""

    principal = policy.get_principal(selected_user_id)
    perms_tags = policy.allowed_groups(principal)
    perms_sap = policy.allowed_sap_tables(principal)
    sf_mode, _sf_ids = policy.salesforce_scope(principal)
    st.markdown(role_badges_html(sorted(principal.roles)), unsafe_allow_html=True)
    st.caption(f"Gruppen: {', '.join(perms_tags) or '—'}  \nSAP: {', '.join(perms_sap) or '—'}  \nCRM: {sf_mode}")

    st.markdown("---")
    st.markdown('<div class="sidebar-section">Data Ingestion</div>', unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📄 Ingest Docs", use_container_width=True):
            # Lock freigeben: laufende Query-Engine schliessen, bevor der Ingest
            # einen neuen Qdrant-Client auf denselben Ordner oeffnet (datei-basiert).
            if st.session_state.query_engine is not None:
                try:
                    st.session_state.query_engine.close()
                except Exception:
                    pass
                st.session_state.query_engine = None
            with st.spinner("Ingesting documents..."):
                progress_bar = st.progress(0); status_text = st.empty()
                def doc_progress(t, frac):
                    status_text.text(t); progress_bar.progress(frac)
                try:
                    stats = ingest_all(progress_callback=doc_progress)
                    st.success(f"✅ {stats.get('sources', 1)} Quelle(n) · "
                               f"{stats['documents']} doc(s) → {stats['chunks']} chunks")
                except Exception as e:
                    st.error(f"❌ Ingestion failed: {e}")
                finally:
                    st.session_state.query_engine = None
    with col2:
        if st.button("🎙️ Transcribe", use_container_width=True):
            # Lock freigeben: laufende Query-Engine schliessen, bevor der Ingest
            # einen neuen Qdrant-Client auf denselben Ordner oeffnet (datei-basiert).
            if st.session_state.query_engine is not None:
                try:
                    st.session_state.query_engine.close()
                except Exception:
                    pass
                st.session_state.query_engine = None
            with st.spinner("Transcribing meetings..."):
                progress_bar = st.progress(0); status_text = st.empty()
                def meeting_progress(t, frac):
                    status_text.text(t); progress_bar.progress(frac)
                try:
                    stats = transcribe_meetings(progress_callback=meeting_progress)
                    st.success(f"✅ {stats['meetings']} file(s) → {stats['chunks']} chunks")
                except Exception as e:
                    st.error(f"❌ Transcription failed: {e}")
                finally:
                    st.session_state.query_engine = None

    st.markdown("---")
    if st.button("🔧 Einrichtung (Setup) starten", use_container_width=True):
        st.session_state["force_setup"] = True
        st.session_state["skip_setup"] = False
        st.rerun()

    st.markdown("---")
    st.markdown('<div class="sidebar-section">System Status</div>', unsafe_allow_html=True)
    ollama_ok = check_ollama_connection()
    if ollama_ok:
        st.markdown('🟢 **Ollama** — <span class="status-online">Online</span>', unsafe_allow_html=True)
    else:
        st.markdown('🔴 **Ollama** — <span class="status-offline">Offline</span>', unsafe_allow_html=True)
    collection_stats = get_collection_stats()
    points = collection_stats.get("points_count", 0) or 0
    if points > 0:
        st.markdown(f'🟢 **Qdrant** — <span class="status-online">{points} Punkte</span>', unsafe_allow_html=True)
    else:
        st.markdown('🟡 **Qdrant** — leer/uneindeutig', unsafe_allow_html=True)
    st.markdown("---")
    st.caption("🔒 100% Local · No data leaves this machine\n\nLlamaIndex · Ollama · Qdrant")


# ── Main ──
st.markdown("""
<div class="main-header">
    <h1>🔒 Enterprise Knowledge Assistant</h1>
    <p>Secure RAG · PolicyEngine (RBAC v2) · ACL-Connector · Meeting-Freigaben · Audit · alles lokal</p>
</div>
""", unsafe_allow_html=True)

tab_chat, tab_audit, tab_meet = st.tabs(["💬 Chat", "📜 Audit-Log", "🗣️ Meetings"])

# ---- Chat ----
with tab_chat:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant" and message.get("sources"):
                with st.expander(f"📚 Sources ({len(message['sources'])})"):
                    for src in message["sources"]:
                        st.markdown(format_source_card(src), unsafe_allow_html=True)
                if message.get("roles") is not None:
                    st.markdown("Answered as: " + role_badges_html(message.get("roles", [])), unsafe_allow_html=True)

    if prompt := st.chat_input("Ask a question about your documents..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            if not ollama_ok:
                msg = "⚠️ **Ollama is not running.** Bitte Ollama starten."
                st.warning(msg)
                st.session_state.messages.append({"role": "assistant", "content": msg})
            else:
                initialize_query_engine()
                if st.session_state.engine_error:
                    st.error(f"❌ Engine error: {st.session_state.engine_error}")
                else:
                    with st.spinner(f"Searching as **{principal.display}**..."):
                        start = time.time()
                        try:
                            result = st.session_state.query_engine.query(query_text=prompt, user_id=selected_user_id)
                            elapsed = time.time() - start
                            st.markdown(result["response"])
                            sources = result.get("sources", [])
                            if sources:
                                with st.expander(f"📚 Sources ({len(sources)})"):
                                    for src in sources:
                                        st.markdown(format_source_card(src), unsafe_allow_html=True)
                            st.markdown("Answered as: " + role_badges_html(result.get("roles", [])) + f" · ⏱️ {elapsed:.1f}s", unsafe_allow_html=True)
                            st.session_state.messages.append({
                                "role": "assistant", "content": result["response"],
                                "sources": sources, "roles": result.get("roles", []),
                            })
                        except Exception as e:
                            msg = f"❌ Query failed: {e}"
                            st.error(msg); logging.error(f"Query error: {e}", exc_info=True)
                            st.session_state.messages.append({"role": "assistant", "content": msg})

# ---- Audit ----
with tab_audit:
    st.caption(f"Quelle: {AUDIT_LOG_FILE} · append-only · neueste zuerst")
    if st.button("🔄 Aktualisieren", key="audit_refresh"):
        pass
    entries = audit_reader.tail(50)
    if not entries:
        st.info("Noch keine Audit-Eintraege. Stelle eine Frage im Chat-Tab.")
    for e in entries:
        title = f"{e.get('ts','')} · {e.get('display', e.get('user','?'))} · {(e.get('query','') or '')[:80]}"
        with st.expander(title):
            st.markdown("Rollen: " + role_badges_html(e.get("roles", [])), unsafe_allow_html=True)
            st.markdown(f"**Frage:** {e.get('query','')}")
            for ev in e.get("tool_events", []):
                dec = ev.get("decision", "")
                cls = "audit-allowed" if dec == "allowed" else ("audit-denied" if dec == "denied" else "audit-other")
                srcs = ev.get("sources", [])
                src_txt = (" · sources: " + ", ".join(srcs)) if srcs else ""
                st.markdown(f'- `{ev.get("tool","")}` → <span class="{cls}">{dec}</span> '
                            f'<span style="color:#94a3b8">({ev.get("detail","")})</span>{src_txt}', unsafe_allow_html=True)
            if not e.get("tool_events"):
                st.caption("Keine Tool-Aufrufe protokolliert.")
            if e.get("answer_preview"):
                st.markdown(f"**Antwort (Auszug):** {e.get('answer_preview')}")
            if e.get("error"):
                st.markdown(f'<span class="audit-denied">Fehler: {e.get("error")}</span>', unsafe_allow_html=True)

# ---- Meetings ----
with tab_meet:
    st.caption("Transkript → LLM extrahiert Entscheidungen + Gruppen-Vorschlag → Host gibt frei → erst dann im RAG")

    all_known_groups = policy.all_groups()

    st.markdown("#### 1) Meeting analysieren")
    transcripts = []
    if os.path.isdir(MEETINGS_TRANSCRIPTS_DIR):
        transcripts = sorted(f for f in os.listdir(MEETINGS_TRANSCRIPTS_DIR) if f.endswith(".txt"))
    pick = st.selectbox("Transkript-Datei", ["(eigener Text)"] + transcripts)
    default_text = ""
    if pick != "(eigener Text)":
        try:
            with open(os.path.join(MEETINGS_TRANSCRIPTS_DIR, pick), "r", encoding="utf-8", errors="replace") as f:
                default_text = f.read()
        except Exception:
            default_text = ""
    transcript = st.text_area("Transkript", value=default_text, height=180)
    if st.button("🧠 Analysieren (LLM)"):
        if not ollama_ok:
            st.warning("Ollama ist offline – bitte starten.")
        elif not transcript.strip():
            st.warning("Bitte ein Transkript eingeben oder auswaehlen.")
        else:
            initialize_query_engine()
            eng = st.session_state.query_engine
            if eng is None:
                st.error(f"Engine nicht bereit: {st.session_state.engine_error}")
            else:
                with st.spinner("Analysiere Meeting (LLM extrahiert Entscheidungen)..."):
                    extractor = OllamaMeetingExtractor(eng.llm)
                    extraction = extractor.extract(transcript, eng.known_groups())
                    item = pending_store.create(
                        source=(pick if pick != "(eigener Text)" else "manuell"),
                        transcript=transcript, extraction=extraction,
                    )
                st.success(f"Analysiert → Vorgang {item['id']} wartet auf Freigabe (unten).")

    st.markdown("---")
    st.markdown("#### 2) Offene Freigaben")
    pend = pending_store.list(status="pending")
    if not pend:
        st.info("Keine offenen Vorgaenge.")
    for item in pend:
        with st.expander(f"📝 {item.get('title','(ohne Titel)')} · {item.get('created','')[:19]} · {item.get('source','')}"):
            st.markdown("**Entscheidungsdokument (wird ins RAG eingespielt):**")
            st.write(item.get("decision_document", ""))
            st.caption("LLM-Vorschlag: " + (", ".join(item.get("suggested_groups", [])) or "—")
                       + (f" · {item.get('rationale','')}" if item.get("rationale") else ""))
            sel = st.multiselect(
                "Lesegruppen (Freigabe)", options=all_known_groups,
                default=[g for g in item.get("suggested_groups", []) if g in all_known_groups],
                key="grp_" + item["id"],
            )
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ Freigeben", key="appr_" + item["id"]):
                    if not sel:
                        st.warning("Mindestens eine Gruppe waehlen (sonst sieht es niemand).")
                    else:
                        initialize_query_engine()
                        eng = st.session_state.query_engine
                        if eng is None:
                            st.error(f"Engine nicht bereit: {st.session_state.engine_error}")
                        else:
                            try:
                                fname = persist_approved_document(DATA_DOCS_DIR, item["id"], item["decision_document"], sel)
                                n = eng.add_meeting_document(fname, item["decision_document"], sel, user_id=selected_user_id)
                                pending_store.set_status(item["id"], "approved", approved_groups=sel)
                                st.success(f"Freigegeben → {fname} ({n} Chunks) fuer: {', '.join(sel)}. Sofort abfragbar.")
                            except Exception as e:
                                st.error(f"Freigabe/Ingest fehlgeschlagen: {e}")
            with c2:
                if st.button("🗑️ Ablehnen", key="rej_" + item["id"]):
                    pending_store.set_status(item["id"], "rejected")
                    st.info("Abgelehnt – nichts wurde ins RAG eingespielt.")
