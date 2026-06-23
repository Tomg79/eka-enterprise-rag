"""
meetings.py -- Meeting -> Entscheidung -> Freigabe -> RAG (Phase 3).

Ablauf:
  1. Ein (transkribiertes) Meeting wird analysiert: ein LLM extrahiert die TATSAECHLICH
     getroffenen Entscheidungen als knappes Entscheidungsdokument und schlaegt vor,
     welche Gruppen es lesen duerfen (nur aus den bekannten Gruppen).
  2. Das Ergebnis landet als 'pending' in einem Freigabe-Speicher.
  3. Der Meeting-Host bestaetigt/korrigiert in der UI die Lesegruppen.
  4. ERST nach Freigabe wird das Entscheidungsdokument mit genau diesen acl_groups
     in den RAG-Index eingespielt (inkrementell) und zusaetzlich als Datei + Sidecar-ACL
     persistiert (damit es einen vollen Re-Ingest ueberlebt).

Sicherheit: Nichts wird ohne menschliche Freigabe sichtbar (fail-closed). Vom LLM
vorgeschlagene Gruppen werden gegen die bekannten Gruppen gefiltert.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Extraktion (LLM)
# ──────────────────────────────────────────────
@dataclass
class MeetingExtraction:
    title: str
    decision_document: str
    suggested_groups: list = field(default_factory=list)
    rationale: str = ""


def _coerce_groups(groups, known_groups) -> list:
    """Behaelt nur Gruppen, die wirklich existieren (fail-closed gegen Halluzination)."""
    known = set(known_groups or [])
    out = []
    for g in (groups or []):
        g = str(g).strip()
        if g in known and g not in out:
            out.append(g)
    return out


def _parse_json_blob(text: str) -> dict:
    """Robustes JSON-Parsing: nimmt den ersten {...}-Block."""
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}
    return {}


class MeetingExtractor:
    def extract(self, transcript: str, known_groups: list) -> MeetingExtraction:
        raise NotImplementedError


_PROMPT = """You process a meeting transcript for a secure company knowledge base.
Extract ONLY decisions that were actually made (ignore small talk and open discussion).

Return STRICT JSON with exactly these keys:
  "title": a short title (<= 8 words),
  "decision_document": a concise plain-text summary of the decisions, who is responsible
     and any deadlines. Use the language of the transcript.
  "suggested_groups": a list with values chosen ONLY from this allowed set: {known_groups}.
     Pick the groups whose members need to read these decisions based on the departments
     and topics involved. If unsure, return an empty list.
  "rationale": one short sentence why those groups.

Transcript:
\"\"\"
{transcript}
\"\"\"

JSON:"""


class OllamaMeetingExtractor(MeetingExtractor):
    """Nutzt das lokale LLM (LlamaIndex-LLM mit .complete)."""
    def __init__(self, llm):
        self.llm = llm

    def extract(self, transcript: str, known_groups: list) -> MeetingExtraction:
        prompt = _PROMPT.format(known_groups=sorted(known_groups), transcript=transcript)
        try:
            raw = str(self.llm.complete(prompt))
        except Exception as e:
            logger.error("MEETING: LLM-Extraktion fehlgeschlagen (%s).", e)
            raw = ""
        data = _parse_json_blob(raw)
        return MeetingExtraction(
            title=str(data.get("title") or "Meeting-Entscheidung"),
            decision_document=str(data.get("decision_document") or "").strip(),
            suggested_groups=_coerce_groups(data.get("suggested_groups"), known_groups),
            rationale=str(data.get("rationale") or ""),
        )


class HeuristicExtractor(MeetingExtractor):
    """Ohne LLM (Tests/Fallback): einfache Stichwort-Heuristik fuer Gruppenvorschlag."""
    KEYWORDS = {
        "SALES": ["kunde", "cust-", "rabatt", "vertrieb", "umsatz", "angebot"],
        "HR_CONFIDENTIAL": ["gehalt", "abmahnung", "mitarbeiter", "personal", "kuendigung"],
        "PROJECT_PHOENIX": ["phoenix", "mat-500"],
    }

    def extract(self, transcript: str, known_groups: list) -> MeetingExtraction:
        low = transcript.lower()
        suggested = []
        for grp, kws in self.KEYWORDS.items():
            if any(k in low for k in kws):
                suggested.append(grp)
        first = (transcript.strip().splitlines() or ["Meeting"])[0][:60]
        return MeetingExtraction(
            title=first or "Meeting-Entscheidung",
            decision_document=transcript.strip(),
            suggested_groups=_coerce_groups(suggested, known_groups),
            rationale="Heuristik (kein LLM).",
        )


# ──────────────────────────────────────────────
# Pending-/Freigabe-Speicher
# ──────────────────────────────────────────────
class PendingStore:
    """Dateibasierter Speicher fuer Freigabe-Vorgaenge (ein JSON je Vorgang)."""
    def __init__(self, dirpath: str):
        self.dir = dirpath
        try:
            os.makedirs(dirpath, exist_ok=True)
        except Exception as e:
            logger.error("MEETING: Pending-Verzeichnis '%s' nicht anlegbar (%s).", dirpath, e)

    def _path(self, item_id: str) -> str:
        return os.path.join(self.dir, f"{item_id}.json")

    def create(self, source: str, transcript: str, extraction: MeetingExtraction) -> dict:
        item_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        item = {
            "id": item_id,
            "created": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "title": extraction.title,
            "transcript_excerpt": transcript.strip()[:500],
            "decision_document": extraction.decision_document,
            "suggested_groups": extraction.suggested_groups,
            "rationale": extraction.rationale,
            "status": "pending",
            "approved_groups": [],
        }
        self._write(item)
        return item

    def _write(self, item: dict):
        try:
            with open(self._path(item["id"]), "w", encoding="utf-8") as f:
                json.dump(item, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("MEETING: Vorgang %s nicht schreibbar (%s).", item.get("id"), e)

    def get(self, item_id: str) -> dict | None:
        try:
            with open(self._path(item_id), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def list(self, status: str | None = None) -> list:
        out = []
        if not os.path.isdir(self.dir):
            return out
        for fn in os.listdir(self.dir):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.dir, fn), "r", encoding="utf-8") as f:
                    item = json.load(f)
            except Exception:
                continue
            if status is None or item.get("status") == status:
                out.append(item)
        out.sort(key=lambda i: i.get("created", ""), reverse=True)
        return out

    def set_status(self, item_id: str, status: str, approved_groups=None) -> dict | None:
        item = self.get(item_id)
        if not item:
            return None
        item["status"] = status
        if approved_groups is not None:
            item["approved_groups"] = list(approved_groups)
        item["decided"] = datetime.now(timezone.utc).isoformat()
        self._write(item)
        return item


def sanitize_doc_name(item_id: str) -> str:
    return "MEETING_" + re.sub(r"[^0-9A-Za-z_-]", "", item_id)


def persist_approved_document(docs_dir: str, item_id: str, text: str, groups: list) -> str:
    """Schreibt das freigegebene Entscheidungsdokument als Datei + Sidecar-ACL in den
    Dokumentenordner, damit es einen vollen Re-Ingest ueberlebt. Gibt den Dateinamen zurueck."""
    name = sanitize_doc_name(item_id) + ".txt"
    path = os.path.join(docs_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n")
    with open(path + ".acl.json", "w", encoding="utf-8") as f:
        json.dump({"groups": list(groups)}, f, ensure_ascii=False)
    return name
