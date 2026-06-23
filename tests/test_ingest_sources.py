"""Onboarding -> Multi-Quellen-Ingest-Manifest. Prueft, dass bootstrap ein
ingest_sources.json schreibt, das ALLE Dokumentquellen (Dateifreigaben + Object-Store)
mit korrektem ACL-Tagging erfasst. Ohne Ollama/Embeddings (Connector-Ebene)."""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from onboarding.discovery import discover
from onboarding.bootstrap import apply_config, build_ingest_sources
from onboarding.landscape import Landscape
from connectors import FilesystemConnector, get_acl_reader


def _gen(root):
    path = os.path.join(ROOT, "tools", "generate_enterprise_landscape.py")
    spec = importlib.util.spec_from_file_location("gen_landscape", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    os.makedirs(root, exist_ok=True)
    fs, _ = mod.gen_file_shares(root)
    mod.gen_databases(root)
    obj = mod.gen_object_store(root)
    mod.gen_apis(root)
    ident = mod.gen_identity(root)
    return mod


@pytest.fixture
def cfg(tmp_path):
    root = str(tmp_path / "ACME")
    _gen(root)
    ls, _ = discover(root)
    out = tmp_path / "cfg"
    (out / "data").mkdir(parents=True)
    apply_config(ls, str(out), str(out / "deploy" / ".env"))
    return out


def test_manifest_lists_all_doc_sources(cfg):
    spec = json.load(open(cfg / "data" / "ingest_sources.json"))
    assert spec["file_shares"], "keine Dateifreigaben im Manifest"
    assert spec["object_stores"], "kein Object-Store im Manifest"


def test_share_documents_carry_correct_acls(cfg):
    spec = json.load(open(cfg / "data" / "ingest_sources.json"))
    fs = spec["file_shares"][0]
    conn = FilesystemConnector(fs["path"], get_acl_reader(fs["acl_reader"]))
    docs = {d.metadata["file_name"]: d for d in conn.iter_documents()}
    assert docs["project_phoenix_memo.txt"].acl_groups == ["PROJECT_PHOENIX"]   # ACL schlaegt Ordner
    assert docs["deal_CUST-097.txt"].acl_groups == ["SALES"]
    assert docs["disciplinary_case_2026_012.txt"].acl_groups == ["HR_CONFIDENTIAL"]
    # ACL-lose Datei im HR-Ordner -> Abteilungs-Zugriff HR (kein PUBLIC-Leck, kein []).
    assert docs["salary_review_notes.txt"].acl_groups == ["HR_CONFIDENTIAL"] or docs["salary_review_notes.txt"].acl_groups == ["HR"]


def test_object_store_seed_indexable_with_acls(cfg):
    spec = json.load(open(cfg / "data" / "ingest_sources.json"))
    ob = spec["object_stores"][0]
    conn = FilesystemConnector(ob["local_seed_path"], get_acl_reader(ob["acl_reader"]))
    docs = {d.metadata["file_name"]: d for d in conn.iter_documents()}
    assert docs["old_contract_2019.txt"].acl_groups == ["LEGAL"]
    assert docs["archived_deal_2024.txt"].acl_groups == ["SALES"]


def test_pdf_and_docx_are_read(cfg):
    """Verschiedene Dateiarten: PDF + DOCX werden gelesen + korrekt beACLt.
    Uebersprungen, falls die Generator-Libs (reportlab/python-docx) fehlen."""
    pytest.importorskip("reportlab")
    pytest.importorskip("docx")
    pytest.importorskip("pypdf")
    import json
    from connectors import FilesystemConnector, get_acl_reader
    spec = json.load(open(cfg / "data" / "ingest_sources.json"))
    fs = spec["file_shares"][0]
    docs = {d.metadata["file_name"]: d
            for d in FilesystemConnector(fs["path"], get_acl_reader(fs["acl_reader"])).iter_documents()}
    pdf = docs.get("master_agreement_2026.pdf")
    dx = docs.get("employee_handbook.docx")
    assert pdf and "Master Agreement" in pdf.text and set(pdf.acl_groups) == {"LEGAL", "EXEC"}
    assert dx and "Mitarbeiterhandbuch" in dx.text and dx.acl_groups == ["PUBLIC"]
