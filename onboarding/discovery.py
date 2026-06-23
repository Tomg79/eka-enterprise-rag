"""
onboarding/discovery.py -- "Ordner rein, Tool erkennt die Landschaft".

Scannt einen Wurzelordner (das "USB-Stick"-Verzeichnis) und rekonstruiert daraus eine
Landscape: SQLite-Datenbanken (mit Schema-Introspektion), Dateifreigaben, Object-Store-
Seeds, SaaS-API-Ordner und die Identity (policy/users). Zusaetzlich erzeugt sie einen
Befund-Report inkl. Sicherheits-Auffaelligkeiten (verwaiste Gruppen, ACL-lose Dateien,
User mit unbekannten Rollen, vertrauliche Tabellen).

Designprinzipien:
  * NUR Lesen. Discovery aendert nichts an den Quelldaten.
  * Fail-closed im Zweifel: was nicht eindeutig erkennbar ist, wird als Hinweis gemeldet,
    nicht stillschweigend freigegeben.
  * KEINE Secrets im Ergebnis -- nur Struktur + Env-Variablennamen.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3

logger = logging.getLogger(__name__)

from .landscape import (
    Landscape, FileShare, Database, SqlTable, ObjectStore, ApiSource, Identity,
)

_TEXT_EXT = (".txt", ".md", ".csv", ".sql", ".yaml", ".yml", ".graphql",
             ".http", ".json", ".pdf", ".docx", ".xlsx")
_SENSITIVE_HINT = re.compile(r"(SALAR|GEHALT|BONUS|PAYROLL|WAGE|COMPENSATION)", re.I)
_RESERVED_DIRS = {"identity", "__pycache__"}


# ──────────────────────────────────────────────────────────────
# SQLite-Introspektion
# ──────────────────────────────────────────────────────────────
def _introspect_sqlite(db_path: str) -> list:
    """Liest Tabellen+Spalten, raet Lookup-Spalte (PK, sonst erste Spalte) und markiert
    vertrauliche Tabellen heuristisch. Returns list[SqlTable].
    ROBUST: ist die Datei keine gueltige SQLite-DB (kaputt/Muelldatei mit .db-Endung),
    wird [] zurueckgegeben statt zu crashen (echte Korpusse enthalten solche Dateien)."""
    out = []
    try:
        con = sqlite3.connect(db_path)
    except Exception as e:
        logger.error("DISCOVERY: '%s' nicht oeffenbar (%s) -> uebersprungen.", db_path, e)
        return []
    try:
        cur = con.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for t in tables:
            info = list(cur.execute(f"PRAGMA table_info({t})"))
            cols = [row[1] for row in info]
            pks = [row[1] for row in info if row[5]]  # row[5] = pk flag
            lookup = pks[0] if pks else (cols[0] if cols else "")
            sensitive = any(_SENSITIVE_HINT.search(c) for c in cols) or bool(_SENSITIVE_HINT.search(t))
            out.append(SqlTable(table=t, lookup_column=lookup, select_columns=cols, sensitive=sensitive))
    except Exception as e:
        logger.error("DISCOVERY: '%s' ist keine gueltige SQLite-DB (%s) -> uebersprungen.",
                     db_path, e)
        out = []
    finally:
        try:
            con.close()
        except Exception:
            pass
    return out


def infer_lookup_pattern(values) -> tuple:
    """Leitet aus Beispielwerten ein Lookup-Regex + Normalisierung ab.
    Returns (pattern, normalize). Konservativ: das Muster schraenkt nur ein, der
    Connector parametrisiert ohnehin."""
    vals = [str(v).strip() for v in values if v is not None and str(v).strip()]
    if not vals:
        return (r"^.{1,128}$", "")
    if all(re.fullmatch(r"[A-Za-z]+-\d+", v) for v in vals):
        prefix = re.match(r"([A-Za-z]+)-", vals[0]).group(1)
        if all(v.upper().startswith(prefix.upper() + "-") for v in vals):
            norm = "upper" if prefix.isupper() or prefix.islower() else ""
            return (rf"^{re.escape(prefix.upper())}-\d{{1,9}}$", "upper")
    if all(re.fullmatch(r"\d+", v) for v in vals):
        return (r"^\d{1,12}$", "")
    return (r"^.{1,128}$", "")


def _sample_lookup_values(db_path: str, table: str, column: str, n: int = 25) -> list:
    if not column:
        return []
    try:
        con = sqlite3.connect(db_path)
        try:
            rows = con.execute(f'SELECT "{column}" FROM "{table}" LIMIT {int(n)}').fetchall()
            return [r[0] for r in rows]
        finally:
            con.close()
    except Exception:
        return []


# ──────────────────────────────────────────────────────────────
# Scanner
# ──────────────────────────────────────────────────────────────
def _find_sqlite_dbs(root: str) -> list:
    found = []
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            if fn.lower().endswith((".db", ".sqlite", ".sqlite3")):
                found.append(os.path.join(dp, fn))
    return sorted(found)


def _dir_has_text(path: str) -> bool:
    for dp, _dn, fns in os.walk(path):
        for fn in fns:
            if fn.endswith(_TEXT_EXT) and not fn.endswith(".acl.json"):
                return True
    return False


def _looks_like_object_store(path: str) -> bool:
    """Heuristik: Ordner mit Text-Objekten, die per <obj>.acl.json beACLt sind."""
    for dp, _dn, fns in os.walk(path):
        if any(fn.endswith(".acl.json") for fn in fns):
            return True
    return False


def _dir_has_api_dumps(path: str) -> bool:
    for dp, _d, fns in os.walk(path):
        if any(f.lower().startswith("dump_") and f.lower().endswith(".json") for f in fns):
            return True
    return False


def _find_api_dirs(root: str) -> list:
    """Ordner mit vielen gleichartigen JSON-Records (z.B. CUST-001.json)."""
    out = []
    for dp, _dn, fns in os.walk(root):
        jsons = [f for f in fns if f.endswith(".json") and not f.endswith(".acl.json")]
        record_like = [f for f in jsons if re.match(r"^[A-Za-z]+-\d+\.json$", f)]
        if len(record_like) >= 5:
            out.append((dp, len(record_like)))
    return out


def discover(root: str) -> tuple:
    """Scannt root und liefert (Landscape, findings:list[str]). Wenn bereits ein
    landscape.json existiert, wird es als Ausgangsbasis geladen und nur validiert."""
    root = os.path.abspath(root)
    findings = []
    company = "Erkanntes Unternehmen"

    existing = os.path.join(root, "landscape.json")
    if os.path.exists(existing):
        ls = Landscape.load(existing)
        findings.append(f"Vorhandenes Manifest geladen: {existing}")
        company = ls.company
    else:
        ls = Landscape(company=company)

    # Wenn kein Manifest da war, Struktur aus dem Ordner rekonstruieren.
    if not os.path.exists(existing):
        # --- Datenbanken ---
        for db in _find_sqlite_dbs(root):
            name = os.path.splitext(os.path.basename(db))[0]
            tables = _introspect_sqlite(db)
            if not tables:
                findings.append(f"HINWEIS: '{name}' keine gueltige/leere DB -> uebersprungen.")
                continue
            ls.databases.append(Database(
                name=name, connection_string=f"sqlite:///{db}", tables=tables,
                note="Automatisch erkannt."))
            findings.append(f"DB erkannt: {name} ({len(tables)} Tabellen)")
            for t in tables:
                if t.sensitive:
                    findings.append(f"  WARNUNG: vertrauliche Tabelle {name}.{t.table} "
                                    f"-> nur an berechtigte Rolle freigeben (fail-closed).")

        # --- Identity ---
        pol = os.path.join(root, "identity", "policy.json")
        usr = os.path.join(root, "identity", "users.json")
        if os.path.exists(pol) and os.path.exists(usr):
            ls.identity = Identity(policy_path=pol, users_path=usr)
            findings.append("Identity erkannt: identity/policy.json + users.json")
        else:
            findings.append("HINWEIS: keine Identity (policy/users) gefunden -> wird beim "
                            "Einrichten AUTOMATISCH aus den Daten abgeleitet (DRAFT, bitte pruefen).")

        # --- Object-Stores, Freigaben, APIs ---
        api_dirs = {d for d, _ in _find_api_dirs(root)}
        for entry in sorted(os.listdir(root)):
            full = os.path.join(root, entry)
            if not os.path.isdir(full) or entry in _RESERVED_DIRS:
                continue
            if entry == "databases":
                continue
            # API-Dumps (dump_*.json mit inline-ACL)?
            if _dir_has_api_dumps(full):
                ls.apis.append(ApiSource(name=entry, kind="api_dump", path=full,
                                         note="API-Dumps (dump_*.json, inline-ACL)."))
                findings.append(f"API-Dumps erkannt: {entry}")
                continue
            # API-Ordner (Salesforce-artige Records)?
            if any(full == d or d.startswith(full + os.sep) for d in api_dirs):
                for d, n in _find_api_dirs(full):
                    ls.apis.append(ApiSource(name=os.path.basename(d), kind="salesforce_json",
                                             path=d, note=f"{n} JSON-Records erkannt."))
                    findings.append(f"API erkannt: {os.path.basename(d)} ({n} Records)")
                continue
            # Object-Store (Buckets mit ACL-Sidecars)?
            if entry in ("object_store", "objects", "buckets") and _looks_like_object_store(full):
                for bucket in sorted(os.listdir(full)):
                    bpath = os.path.join(full, bucket)
                    if os.path.isdir(bpath):
                        ls.object_stores.append(ObjectStore(
                            name=bucket, bucket=bucket, local_seed_path=bpath,
                            acl_reader="composite", credentials_env="",
                            note="Lokaler Seed erkannt; fuer echtes S3 endpoint_url+Creds setzen."))
                        findings.append(f"Object-Store erkannt: {bucket} (lokaler Seed)")
                continue
            # Sonst: Dateifreigabe, wenn Text drin.
            if _dir_has_text(full):
                ls.file_shares.append(FileShare(name=entry, path=full, acl_reader="normalized",
                                                note="Automatisch erkannte Freigabe."))
                findings.append(f"Dateifreigabe erkannt: {entry}")

    # --- Konsistenz-/Sicherheitspruefungen (immer) ---
    findings.extend(_audit_landscape(ls))
    ls.company = company
    return ls, findings


def _collect_acl_groups(share_path: str) -> set:
    groups = set()
    for dp, _dn, fns in os.walk(share_path):
        for fn in fns:
            if fn.endswith(".acl.json"):
                try:
                    with open(os.path.join(dp, fn), "r", encoding="utf-8") as f:
                        for g in (json.load(f).get("groups") or []):
                            groups.add(str(g))
                except Exception:
                    pass  # defekter Sidecar wird separat als fail-closed behandelt
    return groups


def _text_files_without_sidecar(path: str) -> list:
    """Text-Dateien, zu denen KEIN <datei>.acl.json existiert. Bei composite/prefix-Lesern
    fallen diese auf die PUBLIC-Default-Gruppe zurueck -> potenzielle Ueber-Exposition."""
    missing = []
    for dp, _dn, fns in os.walk(path):
        names = set(fns)
        for fn in fns:
            if fn.endswith(_TEXT_EXT) and not fn.endswith(".acl.json"):
                if (fn + ".acl.json") not in names:
                    missing.append(os.path.join(dp, fn))
    return missing


def _audit_landscape(ls: Landscape) -> list:
    """Sicherheits-Audit ueber die (rekonstruierte) Landscape."""
    out = []
    # Bekannte Gruppen aus der Policy.
    policy_groups, policy_roles = set(), {}
    user_roles = {}
    if ls.identity.policy_path and os.path.exists(ls.identity.policy_path):
        try:
            roles = json.load(open(ls.identity.policy_path, encoding="utf-8")).get("roles", {})
            policy_roles = roles
            for rd in roles.values():
                policy_groups.update(rd.get("groups") or [])
        except Exception as e:
            out.append(f"WARNUNG: policy.json nicht lesbar ({e}) -> fail-closed.")
    if ls.identity.users_path and os.path.exists(ls.identity.users_path):
        try:
            user_roles = json.load(open(ls.identity.users_path, encoding="utf-8")).get("users", {})
        except Exception as e:
            out.append(f"WARNUNG: users.json nicht lesbar ({e}).")

    # Gruppen, die in Datei-ACLs vorkommen.
    acl_groups = set()
    for fs in ls.file_shares:
        acl_groups |= _collect_acl_groups(fs.path)
    for os_ in ls.object_stores:
        if os_.local_seed_path:
            acl_groups |= _collect_acl_groups(os_.local_seed_path)

    # 1) ACL-Gruppen, die keine Rolle gewaehrt -> Daten fuer NIEMANDEN sichtbar.
    orphan_acl = sorted(g for g in acl_groups if policy_groups and g not in policy_groups)
    for g in orphan_acl:
        out.append(f"SICHERHEIT: ACL-Gruppe '{g}' wird von KEINER Rolle gewaehrt -> "
                   f"betroffene Daten sind fuer niemanden sichtbar (verwaiste Gruppe).")
    # 2) Policy-Rollen, die unbekannte Gruppen referenzieren.
    for rname, rd in policy_roles.items():
        for g in (rd.get("groups") or []):
            if acl_groups and g not in acl_groups:
                out.append(f"HINWEIS: Rolle '{rname}' gewaehrt Gruppe '{g}', die in keiner "
                           f"Datei-ACL vorkommt (evtl. Tippfehler/ungenutzt).")
    # 3) User mit unbekannten Rollen.
    for uid, u in user_roles.items():
        for r in (u.get("roles") or []):
            if policy_roles and r not in policy_roles:
                out.append(f"SICHERHEIT: User '{uid}' referenziert unbekannte Rolle '{r}' "
                           f"-> erhaelt KEINE Rechte (fail-closed).")
    # 4) Ueber-Exposition: ACL-lose Dateien unter composite/prefix-Freigaben fallen auf
    #    die PUBLIC-Default-Gruppe zurueck -> vertrauliche Inhalte koennten oeffentlich werden.
    for fs in ls.file_shares:
        missing = _text_files_without_sidecar(fs.path)
        if not missing:
            continue
        sample = ", ".join(os.path.relpath(m, fs.path) for m in missing[:3])
        if fs.acl_reader in ("composite", "prefix"):
            out.append(f"SICHERHEIT: Freigabe '{fs.name}' nutzt '{fs.acl_reader}' und hat "
                       f"{len(missing)} Datei(en) OHNE ACL (z.B. {sample}). Der Prefix-Fallback "
                       f"gibt diese als PUBLIC frei -> fuer vertrauliche Freigaben 'normalized'/"
                       f"'sidecar' (fail-closed) oder 'windows' (NTFS) verwenden.")
        else:
            out.append(f"HINWEIS: Freigabe '{fs.name}' ('{fs.acl_reader}') hat {len(missing)} "
                       f"Datei(en) OHNE ACL (z.B. {sample}) -> fail-closed UNSICHTBAR. Falls sie "
                       f"sichtbar sein sollen, ACL/Sidecar ergaenzen.")
    return out


def format_report(ls: Landscape, findings: list) -> str:
    lines = [f"=== Discovery-Report: {ls.company} ===",
             f"Dateifreigaben : {len(ls.file_shares)}",
             f"Datenbanken    : {len(ls.databases)} "
             f"({sum(len(d.tables) for d in ls.databases)} Tabellen)",
             f"Object-Stores  : {len(ls.object_stores)}",
             f"APIs/SaaS      : {len(ls.apis)}",
             f"Identity       : {'ja' if ls.identity.policy_path else 'NEIN (fail-closed)'}",
             "", "Befunde:"]
    for f in findings:
        lines.append(f"  - {f}")
    return "\n".join(lines)
