"""
policy.py -- Zentraler Permission-Layer (RBAC v2) fuer den Enterprise-RAG.

EINZIGER Durchsetzungspunkt fuer Berechtigungen. Jedes Tool fragt ausschliesslich
diese Engine; kein Tool liest die Rechtekonfiguration selbst. Damit gibt es genau
eine Stelle, die ueber Zugriff entscheidet -- leichter zu pruefen und fail-closed
zu halten.

Grundprinzipien (siehe CLAUDE_CODE_BRIEF.md, "nicht verhandelbar"):
  * deny-by-default: Was nicht ausdruecklich erlaubt ist, ist verboten.
  * fail-closed: Bei Lade-/Konfigurationsfehlern oder unbekanntem User -> KEIN Zugriff.
  * Rechteentzug wirkt sofort: Die Policy wird bei jeder Anfrage frisch ausgewertet
    (kein Caching ueber Anfragen hinweg), d.h. Aenderungen an policy.json/users.json
    greifen ab der naechsten Frage -- ohne Reindex.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Principal:
    """Ein anfragender Akteur: User-ID + die (transitiv aufgeloesten) Rollen."""
    user_id: str
    display: str
    roles: frozenset = field(default_factory=frozenset)  # inkl. geerbter Rollen


@dataclass(frozen=True)
class Permissions:
    """Aufgeloeste, effektive Rechte eines Principals (Union ueber alle Rollen)."""
    dms_tags: frozenset = field(default_factory=frozenset)
    groups: frozenset = field(default_factory=frozenset)
    sap_tables: frozenset = field(default_factory=frozenset)      # LEGACY (unqualifiziert)
    sql_tables: frozenset = field(default_factory=frozenset)      # Phase 4: "source.table"
    salesforce_mode: str = "none"            # "none" | "all" | "list"
    salesforce_ids: frozenset = field(default_factory=frozenset)


# Tag-Wert, der garantiert auf KEIN Dokument matcht (fuer den Qdrant-Filter).
DENY_ALL_TAG = "__RBAC_DENY_ALL__"


class PolicyEngine:
    """Laedt Rollen-/User-Definitionen und beantwortet Zugriffsfragen.

    Bewusst ohne anfrageuebergreifenden Cache: jede Methode liest die Dateien
    frisch, damit Rechteentzug sofort wirkt. Bei Lese-/Parsefehlern liefert die
    Engine LEERE Rechte (deny-all) und loggt laut.
    """

    def __init__(self, policy_path: str, users_path: str):
        self.policy_path = policy_path
        self.users_path = users_path

    # ---- Laden (fail-closed) -------------------------------------------------
    def _load(self, path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Top-Level ist kein Objekt")
            return data
        except Exception as e:
            logger.error("POLICY: '%s' nicht ladbar (%s) -> fail-closed (deny all).", path, e)
            return {}

    def _roles_def(self) -> dict:
        return self._load(self.policy_path).get("roles", {}) or {}

    def _users_def(self) -> dict:
        return self._load(self.users_path).get("users", {}) or {}

    # ---- User / Principal ----------------------------------------------------
    def list_users(self) -> list:
        """[(user_id, display), ...] fuer die UI. Leer bei Ladefehler."""
        users = self._users_def()
        out = []
        for uid, u in users.items():
            disp = (u or {}).get("display", uid) if isinstance(u, dict) else uid
            out.append((uid, disp))
        return sorted(out, key=lambda t: t[1].lower())

    def _expand_roles(self, role_names, roles_def) -> set:
        """Transitive Huelle der Rollen-Vererbung. Zyklensicher. Unbekannte
        Rollen werden ignoriert (tragen keine Rechte bei -> fail-closed)."""
        resolved: set = set()
        stack = list(role_names or [])
        while stack:
            r = stack.pop()
            if r in resolved:
                continue
            if r not in roles_def:
                logger.warning("POLICY: unbekannte Rolle '%s' referenziert -> ignoriert.", r)
                continue
            resolved.add(r)
            for parent in (roles_def[r].get("inherits") or []):
                if parent not in resolved:
                    stack.append(parent)
        return resolved

    def get_principal(self, user_id: str) -> Principal:
        """Loest einen User zu einem Principal auf. Unbekannter User -> keine Rollen
        (deny-all)."""
        users = self._users_def()
        roles_def = self._roles_def()
        u = users.get(user_id)
        if not isinstance(u, dict):
            logger.warning("POLICY: unbekannter User '%s' -> keine Rollen (deny all).", user_id)
            return Principal(user_id=user_id, display=user_id, roles=frozenset())
        resolved = self._expand_roles(u.get("roles") or [], roles_def)
        return Principal(
            user_id=user_id,
            display=u.get("display", user_id),
            roles=frozenset(resolved),
        )

    # ---- Effektive Rechte (Union ueber alle Rollen) --------------------------
    def permissions_for(self, principal: Principal) -> Permissions:
        roles_def = self._roles_def()
        dms: set = set()
        grp: set = set()
        sap: set = set()
        sql: set = set()
        sf_mode = "none"
        sf_ids: set = set()
        for r in principal.roles:
            rd = roles_def.get(r)
            if not isinstance(rd, dict):
                continue
            dms.update(rd.get("dms_tags") or [])
            grp.update(rd.get("groups") or [])
            role_sap = rd.get("sap_tables") or []
            sap.update(role_sap)
            # Phase 4: qualifizierte SQL-Tabellen ("source.table").
            sql.update(rd.get("sql_tables") or [])
            # Abwaertskompatibilitaet: unqualifizierte sap_tables zaehlen als sap.<table>.
            sql.update(f"sap.{t}" for t in role_sap)
            sf = rd.get("salesforce") or {}
            mode = sf.get("mode", "none")
            if mode == "all":
                sf_mode = "all"
            elif mode == "list" and sf_mode != "all":
                sf_mode = "list"
                sf_ids.update(sf.get("ids") or [])
        return Permissions(
            dms_tags=frozenset(dms),
            groups=frozenset(grp),
            sap_tables=frozenset(sap),
            sql_tables=frozenset(sql),
            salesforce_mode=sf_mode,
            salesforce_ids=frozenset(sf_ids),
        )

    # ---- Bequeme Zugriffsfragen (alle deny-by-default) -----------------------
    def allowed_dms_tags(self, principal: Principal) -> list:
        """DMS-Tags, die der Principal sehen darf. Leer -> nichts (Aufrufer setzt
        dann den DENY_ALL_TAG-Filter)."""
        return sorted(self.permissions_for(principal).dms_tags)

    def allowed_groups(self, principal: Principal) -> list:
        """Gruppen (AD), in denen der Principal Mitglied ist. Werden gegen die
        acl_groups der Dokumente gematcht (Phase 2). Leer -> kein DMS-Zugriff."""
        return sorted(self.permissions_for(principal).groups)

    def all_groups(self) -> list:
        """Alle in der Policy bekannten Gruppen (fuer Freigabe-Vorschlaege im UI)."""
        groups = set()
        for rd in self._roles_def().values():
            if isinstance(rd, dict):
                groups.update(rd.get("groups") or [])
        return sorted(groups)

    def allowed_sap_tables(self, principal: Principal) -> list:
        return sorted(self.permissions_for(principal).sap_tables)

    def can_access_sap_table(self, principal: Principal, table: str) -> bool:
        return table in self.permissions_for(principal).sap_tables

    # Phase 4: generischer SQL-Connector (qualifizierte Tabellennamen "source.table").
    def allowed_sql_tables(self, principal: Principal) -> list:
        return sorted(self.permissions_for(principal).sql_tables)

    def can_access_sql_table(self, principal: Principal, qualified: str) -> bool:
        return qualified in self.permissions_for(principal).sql_tables

    def salesforce_scope(self, principal: Principal):
        """('none'|'all'|'list', set_of_ids)."""
        p = self.permissions_for(principal)
        return p.salesforce_mode, set(p.salesforce_ids)

    def can_access_customer(self, principal: Principal, customer_id: str) -> bool:
        mode, ids = self.salesforce_scope(principal)
        if mode == "all":
            return True
        if mode == "list":
            return customer_id in ids
        return False
