# Drittanbieter

## Software
| Komponente | Lizenz | Verwendung |
|---|---|---|
| [Leaflet](https://leafletjs.com) 1.9.4 | BSD-2-Clause | Kartendarstellung (mitgeliefert in `app/static/vendor/leaflet`) |
| Inter (Schrift) | SIL Open Font License 1.1 | nur als Umrisse im Logo (`assets/logo*.svg`), erzeugt mit `tools/make_logo.py` |
| FastAPI, Uvicorn, NumPy, Numba, Rasterio (GDAL), httpx, Pillow | MIT / BSD / Apache-2.0 | Backend, über pip installiert |

## Daten (werden zur Laufzeit geladen, nicht mitgeliefert)
| Quelle | Lizenz |
|---|---|
| Geobasis NRW – DGM1, DOM1 (WCS), Luftbilder (WMS) | Datenlizenz Deutschland – Zero – Version 2.0 |
| Copernicus DEM GLO-30 (AWS Open Data) | Copernicus-Lizenz, frei nutzbar |
| OpenStreetMap-Kacheln | © OpenStreetMap-Mitwirkende, ODbL |
| OpenTopoMap | CC-BY-SA |
| Esri World Imagery | Esri-Nutzungsbedingungen |
