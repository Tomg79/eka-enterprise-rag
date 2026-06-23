<!--
  Entwurf fuer das README des Master-Thesis-Repos (Disease Forecasting).
  Basiert auf dem CV, nicht auf dem echten Code (darauf habe ich keinen Zugriff).
  Platzhalter < ... > bitte anhand des Repos fuellen. Ordnerbaum/Ergebnisse ergaenzen.
-->

# Spatio-Temporal Disease Forecasting

Master's thesis project at TH Wildau. The goal is to forecast disease incidence across
regions and over time using a custom spatio-temporal Diffusion Transformer, and to ship the
model as a deployable service rather than a notebook.

This is a follow-up to my earlier work on Leishmania classification (WIKKI26 poster, 2026).
That project classified cases from data; this one moves from classification to forecasting
how incidence develops over space and time.

## What it does

- A custom spatio-temporal Diffusion Transformer for incidence forecasting.
- A full pipeline from training to serving, not just a model file.
- Inference exposed as a REST API with FastAPI.
- Packaged as a Docker image.
- Deployed on AWS, with the image in ECR and model artifacts kept in S3 (decoupled from the image).
- Build and deployment automated with GitHub Actions (CI/CD).

## Tech stack

Python, PyTorch, FastAPI, Docker, AWS (ECR, S3), GitHub Actions.

## Repository layout

<kurz auflisten, z. B.:>
- `training/` <Training und Modelldefinition>
- `api/` <FastAPI-Service>
- `infra/` <Docker, GitHub-Actions-Workflow>
- `data/` <Datenaufbereitung>

## How to run

<Kurzanleitung: Training starten, API lokal starten (docker run ...), Beispiel-Request>

## Results

<Metriken eintragen, z. B. Forecast-Fehler (MAE/RMSE), Vergleich gegen Baseline,
Zeitraum/Region. Zahlen schlagen Beschreibungen.>

## Related work

- Leishmania classification (WIKKI26 poster, 2026) <Link, falls eigenes Repo>
- <ggf. Link zur Thesis-PDF, sobald veroeffentlicht>

## License

<MIT / PolyForm Noncommercial, je nachdem was du moechtest>
