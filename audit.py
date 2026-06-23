"""
audit.py -- Append-only Audit-Log fuer jede RAG-Anfrage.

Nicht verhandelbar (CLAUDE_CODE_BRIEF.md, Abschnitt 4): Jede Anfrage und welche
Quellen/Datensaetze tatsaechlich verwendet wurden, muss nachvollziehbar sein.

Format: eine JSON-Zeile pro Anfrage (JSONL) in data/audit/audit_log.jsonl.
Bleibt lokal (keine Daten verlassen das Netz). Append-only + Lock fuer parallele
Streamlit-Reruns.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_write_lock = threading.Lock()


class AuditLogger:
    def __init__(self, log_path: str):
        self.log_path = log_path
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
        except Exception as e:
            logger.error("AUDIT: Verzeichnis fuer '%s' nicht anlegbar (%s).", log_path, e)

    def log(self, entry: dict) -> None:
        """Schreibt einen Audit-Eintrag (eine JSON-Zeile). Fehler hier duerfen die
        eigentliche Anfrage nicht crashen -> nur loggen."""
        record = dict(entry)
        record.setdefault("ts", datetime.now(timezone.utc).isoformat())
        try:
            line = json.dumps(record, ensure_ascii=False)
            with _write_lock:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
        except Exception as e:
            logger.error("AUDIT: Eintrag nicht schreibbar (%s). Eintrag=%r", e, record)

    def tail(self, n: int = 50) -> list:
        """Liefert die letzten n Eintraege (neueste zuerst) fuer die UI."""
        try:
            if not os.path.exists(self.log_path):
                return []
            with open(self.log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            out = []
            for ln in lines[-n:]:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
            out.reverse()
            return out
        except Exception as e:
            logger.error("AUDIT: Log nicht lesbar (%s).", e)
            return []
