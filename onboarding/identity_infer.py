"""
onboarding/identity_infer.py -- Identitaet (Rollen+User) automatisch aus den Daten ableiten.

Wenn ein Datenordner KEINE identity/ (policy.json/users.json) mitbringt, baut diese Funktion
einen sinnvollen ENTWURF: Benutzer aus einer Mitarbeiter-Tabelle, Rollen aus Abteilungen +
gefundenen Datei-ACL-Gruppen, DB-Rechte per konservativer Heuristik.

WICHTIG (Sicherheit): Das ist ein DRAFT, kein Ersatz fuer das echte Berechtigungssystem
(AD/SSO/HR). Prinzip bleibt fail-closed/deny-by-default: im Zweifel KEIN Zugriff. Sensible
Tabellen (Gehalt) gehen nur an die passende Fachrolle + Exec. Der Aufrufer muss klar
kennzeichnen, dass dies automatisch erzeugt und zu pruefen ist.
"""

from __future__ import annotations

import os
import re
import sqlite3


def _slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "x"


def _sqlite_path(conn: str) -> str:
    return conn[len("sqlite:///"):] if conn.startswith("sqlite:///") else ""


def _collect_acl_groups(paths) -> set:
    import json
    groups = set()
    for root in paths:
        if not root or not os.path.isdir(root):
            continue
        for dp, _dn, fns in os.walk(root):
            for fn in fns:
                if fn.endswith(".acl.json"):
                    try:
                        with open(os.path.join(dp, fn), encoding="utf-8") as f:
                            for g in (json.load(f).get("groups") or []):
                                groups.add(str(g))
                    except Exception:
                        pass
    return groups


def _find_employee_table(landscape):
    """Sucht eine Mitarbeiter-aehnliche Tabelle in einer lesbaren SQLite-DB.
    Returns (db, table_spec) oder (None, None)."""
    for db in landscape.databases:
        path = _sqlite_path(db.connection_string)
        if not path or not os.path.exists(path):
            continue
        for t in db.tables:
            up = [c.upper() for c in t.select_columns]
            has_dept = any(any(k in c for k in ("DEPT", "ABTEIL", "DEPARTMENT")) for c in up)
            has_name = any("NAME" in c for c in up)
            has_idish = any(any(k in c for k in ("EMP", "USER", "STAFF", "MITARB", "MAIL")) for c in up)
            if has_dept and (has_name or has_idish):
                return db, t
    return None, None


# Synonyme Abteilung <-> ACL-Gruppe (heuristisch).
_GROUP_SYNONYMS = {
    "hr": ["HR_CONFIDENTIAL", "HR"],
    "finance": ["FINANCE"],
    "sales": ["SALES"],
    "engineering": ["ENGINEERING", "ENG"],
    "legal": ["LEGAL"],
    "it": ["IT"],
}


def _group_for_dept(dept_slug: str, groups: set) -> str:
    for cand in _GROUP_SYNONYMS.get(dept_slug, []):
        if cand in groups:
            return cand
    for g in groups:
        if _slug(g) == dept_slug or dept_slug in _slug(g):
            return g
    return dept_slug.upper()  # eigene Gruppe, falls keine passt


def _role_for_table(db_name: str, table: str, role_names: set) -> str:
    dom = f"{db_name} {table}".lower()
    if any(k in dom for k in ("salar", "gehalt", "bonus", "payroll", "emp", "staff", "hr")):
        cand = "hr"
    elif any(k in dom for k in ("crm", "customer", "contact", "sales", "order", "material", "inventory", "erp")):
        cand = "sales"
    elif any(k in dom for k in ("warehouse", "fact", "dim", "finance", "revenue", "amount", "ledger")):
        cand = "finance"
    else:
        cand = ""
    return cand if cand in role_names else ""


def infer_identity(landscape) -> tuple:
    """Returns (policy_dict, users_dict, notes:list)."""
    notes = ["IDENTITY AUTOMATISCH ABGELEITET (DRAFT) -- bitte vor Produktivbetrieb pruefen."]

    acl_paths = [fs.path for fs in landscape.file_shares] + \
                [o.local_seed_path for o in landscape.object_stores if o.local_seed_path]
    groups = _collect_acl_groups(acl_paths)
    groups.add("PUBLIC")

    # --- Benutzer + Abteilungen aus der Mitarbeiter-Tabelle ---
    users = {}
    departments = set()
    db, t = _find_employee_table(landscape)
    if t:
        up = [c.upper() for c in t.select_columns]
        def _col(keys):
            for i, c in enumerate(up):
                if any(k in c for k in keys):
                    return t.select_columns[i]
            return None
        dept_c = _col(["DEPT", "ABTEIL", "DEPARTMENT"])
        name_c = _col(["NAME"])
        mail_c = _col(["MAIL"])
        id_c = _col(["EMP", "USER", "STAFF", "MITARB"])
        path = _sqlite_path(db.connection_string)
        try:
            con = sqlite3.connect(path)
            cur = con.execute(f'SELECT * FROM "{t.table}" LIMIT 1000')
            colnames = [d[0] for d in cur.description]
            rows = cur.fetchall()
            con.close()
            idx = {c: colnames.index(c) for c in t.select_columns if c in colnames}
            seen = set()
            for r in rows:
                dept = str(r[idx[dept_c]]) if dept_c in idx else "Allgemein"
                departments.add(dept)
                # user_id: Email-localpart > EMP-ID > Name
                uid = ""
                if mail_c in idx and r[idx[mail_c]]:
                    uid = _slug(str(r[idx[mail_c]]).split("@")[0])
                elif id_c in idx and r[idx[id_c]]:
                    uid = _slug(str(r[idx[id_c]]))
                elif name_c in idx and r[idx[name_c]]:
                    uid = _slug(str(r[idx[name_c]]))
                if not uid:
                    continue
                base = uid; n = 2
                while uid in seen:
                    uid = f"{base}_{n}"; n += 1
                seen.add(uid)
                disp = str(r[idx[name_c]]) if name_c in idx and r[idx[name_c]] else uid
                users[uid] = {"display": f"{disp} ({dept})", "roles": [_slug(dept)]}
            notes.append(f"{len(users)} Benutzer aus Tabelle {db.name}.{t.table} abgeleitet "
                         f"(Abteilungen: {', '.join(sorted(departments)) or '-'}).")
        except Exception as e:
            notes.append(f"Mitarbeiter-Tabelle nicht lesbar ({e}) -> keine Benutzer abgeleitet.")
    else:
        notes.append("Keine Mitarbeiter-Tabelle gefunden -> keine Benutzer abgeleitet "
                     "(ohne Benutzer kann sich niemand anmelden).")

    # --- Rollen ---
    roles = {"base": {"inherits": [], "groups": ["PUBLIC"], "dms_tags": ["PUBLIC"],
                      "sql_tables": [], "salesforce": {"mode": "none"}}}
    dept_roles = []
    for d in sorted(departments):
        rn = _slug(d)
        if rn == "base":
            continue
        grp = _group_for_dept(rn, groups)
        roles[rn] = {"inherits": ["base"], "groups": [grp], "dms_tags": [grp],
                     "sql_tables": [], "salesforce": {"mode": "all" if rn == "sales" else "none"}}
        dept_roles.append(rn)

    # Gruppen ohne passende Abteilungsrolle -> eigene Rolle (z.B. PROJECT_PHOENIX).
    covered = {g for r in roles.values() for g in r["groups"]}
    for g in sorted(groups):
        if g == "PUBLIC" or g in covered:
            continue
        rn = _slug(g)
        if rn not in roles:
            roles[rn] = {"inherits": [], "groups": [g], "dms_tags": [g],
                         "sql_tables": [], "salesforce": {"mode": "none"}}

    # Exec/Admin erbt alle Abteilungsrollen.
    if dept_roles:
        roles["exec"] = {"inherits": dept_roles, "groups": [], "dms_tags": [],
                         "sql_tables": [], "salesforce": {"mode": "all"}}

    # --- DB-Tabellen den Rollen zuordnen (konservativ) ---
    role_names = set(roles.keys())
    unassigned = []
    for db in landscape.databases:
        for t in db.tables:
            qual = f"{db.name}.{t.table}"
            target = _role_for_table(db.name, t.table, role_names)
            if t.sensitive:
                # Sensible Tabellen NUR an Fachrolle (falls vorhanden) -> Exec erbt sie.
                if target:
                    roles[target]["sql_tables"].append(qual)
                else:
                    roles.setdefault("exec", {"inherits": dept_roles, "groups": [],
                                              "dms_tags": [], "sql_tables": [],
                                              "salesforce": {"mode": "all"}})["sql_tables"].append(qual)
                    unassigned.append(qual + " (sensibel -> nur Exec)")
            else:
                if target:
                    roles[target]["sql_tables"].append(qual)
                else:
                    # Keine passende Rolle -> nur Exec (deny-by-default fuer alle anderen).
                    roles.setdefault("exec", {"inherits": dept_roles, "groups": [],
                                              "dms_tags": [], "sql_tables": [],
                                              "salesforce": {"mode": "all"}})["sql_tables"].append(qual)
                    unassigned.append(qual)
    if unassigned:
        notes.append("Ohne klare Fachrolle nur an Exec vergeben: " + ", ".join(unassigned[:8])
                     + (" ..." if len(unassigned) > 8 else ""))

    policy = {"_comment": "AUTOMATISCH ABGELEITET (DRAFT) von identity_infer.py. Pruefen!",
              "roles": roles}
    users_doc = {"_comment": "AUTOMATISCH ABGELEITET (DRAFT). Logins via manage_auth/Wizard.",
                 "users": users}
    return policy, users_doc, notes
