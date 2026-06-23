"""Quickstart von Null: richtet in einem Temp-Verzeichnis alles ein und prueft, dass
Configs + Logins entstehen und ein Login funktioniert. Ohne Ollama."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import quickstart
from auth import AuthManager, LocalBackend


def test_quickstart_from_zero(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    (cfg / "data").mkdir(parents=True)
    demo = tmp_path / "demo"
    monkeypatch.setattr(sys, "argv", [
        "quickstart.py", "--config-dir", str(cfg), "--demo-root", str(demo),
        "--password", "Demo1234!",
    ])
    assert quickstart.main() == 0
    data = cfg / "data"
    for f in ("sql_sources.json", "ingest_sources.json", "policy.json",
              "users.json", "auth_users.json"):
        assert (data / f).exists(), f"fehlt: {f}"
    # Login funktioniert, kein Klartext, User konsistent.
    mgr = AuthManager(LocalBackend(str(data / "auth_users.json")), b"x" * 32, 60)
    tok = mgr.login("hr_head", "Demo1234!")
    assert tok and mgr.user_from_token(tok) == "hr_head"
    auth = json.load(open(data / "auth_users.json"))
    assert "Demo1234!" not in json.dumps(auth)
    assert set(json.load(open(data / "users.json"))["users"]) == set(auth["users"])
