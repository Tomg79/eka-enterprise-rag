# Quickstart -- von Null zum Test

Stell dir vor, du bist die Firma und hast nichts. Diese eine Datei richtet alles ein,
was ohne das lokale LLM moeglich ist, und sagt dir die letzten 2 Schritte.

## 1) Einrichten (ein Befehl)
```powershell
# Windows
powershell -ExecutionPolicy Bypass -File quickstart.ps1
# oder plattformneutral:
python quickstart.py
```
Das erzeugt eine komplette **Demo-Firma** ("ACME"), erkennt automatisch alle Quellen
(Dateifreigaben, 3 DBs, S3-Seed, APIs, Rollen), schreibt die App-Konfiguration und legt
**Logins fuer alle Mitarbeiter** an (Demo-Passwort: `Demo1234!`).

Eigene Firmendaten statt Demo:
```powershell
python quickstart.py --data-root D:\Firma
```

## 2) Lokales LLM bereitstellen (einmalig)
```
ollama pull qwen2.5:14b
```

## 3) Starten mit Login
```powershell
$env:AUTH_ENABLED="true"      # PowerShell  (CMD: set AUTH_ENABLED=true)
python -m streamlit run app.py
# oder die gepackte dist\EKA\EKA.exe
```
Dann in der Sidebar **"Ingest Docs"** klicken (indexiert ALLE erkannten Quellen).

## 4) Als verschiedene Benutzer testen
Anmelden (Passwort `Demo1234!`), z.B.:

| Login | Rolle | Erwartung |
|---|---|---|
| `ceo` | Geschaeftsfuehrung | sieht (fast) alles |
| `hr_head` | Personal | sieht Gehaelter/HR, kein CRM |
| `sales_bob` | Vertrieb | sieht CRM/Auftraege, keine Gehaelter |
| `legal_max` | Recht | sieht Vertraege |
| `intern_ida` | Praktikant | sieht nur Oeffentliches |
| `sales_alice` | Vertrieb + Phoenix | sieht zusaetzlich Projekt Phoenix |

Probe: als `legal_max` nach einem Vertrag fragen (Treffer) und als `intern_ida`
dieselbe Frage (verweigert). Der **Audit-Log**-Tab zeigt jede allow/deny-Entscheidung.

## Aufraeumen / zurueck zur GlobalCorp-Demo
`quickstart` sichert ersetzte Dateien als `data\*.bak`. Diese zuruueckkopieren, um den
vorherigen Stand wiederherzustellen.
