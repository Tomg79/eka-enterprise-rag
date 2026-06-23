"""Deterministischer ACL-Normalisierer (kein LLM): 5 Modelle + Roh-Gruppen + Reader."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from onboarding.acl_normalize import normalize_principal, groups_from_sidecar


def test_normalize_principal_per_model():
    assert normalize_principal("ActiveDirectory", "MUSTERINDUSTRIE\\GG-EK-RW") == {"EK"}
    assert normalize_principal("SAP", "Z_FI_ADMIN_1000") == {"FI"}
    assert normalize_principal("POSIX", "svc-hr:hr-ro") == {"HR"}
    assert normalize_principal("SharePoint", "Besitzer GF") == {"GF"}
    assert normalize_principal("SharePoint", "Jeder außer externe Benutzer") == {"ALL_INTERNAL"}
    assert normalize_principal("Inline", "Praktikant:in,Sachbearbeiter:in") == set()


def test_sidecar_models():
    assert groups_from_sidecar({"model": "POSIX", "owner": "svc-ek", "group": "ek-rw"}) == {"EK"}
    assert groups_from_sidecar({"model": "SAP", "roles": ["Z_FI_ADMIN_1000"]}) == {"FI"}
    assert groups_from_sidecar({"model": "SharePoint", "siteUrl": "https://x/sites/GF",
                                "roleAssignments": [{"principal": "Besitzer GF"}]}) == {"GF"}
    assert groups_from_sidecar({"model": "ActiveDirectory",
                                "groups": ["MUSTERINDUSTRIE\\GG-IT-RW"]}) == {"IT"}
    # Inline -> leer (Job-Rollen, kein Abteilungscode) -> Aufrufer faellt auf Ordner zurueck
    assert groups_from_sidecar({"model": "Inline", "berechtigte": ["Praktikant:in"]}) == set()


def test_sidecar_raw_groups_passthrough():
    # Einfaches Schema (z.B. Demo) ohne Modell -> Roh-Gruppen durchreichen.
    assert groups_from_sidecar({"groups": ["SALES", "PUBLIC"]}) == {"SALES", "PUBLIC"}
    assert groups_from_sidecar({"groups": ["PROJECT_PHOENIX"]}) == {"PROJECT_PHOENIX"}


def test_deny_wins():
    g = groups_from_sidecar({"model": "ActiveDirectory", "acl": [
        {"principal": "MUSTERINDUSTRIE\\GG-EK-RW", "effekt": "Allow"},
        {"principal": "MUSTERINDUSTRIE\\GG-EK-RW", "effekt": "Deny"},
    ]})
    assert g == set()


def test_normalizing_reader(tmp_path):
    from connectors import NormalizingAclReader
    f = tmp_path / "doc.txt"
    f.write_text("x")
    (tmp_path / "doc.txt.acl.json").write_text(json.dumps({"model": "POSIX", "group": "fi-rw"}))
    assert NormalizingAclReader().groups_for(str(f)) == ["FI"]
    # ohne Sidecar + kein Abteilungsordner -> [] (fail-closed)
    f2 = tmp_path / "ohne.txt"
    f2.write_text("x")
    assert NormalizingAclReader().groups_for(str(f2)) == []
