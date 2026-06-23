"""
quickstart.py -- "Von Null zum laufenden Test" in EINEM Befehl.

Stellt sich vor: eine Firma hat NICHTS und will sehen, ob/wie das Tool funktioniert.
Dieser Orchestrator macht alle Schritte, die KEIN Ollama brauchen, automatisch:

  1. Demo-Firma erzeugen (oder eigenen Datenordner via --data-root nehmen)
  2. ONBOARDING: Quellen erkennen + App-Konfiguration schreiben
     (sql_sources.json, ingest_sources.json, policy.json/users.json, deploy/.env)
  3. LOGINS anlegen (ein Demo-Passwort fuer alle Benutzer der Firma)
  4. Anleitung ausgeben (die 2 verbleibenden, LLM-abhaengigen Schritte + Login-Tabelle)

Beispiele:
  python quickstart.py                       # Demo-Firma "ACME" -> alles einrichten
  python quickstart.py --data-root D:\\Firma  # echte Firmendaten statt Demo
  python quickstart.py --password Geheim123
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _gen_demo(demo_root: str):
    """Erzeugt die synthetische Test-Firma (nutzt den vorhandenen Generator)."""
    import importlib.util
    path = os.path.join(HERE, "tools", "generate_enterprise_landscape.py")
    spec = importlib.util.spec_from_file_location("gen_landscape", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    os.makedirs(demo_root, exist_ok=True)
    fs, _q = mod.gen_file_shares(demo_root)
    dbs = mod.gen_databases(demo_root)
    obj = mod.gen_object_store(demo_root)
    apis = mod.gen_apis(demo_root)
    ident = mod.gen_identity(demo_root)
    from onboarding.landscape import Landscape
    Landscape(company=mod.COMPANY, file_shares=fs, databases=dbs, object_stores=obj,
              apis=apis, identity=ident).save(os.path.join(demo_root, "landscape.json"))
    return mod.COMPANY


def _seed_logins(config_dir: str, password: str) -> list:
    """Legt fuer jeden Benutzer aus data/users.json ein Login an (scrypt-Hash)."""
    from auth import hash_password
    users_file = os.path.join(config_dir, "data", "users.json")
    auth_file = os.path.join(config_dir, "data", "auth_users.json")
    users = json.load(open(users_file, encoding="utf-8")).get("users", {})
    out = {"users": {}}
    for uid, u in users.items():
        out["users"][uid] = {"display": (u or {}).get("display", uid),
                             "password": hash_password(password)}
    with open(auth_file, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    try:
        os.chmod(auth_file, 0o600)
    except Exception:
        pass
    return sorted((uid, v["display"]) for uid, v in out["users"].items())


def main() -> int:
    ap = argparse.ArgumentParser(description="EKA Quickstart: von Null zum Test.")
    ap.add_argument("--data-root", default="", help="Eigener Datenordner (sonst Demo).")
    ap.add_argument("--demo-root", default=os.path.join(HERE, "enterprise_demo"),
                    help="Wohin die Demo-Firma erzeugt wird.")
    ap.add_argument("--config-dir", default=HERE, help="Wohin die App-Config geschrieben wird.")
    ap.add_argument("--password", default="Demo1234!", help="Demo-Login-Passwort (>=8 Zeichen).")
    args = ap.parse_args()

    if len(args.password) < 8:
        print("FEHLER: --password braucht mindestens 8 Zeichen.", file=sys.stderr)
        return 2

    print("=" * 60)
    print(" EKA Enterprise-RAG -- Quickstart (von Null)")
    print("=" * 60)

    # 1) Quelle bestimmen
    if args.data_root:
        source = os.path.abspath(args.data_root)
        print(f"[1/3] Eigener Datenordner: {source}")
    else:
        source = os.path.abspath(args.demo_root)
        print(f"[1/3] Erzeuge Demo-Firma in: {source}")
        company = _gen_demo(source)
        print(f"      -> {company} erstellt.")

    # 2) Onboarding
    from onboarding.discovery import discover, format_report
    from onboarding.bootstrap import apply_config
    ls, findings = discover(source)
    print("\n[2/3] Onboarding / Discovery:")
    print(format_report(ls, findings))
    env_out = os.path.join(args.config_dir, "deploy", ".env")
    written = apply_config(ls, args.config_dir, env_out)
    print("      Konfiguration geschrieben:")
    for w in written:
        print("        -", w)

    # 3) Logins
    print("\n[3/3] Lege Logins an (ein Demo-Passwort fuer alle) ...")
    logins = _seed_logins(args.config_dir, args.password)
    print(f"      {len(logins)} Konten angelegt.")

    # Abschluss-Anleitung
    print("\n" + "=" * 60)
    print(" FERTIG eingerichtet. Noch 2 Schritte (brauchen das lokale LLM):")
    print("=" * 60)
    print(" 1) Ollama bereitstellen:   ollama pull qwen2.5:14b")
    print(" 2) App starten MIT Login:")
    print("      PowerShell: $env:AUTH_ENABLED=\"true\"; python -m streamlit run app.py")
    print("      CMD       : set AUTH_ENABLED=true && python -m streamlit run app.py")
    print("      (oder die gepackte EKA.exe nutzen)")
    print("    Dann in der Sidebar 'Ingest Docs' klicken (indexiert ALLE Quellen).")
    print("\n Anmelden zum Testen (Passwort fuer alle: %r):" % args.password)
    for uid, disp in logins:
        print(f"    - {uid:14s} {disp}")
    print("\n Tipp: als 'legal_max' nach einem Vertrag fragen (sieht es),")
    print("       als 'intern_ida' dieselbe Frage (wird verweigert).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
