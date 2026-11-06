<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <img src="assets/logo.svg" alt="TerraRF – radio coverage over real terrain" width="460">
  </picture>
</p>

# TerraRF

**A self-hosted hub for everything about radio reception in a mesh network:** plan stations over real terrain,
look at the real network, measure in the field, and compare the model with reality – in one map, in the browser.

Made for **MeshCore, Meshtastic, LoRa, HAMNET and amateur radio**, with particularly accurate elevation data for
**North Rhine-Westphalia** (Geobasis NRW, 1 m laser scan, stored locally at 5 m) and Copernicus GLO-30 worldwide.
The interface speaks **German and English** (switch in the top bar).

[![CI](https://github.com/RideMyByte/TerraRF/actions/workflows/ci.yml/badge.svg)](https://github.com/RideMyByte/TerraRF/actions/workflows/ci.yml)
[![Image](https://github.com/RideMyByte/TerraRF/actions/workflows/docker.yml/badge.svg)](https://github.com/RideMyByte/TerraRF/pkgs/container/terrarf)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)

## What it does

TerraRF is organised in five workspaces (the rail on the left; a bottom bar on phones). They share one map, and
whatever you tap on the map – a station, a repeater, a link, a measurement, a spot – opens in the **inspector**
on the right.

| Workspace | Question it answers | What you do there |
|---|---|---|
| **Plan** | *Where would a station reach?* | Place stations, set radio and antenna (from a catalog), compute coverage, overlap, best server and line of sight |
| **Network** | *What does the real network look like?* | Repeaters from [CoreScope](https://github.com/Kpa-clawbot/CoreScope), neighbours of a repeater, link details with SNR, counts and last contact |
| **Measure** | *What did we actually receive?* | Drive tests from MeshMapper, CSV or manual entries; series with device and placement (car roof, jacket pocket, bike …) |
| **Compare** | *How good is the model here?* | Model vs. repeater↔repeater SNR and vs. drive tests, per source, with a suggestion for the global model |
| **Report** | *What do I hand over?* | Key figures per station and a PDF report (map, figures, model, comparison) |

Further:
- Terrain profile with Fresnel zone and full link budget for any path (station → point, station → station,
  repeater link), optionally with a different receiver.
- Building footprints (GeoJSON or OpenStreetMap) mark which parts of the surface model are buildings.
- Map layers from the data the model computes with: ground (with hillshade), surface, obstacle height.
- Projects: save, share by link, export/import JSON; public projects are visible to visitors read-only.
- Works on phones and installs as an app (home screen, no browser bar).

## Roles

| Role | Can |
|---|---|
| **Admin** (`RFP_ADMIN_USER`) | everything: all projects, **global model settings**, **catalogs**, data sources, website & legal pages |
| **User** (optional, set up in Administration) | create **own projects** (private or public) and change only those; see public projects and save a copy of them; enter measurements and edit series |
| **Visitor** | view projects marked *public* (if `RFP_PUBLIC_READ`), measurements only if the admin allows it |

A project – public or not – is changed only by whoever created it and by the admin. Projects from before 0.5.1
belong to the admin.

## Installation

### Unraid
**Community Applications:** Apps → search “terrarf” → Install *(once the listing is approved)*.

**Manually until then:** Docker → *Add Container* → enter the template URL
`https://raw.githubusercontent.com/RideMyByte/TerraRF/main/templates/terrarf.xml`,
or copy the file to `/boot/config/plugins/dockerMan/templates-user/my-terrarf.xml`. Then open the WebUI (port 8080).

On first start the container loads the NRW elevation data into `appdata/terrarf` (roughly 3–5 GB per model at 5 m,
a few hours depending on the NRW servers; resumable). To switch this off, empty `RFP_PRELOAD` – data is then loaded
on demand.

### Docker / Docker Compose
```bash
docker run -d --name terrarf -p 8080:8080 -v ./data:/data ghcr.io/ridemybyte/terrarf:latest
```
or `docker compose up -d` with the included `docker-compose.yml`.

New versions are published as `:latest` (Unraid shows “update available”); `:edge` follows `main`.
If TerraRF is reachable from the internet, put it behind a reverse proxy with HTTPS (the session cookie is then
`Secure`; MeshMapper and app installation require HTTPS anyway).

### Configuration (environment variables)
| Variable | Default | Meaning |
|---|---|---|
| `RFP_ADMIN_USER` | `admin` | admin login |
| `RFP_ADMIN_PASSWORD` | – | password; empty = generated on first start, written to the log and `data/admin_password.txt` |
| `RFP_PUBLIC_READ` | `true` | visitors may view projects marked *public* without login |
| `RFP_TILE_PROXY` | `true` | map tiles through the server (visitor IPs do not reach map services); `false` = direct, adds Esri satellite |
| `RFP_TILE_CACHE_MB` | `2000` | size of the tile cache |
| `RFP_CORESCOPE_URL` | – | CoreScope instance for the *Network* workspace, e.g. `https://corescope.meshrheinland.de` |
| `RFP_CORESCOPE_SYNC_H` | `24` | refresh the repeater list every n hours (0 = button only) |
| `RFP_OVERPASS_URL` | `https://overpass-api.de/api/interpreter` | Overpass server for “load OSM buildings” |
| `RFP_PRELOAD` | `nrw_dgm,nrw_dom` | load the NRW models completely at start (empty = on demand) |
| `RFP_NRW_RES` | `5` | cell size of the local NRW store (m) |
| `RFP_PRELOAD_CENTER` | `51.48,7.55` | start of the preload (lat,lon), nearest tiles first |
| `RFP_REGION_WORKERS` | `2` | parallel requests while preloading |
| `RFP_GRID_LAT` | `51.5` | reference latitude of the computation grid; change only far away from 51.5° N |
| `RFP_WCS_MAX_NATIVE_MB` | `400` | limit if a WCS does not scale on the server |
| `RFP_IMPRINT_*`, `RFP_PRIVACY_*`, `RFP_PARENT_*`, `RFP_USER_*`, `RFP_MEASURE_KEY` | – | initial values only – everything is edited later in *Administration* |
| `PUID` / `PGID` | – | owner of the files in `/data` (Unraid template: 99/100) |

## Using it

- **Plan:** *Add station*, click on the map. The inspector opens the station: name, location (or *Set on the map*),
  radio preset, antenna **from the catalog** or own values, optional radiation sector, link to its MeshCore repeater.
  *Compute coverage* computes all ticked stations one after another (unchanged ones come from the cache); a ring at the
  station shows the progress. The layers button (top right) switches the view (best level, overlap, best server,
  line of sight), opacity, required margin, base map and data layers.
- **Right click** (long press on phones) anywhere: levels of all stations at that spot, profile per station,
  *Place station here*, *Enter measurement*.
- **Network:** search a repeater or tap one on the map → details (role, last heard, battery, …) and *Show neighbours*.
  Neighbours are filtered (both ways, counts, SNR); **tap a neighbour or its line** for the link: SNR reported directly by
  either side with the time it was last heard, SNR from packet paths, packet counts, data window, and the terrain
  profile of the link.
- **Measure:** series of drive tests with source, period, number of repeaters and the receiver. *✎ Receiver* sets
  device and placement from the catalog, or height/gain/cable by hand. A measurement point on the map is one **ping**:
  it lists every repeater that heard it, strongest first; repeater names open the repeater.
- **Compare:** pick a station that is linked with its repeater, choose the sources, *Compare*. Tap a row for the
  profile or the measurement point. The drive-test comparison ends with a suggestion for the global receiver loss
  that the admin can apply in one click.
- **Report:** key figures per station; *PDF* downloads the report (the report text is German for now).
- **Administration** (⚙, admin only): access & website, data sources (elevation test/download, building footprints,
  MeshMapper key), **model** (global settings with their sources), **catalogs** (antennas, devices, placements), system.

## Data

### Elevation
| Source | Kind | Note |
|---|---|---|
| **NRW DGM** (default ground) | terrain model, WCS `wcs_nw_dgm` | stored locally as 5 m tiles, dl-de/zero-2-0 |
| **NRW DOM** (default surface) | surface model from laser scan, WCS `wcs_nw_dom` | stored locally as 5 m tiles |
| Copernicus GLO-30 | surface model, 30 m, worldwide | fallback outside NRW |
| Local | own GeoTIFFs in `data/dem/<folder>/` | appear as “Local: …” |

Beyond the main source (e.g. across the NRW border) the fallback fills the holes, so near the border both are used.
Copernicus is a 30 m surface model: buildings and trees are part of its “ground” and are not seen as obstacles – set a
minimum clutter class there.

### Building footprints (#25)
The surface model contains buildings and trees without telling them apart. Footprints tell the model which surface
cells are buildings. Import them in *Administration → Data sources* as **GeoJSON** (e.g. exported from
[overpass-turbo.eu](https://overpass-turbo.eu) with `way["building"]({{bbox}}); out geom;`) or load the **current map
view from OpenStreetMap** (at most 30 km² per request, © OpenStreetMap contributors, ODbL). They are stored in
`data/buildings/footprints.json`.

What footprints change depends on the setting **building mode** (Administration → Model):
- `vegetation` (default) – buildings are handled like everything else in the surface model: diffraction over the
  roof plus the P.833 attenuation where the direct ray runs through them. **Results stay the same as without
  footprints**; footprints are shown in the profile.
- `diffraction` – inside footprints the direct ray is not attenuated by P.833, only diffraction over the roof counts
  (ITU-R P.526). Walls are usually far less transparent than foliage, so this is the physically more consistent choice
  for brick and concrete; it was not made the default because it has not been checked against measurements yet.

### Catalogs (#28, #29)
Admin-maintained lists in `data/catalog.json`, seeded on first start:
- **Antennas** – generic types plus models used in the community (e.g. Interline HORIZON 868 6 dBi / 30°, 8 dBi / 15°,
  ALFA AOA-868-5ACM and the list of the meshrheinland community). Every entry has a *source*; values without a datasheet
  are marked as assumptions.
- **Devices** – e.g. SenseCAP T1000-E (1 dBi, an assumption: the maker gives no gain figure).
- **Placements** – car roof 1.7 m, jacket pocket 1.25 m with 4 dB extra loss, bicycle 1.0 m, handheld raised 1.8 m,
  inside the car 1.2 m with 9 dB (car penetration loss, 3GPP TR 38.901 §7.4.3.2: mean 9 dB, σ 5 dB), trouser pocket
  0.9 m / 4 dB, backpack top 1.5 m / 1 dB, backpack inside 1.2 m / 4 dB. Apart from the car value these are typical
  heights and rules of thumb, marked as assumptions – please replace them with measured values when you have them.
  New seed entries of later versions are added to existing catalogs once; entries the admin deleted stay deleted.

Receiver of a drive-test row, in this order: a height entered in the single measurement → values of its series →
device and placement of the series → global receiver settings.

### MeshCore network (CoreScope)
With `RFP_CORESCOPE_URL` TerraRF reads the repeaters of a CoreScope instance (read-only, on the server, cached).
MeshCore does not announce antenna height, antenna type or transmit power – enter them per repeater for the comparison,
or add the repeater as a station.
- **Direct SNR** comes from the neighbour table a repeater reports itself (`neighbor_report`, directly heard adverts,
  zero hop). Only these values are used in comparisons.
- **SNR from packet paths** is an average over observers: an observer only hears the last hop and short prefixes are
  ambiguous. Such values are shown (in italics), never compared.
- SNR above 10 dB is not used in statistics – LoRa SNR reports saturate at the top.

Please use public instances gently and ask the operators if in doubt.

### Drive tests (MeshMapper, CSV, manual)
1. **MeshMapper forwarding:** in *Administration → Data sources* generate a key, save, copy the link and paste it in
   the MeshMapper app (*Settings → API Endpoints → Import from Clipboard*). The app then also sends every uploaded block
   to `https://<host>/api/ingest/meshmapper` (header `X-API-Key`). MeshMapper requires HTTPS. DISC/TRACE (SNR at phone
   and repeater, RSSI) and TX/RX echoes (SNR per repeater) are stored; duplicate echoes count once. Repeater prefixes
   are resolved to keys through the CoreScope list when unique in the area.
2. **Manual:** right click → *Enter measurement*. The antenna height above ground is required; device and placement
   fill in height, gain and loss. Entries with the same receiver within 30 minutes form one series.
3. **CSV:** header `lat;lon;rep;snr_down`, optionally `snr_up;rssi;ts;rx_h;note` (separator `;` or `,`, decimal comma ok).

A series is one drive (a break of more than 30 min starts a new one; one CSV import is one series). The **measurement
plan** marks a ~300 m cell (MeshMapper's grid) as *enough* when one repeater has at least 3 measurements there with at
most 6 dB scatter. MeshMapper's *Smart Pinging* skips cells that already have coverage on meshmapper.net – switch it off
in orange cells, or no further measurements arrive there.

## Model

Total loss = free space + ground reflection + diffraction + obstacle loss + receiver clutter (+ receiver extra loss).
All settings are **global** (*Administration → Model*); every setting shows its source. Changing a setting never serves
stale maps – cached results are keyed by the settings and the model version.

* **Diffraction** over ground + surface: Deygout (default, 3 edges) or Bullington (ITU-R P.526). The Fresnel zone is
  part of it (v is the clearance relative to the first Fresnel zone). Deygout is the default because Bullington was
  clearly too optimistic in built-up areas.
* **Obstacle loss** (ITU-R P.833): where the direct ray runs through trees/buildings (above ground, below the surface):
  A = Am·(1 − exp(−d·γ/Am)); at 869 MHz about γ ≈ 0.16 dB/m and Am ≈ 25 dB (P.833 table, interpolated in frequency).
  Added to the diffraction. Known buildings: see *building mode* above.
* **Receiver clutter** (ITU-R P.2108 §3.1): added when the receiver stands below the surrounding clutter (at 1.5 m and
  869 MHz about 18 dB suburban, 22 dB urban). *Automatic* takes the clutter height from surface − ground around the
  receiver (default 60 m, at most half the path – obstacles in front of that count fully in the diffraction).
* **Near field of the station:** within 150 m of the antenna the profile is sampled at 5 m even when the grid is 10 or
  25 m, so a building right next to the antenna shadows the direction behind it instead of blurring in the coarse grid.
  The antenna's own 5 m cell counts as free.
* **Radiation sector** (per station, optional): for antennas on a wall – extra loss outside the sector with a soft
  15° edge, unchanged inside. Not a directional pattern.
* **Antenna pattern:** horizontal and vertical parabolic attenuation (3GPP style), limited by the front-to-back ratio.
* **Ground reflection:** two-ray term on line-of-sight paths.
* **Earth curvature:** effective earth radius k · 6371 km, k = 4/3 (ITU-R P.453).
* **Receiver:** gain (default 0 dBi), extra loss for body/building (default 0 dB), noise rise, **fade/location margin**
  (default 10 dB, slider on the map).
* **Noise:** thermal noise kTB (−174 dBm/Hz + 10·log10(bandwidth)) plus the receiver noise figure and the global noise
  rise. A station can carry a **measured noise floor** instead (e.g. from the repeater's statistics); it is used where
  that station receives – its sensitivity on the map and the SNR it reports in the comparison.

**Limits:** no multipath or indoor models, no interferers. The defaults were checked for plausibility against a few
measurements in the Ruhr area, not calibrated. The *Compare* workspace exists to change that – measurements (place,
height, RSSI/SNR) shared as an issue help to improve the defaults.

## Privacy and imprint
TerraRF sets no cookies for visitors, loads no scripts or fonts from third parties and writes no access logs. Map tiles
go through the server by default (`RFP_TILE_PROXY`). Only a login sets a session cookie (technically necessary, so no
cookie banner). The browser stores the chosen interface language locally. `/impressum` and `/datenschutz` are generated
(in German, as German law requires) from the details in *Administration → Access & website*; own versions as
`data/legal/impressum.html` / `datenschutz.html` replace them. **No legal advice:** check yourself whether you need an
imprint and whether the texts are complete for your setup – a reverse proxy in front usually logs IP addresses.

## Development
```bash
pip install -r requirements-dev.txt
python -m pytest -q
RFP_DATA=./data RFP_PRELOAD= uvicorn app.main:app --reload --port 8080
```
Code structure: [docs/architecture.md](docs/architecture.md). Rules for contributions: [CLAUDE.md](CLAUDE.md),
[CONTRIBUTING.md](CONTRIBUTING.md).

## Wishes, automation, releases
- **Issues:** templates “wish” and “bug”. Anyone may open issues.
- **Approval:** the maintainer sets the label `freigegeben`; only then is anything built.
- **Claude Code** (GitHub Action, runs on the maintainer's subscription): `@claude …` as a comment of the owner or the
  label `claude` starts work on that issue/PR; *Actions → “Claude Wunsch-Stapel”* works through up to N approved issues,
  one pull request each. Comments and issues from others never trigger anything; nothing reaches `main` without a merge.
- **CI** tests every pull request; `main` is published as `:edge`.
- **Release:** *Actions → Release → Run workflow* (patch/minor/major) → tag, release notes, image `:x.y.z` and `:latest`.

One-time setup: `scripts/setup-github.sh` (labels, secret `CLAUDE_CODE_OAUTH_TOKEN`).

## License
[AGPL-3.0](LICENSE). Third-party software and data licenses: [THIRD_PARTY.md](THIRD_PARTY.md).
