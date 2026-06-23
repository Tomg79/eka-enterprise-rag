# Windows-.exe / Auslieferung

Macht aus dem Stack ein "Doppelklick"-Produkt. `launcher.py` ist der Einstieg; PyInstaller
verpackt ihn samt App-Code in `dist\EKA\` mit `EKA.exe`.

## Bauen (auf einem Windows-Rechner, im Projekt-Root)
```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
```
Ergebnis: `dist\EKA\EKA.exe` (+ Abhaengigkeiten im selben Ordner). Diesen Ordner auf den
USB-Stick kopieren.

## Nutzung (das "Stick rein"-Szenario)
```
EKA.exe --data-root D:\Firma     # erkennt Quellen im Ordner, richtet ein, startet, oeffnet Browser
EKA.exe                          # nur starten (vorhandene Konfiguration)
EKA.exe --onboard-only --data-root D:\Firma   # nur einrichten
```
Login pro Benutzer: vorher `AUTH_ENABLED=true` setzen und Konten anlegen
(`python tools\manage_auth.py seed --password ...`), siehe AUTH.md.

## Was NICHT im .exe steckt (bewusst)
- **Ollama + LLM-Modell** (mehrere GB): einmalig auf dem Zielrechner bereitstellen
  (`ollama pull qwen2.5:14b`) oder den Docker-Stack aus `deploy/` nutzen.
- **bge-m3-Embeddings**: werden beim ersten Lauf in den HF-Cache geladen (danach offline).
Fuer voll abgeschottete Umgebungen: Docker-Appliance aus `deploy/` (Qdrant+Ollama+App)
ist die robustere Auslieferung; die .exe ist der Komfort-Wrapper fuer Einzelplatz/Pilot.

## Code-Signing (vor Verteilung dringend empfohlen)
Ungesignierte .exe loest SmartScreen-Warnungen aus. Mit einem (idealerweise EV-)
Code-Signing-Zertifikat:
```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 ^
  /a dist\EKA\EKA.exe
signtool verify /pa dist\EKA\EKA.exe
```
EV-Zertifikate (Hardware-Token) bauen sofortige SmartScreen-Reputation auf; OV-Zertifikate
brauchen Reputations-Aufbau. Optional die ganze Auslieferung zusaetzlich als signiertes
Installer-Paket (Inno Setup / WiX) buendeln.

## Hinweise
- Erststart dauert (Embeddings-Download, Index-Aufbau). Konsolenfenster zeigt den Fortschritt.
- Antivirus/Allowlisting: PyInstaller-Binaries gelegentlich falsch-positiv -> signieren +
  ggf. beim Kunden allowlisten.
