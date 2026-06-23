"""
onboarding/acl_normalize.py -- Deterministischer ACL-Normalisierer (KEIN LLM).

Liest die in den Quellen vorhandenen Berechtigungen (DB-`_acl`-Tabellen in den Modellen
ActiveDirectory / SAP / SharePoint / POSIX / Inline, sonst Sidecar, sonst Ordner) und
leitet pro Objekt die erlaubten Lesegruppen ab -- regelbasiert, reproduzierbar.

Kernidee: Jedes Prinzipal (AD-Gruppe, SAP-Rolle, SharePoint-Gruppe, POSIX-Gruppe) wird
auf einen kanonischen Abteilungscode (EK/FI/HR/...) abgebildet. Genau diese Codes sind die
acl_groups, gegen die die PolicyEngine die Rollen prueft. Deny gewinnt; was nicht erlaubt
ist, ist verboten (fail-closed).
"""
from __future__ import annotations

import os
import re
import sqlite3

KNOWN_CODES = {"EK", "FI", "HR", "PP", "IT", "MK", "RE", "VK", "GF", "FE"}

# Ausgeschriebene Abteilungs-/Rollennamen (DE+EN) -> kanonischer Code. Damit werden auch
# Inline-/SharePoint-/Klartext-Berechtigungen wie "Geschaeftsfuehrung" erkannt.
NAME_CODE = {
    "geschaeftsfuehrung": "GF", "geschäftsführung": "GF", "management": "GF",
    "executive": "GF", "vorstand": "GF", "board": "GF", "leitung": "GF",
    "einkauf": "EK", "beschaffung": "EK", "purchasing": "EK", "procurement": "EK",
    "finanzen": "FI", "finance": "FI", "buchhaltung": "FI", "controlling": "FI", "rechnungswesen": "FI",
    "personal": "HR", "human resources": "HR", "humanresources": "HR", "hr": "HR", "personalwesen": "HR",
    "produktion": "PP", "production": "PP", "fertigung": "PP", "manufacturing": "PP",
    "it": "IT", "informationstechnik": "IT", "informationstechnologie": "IT",
    "marketing": "MK", "kommunikation": "MK",
    "recht": "RE", "legal": "RE", "rechtsabteilung": "RE", "justiziariat": "RE",
    "vertrieb": "VK", "sales": "VK", "verkauf": "VK",
    "forschung und entwicklung": "FE", "forschung": "FE", "f&e": "FE", "f und e": "FE",
    "research": "FE", "engineering": "FE", "entwicklung": "FE", "r&d": "FE",
}
_NAME_NORM = {re.sub(r"[^a-zäöüß&]", "", k): v for k, v in NAME_CODE.items()}


def name_to_code(token: str):
    """Ausgeschriebenen Namen -> Code (oder None). Exakt-Match; Teilstring nur fuer lange,
    eindeutige Namen (>=5 Zeichen), um Fehltreffer (z.B. 'it') zu vermeiden."""
    t = re.sub(r"[^a-zäöüß&]", "", (token or "").lower())
    if not t:
        return None
    return _NAME_NORM.get(t)
ALL_INTERNAL = "ALL_INTERNAL"   # SharePoint "Jeder ausser externe" -> alle internen Rollen

# Ordnername (normalisiert) -> Code (Fallback, wenn keine ACL lesbar).
FOLDER_CODE = {
    "einkauf": "EK", "finanzen": "FI", "personal": "HR", "produktion": "PP",
    "it": "IT", "marketing": "MK", "f_und_e": "FE", "fue": "FE", "recht": "RE",
    "vertrieb": "VK", "geschaeftsfuehrung": "GF", "geschaftsfuhrung": "GF",
    "ek": "EK", "fi": "FI", "hr": "HR", "pp": "PP", "it": "IT", "mk": "MK",
    "re": "RE", "vk": "VK", "gf": "GF", "fe": "FE",
}


def normalize_principal(model: str, principal: str) -> set:
    """Prinzipal-String eines Modells -> Menge kanonischer Codes (oder leer/None-artig)."""
    p = principal or ""
    m = (model or "").lower()
    codes = set()
    if "activedirectory" in m or m == "ad":
        for c in re.findall(r"GG-([A-Z]{2})-", p):
            if c in KNOWN_CODES:
                codes.add(c)
    elif "sap" in m:
        for c in re.findall(r"\bZ_([A-Z]{2})_", p):
            if c in KNOWN_CODES:
                codes.add(c)
    elif "posix" in m:
        for c in re.findall(r"\b([a-z]{2})-(?:rw|ro)\b", p) + re.findall(r"svc-([a-z]{2})\b", p):
            if c.upper() in KNOWN_CODES:
                codes.add(c.upper())
    elif "sharepoint" in m:
        if "jeder" in p.lower():
            codes.add(ALL_INTERNAL)
        for c in re.findall(r"(?:Mitglieder|Mitglied|Besucher|Besitzer|Owner|Eigent[uü]mer|Gruppe)\s+([A-Z]{2})", p):
            if c in KNOWN_CODES:
                codes.add(c)
    # Inline-Modell: ausgeschriebene Abteilungsnamen erkennen (DE+EN), z.B. "Geschaeftsfuehrung".
    if not codes and "inline" in m:
        for part in re.split(r"[,;/]| und | & ", p):
            c = name_to_code(part)
            if c:
                codes.add(c)
    return codes


def _read_db_acl(db_path: str) -> set:
    """Liest die `_acl`-Tabelle (falls vorhanden). Returns (codes:set, had_acl:bool)."""
    try:
        con = sqlite3.connect(db_path)
        tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        if "_acl" not in tabs:
            con.close()
            return set(), False
        allow, deny = set(), set()
        for objekt, modell, prinzipal, recht, effekt, geerbt in con.execute(
                "SELECT objekt,modell,prinzipal,recht,effekt,geerbt FROM _acl"):
            codes = normalize_principal(modell, prinzipal)
            (deny if str(effekt).lower().startswith("deny") else allow).update(codes)
        con.close()
        return (allow - deny), True
    except Exception:
        return set(), False


def _folder_code(path: str) -> str:
    """Sucht IRGENDEINEN bekannten Abteilungs-Ordner im Pfad (egal ob unter 01_datenbanken,
    02_api, shares, ...). Robust fuer beliebige Korpus-Layouts."""
    for seg in re.split(r"[\\/]+", path.replace("\\", "/")):
        norm = re.sub(r"[^a-z0-9]+", "_", seg.lower()).strip("_")
        if norm in FOLDER_CODE:
            return FOLDER_CODE[norm]
    return ""


def resolve_groups(conn_string: str, db_path_resolver=None) -> tuple:
    """Ermittelt die Lesegruppen einer DB deterministisch.
    Reihenfolge: _acl-Tabelle -> Sidecar(.acl.json) -> Ordner-Abteilung -> leer (deny).
    Returns (codes:set, quelle:str)."""
    raw = conn_string[len("sqlite:///"):] if conn_string.startswith("sqlite:///") else conn_string
    local = db_path_resolver(raw) if db_path_resolver else raw

    codes, had = _read_db_acl(local)
    if had and codes:
        return codes, "acl_tabelle"

    # Sidecar?
    for cand in (local + ".acl.json", os.path.splitext(local)[0] + ".acl.json"):
        if os.path.exists(cand):
            try:
                import json
                data = json.load(open(cand, encoding="utf-8"))
                g = groups_from_sidecar(data)
                if g:
                    return g, "sidecar"
            except Exception:
                pass

    fc = _folder_code(raw)
    if fc:
        return {fc}, "ordner"
    return set(), "deny"


def groups_from_sidecar(data: dict) -> set:
    """Normalisiert einen `.acl.json`-Sidecar BELIEBIGEN Modells auf Lesegruppen.
    Dual: explizite Gruppenlisten ('groups'/'principals') werden ROH durchgereicht, wenn
    sie nicht auf einen Abteilungscode normalisierbar sind (unterstuetzt einfache Schemata
    wie {"groups":["SALES","PUBLIC"]}). Modellspezifische Felder (POSIX owner/group, SAP
    'roles', SharePoint 'roleAssignments'/siteUrl, 'berechtigte', 'acl') werden nur
    regelbasiert normalisiert (Inline-Jobrollen -> leer -> Ordner-Fallback). Deny gewinnt."""
    if not isinstance(data, dict):
        return set()
    model = data.get("model") or data.get("modell") or ""
    allow, deny = set(), set()

    # POSIX owner/group direkt
    for tok in (data.get("owner"), data.get("group")):
        if tok:
            allow |= normalize_principal("POSIX", str(tok))

    # SharePoint siteUrl -> /sites/XX
    su = data.get("siteUrl") or data.get("sharepoint_site") or ""
    for c in re.findall(r"/sites/([A-Za-z]{2})\b", su):
        if c.upper() in KNOWN_CODES:
            allow.add(c.upper())

    def _add_principal(pr, md, eff="Allow"):
        cs = normalize_principal(md, str(pr))
        if str(pr).upper() in KNOWN_CODES:
            cs.add(str(pr).upper())
        (deny if str(eff).lower().startswith("deny") else allow).update(cs)

    # Explizite Gruppenlisten: ROH-Durchreichung erlaubt (einfaches Schema).
    for key in ("groups", "principals"):
        v = data.get(key)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    cs = normalize_principal(model, item)
                    if item.upper() in KNOWN_CODES:
                        cs.add(item.upper())
                    allow |= (cs if cs else {item})   # nicht normalisierbar -> roh
                elif isinstance(item, dict):
                    _add_principal(item.get("prinzipal") or item.get("principal")
                                   or item.get("group") or "",
                                   item.get("modell") or item.get("model") or model,
                                   item.get("effekt") or item.get("effect") or "Allow")

    # Modellspezifische Felder: NUR normalisieren (keine Roh-Durchreichung).
    for key in ("berechtigte", "acl", "entries", "zuordnung", "roles", "roleAssignments"):
        v = data.get(key)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    _add_principal(item, model)
                elif isinstance(item, dict):
                    _add_principal(item.get("prinzipal") or item.get("principal")
                                   or item.get("group") or item.get("ad_gruppe") or "",
                                   item.get("modell") or item.get("model") or model,
                                   item.get("effekt") or item.get("effect") or "Allow")
    return allow - deny


# ──────────────────────────────────────────────
# _MANIFEST.csv -- Ground-Truth Klassifizierung + Lebenszyklus (deterministisch)
# ──────────────────────────────────────────────
# Lebenszyklus-Marker fuer veraltete/widerrufene Beschluesse -> NICHT indexieren.
OBSOLETE_CLASS = {"HINFAELLIG", "HINFÄLLIG", "WIDERRUFEN", "VERALTET", "ERSETZT", "ANGEPASST"}
# Klassifizierung -> Default-Lesegruppe (wenn keine echte ACL vorliegt).
CLASS_GROUP = {"OEFFENTLICH": "PUBLIC", "ÖFFENTLICH": "PUBLIC", "INTERN": ALL_INTERNAL}

_MANIFEST_CACHE = {}


def find_manifest_root(start_path: str) -> str:
    """Sucht aufwaerts nach _MANIFEST.csv und gibt dessen Ordner (Korpus-Wurzel) zurueck."""
    d = os.path.abspath(start_path)
    if os.path.isfile(d):
        d = os.path.dirname(d)
    for _ in range(8):
        if os.path.exists(os.path.join(d, "_MANIFEST.csv")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return ""


def load_manifest(root: str) -> dict:
    """Laedt _MANIFEST.csv -> {relpath_normalisiert: klassifizierung_upper}. Gecacht."""
    if not root:
        return {}
    mpath = os.path.join(root, "_MANIFEST.csv")
    if mpath in _MANIFEST_CACHE:
        return _MANIFEST_CACHE[mpath]
    out = {}
    try:
        import csv
        with open(mpath, "r", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f, delimiter=";"):
                rel = (row.get("pfad") or "").replace("\\", "/").strip().lower()
                kl = (row.get("klassifizierung") or "").strip().upper()
                if rel:
                    out[rel] = kl
    except Exception as e:
        logger.error("MANIFEST: '%s' nicht lesbar (%s).", mpath, e)
    _MANIFEST_CACHE[mpath] = out
    return out


def classification_for(path: str) -> str:
    """Klassifizierung einer Datei laut _MANIFEST (oder '')."""
    root = find_manifest_root(path)
    if not root:
        return ""
    man = load_manifest(root)
    rel = os.path.relpath(os.path.abspath(path), root).replace("\\", "/").lower()
    return man.get(rel, "")


def is_obsolete(path: str) -> bool:
    """True, wenn die Datei veraltet/widerrufen ist -- erkannt am Dateinamen
    (_HINFAELLIG_/_WIDERRUFEN_/_VERALTET_/_ERSETZT_/_ANGEPASST_) ODER laut _MANIFEST."""
    name = os.path.basename(path).upper()
    if any(tok in name for tok in OBSOLETE_CLASS):
        return True
    return classification_for(path).upper() in OBSOLETE_CLASS


def classification_default_groups(path: str) -> set:
    """Default-Lesegruppen rein aus der Klassifizierung (OEFFENTLICH->PUBLIC, INTERN->alle
    intern). Sonst leer (fail-closed)."""
    kl = classification_for(path).upper()
    g = CLASS_GROUP.get(kl)
    return {g} if g else set()
