"""
launcher.py -- Einstiegspunkt fuer die gepackte Windows-.exe (EKA Enterprise-RAG).

Macht aus dem Stack ein "Doppelklick"-Erlebnis:
  1. (optional) ONBOARDING: ist ein Datenordner angegeben/gefunden (USB/Server-Mount),
     wird er erkannt und die App-Konfiguration daraus erzeugt (onboarding.bootstrap).
  2. CHECK: prueft, ob Ollama erreichbar ist (nur Hinweis, kein Abbruch).
  3. START: startet die Streamlit-App und oeffnet den Browser.

Funktioniert sowohl normal (python launcher.py) als auch als PyInstaller-Binary
(sys.frozen). Schwere Importe sind lazy, damit die Datei ueberall importierbar bleibt.

Aufruf:
  EKA.exe                         # startet die App
  EKA.exe --data-root D:\\Firma    # richtet zuerst aus dem Ordner ein, dann Start
  EKA.exe --no-browser --port 8502
"""

from __future__ import annotations

import argparse
import os
import sys


def _app_dir() -> str:
    """Verzeichnis mit app.py -- im PyInstaller-Bundle liegt es unter sys._MEIPASS."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _looks_like_data_root(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    if os.path.exists(os.path.join(path, "landscape.json")):
        return True
    # Heuristik: typische Unterordner einer Firmen-Landschaft.
    for sub in ("shares", "databases", "identity", "object_store", "apis"):
        if os.path.isdir(os.path.join(path, sub)):
            return True
    return False


def run_onboarding(data_root: str) -> None:
    """Erkennt Quellen im Ordner und schreibt die App-Konfiguration (fail-closed)."""
    app_dir = _app_dir()
    sys.path.insert(0, app_dir)
    from onboarding.discovery import discover, format_report
    from onboarding.bootstrap import apply_config

    ls, findings = discover(data_root)
    print(format_report(ls, findings))
    env_out = os.path.join(app_dir, "deploy", ".env")
    written = apply_config(ls, app_dir, env_out)
    print("\nKonfiguration geschrieben:")
    for w in written:
        print("  -", w)


def check_ollama() -> bool:
    try:
        sys.path.insert(0, _app_dir())
        from config import OLLAMA_BASE_URL
        import urllib.request
        with urllib.request.urlopen(OLLAMA_BASE_URL + "/api/tags", timeout=3):
            return True
    except Exception:
        return False


def launch_app(port: int, open_browser: bool) -> int:
    app_dir = _app_dir()
    app_path = os.path.join(app_dir, "app.py")
    if open_browser:
        try:
            import threading
            import webbrowser
            threading.Timer(2.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
        except Exception:
            pass
    # Streamlit ueber seine CLI starten -- funktioniert auch im PyInstaller-Bundle.
    sys.argv = ["streamlit", "run", app_path,
                "--server.address=0.0.0.0", f"--server.port={port}",
                "--server.headless=true"]
    from streamlit.web import cli as stcli
    return stcli.main()


def main() -> int:
    ap = argparse.ArgumentParser(description="EKA Enterprise-RAG Launcher")
    ap.add_argument("--data-root", default=os.environ.get("EKA_DATA_ROOT", ""),
                    help="Datenordner (USB/Server-Mount) fuer das Onboarding.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("EKA_PORT", "8501")))
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--onboard-only", action="store_true",
                    help="Nur einrichten, nicht starten.")
    args = ap.parse_args()

    print("== EKA Enterprise-RAG ==")
    if args.data_root and _looks_like_data_root(args.data_root):
        print(f"Datenordner erkannt: {args.data_root} -> Onboarding ...")
        run_onboarding(args.data_root)
    elif args.data_root:
        print(f"WARNUNG: '{args.data_root}' sieht nicht nach einem Datenordner aus "
              f"-> ueberspringe Onboarding.")

    if args.onboard_only:
        print("Onboarding abgeschlossen (--onboard-only).")
        return 0

    if not check_ollama():
        print("HINWEIS: Ollama scheint offline. Bitte starten und Modell laden "
              "(z.B. 'ollama pull qwen2.5:14b'). Die App startet trotzdem.")

    print(f"Starte Oberflaeche auf http://localhost:{args.port} ...")
    return launch_app(args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    sys.exit(main())
