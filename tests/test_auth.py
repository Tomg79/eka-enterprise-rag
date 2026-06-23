"""Security-Regression: Authentifizierung (Hashing, Tokens, Backend, fail-closed)."""
import json
import time

import pytest

from auth import (
    hash_password, verify_password, issue_token, verify_token,
    LocalBackend, AuthManager,
)

SECRET = b"unit-test-secret-key-0123456789ab"


# ---- Passwort-Hashing ----
def test_password_roundtrip():
    rec = hash_password("Geheim123!")
    assert rec["algo"] == "scrypt" and "hash" in rec and "salt" in rec
    assert "Geheim123!" not in json.dumps(rec)        # kein Klartext gespeichert
    assert verify_password("Geheim123!", rec)
    assert not verify_password("falsch", rec)


def test_password_tampered_record_fails():
    rec = hash_password("Geheim123!")
    rec["hash"] = "00" * 32
    assert not verify_password("Geheim123!", rec)
    assert not verify_password("Geheim123!", {})       # leerer Record -> deny


# ---- Session-Tokens ----
def test_token_roundtrip():
    tok = issue_token("alice", SECRET, ttl_seconds=60)
    assert verify_token(tok, SECRET) == "alice"


def test_token_expired():
    tok = issue_token("alice", SECRET, ttl_seconds=-1)
    assert verify_token(tok, SECRET) is None


def test_token_wrong_secret():
    tok = issue_token("alice", SECRET, ttl_seconds=60)
    assert verify_token(tok, b"anderer-secret-key-xxxxxxxxxxxxxx") is None


def test_token_tampered():
    tok = issue_token("alice", SECRET, ttl_seconds=60)
    body, sig = tok.split(".", 1)
    # Payload faelschen (anderer User) -> Signatur passt nicht mehr.
    import base64, json as _j
    forged = base64.urlsafe_b64encode(_j.dumps({"uid": "ceo", "exp": int(time.time())+60}).encode()).decode().rstrip("=")
    assert verify_token(f"{forged}.{sig}", SECRET) is None


# ---- LocalBackend + Manager ----
@pytest.fixture
def auth_file(tmp_path):
    p = tmp_path / "auth_users.json"
    data = {"users": {"alice": {"display": "Alice", "password": hash_password("Sommer2026!")}}}
    p.write_text(json.dumps(data))
    return str(p)


def test_backend_authenticate(auth_file):
    be = LocalBackend(auth_file)
    assert be.authenticate("alice", "Sommer2026!")
    assert not be.authenticate("alice", "falsch")
    assert not be.authenticate("unbekannt", "Sommer2026!")   # User-Enumeration vermieden


def test_backend_missing_file_fail_closed(tmp_path):
    be = LocalBackend(str(tmp_path / "gibt-es-nicht.json"))
    assert not be.authenticate("alice", "egal")


def test_manager_login_and_session(auth_file):
    mgr = AuthManager(LocalBackend(auth_file), SECRET, ttl_seconds=60)
    tok = mgr.login("alice", "Sommer2026!")
    assert tok and mgr.user_from_token(tok) == "alice"
    assert mgr.login("alice", "falsch") is None
    assert mgr.user_from_token("kaputt.token") is None
