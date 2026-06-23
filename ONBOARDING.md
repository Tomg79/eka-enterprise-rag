# Onboarding & Test-Umgebung ("USB-Stick"-Szenario)

Dieses Dokument beschreibt, wie aus einem rohen Quellordner (Dateifreigaben, DBs,
Cloud-Seeds, APIs, Identity) automatisch ein einsatzbereites RAG-Deployment wird --
und wie man das Ganze mit einer realistisch komplexen Test-Firma ausprobiert.

## Das Bild
```
[USB / Server-Mount: Firmendaten]
        │
   1) DISCOVER   onboarding/discovery.py   -> erkennt alle Quellen, baut landscape.json,
        │                                     meldet Sicherheits-Auffaelligkeiten
   2) BOOTSTRAP  onboarding/bootstrap.py   -> schreibt data/sql_sources.json, uebernimmt
        │                                     policy/users, erzeugt deploy/.env
   3) DEPLOY     deploy/install.(ps1|sh)   -> Docker-Stack (Qdrant+Ollama+App)
        │
   4) INGEST + Browser  http://localhost:8501
```
Der Vertrag zwischen allen Schritten ist **landscape.json** (`onboarding/landscape.py`).

## Wichtige Designentscheidung (Sicherheit)
"Greift automatisch auf ALLES zu" gibt es bewusst nicht -- das wuerde keinen Konzern-
Security-Review bestehen. Stattdessen: das Tool **erkennt** Quellen und schlaegt eine
Konfiguration vor; Zugangsdaten (S3 etc.) gibt ein Admin **explizit** als Env frei,
niemals im Code/Repo. Alles bleibt fail-closed.

## A) Komplexe Test-Firma erzeugen
```bash
python tools/generate_enterprise_landscape.py --root /pfad/zu/ACME
```
Erzeugt "ACME Industriewerke GmbH": verschachtelte Abteilungsfreigaben mit per-Datei-
ACLs, drei SQLite-DBs (hr/crm/erp), einen S3/MinIO-Seed, Salesforce-artige JSON-APIs
und eine Identity (Rollen/User). **Eingebaute Macken**, die das Tool sicher behandeln
muss:
- kaputter ACL-Sidecar (Finance/Q2-Forecast) -> mit sidecar-Reader unsichtbar,
- Datei ohne ACL (HR/salary_review_notes) -> Prefix-Fallback wuerde sie PUBLIC machen,
- per-Datei-ACL schlaegt Abteilung (Sales/project_phoenix),
- verwaiste ACL-Gruppe (MARKETING_TYPO) -> fuer niemanden sichtbar,
- Rolle 'marketing' mit ungenutzter/typo Gruppe,
- User 'ghost_gwen' mit unbekannter Rolle -> keine Rechte.

## B) Onboarding ausfuehren
Trockenlauf (nur Report + Vorschau, schreibt nichts):
```bash
python onboarding/bootstrap.py --root /pfad/zu/ACME
```
Anwenden (schreibt data/sql_sources.json, policy.json, users.json + deploy/.env;
vorhandene Dateien werden als *.bak gesichert):
```bash
python onboarding/bootstrap.py --root /pfad/zu/ACME --apply
```
Der Discovery-Report listet alle erkannten Quellen UND die Sicherheits-Befunde oben.

## C) Starten & nutzen
```bash
bash deploy/install.sh      # bzw. deploy\install.ps1 (Windows)
# danach Daten indexieren (App-Oberflaeche) und oeffnen:
# http://localhost:8501
```

## Grenzen / ehrliche Hinweise
- **End-to-End mit echtem LLM** (Ollama) muss lokal laufen -- in der Build-/CI-Umgebung
  ist kein Ollama vorhanden; verifiziert ist die gesamte Kette statisch + gegen die
  generierten DBs (siehe tests/).
- Der Object-Store wird als **lokaler Seed** erkannt; fuer echtes S3/MinIO `endpoint_url`
  + Credentials in `.env` setzen und den Seed hochladen.
- Discovery introspektiert aktuell **SQLite** (Schema/Lookup-Spalte/Sample) vollautomatisch;
  fuer Postgres/MySQL den Connection-String im Manifest setzen (Connector kann es bereits).

## Wiederholbare Verifikation
```bash
python -m pytest tests/ -q
```
Deckt ab: Discovery findet alle Quellen, meldet die Macken; Bootstrap-Config treibt echte
SQL-Lookups (inkl. Injection-Block); uebernommene Identity setzt Rechte/Vererbung/fail-closed.

## Multi-Quellen-Ingest (alle Quellen, nicht nur data/docs)
`bootstrap --apply` schreibt zusaetzlich `data/ingest_sources.json` (alle erkannten
Dateifreigaben + Object-Stores). Der "Ingest Docs"-Button (bzw. `ingest.ingest_all()`)
indexiert dann JEDE Freigabe mit ihrem ACL-Leser und den S3-/Seed-Inhalt in einen
gemeinsamen Index -- jeder Chunk traegt die per-Datei/-Objekt-ACL. Ohne Manifest faellt
der Ingest auf `data/docs` zurueck (bestehendes Verhalten, GlobalCorp unveraendert).
So wird aus "Ordner waehlen" tatsaechlich "alle Dokumentquellen werden indexiert".
