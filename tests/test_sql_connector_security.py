"""Security-Regression: GenericSQLConnector (Whitelist, Parametrisierung, fail-closed).

Quelle der Whitelist ist config.SQL_SOURCES_FILE; fuer abweichende Testumgebungen kann
EKA_TEST_SQL_SOURCES auf eine alternative Sources-Datei zeigen (z.B. valide DB-Kopien).
"""
import os
import pytest

from config import SQL_SOURCES_FILE, BASE_DIR
from connectors.sql import GenericSQLConnector, load_sql_sources

SOURCES_FILE = os.environ.get("EKA_TEST_SQL_SOURCES", SQL_SOURCES_FILE)


@pytest.fixture
def conn():
    src = load_sql_sources(SOURCES_FILE)
    if not src:
        pytest.skip("keine SQL-Quellen ladbar")
    return GenericSQLConnector(src, base_dir=BASE_DIR)


def test_only_whitelisted_tables(conn):
    assert set(conn.qualified_tables()) <= {
        "sap.T_EMP_DATA", "sap.T_MAT_MASTER", "erp.T_SALES_ORDERS"
    }
    # Nicht gelistete Tabelle ist nicht erreichbar (fail-closed).
    status, _ = conn.lookup("sap.T_SYS_ACL", "x")
    assert status == "no_table"


def test_value_pattern_blocks_injection(conn):
    # Klassischer Injection-String erfuellt das Lookup-Pattern NICHT -> nie an die DB.
    status, msg = conn.lookup("sap.T_EMP_DATA", "E-001' OR '1'='1")
    assert status == "bad_value"


def test_parametrized_filter_no_bypass(conn):
    if "erp.T_SALES_ORDERS" not in conn.qualified_tables():
        pytest.skip("erp-Quelle nicht verfuegbar")
    # Auch in der filterbaren Spalte wird der Wert parametrisiert -> 0 Treffer, kein Bypass.
    status, rows = conn.filter_rows("erp.T_SALES_ORDERS", "STATUS", "OPEN' OR '1'='1", limit=5)
    assert status == "ok" and rows == []


def test_filter_column_whitelist(conn):
    if "erp.T_SALES_ORDERS" not in conn.qualified_tables():
        pytest.skip("erp-Quelle nicht verfuegbar")
    # Nicht-filterbare Spalte -> abgelehnt.
    status, _ = conn.filter_rows("erp.T_SALES_ORDERS", "NET_EUR", "100", limit=5)
    assert status == "bad_column"


def test_filter_row_cap(conn):
    if "erp.T_SALES_ORDERS" not in conn.qualified_tables():
        pytest.skip("erp-Quelle nicht verfuegbar")
    status, rows = conn.filter_rows("erp.T_SALES_ORDERS", "STATUS", "OPEN", limit=10_000)
    assert status == "ok" and len(rows) <= 50


def test_known_value_lookup(conn):
    status, row = conn.lookup("sap.T_EMP_DATA", "E-001")
    assert status == "ok"
    out = conn.format_row("sap.T_EMP_DATA", row)
    assert out.startswith("EMP_ID=E-001") and "NAME=" in out


def test_relative_sqlite_paths_resolve_under_project_root():
    """Regression: query.py muss die Projektwurzel als base_dir uebergeben, sonst werden
    relative 'sqlite:///data/...'-Pfade doppelt praefixiert ('data/data/...') und die
    DB-Abfrage schlaegt fehl (Live-Bug 2026-06-22)."""
    from config import SQL_SOURCES_FILE, BASE_DIR
    real = load_sql_sources(SQL_SOURCES_FILE)
    if not real:
        pytest.skip("keine echten SQL-Quellen")
    c = GenericSQLConnector(real, base_dir=BASE_DIR)
    for _name, s in real.items():
        cs = s.get("connection_string", "")
        if cs.startswith("sqlite:///"):
            path = c._resolve_sqlite_path(cs)[len("sqlite:///"):].replace("\\", "/")
            assert "data/data" not in path, f"Doppelter Pfad-Prefix: {path}"
            assert os.path.isabs(path), f"Pfad nicht absolut aufgeloest: {path}"
