"""
onboarding/decisions.py -- Deterministischer Beschluss-Extraktor (read-only, KEIN LLM).

Liest Meeting-Protokolle (03_meetings) und extrahiert die strukturierten Beschluss-Zeilen
('**BESCHLUSS-JJJJ-NNNN** [TYP]: ...') in maschinenlesbare Records: Typ, Objekt, Prinzipal
(auf kanonische Abteilung normalisiert), Recht, Wirkung (gewaehrt/entzogen/...), Datum.

WICHTIG (Sicherheit): Diese Beschluesse werden NICHT automatisch auf die Live-Rechte
angewendet. Aus Freitext Zugriffe zu *gewaehren* waere fail-OPEN bei Fehlparsung. Der
Extraktor dient Transparenz/Audit + menschlichem Review. Veraltete/widerrufene Protokolle
(_MANIFEST/Dateiname) werden uebersprungen.
"""
from __future__ import annotations

import os
import re

_BESCHLUSS = re.compile(r"\*\*(BESCHLUSS-\d{4}-\d+)\*\*\s*\[([A-Z_]+)\]\s*:?\s*(.*)")
_OBJ = re.compile(r"'([^']+)'")
_RIGHTS = ("FullControl", "ReadAndExecute", "Nur anzeigen", "Lesen", "Schreiben",
           "Modify", "Vollzugriff", "Bearbeiten", "Write", "Read")

# Beschluss-Typ -> Wirkung (deterministische Zuordnung).
EFFECT = {
    "GEWAEHRT": "gewaehrt", "NEU_ROLLE": "gewaehrt", "AENDERUNG": "geaendert",
    "FRIST": "befristet", "ENTZUG": "entzogen", "ENTZOGEN": "entzogen",
    "WIDERRUF": "entzogen", "WIDERRUFEN": "entzogen", "SPERRE": "entzogen",
}


def parse_decisions(meetings_root: str) -> list:
    """Scannt meetings_root rekursiv und gibt Beschluss-Records zurueck (ohne veraltete)."""
    from onboarding.acl_normalize import is_obsolete, normalize_principal
    out = []
    for dp, _d, fns in os.walk(meetings_root):
        for fn in sorted(fns):
            if not fn.lower().endswith((".md", ".txt")):
                continue
            path = os.path.join(dp, fn)
            if is_obsolete(path):
                continue
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for line in text.splitlines():
                m = _BESCHLUSS.search(line)
                if not m:
                    continue
                bid, typ, body = m.group(1), m.group(2).upper(), m.group(3).strip()
                obj = (_OBJ.search(body).group(1) if _OBJ.search(body) else "")
                right = next((r for r in _RIGHTS if r.lower() in body.lower()), "")
                # Prinzipal -> kanonische Abteilung (AD/SAP/SharePoint/POSIX), sonst leer.
                codes = set()
                for token in re.findall(r"[A-Za-zÄÖÜäöü\\\\_:.\-]+", body):
                    for model in ("ActiveDirectory", "SAP", "POSIX", "SharePoint"):
                        codes |= normalize_principal(model, token)
                out.append({
                    "id": bid, "typ": typ, "wirkung": EFFECT.get(typ, typ.lower()),
                    "objekt": obj, "recht": right, "abteilungen": sorted(codes),
                    "datei": os.path.relpath(path, meetings_root),
                })
    return out


def summarize(records: list) -> str:
    import collections
    by_eff = collections.Counter(r["wirkung"] for r in records)
    by_dept = collections.Counter(c for r in records for c in r["abteilungen"])
    lines = [f"Beschluesse gesamt: {len(records)}",
             "Nach Wirkung: " + ", ".join(f"{k}={v}" for k, v in by_eff.most_common()),
             "Nach Abteilung: " + ", ".join(f"{k}={v}" for k, v in by_dept.most_common())]
    return "\n".join(lines)
