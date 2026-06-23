# Authentifizierung (Login) -- Phase 8

Ersetzt die bisherige User-Dropdown-Simulation durch echtes Login. Klare Trennung:
**auth.py beweist die Identitaet** (wer), **policy.py entscheidet die Rechte** (was).
Die App vertraut nur einer auth-bestaetigten `user_id`.

## Aktivieren
1. Login-Konten anlegen (Passwoerter werden scrypt-gehasht, nie im Klartext gespeichert):
   ```bash
   # Demo: gleiches Passwort fuer alle User aus data/users.json
   python tools/manage_auth.py seed --password "Demo1234!"
   # oder einzeln (interaktiv):
   python tools/manage_auth.py set-password alice
   python tools/manage_auth.py list
   ```
2. Login einschalten und App starten:
   ```bash
   set AUTH_ENABLED=true            # Windows (cmd);  PowerShell: $env:AUTH_ENABLED="true"
   python -m streamlit run app.py
   ```
   Im Docker-Compose: `AUTH_ENABLED=true` in `deploy/.env`.

Mit `AUTH_ENABLED=false` (Default) bleibt alles wie gehabt (Dropdown) -- praktisch fuer
Demos/Tests.

## Wie es funktioniert
- **Passwoerter:** `scrypt` (stdlib, N=2^14) + zufaelliger Salt; Pruefung constant-time.
  Datei `data/auth_users.json` (0600, gitignored).
- **Sessions:** HMAC-SHA256-signiertes Token mit Ablauf (`AUTH_SESSION_TTL_MIN`, Default
  8h). Secret in `data/.auth_secret` (auto-erzeugt, 0600) oder via Env `EKA_AUTH_SECRET`
  (empfohlen im Container). Manipuliertes/abgelaufenes/fremd signiertes Token -> kein Login.
- **fail-closed:** fehlt/defekt `auth_users.json` -> niemand kommt rein. Unbekannte User
  durchlaufen denselben Hash-Aufwand (kein Timing-Leak / keine User-Enumeration).

## Unternehmens-SSO (AD/OIDC) andocken
`auth.py` definiert das Interface `AuthBackend`. Fuer echtes SSO ein `OIDCBackend`
(Keycloak/Azure AD, Authorization-Code-Flow) oder `LDAPBackend` (Bind gegen AD,
Gruppen->Rollen-Mapping) ergaenzen -- es muss am Ende nur eine bestaetigte `user_id`
liefern. Die App und die gesamte Autorisierung (policy.py) bleiben unveraendert.
Das `user_id` muss zu den Keys in `data/users.json` passen (dort haengen die Rollen).

## Tests
`tests/test_auth.py` (Teil von `python -m pytest tests/ -q`): Hashing-Roundtrip, kein
Klartext, Token-Ablauf/Manipulation/fremder Secret, Backend-Login inkl. fail-closed.
