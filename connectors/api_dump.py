"""
connectors/api_dump.py -- API-Dump-Connector (z.B. 02_api_datenbanken/dump_*.json).

Liest JSON-API-Exporte (Felder `_meta` + `records`), leitet die Lesegruppen DETERMINISTISCH
aus der inline-ACL (`_meta.acl`, beliebiges Modell) ab, sonst Ordner-Abteilung, sonst
Klassifizierung. Veraltete/widerrufene Exporte (_MANIFEST) werden uebersprungen.
Zugangs-Configs (connection_*.ini) werden NICHT gelesen (Secrets, kein Inhalt).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Iterator

from .base import Connector, ConnectorDocument

logger = logging.getLogger(__name__)


class ApiDumpConnector(Connector):
    name = "api"

    def __init__(self, root: str, max_records: int = 50):
        self.root = root
        self.max_records = max_records

    def iter_documents(self) -> Iterator[ConnectorDocument]:
        from onboarding.acl_normalize import (
            groups_from_sidecar, _folder_code, is_obsolete, classification_default_groups,
        )
        for dp, _dirs, fns in os.walk(self.root):
            for fn in sorted(fns):
                low = fn.lower()
                if not (low.endswith(".json") and low.startswith("dump_")):
                    continue
                path = os.path.join(dp, fn)
                if is_obsolete(path):
                    logger.info("API: '%s' laut _MANIFEST veraltet -> uebersprungen.", path)
                    continue
                try:
                    data = json.load(open(path, encoding="utf-8"))
                except Exception as e:
                    logger.error("API: '%s' nicht lesbar (%s) -> uebersprungen.", path, e)
                    continue
                meta = data.get("_meta", {}) if isinstance(data, dict) else {}
                groups = set()
                if isinstance(meta.get("acl"), dict):
                    groups = groups_from_sidecar(meta["acl"])
                if not groups:
                    fc = _folder_code(path)
                    groups = {fc} if fc else classification_default_groups(path)
                records = (data.get("records") if isinstance(data, dict)
                           else (data if isinstance(data, list) else [])) or []
                lines = [f"API-Quelle: {meta.get('source', '?')} (Export {meta.get('exported', '?')})"]
                for r in records[:self.max_records]:
                    lines.append("; ".join(f"{k}={v}" for k, v in r.items())
                                 if isinstance(r, dict) else str(r))
                text = "\n".join(lines)
                if not text.strip():
                    continue
                if not groups:
                    logger.warning("API: '%s' ohne erlaubte Gruppen -> fail-closed unsichtbar.", path)
                yield ConnectorDocument(
                    doc_id=fn, text=text, acl_groups=sorted(groups),
                    metadata={"file_name": fn, "source_type": "api", "path": path},
                )
