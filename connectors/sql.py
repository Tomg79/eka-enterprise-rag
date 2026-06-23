"""
connectors/sql.py -- Phase 4: Generischer SQL-Connector.

Ziel (CLAUDE_CODE_BRIEF.md, Roadmap Phase 4): "Zweiter Connector: generisches SQL
(beliebiger Connection-String + Tabellen-Whitelist)".

WICHTIGE SICHERHEITSENTSCHEIDUNG
--------------------------------
Es wird BEWUSST KEIN frei vom LLM generiertes SQL (Text-to-SQL/NLSQL) ausgefuehrt --
das wurde im SAP-Schritt absichtlich entfernt, weil es nicht deterministisch und nicht
fail-closed abzusichern ist. Stattdessen ist der Connector rein DEKLARATIV:

  * Welche Quellen (Connection-Strings), Tabellen, Spalten, Lookup-/Filter-Spalten
    erreichbar sind, steht ausschliesslich in der Config (data/sql_sources.json).
  * Tabellen-/Spaltennamen (SQL-Identifier) stammen IMMER aus dieser Whitelist,
    niemals aus Nutzer-/LLM-Eingaben. Zusaetzlich werden sie als Defense-in-Depth
    gegen ein striktes Identifier-Muster geprueft.
  * Werte gehen AUSSCHLIESSLICH parametrisiert in die Query (SQLAlchemy bind params)
    -> keine String-Konkatenation -> keine SQL-Injection.
  * Alles nicht ausdruecklich Erlaubte ist verboten (deny-by-default, fail-closed).

Die eigentliche Rollen-/Rechtepruefung liegt weiterhin allein in der PolicyEngine;
dieser Connector fuehrt nur aus, was Whitelist UND Policy gemeinsam erlauben.
"""

from __future__ import annotations

import os
import re
import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

# Strenges Identifier-Muster fuer Tabellen-/Spaltennamen (Defense-in-Depth).
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Obergrenze fuer Zeilen im (optionalen) Filter-Modus -- Kontext-/DoS-Schutz.
_HARD_ROW_CAP = 50


class SqlConfigError(Exception):
    """Fehlerhafte/auffaellige SQL-Connector-Konfiguration."""


def load_sql_sources(path: str) -> dict:
    """Laedt data/sql_sources.json fail-closed: bei jedem Fehler -> {} (keine Quellen,
    also kein SQL-Zugriff), und laut loggen."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Top-Level ist kein Objekt")
        return data.get("sources", {}) or {}
    except Exception as e:
        logger.error("SQL-CONFIG: '%s' nicht ladbar (%s) -> fail-closed (keine Quellen).",
                     path, e)
        return {}


@dataclass(frozen=True)
class TableSpec:
    """Aufgeloeste Whitelist-Beschreibung einer Tabelle."""
    source: str
    table: str
    lookup_column: str
    lookup_pattern: str                       # Regex, das ein gueltiger Lookup-Wert erfuellen muss
    select_columns: tuple                      # auszugebende Spalten (Whitelist)
    labels: dict = field(default_factory=dict) # Spalte -> Anzeigename (optional)
    filterable_columns: tuple = ()             # Spalten, nach denen gefiltert werden darf
    rbac_id: str = ""                          # qualifizierter Name fuer die PolicyEngine
    tool_name: str = ""                        # Name des zu registrierenden Agenten-Tools
    tool_description: str = ""                 # Beschreibung fuers LLM
    normalize: str = ""                        # ""|"upper"|"lower" -- auf Lookup-/Filterwert

    @property
    def qualified(self) -> str:
        return f"{self.source}.{self.table}"

    def norm(self, value: str) -> str:
        v = (value or "").strip()
        if self.normalize == "upper":
            return v.upper()
        if self.normalize == "lower":
            return v.lower()
        return v


def _check_ident(kind: str, value: str) -> str:
    if not isinstance(value, str) or not _IDENT_RE.match(value):
        raise SqlConfigError(f"Ungueltiger {kind}-Identifier in SQL-Config: {value!r}")
    return value


class GenericSQLConnector:
    """Deklarativer, fail-closed SQL-Connector ueber beliebig viele Quellen.

    Engines werden pro Quelle lazy erzeugt und gecacht. Relative sqlite-Pfade im
    Connection-String werden gegen base_dir aufgeloest, damit die Config portabel bleibt.
    """

    def __init__(self, sources: dict, base_dir: str = "."):
        self.base_dir = base_dir
        self._engines = {}
        self._specs = {}      # qualified -> TableSpec
        self._raw = sources or {}
        self._build_specs()

    # ---- Config -> TableSpecs (mit Validierung) ------------------------------
    def _build_specs(self):
        for source, sconf in (self._raw or {}).items():
            if not isinstance(sconf, dict):
                logger.error("SQL-CONFIG: Quelle '%s' ist kein Objekt -> uebersprungen.", source)
                continue
            # Hinweis: Der Quell-NAME ist nur ein Label/Dict-Key (steckt im qualifizierten
            # Namen 'source.table'), geht aber NIE in SQL -> darf Leerzeichen/Klammern
            # enthalten (z.B. 'lieferanten_2024_0265 (1)'). Nur Tabellen-/Spaltennamen
            # werden als Identifier validiert (in _parse_table), denn die landen im SQL.
            if not isinstance(source, str) or not source:
                logger.error("SQL-CONFIG: ungueltiger Quell-Schluessel %r -> uebersprungen.", source)
                continue
            tables = sconf.get("tables", {}) or {}
            for table, tconf in tables.items():
                try:
                    spec = self._parse_table(source, table, tconf)
                except SqlConfigError as e:
                    # Eine kaputte Tabelle darf NICHT die ganze Config kippen,
                    # aber sie wird auch NICHT erreichbar (fail-closed) + laut geloggt.
                    logger.error("SQL-CONFIG: Tabelle '%s.%s' verworfen: %s", source, table, e)
                    continue
                self._specs[spec.qualified] = spec

    def _parse_table(self, source: str, table: str, tconf: dict) -> TableSpec:
        if not isinstance(tconf, dict):
            raise SqlConfigError("Tabellen-Eintrag ist kein Objekt")
        _check_ident("table", table)
        lookup_column = _check_ident("column", tconf.get("lookup_column", ""))
        select_columns = tuple(_check_ident("column", c) for c in (tconf.get("select_columns") or []))
        if not select_columns:
            raise SqlConfigError("select_columns leer")
        if lookup_column not in select_columns:
            # Lookup-Spalte muss Teil der Whitelist sein (sonst koennte man ueber WHERE
            # auf eine nicht freigegebene Spalte schliessen).
            raise SqlConfigError("lookup_column nicht in select_columns")
        filterable = tuple(_check_ident("column", c) for c in (tconf.get("filterable_columns") or []))
        for c in filterable:
            if c not in select_columns:
                raise SqlConfigError(f"filterable_column {c!r} nicht in select_columns")
        pattern = tconf.get("lookup_pattern", r"^.{1,64}$")
        try:
            re.compile(pattern)
        except re.error as e:
            raise SqlConfigError(f"ungueltiges lookup_pattern: {e}")
        labels = tconf.get("labels", {}) or {}
        rbac_id = tconf.get("rbac_id") or f"{source}.{table}"
        return TableSpec(
            source=source,
            table=table,
            lookup_column=lookup_column,
            lookup_pattern=pattern,
            select_columns=select_columns,
            labels=dict(labels),
            filterable_columns=filterable,
            rbac_id=rbac_id,
            tool_name=tconf.get("tool_name") or f"{source}_{table}_lookup".lower(),
            tool_description=tconf.get("tool_description", ""),
            normalize=(tconf.get("normalize") or ""),
        )

    # ---- Engines (lazy, gecacht) --------------------------------------------
    def _engine(self, source: str):
        if source in self._engines:
            return self._engines[source]
        sconf = self._raw.get(source) or {}
        conn = sconf.get("connection_string")
        if not conn or not isinstance(conn, str):
            raise SqlConfigError(f"Quelle '{source}' ohne connection_string")
        conn = self._resolve_sqlite_path(conn)
        engine = create_engine(conn)
        self._engines[source] = engine
        return engine

    def _resolve_sqlite_path(self, conn: str) -> str:
        """Macht relative sqlite-Pfade absolut (gegen base_dir). Andere DBMS
        (postgresql://, mysql://, ...) bleiben unveraendert."""
        prefix = "sqlite:///"
        if conn.startswith(prefix):
            raw = conn[len(prefix):]
            if raw and not os.path.isabs(raw):
                raw = os.path.join(self.base_dir, raw)
            return prefix + raw
        return conn

    # ---- Oeffentliche API ----------------------------------------------------
    def specs(self) -> list:
        return list(self._specs.values())

    def spec(self, qualified: str):
        return self._specs.get(qualified)

    def qualified_tables(self) -> list:
        return sorted(self._specs.keys())

    def lookup(self, qualified: str, value: str):
        """Parametrisierter Einzelsatz-Lookup ueber die Lookup-Spalte.

        Returns: (status, payload)
          ("ok", dict)        -> Treffer (Spalte->Wert, nur Whitelist-Spalten)
          ("not_found", None) -> Wert valide, aber kein Datensatz
          ("bad_value", msg)  -> Wert verletzt lookup_pattern (kommt gar nicht in die DB)
          ("no_table", None)  -> Tabelle nicht in der Whitelist (fail-closed)
        """
        spec = self._specs.get(qualified)
        if spec is None:
            return ("no_table", None)
        v = spec.norm(value)
        if not re.match(spec.lookup_pattern, v):
            return ("bad_value", f"Wert {value!r} entspricht nicht dem erwarteten Format.")
        # Identifier kommen aus der validierten Whitelist (TableSpec), nicht vom Aufrufer.
        cols = ", ".join(spec.select_columns)
        sql = f"SELECT {cols} FROM {spec.table} WHERE {spec.lookup_column} = :v"
        engine = self._engine(spec.source)
        with engine.connect() as conn:
            row = conn.execute(text(sql), {"v": v}).fetchone()
        if not row:
            return ("not_found", None)
        return ("ok", {c: row[i] for i, c in enumerate(spec.select_columns)})

    def filter_rows(self, qualified: str, column: str, value: str, limit: int = 10):
        """Optionaler, gedeckelter Gleichheits-Filter auf einer freigegebenen Spalte.

        column MUSS in filterable_columns stehen, sonst ("bad_column", msg).
        Werte parametrisiert; LIMIT hart gedeckelt.
        Returns: (status, payload) analog zu lookup(); ("ok", [dict, ...]).
        """
        spec = self._specs.get(qualified)
        if spec is None:
            return ("no_table", None)
        if column not in spec.filterable_columns:
            return ("bad_column", f"Spalte {column!r} ist nicht filterbar.")
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = 10
        n = max(1, min(n, _HARD_ROW_CAP))
        cols = ", ".join(spec.select_columns)
        sql = f"SELECT {cols} FROM {spec.table} WHERE {column} = :v LIMIT {n}"
        engine = self._engine(spec.source)
        with engine.connect() as conn:
            rows = conn.execute(text(sql), {"v": spec.norm(value)}).fetchall()
        out = [{c: r[i] for i, c in enumerate(spec.select_columns)} for r in rows]
        return ("ok", out)

    def sample_rows(self, qualified: str, limit: int = 10):
        """Erste N Zeilen einer Tabelle (ohne Schluessel) -- fuer 'was steht in X?'.
        Returns (status, list[dict]). Row-Cap hart gedeckelt."""
        spec = self._specs.get(qualified)
        if spec is None:
            return ("no_table", None)
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = 10
        n = max(1, min(n, _HARD_ROW_CAP))
        cols = ", ".join(spec.select_columns)
        sql = f"SELECT {cols} FROM {spec.table} LIMIT {n}"
        with self._engine(spec.source).connect() as conn:
            rows = conn.execute(text(sql)).fetchall()
        return ("ok", [{c: r[i] for i, c in enumerate(spec.select_columns)} for r in rows])

    def format_row(self, qualified: str, row: dict) -> str:
        """Einzeiler aus einem Ergebnis-Dict, mit konfigurierten Anzeige-Labels."""
        spec = self._specs.get(qualified)
        if spec is None:
            return ""
        parts = []
        for c in spec.select_columns:
            label = spec.labels.get(c, c)
            val = row.get(c)
            if isinstance(val, float) and val.is_integer():
                val = int(val)
            parts.append(f"{label}={val}")
        return ", ".join(parts)
