# Sicherheits-Review & Pentest-Vorbereitung (Phase 7)

Dieses Dokument fasst das Sicherheitsmodell des EKA Enterprise-RAG zusammen und dient
als Grundlage fuer ein externes Review/Pentest, BEVOR echte Unternehmensdaten laufen.

## 1. Nicht verhandelbare Garantien (gelten in jeder Phase)
- **Fail-closed:** Bei Unsicherheit ueber Berechtigungen -> kein Zugriff. Lade-/
  Konfigurationsfehler, unbekannter User, defekte ACL, unbekannte Tabelle -> leere
  Rechte statt offener Tueren.
- **Deny-by-default:** Was nicht ausdruecklich erlaubt ist, ist verboten.
- **Rechteentzug wirkt sofort:** Die Policy wird pro Anfrage frisch ausgewertet
  (kein anfrageuebergreifender Cache) -> Aenderungen greifen ab der naechsten Frage,
  ohne Reindex.
- **Keine Daten verlassen das Netz:** LLM (Ollama) und Embeddings (bge-m3) laufen lokal;
  Backend-Dienste (Qdrant, Ollama) sind im Compose-Setup nur intern erreichbar;
  Audit-Log bleibt lokal.

## 2. Durchsetzungspunkte (wo Sicherheit entschieden wird)
| Bereich | Datei | Mechanismus |
|---|---|---|
| Berechtigungen (zentral) | `policy.py` | EINZIGE Entscheidungsstelle. Rollen-Vererbung, Union, deny-by-default, fail-closed bei Ladefehler. |
| Dokument-Retrieval (DMS) | `query.py` `_dms_filter_for_groups` | Qdrant-Payload-Filter auf `acl_groups`; leere Gruppen -> DENY_ALL_TAG (matcht nichts). |
| Strukturierte Daten (SQL) | `connectors/sql.py` + `query.py` | Whitelist-Identifier, parametrisierte Werte, kein frei generiertes SQL; Tool wird nur fuer berechtigte Tabellen registriert + Re-Check pro Aufruf. |
| CRM (Salesforce) | `query.py` `fetch_salesforce_data` | Scope ueber PolicyEngine (none/all/list), pro-Kunde-Check. |
| Datei-/Objekt-ACL | `connectors/acl_readers.py`, `connectors/s3.py` | ACL aus der Quelle (Sidecar/NTFS/Prefix); defekt -> []. |
| Nachvollziehbarkeit | `audit.py` | Append-only JSONL: pro Anfrage User, Rollen, Tool-Events (allow/deny + Quellen). |

## 3. Bekannte Schutzmassnahmen je Angriffsklasse
- **SQL-Injection:** Tabellen-/Spaltennamen ausschliesslich aus der Whitelist
  (`data/sql_sources.json`) + striktes Identifier-Muster; Werte nur als gebundene
  Parameter. Frei vom LLM erzeugtes SQL ist bewusst NICHT moeglich. (Regressionstests:
  `tests/test_sql_connector_security.py`.)
- **Prompt-Injection / Halluzination:** System-Prompt zwingt den Agenten, nur Tool-
  Ausgaben zu verwenden. Selbst bei manipulativem Prompt kann das Modell nur Daten
  sehen, die die Tools (nach RBAC-Filter) zurueckgeben -- es gibt keinen Tool-Pfad an
  der Policy vorbei.
- **Privilege Escalation:** Rechte = Union der (geerbten) Rollen; keine Rolle kann
  mehr als ihre Definition. Unbekannte/zyklische Rollen werden ignoriert (kein Recht).
- **Datenabfluss:** keine ausgehenden Aufrufe an Cloud-LLMs; S3-Credentials nur aus
  der Umgebung, nie im Code/Image/Log.

## 4. Restrisiken / offene Punkte (vor Produktivbetrieb klaeren)
- **Live-Tests mit echtem LLM stehen aus** fuer Phase 2/3/4/5 (in dieser Umgebung kein
  Ollama). Logik ist statisch + mit Mocks verifiziert; End-to-End beim Kunden testen.
- **Windows-NTFS-ACL-Leser** (`WindowsAclReader`) ist nur auf Windows lauffaehig und
  noch nicht gegen ein echtes AD getestet (Brief verlangt Test-AD/-Freigabe).
- **Filter-Modus des SQL-Connectors** (`filter_rows`) ist implementiert/getestet, aber
  noch nicht als Agenten-Tool freigeschaltet -- bei Aktivierung erneut reviewen.
- **Audit-Log-Integritaet:** aktuell append-only Datei; fuer Manipulationssicherheit
  ggf. WORM-Storage/Signaturen ergaenzen.
- **Authentifizierung der Nutzer:** die App vertraut der User-Auswahl; in Produktion
  muss ein echtes Login/SSO davorgeschaltet werden (derzeit Scope-Grenze v1).
- **Transport/Netz:** wer Ports oeffnet, braucht Reverse-Proxy + TLS + AuthN.

## 5. Pentest-Checkliste
1. RBAC-Matrix gegen alle Nutzer/Rollen durchspielen (auch Negativfaelle): erwartete
   vs. tatsaechlich sichtbare DMS-Tags, SQL-Tabellen, CRM-Scope.
2. Sofortigen Rechteentzug live pruefen: Rolle in `policy.json` entfernen -> naechste
   Frage muss verweigern (ohne Reindex/Neustart).
3. SQL-Injection-Strings ueber die Chat-Oberflaeche in Lookups/Filter einschleusen.
4. Prompt-Injection: Dokument/Meeting mit Anweisungen praeparieren, das den Agenten zum
   Umgehen der Tools bewegen soll.
5. ACL-Manipulation: Sidecar entfernen/zerstoeren -> Dokument darf nicht sichtbar werden.
6. Netzwerk: pruefen, dass Qdrant/Ollama nicht von aussen erreichbar sind.
7. Secrets-Scan: Repo/Image auf AWS-Keys o.ae. pruefen (`deploy/.env` nicht committed).

## 6. Automatisierte Security-Regression
`tests/` enthaelt eine ohne Ollama lauffaehige Suite (PolicyEngine fail-closed,
SQL-Whitelist/Injection, ACL fail-closed). Vor jedem Release ausfuehren:
```bash
python -m pytest tests/ -q
```
