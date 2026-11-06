# CLAUDE.md – Arbeitsanleitung für dieses Repository

TerraRF: selbst gehosteter Hub rund um Funkempfang im Mesh – planen (Abdeckung, Sichtlinie, Linkbudget mit
NRW-Höhendaten), das echte Netz ansehen (CoreScope), messen (MeshMapper, CSV, von Hand) und Modell und Messung
abgleichen. Läuft als ein Docker-Container (FastAPI + statisches Leaflet-Frontend), Zielplattform u. a. Unraid.

## Aufbau
Ausführlich in `docs/architecture.md`. Kurz:
- `app/api/` – nur HTTP: Parameter prüfen, Rechte (`dependencies=ADMIN | MEMBER | READ` aus `api/deps.py`), Module aufrufen.
  Schreibende Endpunkte immer mit `ADMIN` (oder `MEMBER`, wenn der Benutzer es darf).
- `app/model/` – Physik. `terrain.py` (Höhenquellen, alles auf einem Raster in EPSG:3857 `Grid`; NRW-WCS-Quellen lesen
  über den Kachelspeicher `data/dem/<quelle>_<res>m/`), `propagation.py` (numba-Kernels: Beugung P.526, Hindernisse
  P.833, Clutter P.2108, Bodenreflexion, Antennendiagramm), `coverage.py` (Rechnung je Station mit Cache, Datenbilder,
  Profil), `settings.py` (die **globalen** Modell-Einstellungen), `buildings.py` (Gebäudegrundrisse).
  **Bei jeder Modelländerung `MODEL_VERSION` in `coverage.py` erhöhen**, sonst liefern Caches alte Werte.
- `app/analysis/` – Abgleich Modell ↔ Messung (`compare.py`), Kennzahlen und PDF (`report.py`).
- `app/sources/` – CoreScope, Downloads/Vorladen, Kachel-Proxy, Kartenebenen.
- `app/store/` – Projekte (inkl. Migration alter Dateien), Messungen (SQLite), Katalog, Instanz-Einstellungen.
- `app/site/` – Login (ein Admin aus Env, optional ein Benutzer), Impressum/Datenschutz, Serverstatus.
- `app/static/` – Frontend, Vanilla-JS-ES-Module ohne Build-Tools: `js/core/`, `js/map/`, `js/workspaces/`,
  `inspector.js`, `admin.js`, `main.js`. Jede Station kommt als Datenbild (`/api/station/<key>/<factor>.png`:
  R = dBm+200, G = Sichtklasse | Dachsicht<<2); das Zusammensetzen passiert im Browser (`js/map/coverage.js`).
- `templates/terrarf.xml`, `ca_profile.xml` – Unraid / Community Applications.

## Regeln
- Oberfläche zweisprachig: Texte im Code englisch in `t("…")`, die deutsche Fassung in `app/static/locales/de.json`
  (`tests/test_i18n.py` prüft das). README und `docs/` englisch, Code, Kommentare und Bezeichner englisch.
  CHANGELOG, Impressum/Datenschutz und der PDF-Bericht bleiben vorerst deutsch.
- Physikalische Modelle nur mit Quelle (ITU-R-Empfehlung o. ä.) im Docstring und in README → „Model“.
  Keine erfundenen Konstanten. Neue Standardwerte im README begründen; Katalogwerte ohne Datenblatt als Annahme kennzeichnen.
- Modell-Einstellungen sind global (Admin). Neue Einstellung: Eintrag in `model/settings.SPEC` (mit Quelle), in
  `coverage._station_key` aufnehmen, falls sie das Ergebnis beeinflusst, Beschriftung in `SETTING_LABELS` in
  `static/js/admin.js` und deutsch in `locales/de.json`. Die Admin-Seite baut sich aus `SPEC` selbst.
- Kataloge (Antennen, Geräte, Trageorte) pflegt nur der Admin; Felder in `store/catalog.FIELDS`.
- Tests: `python -m pytest -q` muss grün sein. Tests dürfen kein Netzwerk benutzen
  (synthetisches Gelände aus `tests/conftest.py`, ggf. `tools/mock_wcs.py`).
- Frontend nach Änderungen: `node --check` für jede geänderte Datei unter `app/static/js/`.
- Keine Secrets, keine Telemetrie, keine externen Dienste außer den dokumentierten Datenquellen.
- `.github/workflows` nicht ändern, außer das Issue verlangt es ausdrücklich und der Owner hat es freigegeben.
- Nutzer-sichtbare Änderungen in `CHANGELOG.md` unter „Unreleased“ eintragen.

## Lokal starten
```bash
pip install -r requirements-dev.txt
RFP_DATA=./data RFP_PRELOAD= uvicorn app.main:app --reload --port 8080
```
