"""
auth.py -- Authentifizierung (WER ist der Nutzer) fuer den Enterprise-RAG.

WICHTIG -- klare Trennung:
  * auth.py   = AUTHENTIFIZIERUNG: Identitaet nachweisen (Login). Liefert eine user_id.
  * policy.py = AUTORISIERUNG:     was darf diese user_id (Rollen/Rechte).
Die App vertraut NUR einer auth-bestaetigten user_id und reicht sie an die PolicyEngine.
Damit ist das im Brief geforderte echte Login da statt der bisherigen Dropdown-Simulation.

Designprinzipien:
  * On-Premise, keine externe Abhaengigkeit: Passwort-Hashing mit stdlib (scrypt),
    Session-Tokens via HMAC -- kein PyJWT/bcrypt noetig.
  * fail-closed: fehlende/defekte Auth-Datei, falsches/abgelaufenes/manipuliertes Token
    -> NICHT eingeloggt.
  * Erweiterbar: AuthBackend-Interface; LocalBackend ist Default, OIDC/LDAP koennen
    spaeter andocken, ohne dass die App sich aendert.
  * Keine Klartext-Passwoerter, nirgends (nicht im Log, nicht in der Datei).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time

logger = logging.getLogger(__name__)

# scrypt-Parameter (RFC-7914-empfohlene Groessenordnung fuer interaktive Logins).
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32


# ──────────────────────────────────────────────────────────────
# Passwort-Hashing (scrypt, stdlib)
# ──────────────────────────────────────────────────────────────
def hash_password(password: str) -> dict:
    """Erzeugt einen scrypt-Hash-Record (kein Klartext gespeichert)."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN)
    return {"algo": "scrypt", "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P,
            "salt": salt.hex(), "hash": dk.hex()}


def verify_password(password: str, record: dict) -> bool:
    """Prueft ein Passwort gegen einen Hash-Record. Constant-time, fail-closed."""
    try:
        if (record or {}).get("algo") != "scrypt":
            return False
        salt = bytes.fromhex(record["salt"])
        expected = bytes.fromhex(record["hash"])
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                            n=int(record["n"]), r=int(record["r"]), p=int(record["p"]),
                            dklen=len(expected))
        return hmac.compare_digest(dk, expected)
    except Exception as e:
        logger.error("AUTH: Passwortpruefung fehlgeschlagen (%s) -> deny.", e)
        return False


# ──────────────────────────────────────────────────────────────
# Session-Tokens (HMAC-signiert, mit Ablauf)
# ──────────────────────────────────────────────────────────────
def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(user_id: str, secret: bytes, ttl_seconds: int) -> str:
    """Signiertes Token 'payload.signature'. payload = base64({uid, exp})."""
    payload = {"uid": user_id, "exp": int(time.time()) + int(ttl_seconds)}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = _b64e(raw)
    sig = _b64e(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token: str, secret: bytes) -> str | None:
    """Gibt die user_id zurueck, wenn Signatur gueltig UND nicht abgelaufen; sonst None."""
    try:
        body, sig = token.split(".", 1)
        expected = _b64e(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload.get("uid")
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────
# Backends
# ──────────────────────────────────────────────────────────────
class AuthBackend:
    """Schnittstelle. authenticate(user_id, password) -> bool."""
    def authenticate(self, user_id: str, password: str) -> bool:
        raise NotImplementedError

    def list_users(self) -> list:
        return []


class LocalBackend(AuthBackend):
    """Liest data/auth_users.json: { "users": { uid: {display, password: <record>} } }.
    Fail-closed: Datei fehlt/defekt -> kein Login moeglich."""
    def __init__(self, path: str):
        self.path = path

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return (data or {}).get("users", {}) or {}
        except FileNotFoundError:
            logger.error("AUTH: '%s' nicht gefunden -> kein Login moeglich (fail-closed).", self.path)
            return {}
        except Exception as e:
            logger.error("AUTH: '%s' defekt (%s) -> kein Login moeglich.", self.path, e)
            return {}

    def authenticate(self, user_id: str, password: str) -> bool:
        users = self._load()
        rec = users.get(user_id)
        if not isinstance(rec, dict):
            # Konstante Arbeit auch fuer unbekannte User (kein User-Enumeration-Timing-Leak).
            verify_password(password, {"algo": "scrypt", "n": _SCRYPT_N, "r": _SCRYPT_R,
                                       "p": _SCRYPT_P, "salt": "00", "hash": "00"})
            return False
        return verify_password(password, rec.get("password") or {})

    def list_users(self) -> list:
        return sorted((uid, (u or {}).get("display", uid)) for uid, u in self._load().items())


# OIDC/LDAP-Andockpunkte (Skizze; bewusst nicht aktiv, On-Prem-Default ist LocalBackend):
#   class OIDCBackend(AuthBackend): ...  # Authorization-Code-Flow gegen Keycloak/Azure AD
#   class LDAPBackend(AuthBackend): ...  # bind gegen AD/LDAP, Gruppen -> Rollen-Mapping
# Beide muessen am Ende nur eine bestaetigte user_id liefern; die Rechte bleiben in policy.py.


# ──────────────────────────────────────────────────────────────
# Manager
# ──────────────────────────────────────────────────────────────
def load_or_create_secret(secret_path: str) -> bytes:
    """Liest den HMAC-Session-Secret oder erzeugt ihn (0600). Env EKA_AUTH_SECRET hat
    Vorrang (z.B. im Container gesetzt)."""
    env = os.environ.get("EKA_AUTH_SECRET")
    if env:
        return env.encode("utf-8")
    try:
        if os.path.exists(secret_path):
            with open(secret_path, "r", encoding="utf-8") as f:
                val = f.read().strip()
            if val:
                return bytes.fromhex(val)
        os.makedirs(os.path.dirname(os.path.abspath(secret_path)) or ".", exist_ok=True)
        token = secrets.token_bytes(32)
        with open(secret_path, "w", encoding="utf-8") as f:
            f.write(token.hex())
        try:
            os.chmod(secret_path, 0o600)
        except Exception:
            pass
        return token
    except Exception as e:
        # Letzter Ausweg: prozesslokaler Zufallssecret (Sessions ueberleben Neustart nicht).
        logger.error("AUTH: Secret nicht persistierbar (%s) -> ephemerer Secret.", e)
        return secrets.token_bytes(32)


class AuthManager:
    def __init__(self, backend: AuthBackend, secret: bytes, ttl_seconds: int = 8 * 3600):
        self.backend = backend
        self.secret = secret
        self.ttl = ttl_seconds

    def login(self, user_id: str, password: str) -> str | None:
        """Bei Erfolg signiertes Session-Token, sonst None."""
        if not user_id or not password:
            return None
        if self.backend.authenticate(user_id, password):
            logger.info("AUTH: Login ok fuer '%s'.", user_id)
            return issue_token(user_id, self.secret, self.ttl)
        logger.warning("AUTH: Login fehlgeschlagen fuer '%s'.", user_id)
        return None

    def user_from_token(self, token: str) -> str | None:
        if not token:
            return None
        return verify_token(token, self.secret)

    def list_users(self) -> list:
        return self.backend.list_users()
