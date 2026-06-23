"""
onboarding/governance_import.py -- Rollen/User aus einer ABTEILUNGS-Ordnerstruktur ableiten.

Fuer Korpora wie 'enterprise_data', wo die DBs nach Abteilung in Ordnern liegen
(01_datenbanken/<Abteilung>/...) und es KEINE saubere User-Mitgliedschaftstabelle gibt.
Erzeugt: Rolle pro Abteilung (Gruppe=Abteilungscode), jede DB-Tabelle wird ueber ihren
Ordner der Abteilungsrolle zugeordnet, GF (Geschaeftsfuehrung) erbt alles, und pro
Abteilung ein Demo-Login zum Testen. Fail-closed: Tabellen ohne erkennbare Abteilung -> nur GF.
"""
from __future__ import annotations

import json
import os
import re

# Ordnername (normalisiert) -> Abteilungscode.
FOLDER_CODE = {
    "einkauf": "EK", "finanzen": "FI", "personal": "HR", "produktion": "PP",
    "it": "IT", "marketing": "MK", "f_und_e": "FE", "fue": "FE", "recht": "RE",
    "vertrieb": "VK", "geschaeftsfuehrung": "GF", "geschaftsfuhrung": "GF",
}
CODE_NAME = {"EK": "Einkauf", "FI": "Finanzen", "HR": "Personal", "PP": "Produktion",
             "IT": "IT", "MK": "Marketing", "FE": "F&E", "RE": "Recht",
             "VK": "Vertrieb", "GF": "Geschaeftsfuehrung"}


def _area_from_conn(conn: str) -> str:
    """Abteilungscode aus dem DB-Pfad (Ordner nach 01_datenbanken)."""
    p = conn.replace("\\", "/")
    m = re.search(r"01_datenbanken/([^/]+)/", p)
    if not m:
        return ""
    folder = re.sub(r"[^a-z0-9]+", "_", m.group(1).lower()).strip("_")
    return FOLDER_CODE.get(folder, "")


def build_dicts(sql_sources_path: str, db_path_resolver=None):
    """Baut policy/users-Dicts aus den normalisierten ACLs (Abteilungs-Struktur), OHNE zu
    schreiben. Returns (policy_doc, users_doc, info). Genutzt von Wizard/Bootstrap."""
    from onboarding.acl_normalize import resolve_groups, ALL_INTERNAL
    sources = json.load(open(sql_sources_path, encoding="utf-8")).get("sources", {})

    roles = {"base": {"inherits": [], "groups": ["PUBLIC", ALL_INTERNAL], "dms_tags": ["PUBLIC"],
                      "sql_tables": set(), "salesforce": {"mode": "none"}}}
    used = set()
    unassigned = set()
    quelle_stat = {}

    for src, s2 in sources.items():
        quals = [f"{src}.{t}" for t in s2.get("tables", {})]
        codes, quelle = resolve_groups(s2.get("connection_string", ""), db_path_resolver)
        quelle_stat[quelle] = quelle_stat.get(quelle, 0) + 1
        if not codes:
            unassigned.update(quals); continue
        for code in codes:
            if code == ALL_INTERNAL:
                roles["base"]["sql_tables"].update(quals)
                continue
            rn = code.lower(); used.add(code)
            roles.setdefault(rn, {"inherits": ["base"], "groups": [code], "dms_tags": [code],
                                  "sql_tables": set(), "salesforce": {"mode": "none"}})
            roles[rn]["sql_tables"].update(quals)

    dept_roles = sorted(r for r in roles if r not in ("base", "gf"))
    gf = roles.setdefault("gf", {"inherits": [], "groups": ["GF"], "dms_tags": ["GF"],
                                 "sql_tables": set(), "salesforce": {"mode": "none"}})
    gf["inherits"] = sorted(set(gf["inherits"]) | set(dept_roles) | {"base"})
    gf["sql_tables"].update(unassigned)
    for rd in roles.values():
        rd["sql_tables"] = sorted(rd["sql_tables"])

    users = {}
    for code in sorted(used | {"GF"}):
        uid = code.lower()
        users[uid] = {"display": f"{CODE_NAME.get(code, code)} (Demo-Login)", "roles": [uid]}

    policy_doc = {"_comment": "Aus Abteilungs-Struktur/normalisierten ACLs abgeleitet (governance).",
                  "roles": roles}
    users_doc = {"_comment": "Demo-Logins pro Abteilung.", "users": users}
    info = {"roles": sorted(roles), "users": sorted(users),
            "tables_total": sum(len(s2.get("tables", {})) for s2 in sources.values()),
            "quellen": quelle_stat}
    return policy_doc, users_doc, info


def build(sql_sources_path: str, out_data_dir: str, password: str = "Demo1234!",
          db_path_resolver=None) -> dict:
    """Wie build_dicts, aber schreibt policy.json/users.json/auth_users.json."""
    from auth import hash_password
    policy_doc, users_doc, info = build_dicts(sql_sources_path, db_path_resolver)
    os.makedirs(out_data_dir, exist_ok=True)

    def _save(name, obj):
        path = os.path.join(out_data_dir, name)
        if os.path.exists(path):
            try:
                os.replace(path, path + ".bak")
            except Exception:
                pass
        json.dump(obj, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    _save("policy.json", policy_doc)
    _save("users.json", users_doc)
    _save("auth_users.json", {"users": {u: {"display": v["display"],
                                            "password": hash_password(password)}
                                        for u, v in users_doc["users"].items()}})
    info["password"] = password
    return info


def _legacy_unused():
    pass


if __name__ == "__main__":
    import sys
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    res = build(os.path.join(here, "data", "sql_sources.json"),
                os.path.join(here, "data"),
                sys.argv[1] if len(sys.argv) > 1 else "Demo1234!")
    print(json.dumps(res, ensure_ascii=False, indent=2))
