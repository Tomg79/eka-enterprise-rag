"""
tools/manage_auth.py -- Login-Konten verwalten (Passwoerter setzen).

Schreibt data/auth_users.json (scrypt-Hashes, NIE Klartext). Display-Namen werden aus
data/users.json uebernommen, damit Auth (wer kann sich anmelden) und Policy (welche
Rollen) konsistent bleiben.

Beispiele:
  python tools/manage_auth.py list
  python tools/manage_auth.py set-password alice            # fragt Passwort interaktiv
  python tools/manage_auth.py set-password alice --password Geheim123
  python tools/manage_auth.py seed --password Demo123!      # setzt PW fuer ALLE users.json-User (Demo)
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import AUTH_USERS_FILE, USERS_FILE
from auth import hash_password


def _load(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"users": {}}


def _save(path: str, data: dict):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _display_for(uid: str) -> str:
    try:
        users = json.load(open(USERS_FILE, encoding="utf-8")).get("users", {})
        return (users.get(uid) or {}).get("display", uid)
    except Exception:
        return uid


def cmd_list(_args):
    data = _load(AUTH_USERS_FILE)
    users = data.get("users", {})
    if not users:
        print("Keine Login-Konten. Mit 'set-password'/'seed' anlegen.")
        return
    for uid, u in sorted(users.items()):
        print(f"  {uid:20s} {u.get('display', uid)}")


def cmd_set_password(args):
    pw = args.password or getpass.getpass(f"Neues Passwort fuer {args.user}: ")
    if len(pw) < 8:
        print("FEHLER: Passwort muss mindestens 8 Zeichen haben.", file=sys.stderr)
        sys.exit(2)
    data = _load(AUTH_USERS_FILE)
    data.setdefault("users", {})[args.user] = {
        "display": _display_for(args.user), "password": hash_password(pw)}
    _save(AUTH_USERS_FILE, data)
    print(f"Passwort fuer '{args.user}' gesetzt.")


def cmd_seed(args):
    if len(args.password) < 8:
        print("FEHLER: Passwort muss mindestens 8 Zeichen haben.", file=sys.stderr)
        sys.exit(2)
    try:
        policy_users = json.load(open(USERS_FILE, encoding="utf-8")).get("users", {})
    except Exception as e:
        print(f"FEHLER: users.json nicht lesbar ({e}).", file=sys.stderr)
        sys.exit(2)
    data = _load(AUTH_USERS_FILE)
    data.setdefault("users", {})
    for uid, u in policy_users.items():
        data["users"][uid] = {"display": (u or {}).get("display", uid),
                              "password": hash_password(args.password)}
    _save(AUTH_USERS_FILE, data)
    print(f"{len(policy_users)} Login-Konten angelegt (gleiches Demo-Passwort). "
          f"Fuer Produktion einzeln aendern!")


def main():
    ap = argparse.ArgumentParser(description="Login-Konten verwalten.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(func=cmd_list)
    sp = sub.add_parser("set-password"); sp.add_argument("user"); sp.add_argument("--password"); sp.set_defaults(func=cmd_set_password)
    se = sub.add_parser("seed"); se.add_argument("--password", required=True); se.set_defaults(func=cmd_seed)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
