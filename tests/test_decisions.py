"""Deterministischer Beschluss-Extraktor (read-only)."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from onboarding.decisions import parse_decisions, summarize


def test_parse_decisions(tmp_path):
    (tmp_path / "2021").mkdir()
    (tmp_path / "2021" / "prot.md").write_text(
        "## Beschluesse\n"
        "- **BESCHLUSS-2021-0001** [GEWAEHRT]: Zugriff auf Datenbank/Tabelle 'kunden_x' "
        "wird AD-Gruppe MUSTERINDUSTRIE\\GG-VK-RW Lesen gewaehrt.\n"
        "- **BESCHLUSS-2021-0002** [ENTZUG]: Berechtigung auf 'hauptbuch_y' fuer "
        "Z_FI_PFLEGE_1000 wird entzogen.\n", encoding="utf-8")
    # veraltetes Protokoll -> ignoriert
    (tmp_path / "2021" / "alt_WIDERRUFEN.md").write_text(
        "- **BESCHLUSS-2021-9999** [GEWAEHRT]: 'geheim' an Z_GF_ADMIN_1000.\n", encoding="utf-8")
    recs = parse_decisions(str(tmp_path))
    ids = {r["id"]: r for r in recs}
    assert "BESCHLUSS-2021-0001" in ids and "BESCHLUSS-2021-0002" in ids
    assert "BESCHLUSS-2021-9999" not in ids                      # veraltet uebersprungen
    assert ids["BESCHLUSS-2021-0001"]["wirkung"] == "gewaehrt"
    assert ids["BESCHLUSS-2021-0001"]["objekt"] == "kunden_x"
    assert ids["BESCHLUSS-2021-0001"]["abteilungen"] == ["VK"]
    assert ids["BESCHLUSS-2021-0002"]["wirkung"] == "entzogen"
    assert ids["BESCHLUSS-2021-0002"]["abteilungen"] == ["FI"]
    assert "Beschluesse gesamt: 2" in summarize(recs)
