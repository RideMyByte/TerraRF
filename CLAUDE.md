# CLAUDE.md – Arbeitsanleitung für dieses Repository

TerraRF: selbst gehostete Funkplanung (Abdeckung, Sichtlinie, Linkbudget) mit NRW-Höhendaten.
Läuft als ein Docker-Container (FastAPI + statisches Leaflet-Frontend), Zielplattform u. a. Unraid.

## Aufbau
- `app/terrain.py` – Höhenquellen. Alles rechnet auf einem Raster in EPSG:3857 (`Grid`).
  NRW-WCS-Quellen (`nrw_dgm`, `nrw_dom`) lesen ausschließlich über den lokalen Kachelspeicher
  `data/dem/<quelle>_<res>m/` (10-km-Kacheln, EPSG:25832); fehlende Kacheln werden nachgeladen.
- `app/propagation.py` – numba-Kernels: radiale Strahlen, Beugung (Deygout/Bullington, ITU-R P.526),
  Terminal-Clutter (P.2108 §3.1), Hindernisdämpfung (P.833), Bodenreflexion, Antennendiagramm.
- `app/coverage.py` – Orchestrierung pro Station (Cache auf Platte + RAM), Composite-Layer, PNGs,
  Inspect und Profil. **Bei jeder Modelländerung `MODEL_VERSION` erhöhen**, sonst liefern Caches alte Werte.
- `app/regions.py` – Download-Jobs und Vorladen von NRW beim Start.
- `app/main.py` – API. `app/static/` – Frontend (Vanilla JS, keine Build-Tools).
- `templates/terrarf.xml`, `ca_profile.xml` – Unraid / Community Applications.

## Regeln
- Oberfläche und Doku auf Deutsch; Code-Kommentare und Bezeichner englisch.
- Physikalische Modelle nur mit Quelle (ITU-R-Empfehlung o. ä.) im Docstring und in README → „Modell“.
  Keine erfundenen Konstanten. Neue Standardwerte im README begründen.
- Tests: `python -m pytest -q` muss grün sein. Tests dürfen kein Netzwerk benutzen
  (synthetisches Gelände aus `tests/conftest.py`, ggf. `tools/mock_wcs.py`).
- Frontend nach Änderungen: `node --check app/static/app.js`.
- Neue Einstellungen: Default in `coverage.DEFAULT_SETTINGS`, in `_station_key` aufnehmen, falls sie das
  Ergebnis beeinflussen, UI in `index.html` + `app.js` (`renderSettings`/`bindSettings`).
- Keine Secrets, keine Telemetrie, keine externen Dienste außer den dokumentierten Datenquellen.
- `.github/workflows` nicht ändern, außer das Issue verlangt es ausdrücklich und der Owner hat es freigegeben.
- Nutzer-sichtbare Änderungen in `CHANGELOG.md` unter „Unreleased“ eintragen.

## Lokal starten
```bash
pip install -r requirements-dev.txt
RFP_DATA=./data RFP_PRELOAD= uvicorn app.main:app --reload --port 8080
```
