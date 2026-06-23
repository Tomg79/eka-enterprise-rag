"""
onboarding/bootstrap.py -- "Ordner gewaehlt -> Tool richtet sich selbst ein".

Der Orchestrator des USB-Stick-Szenarios. Ablauf:

    1. DISCOVER  -- Quellen im gewaehlten Ordner erkennen (onboarding/discovery.py),
                    bzw. ein vorhandenes landscape.json laden.
    2. GENERATE  -- aus der Landscape die App-Konfiguration erzeugen, die der bestehende
                    Stack bereits versteht:
                      * data/sql_sources.json  (generischer SQL-Connector, Phase 4)
                      * data/policy.json + data/users.json  (Identity uebernehmen)
                      * .env-Schnipsel fuer S3/MinIO + Qdrant/Ollama
                    Bestehende Dateien werden vorher gesichert (*.bak).
    3. REPORT    -- Readiness-/Sicherheitsbericht + naechste Schritte (Ingest, Browser).

Sicherheit: kein frei generiertes SQL (nur Lookup-Whitelist), Identity 1:1 uebernommen
(Rechte entscheidet weiterhin allein die PolicyEngine), KEINE Secrets ins Repo -- S3-
Zugangsdaten nur als Env-Variablennamen referenziert.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from onboarding.landscape import Landscape
from onboarding.discovery import discover, format_report, infer_lookup_pattern, _sample_lookup_values


def _backup(path: str):
    if os.path.exists(path):
        shutil.copyfile(path, path + ".bak")


def _sqlite_path_from_conn(conn: str) -> str:
    return conn[len("sqlite:///"):] if conn.startswith("sqlite:///") else ""


def build_sql_sources(ls: Landscape) -> dict:
    """Uebersetzt die Datenbanken der Landscape in data/sql_sources.json. Pro Tabelle ein
    parametrisiertes Lookup; Pattern/Normalisierung aus Beispielwerten abgeleitet."""
    sources = {}
    for db in ls.databases:
        tables = {}
        db_file = _sqlite_path_from_conn(db.connection_string)
        for t in db.tables:
            lookup = t.lookup_column or (t.select_columns[0] if t.select_columns else "")
            if not lookup or not t.select_columns:
                continue  # nicht eindeutig -> ueberspringen (fail-closed)
            samples = _sample_lookup_values(db_file, t.table, lookup) if db_file else []
            pattern, normalize = infer_lookup_pattern(samples)
            tables[t.table] = {
                "lookup_column": lookup,
                "lookup_pattern": pattern,
                "normalize": normalize,
                "select_columns": list(t.select_columns),
                "rbac_id": f"{db.name}.{t.table}",
                "tool_name": f"{db.name}_{t.table}_lookup".lower(),
                "tool_description": (f"Look up a row in {db.name}.{t.table} by its "
                                     f"{lookup} (e.g. one of {samples[:1]})."),
            }
        if tables:
            sources[db.name] = {"connection_string": db.connection_string, "tables": tables}
    return {"_comment": f"Auto-generiert von bootstrap.py aus landscape ({ls.company}).",
            "sources": sources}


def build_env(ls: Landscape) -> str:
    """Erzeugt einen .env-Schnipsel fuer Deployment (Qdrant/Ollama/S3)."""
    lines = ["# Auto-generiert von bootstrap.py -- bei Bedarf anpassen. KEINE Secrets committen.",
             "OLLAMA_MODEL=qwen2.5:14b"]
    if ls.object_stores:
        os0 = ls.object_stores[0]
        if os0.endpoint_url:
            lines += ["S3_ENABLED=true", f"S3_BUCKET={os0.bucket}", f"S3_PREFIX={os0.prefix}",
                      f"S3_ENDPOINT_URL={os0.endpoint_url}", f"S3_ACL_READER={os0.acl_reader}",
                      "# S3-Credentials NUR hier lokal setzen (nicht committen):",
                      "AWS_ACCESS_KEY_ID=", "AWS_SECRET_ACCESS_KEY="]
        else:
            lines += ["# Object-Store als lokaler Seed erkannt. Fuer echtes S3/MinIO: endpoint_url",
                      "# + Credentials setzen und Seed hochladen (deploy/minio_seed.sh).",
                      "S3_ENABLED=false"]
    return "\n".join(lines) + "\n"


def build_ingest_sources(ls: Landscape) -> dict:
    """Manifest fuer den Multi-Quellen-Ingest: alle Dateifreigaben + Object-Stores."""
    return {
        "_comment": f"Auto-generiert von bootstrap.py ({ls.company}). Steuert ingest_all().",
        "file_shares": [
            {"name": f.name, "path": f.path, "acl_reader": f.acl_reader}
            for f in ls.file_shares
        ],
        "object_stores": [
            {"name": o.name, "bucket": o.bucket, "prefix": o.prefix,
             "endpoint_url": o.endpoint_url, "acl_reader": o.acl_reader,
             "local_seed_path": o.local_seed_path}
            for o in ls.object_stores
        ],
        "apis": [
            {"name": a.name, "kind": a.kind, "path": a.path}
            for a in ls.apis if a.kind == "api_dump"
        ],
    }


def apply_config(ls: Landscape, config_dir: str, env_out: str) -> list:
    """Schreibt sql_sources.json, uebernimmt Identity, schreibt .env. Returns Liste der
    geschriebenen/gesicherten Dateien."""
    written = []
    data_dir = os.path.join(config_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    # 1) sql_sources.json
    sql_path = os.path.join(data_dir, "sql_sources.json")
    _backup(sql_path)
    with open(sql_path, "w", encoding="utf-8") as f:
        json.dump(build_sql_sources(ls), f, indent=2, ensure_ascii=False)
    written.append(sql_path)

    # 1b) ingest_sources.json -- steuert den Multi-Quellen-Ingest (file shares + S3).
    ing_path = os.path.join(data_dir, "ingest_sources.json")
    _backup(ing_path)
    with open(ing_path, "w", encoding="utf-8") as f:
        json.dump(build_ingest_sources(ls), f, indent=2, ensure_ascii=False)
    written.append(ing_path)

    # 2) Identity: vorhandene uebernehmen, sonst AUTOMATISCH aus den Daten ableiten (DRAFT).
    has_identity = bool(ls.identity.policy_path and os.path.exists(ls.identity.policy_path)
                        and ls.identity.users_path and os.path.exists(ls.identity.users_path))
    pol_dst = os.path.join(data_dir, "policy.json")
    usr_dst = os.path.join(data_dir, "users.json")
    if has_identity:
        _backup(pol_dst); shutil.copyfile(ls.identity.policy_path, pol_dst); written.append(pol_dst)
        _backup(usr_dst); shutil.copyfile(ls.identity.users_path, usr_dst); written.append(usr_dst)
    else:
        # Keine identity/ vorhanden -> AUTOMATISCH ableiten (DRAFT):
        #  (1) bevorzugt aus der Abteilungs-Struktur/ACLs (governance) -> erzeugt
        #      Abteilungs-Rollen + ein Demo-Login pro Abteilung;
        #  (2) sonst Fallback auf die Mitarbeiter-Tabellen-Heuristik (identity_infer).
        policy_doc = users_doc = None
        notes = []
        try:
            from onboarding.governance_import import build_dicts
            gpol, gusr, ginfo = build_dicts(sql_path, db_path_resolver=None)
            if gusr.get("users"):
                policy_doc, users_doc = gpol, gusr
                notes = [f"Identity aus Abteilungs-Struktur abgeleitet: "
                         f"{len(gusr['users'])} Rollen/Logins (governance)."]
        except Exception as e:
            notes = [f"governance-Ableitung fehlgeschlagen ({e}); nutze Heuristik."]
        if policy_doc is None:
            from onboarding.identity_infer import infer_identity
            policy_doc, users_doc, inotes = infer_identity(ls)
            notes += inotes
        _backup(pol_dst)
        with open(pol_dst, "w", encoding="utf-8") as f:
            json.dump(policy_doc, f, indent=2, ensure_ascii=False)
        written.append(pol_dst)
        _backup(usr_dst)
        with open(usr_dst, "w", encoding="utf-8") as f:
            json.dump(users_doc, f, indent=2, ensure_ascii=False)
        written.append(usr_dst)
        ls.notes = list(ls.notes) + notes
        ls.identity.policy_path = pol_dst
        ls.identity.users_path = usr_dst

    # 3) .env-Schnipsel
    os.makedirs(os.path.dirname(os.path.abspath(env_out)) or ".", exist_ok=True)
    _backup(env_out)
    with open(env_out, "w", encoding="utf-8") as f:
        f.write(build_env(ls))
    written.append(env_out)
    return written


def main():
    ap = argparse.ArgumentParser(description="Onboarding: Ordner -> App-Konfiguration.")
    ap.add_argument("--root", required=True, help="Gewaehlter Quellordner (USB/Server-Mount).")
    ap.add_argument("--config-dir", default=".",
                    help="Wohin die App-Config geschrieben wird (Default: Projektwurzel).")
    ap.add_argument("--env-out", default="deploy/.env", help="Pfad fuer den .env-Schnipsel.")
    ap.add_argument("--apply", action="store_true",
                    help="Config tatsaechlich schreiben (sonst nur Report/Trockenlauf).")
    args = ap.parse_args()

    ls, findings = discover(args.root)
    # Manifest im Quellordner aktualisieren/ablegen (idempotent).
    try:
        ls.save(os.path.join(os.path.abspath(args.root), "landscape.json"))
    except Exception:
        pass

    print(format_report(ls, findings))
    print()

    if not args.apply:
        print("TROCKENLAUF (kein --apply). Vorschau der App-Konfiguration:")
        ss = build_sql_sources(ls)
        n_tab = sum(len(s["tables"]) for s in ss["sources"].values())
        print(f"  sql_sources.json: {len(ss['sources'])} Quelle(n), {n_tab} Lookup-Tabelle(n)")
        print(f"  Identity: {'wird uebernommen' if ls.identity.policy_path else 'FEHLT'}")
        print("  -> Mit --apply schreiben.")
        return

    os.makedirs(os.path.dirname(os.path.abspath(args.env_out)) or ".", exist_ok=True)
    written = apply_config(ls, args.config_dir, args.env_out)
    print("Geschrieben:")
    for w in written:
        print(f"  - {w}")
    print("\nNaechste Schritte:")
    print("  1) Backend starten:   bash deploy/install.sh   (oder deploy\\install.ps1)")
    print("  2) Daten indexieren:  ueber die App-Oberflaeche bzw. Ingest ausloesen")
    print("  3) Im Browser oeffnen: http://localhost:8501")


if __name__ == "__main__":
    main()
