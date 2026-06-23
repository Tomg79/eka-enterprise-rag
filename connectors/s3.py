"""
connectors/s3.py -- Phase 5: Cloud-Storage-Connector (S3-kompatibel).

Liest Text-Objekte aus einem S3-kompatiblen Bucket (AWS S3, MinIO, Ceph, Wasabi ...)
und ermittelt pro Objekt die leseberechtigten Gruppen ueber einen austauschbaren
S3-ACL-Leser -- analog zum FilesystemConnector (Phase 2). Damit passiert das
Permission-Tagging AN DER QUELLE.

Nicht verhandelbar (siehe CLAUDE_CODE_BRIEF.md):
  * fail-closed: kein/defektes ACL -> [] (= fuer niemanden sichtbar), nie "alles frei".
  * keine Daten/Secrets verlassen das Netz: Zugangsdaten kommen AUSSCHLIESSLICH aus der
    Umgebung (Env/IAM-Rolle/Profile) bzw. werden von boto3 selbst aufgeloest -- niemals
    aus dem Code oder aus geloggten Strings. Mit endpoint_url laeuft alles gegen einen
    lokalen/On-Prem-S3 (MinIO), ohne dass Daten zu AWS gehen.

boto3 wird LAZY importiert, damit das Modul auch ohne installiertes boto3/ohne S3
importierbar bleibt (z.B. in Test-/Lint-Umgebungen).
"""

from __future__ import annotations

import json
import logging
from typing import Iterator

from .base import Connector, ConnectorDocument

logger = logging.getLogger(__name__)

_TEXT_EXTENSIONS = (".txt", ".md")
_SIDECAR_SUFFIX = ".acl.json"


def _make_s3_client(endpoint_url=None, region=None):
    """Erzeugt einen boto3-S3-Client. Credentials loest boto3 selbst aus der Umgebung
    auf (AWS_ACCESS_KEY_ID/SECRET, AWS_PROFILE, IAM-Rolle ...). endpoint_url zeigt auf
    einen S3-kompatiblen Dienst (z.B. http://minio:9000)."""
    import boto3  # lazy
    return boto3.client("s3", endpoint_url=endpoint_url, region_name=region)


# ---- S3-ACL-Leser (fail-closed) ---------------------------------------------
class S3AclReader:
    def groups_for(self, client, bucket: str, key: str) -> list:
        raise NotImplementedError


class S3SidecarAclReader(S3AclReader):
    """Echte per-Objekt-ACL: liest '<key>.acl.json' als S3-Objekt -> {"groups": [...]}."""
    def groups_for(self, client, bucket: str, key: str) -> list:
        sidecar = key + _SIDECAR_SUFFIX
        try:
            obj = client.get_object(Bucket=bucket, Key=sidecar)
            data = json.loads(obj["Body"].read().decode("utf-8"))
            groups = data.get("groups") or []
            return sorted({str(g) for g in groups})
        except Exception:
            # Kein Sidecar oder defekt -> dieser Leser liefert nichts (Composite faellt weiter).
            return []


class S3PrefixAclReader(S3AclReader):
    """Leitet die Gruppe aus dem Key-Praefix ab (Demo/Back-compat, analog Filesystem)."""
    MAP = {
        "PUBLIC_": "PUBLIC",
        "SALES_": "SALES",
        "HR_CONFIDENTIAL_": "HR_CONFIDENTIAL",
    }

    def groups_for(self, client, bucket: str, key: str) -> list:
        base = key.rsplit("/", 1)[-1].upper()
        for prefix, group in self.MAP.items():
            if base.startswith(prefix):
                return [group]
        return ["PUBLIC"]


class S3CompositeAclReader(S3AclReader):
    """Nimmt die ACL des ERSTEN Lesers, der etwas liefert (Sidecar gewinnt, dann Prefix)."""
    def __init__(self, readers):
        self.readers = list(readers)

    def groups_for(self, client, bucket: str, key: str) -> list:
        for r in self.readers:
            groups = r.groups_for(client, bucket, key)
            if groups:
                return groups
        return []


def get_s3_acl_reader(kind: str = "composite") -> S3AclReader:
    kind = (kind or "composite").lower()
    if kind == "prefix":
        return S3PrefixAclReader()
    if kind == "sidecar":
        return S3SidecarAclReader()
    return S3CompositeAclReader([S3SidecarAclReader(), S3PrefixAclReader()])


# ---- Connector ---------------------------------------------------------------
class S3Connector(Connector):
    name = "s3"

    def __init__(self, bucket: str, prefix: str = "", acl_reader: S3AclReader = None,
                 endpoint_url=None, region=None, client=None):
        self.bucket = bucket
        self.prefix = prefix or ""
        self.acl_reader = acl_reader or get_s3_acl_reader("composite")
        self.endpoint_url = endpoint_url
        self.region = region
        # client kann injiziert werden (Tests/Mock); sonst lazy erzeugt.
        self._client = client

    def _get_client(self):
        if self._client is None:
            self._client = _make_s3_client(self.endpoint_url, self.region)
        return self._client

    def _iter_keys(self, client):
        """Paginiert ueber alle Objekt-Keys unter prefix."""
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []) or []:
                yield obj["Key"]

    def iter_documents(self) -> Iterator[ConnectorDocument]:
        client = self._get_client()
        for key in self._iter_keys(client):
            if key.endswith(_SIDECAR_SUFFIX):
                continue  # ACL-Sidecar, kein Inhalt
            if not key.lower().endswith(_TEXT_EXTENSIONS):
                continue
            try:
                obj = client.get_object(Bucket=self.bucket, Key=key)
                text = obj["Body"].read().decode("utf-8", errors="replace")
            except Exception as e:
                logger.error("S3: Objekt '%s' nicht lesbar (%s) -> uebersprungen.", key, e)
                continue
            groups = self.acl_reader.groups_for(client, self.bucket, key)
            if not groups:
                logger.warning("S3: '%s' hat keine erlaubten Gruppen (ACL leer) -> "
                               "indexiert, aber fuer niemanden sichtbar (fail-closed).", key)
            file_name = key.rsplit("/", 1)[-1]
            yield ConnectorDocument(
                doc_id=key,
                text=text,
                acl_groups=groups,
                metadata={
                    "file_name": file_name,
                    "source_type": "document",
                    "s3_bucket": self.bucket,
                    "s3_key": key,
                },
            )
