"""API-Dump-Connector + Klassifizierungs-Default + Veraltet-Ausschluss (deterministisch)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from onboarding.acl_normalize import (
    is_obsolete, classification_default_groups, find_manifest_root,
)
from connectors.api_dump import ApiDumpConnector


def test_is_obsolete_filename():
    assert is_obsolete("/x/01.03.2023_HINFÄLLIG_BESCHLUSS_0969.md")
    assert is_obsolete("/x/beschluss_WIDERRUFEN_v2.txt")
    assert not is_obsolete("/x/aktueller_beschluss_2025.md")


def test_classification_default(tmp_path):
    (tmp_path / "_MANIFEST.csv").write_text(
        "pfad;typ;berechtigungsmodell;klassifizierung\n"
        "docs/oeff.txt;doc;Inline;OEFFENTLICH\n"
        "docs/intern.txt;doc;Inline;INTERN\n"
        "docs/streng.txt;doc;Inline;STRENG_VERTRAULICH\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    for n in ("oeff.txt", "intern.txt", "streng.txt"):
        (tmp_path / "docs" / n).write_text("x")
    assert find_manifest_root(str(tmp_path / "docs" / "oeff.txt")) == str(tmp_path)
    assert classification_default_groups(str(tmp_path / "docs" / "oeff.txt")) == {"PUBLIC"}
    assert classification_default_groups(str(tmp_path / "docs" / "intern.txt")) == {"ALL_INTERNAL"}
    assert classification_default_groups(str(tmp_path / "docs" / "streng.txt")) == set()  # fail-closed


def test_api_dump_connector(tmp_path):
    d = tmp_path / "02_api_datenbanken" / "Einkauf"
    d.mkdir(parents=True)
    (d / "dump_bestellungen_0001.json").write_text(json.dumps({
        "_meta": {"source": "api://ek/bestellungen",
                  "acl": {"model": "POSIX", "owner": "svc-ek", "group": "ek-rw"}},
        "records": [{"id": 1, "bezeichnung": "x", "wert": 9.9}]}), encoding="utf-8")
    (d / "connection_ek.ini").write_text("[connection]\npassword=secret\n")  # darf NICHT rein
    (d / "dump_alt_0002_WIDERRUFEN.json").write_text(json.dumps({"records": []}))  # veraltet
    docs = {x.metadata["file_name"]: x for x in ApiDumpConnector(str(tmp_path)).iter_documents()}
    assert "dump_bestellungen_0001.json" in docs
    assert docs["dump_bestellungen_0001.json"].acl_groups == ["EK"]
    assert "api://ek/bestellungen" in docs["dump_bestellungen_0001.json"].text
    assert "connection_ek.ini" not in docs            # Secrets nicht indexiert
    assert "dump_alt_0002_WIDERRUFEN.json" not in docs  # veraltet uebersprungen
