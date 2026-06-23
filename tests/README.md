# Security-Regressionstests

Ohne Ollama/Embeddings lauffaehig. Decken die sicherheitskritische Logik ab:
PolicyEngine (deny-by-default, fail-closed, Vererbung), generischer SQL-Connector
(Whitelist, Parametrisierung/Injection, Filter-Deckel) und die ACL-Leser (fail-closed).

```bash
pip install pytest boto3 "moto[s3]" sqlalchemy
python -m pytest tests/ -q
```

Hinweise:
- Die SQL-Tests lesen die Whitelist aus `data/sql_sources.json`. Soll eine alternative
  Quelle/DB genutzt werden, `EKA_TEST_SQL_SOURCES` auf eine andere Sources-Datei zeigen.
- Der S3-Test nutzt `moto` (Mock-S3) und wird uebersprungen, falls boto3/moto fehlen.
- Voraussetzung fuer die SQL-Tests: `data/sap_legacy.db` und `data/erp_sales.db`
  (Letztere via `python generate_erp_sales.py` erzeugen).
```
