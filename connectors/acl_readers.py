"""
connectors/acl_readers.py -- Austauschbare ACL-Leser.

Liefern fuer eine Datei die Liste der Gruppen mit Leserecht. Fail-closed:
keine/defekte ACL -> [] (= kein Zugriff). Die Windows-Variante liest echte
NTFS-ACLs (pywin32); die portablen Varianten (Sidecar/Prefix/Composite) sind
hier ohne Windows testbar.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


class AclReader:
    def groups_for(self, path: str) -> list:
        raise NotImplementedError


class SidecarAclReader(AclReader):
    """Liest erlaubte Gruppen aus '<datei>.acl.json' -> {"groups": [...]}.
    Repraesentiert eine echte, datei-individuelle ACL (portabel/testbar)."""
    def groups_for(self, path: str) -> list:
        sidecar = path + ".acl.json"
        if not os.path.exists(sidecar):
            return []
        try:
            with open(sidecar, "r", encoding="utf-8") as f:
                data = json.load(f)
            groups = data.get("groups") or []
            return sorted({str(g) for g in groups})
        except Exception as e:
            logger.error("ACL: Sidecar '%s' defekt (%s) -> kein Zugriff (fail-closed).", sidecar, e)
            return []


class PrefixAclReader(AclReader):
    """Back-compat/Demo: leitet die Gruppe aus dem Dateinamen-Praefix ab
    (wie die fruehere Simulation in Phase 0/1)."""
    MAP = {
        "PUBLIC_": "PUBLIC",
        "SALES_": "SALES",
        "HR_CONFIDENTIAL_": "HR_CONFIDENTIAL",
    }

    def groups_for(self, path: str) -> list:
        name = os.path.basename(path).upper()
        for prefix, group in self.MAP.items():
            if name.startswith(prefix):
                return [group]
        return ["PUBLIC"]  # Fallback wie bisher


class CompositeAclReader(AclReader):
    """Nimmt die ACL des ERSTEN Lesers, der etwas liefert. So koennen einzelne
    Dateien per Sidecar eine echte ACL bekommen, der Rest faellt auf den naechsten
    Leser (z.B. Prefix) zurueck."""
    def __init__(self, readers):
        self.readers = list(readers)

    def groups_for(self, path: str) -> list:
        for r in self.readers:
            groups = r.groups_for(path)
            if groups:
                return groups
        return []


class WindowsAclReader(AclReader):
    """Echte NTFS-ACL via pywin32: Namen der Konten/Gruppen mit Leserecht
    (FILE_GENERIC_READ). NUR auf Windows lauffaehig; Import lazy, damit das Modul
    auf Linux importierbar bleibt. Fail-closed bei jedem Problem."""
    def groups_for(self, path: str) -> list:
        try:
            import win32security
            import ntsecuritycon
        except Exception as e:
            logger.error("ACL: pywin32 nicht verfuegbar (%s) -> kein Zugriff.", e)
            return []
        try:
            sd = win32security.GetFileSecurity(path, win32security.DACL_SECURITY_INFORMATION)
            dacl = sd.GetSecurityDescriptorDacl()
            if dacl is None:
                logger.warning("ACL: '%s' ohne DACL -> kein Zugriff (fail-closed).", path)
                return []
            read_mask = ntsecuritycon.FILE_GENERIC_READ
            groups = set()
            for i in range(dacl.GetAceCount()):
                ace = dacl.GetAce(i)
                ace_type = ace[0][0]
                mask = ace[1]
                sid = ace[2]
                if ace_type != win32security.ACCESS_ALLOWED_ACE_TYPE:
                    continue
                if not (mask & read_mask):
                    continue
                try:
                    name, domain, _ = win32security.LookupAccountSid(None, sid)
                    groups.add(f"{domain}\\{name}" if domain else name)
                except Exception:
                    groups.add(str(win32security.ConvertSidToStringSid(sid)))
            return sorted(groups)
        except Exception as e:
            logger.error("ACL: NTFS-ACL fuer '%s' nicht lesbar (%s) -> kein Zugriff.", path, e)
            return []


class NormalizingAclReader(AclReader):
    """Liest `<datei>.acl.json` in BELIEBIGEM Modell (AD/SAP/SharePoint/POSIX/Inline) und
    normalisiert die Prinzipale regelbasiert auf kanonische Abteilungs-Codes
    (onboarding.acl_normalize). Faellt sonst auf die Ordner-Abteilung zurueck, sonst []
    (fail-closed). KEIN LLM."""
    def groups_for(self, path: str) -> list:
        import json
        from onboarding.acl_normalize import groups_from_sidecar, _folder_code
        for cand in (path + ".acl.json", __import__("os").path.splitext(path)[0] + ".acl.json"):
            if os.path.exists(cand):
                try:
                    data = json.load(open(cand, encoding="utf-8"))
                    g = groups_from_sidecar(data)
                    if g:
                        return sorted(g)
                except Exception as e:
                    logger.error("ACL: Sidecar '%s' defekt (%s) -> weiter/Fallback.", cand, e)
        fc = _folder_code(path)
        if fc:
            return [fc]
        # Klassifizierungs-Default (OEFFENTLICH->PUBLIC, INTERN->alle intern).
        from onboarding.acl_normalize import classification_default_groups
        g = classification_default_groups(path)
        if g:
            return sorted(g)
        # Governance/Beschluss-/Meeting-Dokumente ohne eigene ACL gelten unternehmensweit
        # intern (Berechtigungskonzept, Protokolle, Widerrufe) -> ALL_INTERNAL.
        low = path.replace("\\", "/").lower()
        if any(k in low for k in ("00_governance", "03_meetings", "06_veraltet",
                                  "widerrufen", "entscheidung", "beschluss",
                                  "protokoll", "governance", "meeting")):
            return ["ALL_INTERNAL"]
        return []  # sonst fail-closed


def get_acl_reader(kind: str = "composite") -> AclReader:
    """Factory. 'composite' (Default) = Sidecar, sonst Prefix. 'sidecar'/'prefix'/
    'windows' waehlen einen einzelnen Leser. 'windows' nur auf Windows sinnvoll."""
    kind = (kind or "composite").lower()
    if kind == "prefix":
        return PrefixAclReader()
    if kind == "sidecar":
        return SidecarAclReader()
    if kind == "windows":
        return WindowsAclReader()
    if kind == "normalized":
        return NormalizingAclReader()
    # composite: echte Sidecar-ACL gewinnt, sonst Prefix-Fallback
    return CompositeAclReader([SidecarAclReader(), PrefixAclReader()])
