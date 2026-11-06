# Architecture

TerraRF is one Docker container: a FastAPI backend that computes coverage with numba kernels, and a static
frontend (plain ES modules, Leaflet, no build step). This page is the map of the code.

## Backend (`app/`)

```
app/
  main.py          assembles the routers, starts background work (numba warm-up, NRW preload, CoreScope sync)
  paths.py         DATA_DIR, CACHE_DIR, DEM_DIR (all below RFP_DATA)
  version.py
  api/             HTTP layer only – parse, check permissions, call the modules below
    deps.py          ADMIN / MEMBER / READ dependencies, project lookup (visitors only see public projects)
    session.py       login, logout, /api/config (everything the frontend needs at start), /api/status
    projects.py      list / load / save / delete projects
    coverage.py      coverage, station data images, profile, elevation at a point
    network.py       CoreScope nodes, links, overrides, repeater↔repeater calibration
    measurements.py  MeshMapper ingest, list, series, manual/CSV, delete, drive-test comparison
    reports.py       key figures and PDF
    admin.py         instance config, global model settings, buildings, elevation downloads, cache
    catalog.py       antennas, devices, placements (read: everybody, write: admin)
    maps.py          tile proxy, data overlays, building footprints for the map
    site.py          index page, manifest, imprint, privacy policy
  model/           the physics
    terrain.py       elevation sources, the EPSG:3857 Grid, NRW WCS tile store, Copernicus, local GeoTIFFs
    propagation.py   numba kernels: radial rays, diffraction (P.526), obstacle loss (P.833), clutter (P.2108),
                     ground reflection, antenna pattern
    coverage.py      per-station computation with disk + RAM cache, data images, profile & link budget
                     – bump MODEL_VERSION on every change that alters results
    settings.py      the one global set of model settings (SPEC = default, type, limits, group, source)
    buildings.py     building footprints: import (GeoJSON, Overpass), storage, raster mask per grid
  analysis/        derived results
    compare.py       model vs. CoreScope SNR, model vs. drive tests, suggestion for the global model
    report.py        key figures and the PDF report
  sources/         external data
    corescope.py     MeshCore network from a CoreScope instance (read-only, cached)
    regions.py       offline downloads and the NRW preload
    tiles.py         map tile proxy with disk cache
    overlay.py       map layers rendered from local elevation data
  store/           what TerraRF keeps on disk
    projects.py      project files, owner and rights (#32), migration of old projects (settings dropped, antenna presets → catalog)
    measurements.py  SQLite with drive-test rows and series
    catalog.py       antennas / devices / placements, seeded on first start
    appconfig.py     instance settings edited in Administration
    accounts.py      collaborator logins that belong to one project
  site/            running the site
    auth.py          admin + optional user, signed session cookie
    legal.py         generated imprint and privacy policy (German)
    sysstat.py       what is computing, container load
```

### Data on disk (`RFP_DATA`, `/data` in the container)
| Path | Content |
|---|---|
| `projects/*.json` | projects (stations, view, public flag) |
| `settings.json` | global model settings (only values that differ from the defaults) |
| `catalog.json` | antenna, device and placement catalog |
| `appconfig.json` | access, website, legal, measurement key (mode 0600: contains the user password) |
| `accounts.json` | collaborator logins per project (password hashes only, mode 0600) |
| `measurements.db` | drive-test rows and series (SQLite) |
| `buildings/footprints.json` | building footprints |
| `corescope/` | cached CoreScope nodes, links, repeater overrides |
| `dem/<source>_<res>m/`, `dem/<name>/` | NRW tile store, own GeoTIFFs |
| `cache/` | coverage results, terrain grids, map tiles |

### How a coverage computation runs
1. `api/coverage.py` receives the project (members) or a project id (visitors) and the station ids.
2. `model/coverage.py` builds the station key from station parameters, global settings, `MODEL_VERSION` and the
   building footprint version. A hit in RAM or on disk is returned at once.
3. Otherwise it reads ground and surface for the grid (`model/terrain.py`, fallback source for holes), a fine 5 m
   near-field grid around the station and the building masks, then runs `propagation.coverage_rays`.
4. The result is stored and delivered as a small **data image** per station:
   R = level in dBm + 200, G = sight class | roof visible << 2.
5. The browser stacks the active stations itself (`static/js/map/coverage.js`), so ticking stations, switching the
   view and moving the margin slider need no server round trip.

## Frontend (`app/static/`)

```
index.html         the shell: top bar, rail, panel, map, inspector, dialogs (content is rendered by the modules)
style.css          layout; on phones (≤ 800 px) rail → bottom bar, panel and inspector → bottom sheets
locales/de.json    German translations; source strings in the code are English
js/
  main.js          start-up, login, project menu, map clicks, server status, footer
  core/            i18n (t(), data-i18n), util (formatting, geometry), api (fetch helpers),
                   state (shared state + tiny event bus), ui (workspaces, inspector, status, sections)
  map/             map.js (Leaflet, layers panel with registered sections, base maps, data overlays, buildings),
                   coverage.js (compositing the data images), stations.js (markers, presets, catalog antennas)
  network.js       CoreScope nodes and neighbour lines (lines are clickable → link inspector)
  measurements.js  drive-test points grouped into pings, measurement plan
  profile.js       terrain profile and link budget panel
  inspector.js     station · repeater · link · ping · location
  workspaces/      plan · network · measure · compare · report
  admin.js         administration dialog
```

**Events** (`core/state.js`): `project-loaded`, `project-changed`, `stations-changed`, `coverage-updated`,
`busy-changed`, `network-loaded`, `network-focus`, `neighbours-loaded`, `measurements-loaded`, `catalog-loaded`,
`config-reloaded`, `settings-changed`, `compare-station`, `workspace`. Modules render themselves when the events they
care about fire; nothing calls into a workspace directly except to open it.

**Translations:** write UI strings in English inside `t("…")` (placeholders `{name}`) and add the German text to
`locales/de.json`. `tests/test_i18n.py` fails when a literal string has no German entry or placeholders differ.
Static markup uses `data-i18n`, `data-i18n-title`, `data-i18n-placeholder`.

**Caching:** `index.html` (never cached) loads `/assets/<version>/js/main.js`. Modules import each other by relative
URL, so all of them – and `locales/` – live under the version path: every release has new URLs, the current version
is cached for good, and a browser or proxy can never mix modules of two versions. `/static/js/` keeps working with
`Cache-Control: no-cache`.
