"""
connectors/base.py -- Gemeinsame Connector-Schnittstelle.

Ein Connector liefert Dokumente aus einer Datenquelle (Filesystem, spaeter SQL,
S3 ...) inkl. der Liste der Gruppen, die das Dokument lesen duerfen (acl_groups).
Das Permission-Tagging passiert damit AN DER QUELLE (echte ACL) statt simuliert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class ConnectorDocument:
    doc_id: str
    text: str
    acl_groups: list = field(default_factory=list)   # Gruppen mit Leserecht
    metadata: dict = field(default_factory=dict)


class Connector:
    """Basisklasse. Konkrete Connectoren implementieren iter_documents()."""
    name = "base"

    def iter_documents(self) -> Iterator[ConnectorDocument]:
        raise NotImplementedError
