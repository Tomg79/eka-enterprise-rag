# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-Spec fuer den EKA Enterprise-RAG Launcher.
# Bauen:  pyinstaller packaging/eka.spec   (auf Windows, im Projekt-Root)
#
# Hinweis: Das LLM-Modell (Ollama) und die bge-m3-Embeddings werden NICHT eingebettet
# (mehrere GB). Sie werden zur Laufzeit bereitgestellt (ollama pull / HF-Cache).
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
# Schwere, dynamisch ladende Pakete vollstaendig mitnehmen.
for pkg in ("streamlit", "llama_index", "qdrant_client", "sqlalchemy",
            "huggingface_hub", "tokenizers", "boto3", "botocore"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass
hiddenimports += collect_submodules("llama_index")

# App-Code + Default-Daten/Config ins Bundle (neben die .exe, via _MEIPASS erreichbar).
PROJECT = os.path.abspath(os.getcwd())
for item in ("app.py", "config.py", "policy.py", "query.py", "ingest.py", "audit.py",
             "meetings.py", "auth.py", "connectors", "onboarding", "deploy", ".streamlit"):
    p = os.path.join(PROJECT, item)
    if os.path.isdir(p):
        datas.append((p, item))
    elif os.path.isfile(p):
        datas.append((p, "."))

a = Analysis(
    ["launcher.py"],
    pathex=[PROJECT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="EKA",
    console=True,            # Konsole zeigt Start-/Onboarding-Logs (fuer Admins nuetzlich)
    icon=None,               # optional: packaging/eka.ico
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    name="EKA",
)
