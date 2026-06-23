"""
wizard.py -- Gefuehrter In-App-Setup-Assistent (Streamlit).

Ziel: "Tool starten -> (Modell laden) -> Demo-Ordner waehlen -> Tool baut Index/Config
auf -> ab in die UI". Kapselt das, was sonst quickstart.py auf der Kommandozeile macht,
als sichtbaren Ablauf. app.py ruft render_setup_wizard() auf, solange noch nicht
eingerichtet ist.

Ehrliche Grenzen:
  * Ollama (die Engine) muss EINMAL als Programm installiert sein. Das MODELL wird hier
    per Knopf via `ollama pull` geladen.
  * Der Ordner-Dialog nutzt tkinter (lokaler Desktop). Headless/Server -> Pfad-Eingabe.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import streamlit as st

from config import (
    OLLAMA_MODEL, OLLAMA_BASE_URL, INGEST_SOURCES_FILE,
    QDRANT_STORAGE_PATH, QDRANT_URL, QDRANT_COLLECTION_NAME,
)


# ──────────────────────────────────────────────
# Zustands-Checks
# ──────────────────────────────────────────────
def ollama_installed() -> bool:
    return shutil.which("ollama") is not None


def ollama_running() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(OLLAMA_BASE_URL + "/api/tags", timeout=3):
            return True
    except Exception:
        return False


def model_present(model: str = OLLAMA_MODEL) -> bool:
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(OLLAMA_BASE_URL + "/api/tags", timeout=3) as r:
            names = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
        base = model.split(":")[0]
        return any(model == n or n.startswith(base) for n in names)
    except Exception:
        return False


def index_ready() -> bool:
    """Index vorhanden? Server-Modus: Collection existiert. Datei-Modus: Storage-Ordner
    nicht leer."""
    try:
        import qdrant_client
        client = (qdrant_client.QdrantClient(url=QDRANT_URL) if QDRANT_URL
                  else qdrant_client.QdrantClient(path=QDRANT_STORAGE_PATH))
        try:
            info = client.get_collection(QDRANT_COLLECTION_NAME)
            return (info.points_count or 0) > 0
        finally:
            try:
                client.close()
            except Exception:
                pass
    except Exception:
        return os.path.isdir(QDRANT_STORAGE_PATH) and bool(os.listdir(QDRANT_STORAGE_PATH))


def needs_setup() -> bool:
    """Setup zeigen, wenn weder ein Ingest-Manifest noch ein gefuellter Index existiert."""
    return not (os.path.exists(INGEST_SOURCES_FILE) or index_ready())


# ──────────────────────────────────────────────
# Aktionen
# ──────────────────────────────────────────────
def pull_model_stream(model: str, line_cb) -> int:
    """Laedt das Modell via `ollama pull` und streamt Ausgabe zeilenweise an line_cb."""
    proc = subprocess.Popen(["ollama", "pull", model],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    for line in proc.stdout:
        line_cb(line.rstrip())
    proc.wait()
    return proc.returncode


def pick_folder_dialog() -> str:
    """Nativer Ordner-Dialog (tkinter). Liefert "" wenn nicht moeglich/abgebrochen."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title="Demo-/Datenordner waehlen")
        root.destroy()
        return path or ""
    except Exception:
        return ""


def run_pipeline(data_root: str, progress, status) -> dict:
    """Discovery -> Config schreiben -> alle Quellen indexieren. Gibt den Discovery-Report
    + Stats zurueck. Laeuft ohne Ollama (Embeddings reichen)."""
    from onboarding.discovery import discover, format_report
    from onboarding.bootstrap import apply_config
    from ingest import ingest_all

    status("Erkenne Datenquellen (Discovery) ...")
    progress(0.1)
    ls, findings = discover(data_root)
    report = format_report(ls, findings)

    status("Schreibe Konfiguration (sql_sources, ingest_sources, policy/users) ...")
    progress(0.3)
    here = os.path.dirname(os.path.abspath(__file__))
    apply_config(ls, here, os.path.join(here, "deploy", ".env"))

    status("Indexiere alle Dokumentquellen (Embeddings -> Qdrant) ...")
    progress(0.5)

    def _cb(msg, frac):
        status(msg)
        progress(0.5 + 0.5 * float(frac))

    stats = ingest_all(progress_callback=_cb)
    progress(1.0)
    status("Fertig.")
    return {"report": report, "stats": stats, "company": ls.company}


def seed_logins(password: str = "Demo1234!") -> list:
    """Legt fuer jeden Benutzer aus data/users.json ein Login an (scrypt-Hash, kein Klartext)."""
    import json
    from auth import hash_password
    from config import AUTH_USERS_FILE, USERS_FILE
    users = json.load(open(USERS_FILE, encoding="utf-8")).get("users", {})
    out = {"users": {uid: {"display": (u or {}).get("display", uid),
                           "password": hash_password(password)}
                     for uid, u in users.items()}}
    os.makedirs(os.path.dirname(AUTH_USERS_FILE), exist_ok=True)
    with open(AUTH_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    return sorted(out["users"])


# ──────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────
def render_setup_wizard():
    st.markdown('<div class="main-header"><h1>🔧 Einrichtung</h1>'
                '<p>In wenigen Schritten von Null zur einsatzbereiten Demo</p></div>',
                unsafe_allow_html=True)

    # Schritt 1: lokales LLM
    st.subheader("1) Lokales LLM (Ollama)")
    if not ollama_installed():
        st.warning("Ollama ist nicht installiert. Einmalig installieren: "
                   "https://ollama.com/download — danach diese Seite neu laden.")
    elif not ollama_running():
        st.warning("Ollama ist installiert, laeuft aber nicht. Bitte Ollama starten.")
    elif not model_present(OLLAMA_MODEL):
        st.info(f"Modell **{OLLAMA_MODEL}** noch nicht geladen (mehrere GB).")
        if st.button(f"⬇️ {OLLAMA_MODEL} jetzt laden"):
            box = st.empty()
            lines = []
            def _cb(l):
                lines.append(l)
                box.code("\n".join(lines[-12:]))
            rc = pull_model_stream(OLLAMA_MODEL, _cb)
            if rc == 0:
                st.success("Modell geladen.")
                st.rerun()
            else:
                st.error("Laden fehlgeschlagen. Bitte im Terminal pruefen: "
                         f"`ollama pull {OLLAMA_MODEL}`")
    else:
        st.success(f"Ollama bereit, Modell {OLLAMA_MODEL} vorhanden.")

    st.markdown("---")

    # Schritt 2: Ordner waehlen
    st.subheader("2) Daten-/Demo-Ordner waehlen")
    st.caption("Der Ordner mit deinen Quellen (Dateifreigaben, DBs, S3-Seed, APIs, "
               "identity). Bei der Demo der erzeugte `enterprise_demo`-Ordner.")
    col_a, col_b = st.columns([3, 1])
    with col_a:
        folder = st.text_input("Ordnerpfad", value=st.session_state.get("wiz_folder", ""))
    with col_b:
        st.write("")
        st.write("")
        if st.button("📂 Durchsuchen"):
            picked = pick_folder_dialog()
            if picked:
                st.session_state["wiz_folder"] = picked
                st.rerun()
            else:
                st.caption("Kein Dialog moeglich — Pfad bitte einfuegen.")
    if folder:
        st.session_state["wiz_folder"] = folder

    chosen = st.session_state.get("wiz_folder", "")
    valid = bool(chosen) and os.path.isdir(chosen)
    if chosen and not valid:
        st.error("Ordner nicht gefunden.")
    elif valid:
        st.success(f"Ordner: {chosen}")

    st.markdown("---")

    # Schritt 3: Aufbauen
    st.subheader("3) Einrichten (Discovery + Index)")
    if not valid:
        st.caption("Erst einen gueltigen Ordner waehlen.")
    else:
        if st.button("🚀 Jetzt einrichten", type="primary"):
            # Laufende Engine schliessen (Qdrant-Lock freigeben).
            if st.session_state.get("query_engine") is not None:
                try:
                    st.session_state.query_engine.close()
                except Exception:
                    pass
                st.session_state.query_engine = None
            prog = st.progress(0.0)
            stat = st.empty()
            try:
                result = run_pipeline(chosen,
                                      lambda f: prog.progress(min(max(f, 0.0), 1.0)),
                                      lambda m: stat.info(m))
                st.session_state["setup_result"] = result
                st.session_state["setup_done"] = True
                st.success(f"Eingerichtet: {result['company']} · "
                           f"{result['stats'].get('sources', 0)} Quelle(n), "
                           f"{result['stats'].get('chunks', 0)} Chunks.")
                with st.expander("Discovery-Report (inkl. Sicherheits-Befunde)"):
                    st.code(result["report"])
            except Exception as e:
                st.error(f"Einrichtung fehlgeschlagen: {e}")

    if st.session_state.get("setup_done"):
        st.markdown("---")
        st.subheader("4) Logins fuer die Benutzer anlegen (optional)")
        st.caption("Legt fuer jeden Mitarbeiter der erkannten Firma ein Login an. "
                   "Der Login-Screen erscheint aber nur, wenn die App mit AUTH_ENABLED=true "
                   "laeuft.")
        pw = st.text_input("Demo-Passwort fuer alle (min. 8 Zeichen)", value="Demo1234!")
        if st.button("👤 Logins anlegen"):
            if len(pw) < 8:
                st.error("Passwort muss mindestens 8 Zeichen haben.")
            else:
                try:
                    ids = seed_logins(pw)
                    st.success(f"{len(ids)} Login(s) angelegt: {', '.join(ids)}")
                    st.caption('Fuer den Login-Screen App neu starten: '
                               'PowerShell  $env:AUTH_ENABLED=\"true\"; python -m streamlit run app.py')
                except Exception as e:
                    st.error(f"Konnte Logins nicht anlegen: {e}")

        st.markdown("---")
        st.subheader("5) Loslegen")
        if st.button("✅ Zum Assistenten", type="primary"):
            st.session_state["skip_setup"] = True
            st.rerun()
