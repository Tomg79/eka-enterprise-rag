"""
onboarding/landscape.py -- Der zentrale Vertrag ("landscape.json").

Eine Landscape beschreibt ALLE Datenquellen eines Kunden-Deployments an EINER Stelle:
Dateifreigaben, SQL-Datenbanken, Object-Stores (S3/MinIO), APIs/SaaS und die Identitaet
(Rollen/User). Drei Werkzeuge teilen sich diesen Vertrag:

  * der Generator (tools/generate_enterprise_landscape.py) SCHREIBT eine Landscape,
  * die Discovery (onboarding/discovery.py) REKONSTRUIERT eine Landscape aus einem
    unbekannten Ordner ("USB-Stick"),
  * der Bootstrap (onboarding/bootstrap.py) UEBERSETZT sie in die App-Konfiguration.

Sicherheitsprinzip: Das Manifest enthaelt NIEMALS Klartext-Secrets. Es referenziert nur
Env-Variablennamen (z.B. credentials_env: "MINIO_KEY"). Geladen wird fail-closed: kaputtes
Manifest -> leere Landscape (keine Quellen).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass
class FileShare:
    name: str
    path: str                      # absoluter/relativer Pfad zur Freigabe-Wurzel
    acl_reader: str = "composite"  # composite|sidecar|prefix|windows
    note: str = ""


@dataclass
class SqlTable:
    table: str
    lookup_column: str = ""
    select_columns: list = field(default_factory=list)
    sensitive: bool = False        # markiert vertrauliche Tabellen (z.B. Gehaelter)


@dataclass
class Database:
    name: str
    connection_string: str         # z.B. sqlite:///.../hr.db oder postgresql://...
    tables: list = field(default_factory=list)   # list[SqlTable]
    note: str = ""


@dataclass
class ObjectStore:
    name: str
    endpoint_url: str = ""          # http://minio:9000 ; leer = lokaler Seed-Ordner
    bucket: str = ""
    prefix: str = ""
    local_seed_path: str = ""       # Ordner, der den Bucket-Inhalt enthaelt (Demo/Seed)
    acl_reader: str = "composite"
    credentials_env: str = ""       # NAME der Env-Var-Paare, nie das Secret selbst
    note: str = ""


@dataclass
class ApiSource:
    name: str
    kind: str = "salesforce_json"   # Typ-Hinweis fuer den passenden Connector
    path: str = ""                  # Ordner mit JSON-Records
    note: str = ""


@dataclass
class Identity:
    policy_path: str = ""           # policy.json (Rollen/Rechte)
    users_path: str = ""            # users.json (User->Rollen)


@dataclass
class Landscape:
    company: str = "Unbenanntes Unternehmen"
    schema_version: int = SCHEMA_VERSION
    file_shares: list = field(default_factory=list)
    databases: list = field(default_factory=list)
    object_stores: list = field(default_factory=list)
    apis: list = field(default_factory=list)
    identity: Identity = field(default_factory=Identity)
    notes: list = field(default_factory=list)

    # ---- Serialisierung ----
    def to_dict(self) -> dict:
        return {
            "company": self.company,
            "schema_version": self.schema_version,
            "file_shares": [asdict(x) for x in self.file_shares],
            "databases": [
                {**{k: v for k, v in asdict(d).items() if k != "tables"},
                 "tables": [asdict(t) for t in d.tables]}
                for d in self.databases
            ],
            "object_stores": [asdict(x) for x in self.object_stores],
            "apis": [asdict(x) for x in self.apis],
            "identity": asdict(self.identity),
            "notes": list(self.notes),
        }

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "Landscape":
        dbs = []
        for d in data.get("databases", []) or []:
            tables = [SqlTable(**t) for t in (d.get("tables") or [])]
            d2 = {k: v for k, v in d.items() if k != "tables"}
            dbs.append(Database(tables=tables, **d2))
        return cls(
            company=data.get("company", "Unbenanntes Unternehmen"),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            file_shares=[FileShare(**x) for x in (data.get("file_shares") or [])],
            databases=dbs,
            object_stores=[ObjectStore(**x) for x in (data.get("object_stores") or [])],
            apis=[ApiSource(**x) for x in (data.get("apis") or [])],
            identity=Identity(**(data.get("identity") or {})),
            notes=list(data.get("notes") or []),
        )

    @classmethod
    def load(cls, path: str) -> "Landscape":
        """Fail-closed: bei jedem Fehler -> leere Landscape (keine Quellen)."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Top-Level ist kein Objekt")
            return cls.from_dict(data)
        except Exception as e:
            logger.error("LANDSCAPE: '%s' nicht ladbar (%s) -> leere Landscape.", path, e)
            return cls(company="(unlesbar)")
