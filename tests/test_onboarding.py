"""End-to-End-Test der Onboarding-Pipeline: Generate -> Discover -> Bootstrap.

Erzeugt eine synthetische Firma in einem Temp-Ordner, laesst die Discovery sie
rekonstruieren, baut die App-Config und prueft sie mit den ECHTEN Connectoren/der
PolicyEngine. Laeuft ohne Ollama/Embeddings.
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from onboarding.discovery import discover
from onboarding.bootstrap import build_sql_sources, apply_config
from connectors.sql import GenericSQLConnector, load_sql_sources
from policy import PolicyEngine


def _load_generator():
    path = os.path.join(ROOT, "tools", "generate_enterprise_landscape.py")
    spec = importlib.util.spec_from_file_location("gen_landscape", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def landscape_root(tmp_path):
    gen = _load_generator()
    root = tmp_path / "ACME"
    # Generator nutzt argparse -> direkt die Bausteine aufrufen.
    os.makedirs(root, exist_ok=True)
    fs, _q = gen.gen_file_shares(str(root))
    dbs = gen.gen_databases(str(root))
    obj = gen.gen_object_store(str(root))
    apis = gen.gen_apis(str(root))
    ident = gen.gen_identity(str(root))
    from onboarding.landscape import Landscape
    Landscape(company=gen.COMPANY, file_shares=fs, databases=dbs, object_stores=obj,
              apis=apis, identity=ident).save(str(root / "landscape.json"))
    # Manifest entfernen -> echte Rekonstruktion testen.
    os.remove(root / "landscape.json")
    return str(root)


def test_discovery_finds_all_sources(landscape_root):
    ls, findings = discover(landscape_root)
    assert {d.name for d in ls.databases} == {"hr", "crm", "erp"}
    assert len(ls.file_shares) >= 1
    assert len(ls.object_stores) >= 1
    assert len(ls.apis) >= 1
    assert ls.identity.policy_path


def test_discovery_flags_security_quirks(landscape_root):
    _ls, findings = discover(landscape_root)
    j = "\n".join(findings)
    assert "MARKETING_TYPO" in j           # verwaiste ACL-Gruppe
    assert "ghost_gwen" in j               # user mit unbekannter Rolle
    assert "T_SALARIES" in j               # vertrauliche Tabelle
    assert "OHNE ACL" in j                 # Ueber-Exposition durch Prefix-Fallback
    assert "T_SALES_ORDERS" not in j or "vertrauliche Tabelle erp.T_SALES_ORDERS" not in j


def test_bootstrap_sql_sources_drive_real_lookups(landscape_root, tmp_path):
    ls, _ = discover(landscape_root)
    cfg = tmp_path / "appcfg"
    (cfg / "data").mkdir(parents=True)
    apply_config(ls, str(cfg), str(cfg / "deploy" / ".env"))
    conn = GenericSQLConnector(load_sql_sources(str(cfg / "data" / "sql_sources.json")), base_dir="/")
    assert "hr.T_EMPLOYEES" in conn.qualified_tables()
    status, row = conn.lookup("hr.T_EMPLOYEES", "E-001")
    assert status == "ok" and row["EMP_ID"] == "E-001"
    # Injection scheitert am abgeleiteten Pattern.
    status, _ = conn.lookup("hr.T_SALARIES", "E-001' OR '1'='1")
    assert status == "bad_value"


def test_bootstrap_identity_enforced(landscape_root, tmp_path):
    ls, _ = discover(landscape_root)
    cfg = tmp_path / "appcfg"
    (cfg / "data").mkdir(parents=True)
    apply_config(ls, str(cfg), str(cfg / "deploy" / ".env"))
    pol = PolicyEngine(str(cfg / "data" / "policy.json"), str(cfg / "data" / "users.json"))
    assert pol.can_access_sql_table(pol.get_principal("hr_head"), "hr.T_SALARIES")
    assert not pol.can_access_sql_table(pol.get_principal("sales_bob"), "hr.T_SALARIES")
    assert pol.can_access_sql_table(pol.get_principal("ceo"), "hr.T_SALARIES")   # exec erbt
    assert pol.allowed_sql_tables(pol.get_principal("ghost_gwen")) == []          # fail-closed
