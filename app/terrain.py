"""Terrain sources and grid handling.

All computations run on a regular grid in EPSG:3857 (Web Mercator) so the
result PNGs can be laid over Leaflet maps without distortion. Every source
reprojects its data into that grid.

Sources:
  nrw_dgm      Geobasis NRW, WCS NW DGM (ground model, 1 m native)
  copernicus   Copernicus GLO-30 DSM (surface incl. buildings/trees, 30 m), AWS Open Data
  local:<dir>  GeoTIFFs (any CRS) in /data/dem/<dir>, e.g. downloaded DGM1/bDOM/nDOM tiles
  none         no surface model (obstacles = ground only)
"""
from __future__ import annotations

import glob
import hashlib
import re
import json
import logging
import math
import os
import threading
from dataclasses import dataclass

import httpx
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform_bounds

log = logging.getLogger("rfplanner.terrain")

DATA_DIR = os.environ.get("RFP_DATA", "/data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
DEM_DIR = os.path.join(DATA_DIR, "dem")
WEBM = CRS.from_epsg(3857)
R_EARTH = 6378137.0

NRW_WCS_URL = os.environ.get("RFP_NRW_WCS_URL", "https://www.wcs.nrw.de/geobasis/wcs_nw_dgm")
NRW_WCS_COVERAGE = os.environ.get("RFP_NRW_WCS_COVERAGE", "nw_dgm")
NRW_DOM_URL = os.environ.get("RFP_NRW_DOM_URL", "https://www.wcs.nrw.de/geobasis/wcs_nw_dom")
NRW_DOM_COVERAGE = os.environ.get("RFP_NRW_DOM_COVERAGE", "")  # empty = discover via GetCapabilities
COP_BASE = os.environ.get("RFP_COPERNICUS_URL", "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com")
HTTP_TIMEOUT = float(os.environ.get("RFP_HTTP_TIMEOUT", "120"))
MAX_NATIVE_MB = float(os.environ.get("RFP_WCS_MAX_NATIVE_MB", "400"))
TILE_RES = float(os.environ.get("RFP_NRW_RES", "5"))       # resolution of the local NRW tile store (m)
TILE_M = float(os.environ.get("RFP_TILE_M", "10000"))       # tile edge length (m, EPSG:25832)
TILE_WORKERS = int(os.environ.get("RFP_TILE_WORKERS", "3"))

for d in (CACHE_DIR, DEM_DIR):
    os.makedirs(d, exist_ok=True)


def lonlat_to_merc(lon, lat):
    x = math.radians(lon) * R_EARTH
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R_EARTH
    return x, y


def merc_to_lonlat(x, y):
    lon = math.degrees(x / R_EARTH)
    lat = math.degrees(2 * math.atan(math.exp(y / R_EARTH)) - math.pi / 2)
    return lon, lat


@dataclass(frozen=True)
class Grid:
    """Regular grid in EPSG:3857. (ix0, iy0) are integer cell indices of the
    upper-left cell relative to the global origin, so grids sharing `cs` can be
    pasted into each other by integer offsets."""

    cs: float  # cell size in mercator metres
    ix0: int
    iy0: int  # row index counted downward from y=0 (iy = floor(-y / cs))
    nx: int
    ny: int

    @property
    def x0(self):
        return self.ix0 * self.cs

    @property
    def y1(self):
        return -self.iy0 * self.cs

    @property
    def transform(self):
        return from_origin(self.x0, self.y1, self.cs, self.cs)

    @property
    def bounds(self):  # left, bottom, right, top
        return (self.x0, self.y1 - self.ny * self.cs, self.x0 + self.nx * self.cs, self.y1)

    def latlon_bounds(self):
        l, b, r, t = self.bounds
        lon0, lat0 = merc_to_lonlat(l, b)
        lon1, lat1 = merc_to_lonlat(r, t)
        return [[lat0, lon0], [lat1, lon1]]

    def key(self):
        return f"{self.cs:.4f}_{self.ix0}_{self.iy0}_{self.nx}_{self.ny}"

    @staticmethod
    def around(x, y, radius_merc, cs):
        ix0 = int(math.floor((x - radius_merc) / cs))
        ix1 = int(math.ceil((x + radius_merc) / cs))
        iy0 = int(math.floor(-(y + radius_merc) / cs))
        iy1 = int(math.ceil(-(y - radius_merc) / cs))
        return Grid(cs, ix0, iy0, ix1 - ix0, iy1 - iy0)

    @staticmethod
    def union(grids):
        cs = grids[0].cs
        ix0 = min(g.ix0 for g in grids)
        iy0 = min(g.iy0 for g in grids)
        ix1 = max(g.ix0 + g.nx for g in grids)
        iy1 = max(g.iy0 + g.ny for g in grids)
        return Grid(cs, ix0, iy0, ix1 - ix0, iy1 - iy0)

    def true_res(self):
        """Approximate true ground resolution in metres at the grid centre."""
        l, b, r, t = self.bounds
        _, lat = merc_to_lonlat((l + r) / 2, (b + t) / 2)
        return self.cs * math.cos(math.radians(lat))


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------
class SourceError(RuntimeError):
    pass


class _NeedNative(Exception):
    pass


class BaseSource:
    id = "base"
    label = "base"
    kind = "ground"  # ground | surface | both

    def read(self, grid: Grid, resampling: Resampling) -> np.ndarray:
        raise NotImplementedError

    @staticmethod
    def _warp_into(dst, src_path_or_ds, grid, resampling):
        ds = src_path_or_ds
        tmp = np.full((grid.ny, grid.nx), np.nan, dtype=np.float32)
        nodata = ds.nodata
        reproject(
            source=rasterio.band(ds, 1),
            destination=tmp,
            src_transform=ds.transform,
            src_crs=ds.crs,
            src_nodata=nodata,
            dst_transform=grid.transform,
            dst_crs=WEBM,
            dst_nodata=np.nan,
            resampling=resampling,
        )
        if nodata is None:
            # Typical "no data" sentinels in DEMs
            tmp[(tmp < -1000) | (tmp > 9000)] = np.nan
        m = np.isnan(dst) & ~np.isnan(tmp)
        dst[m] = tmp[m]


class WCSSource(BaseSource):
    """OGC WCS 2.0.1 GetCoverage in chunks. Tries server-side downscaling first
    (SCALESIZE, then SCALEFACTOR) and falls back to native resolution."""

    def __init__(self, sid, label, url, coverage, epsg, native_res, kind="ground", chunk_px=2000):
        self.id = sid
        self.label = label
        self.url = url
        self.coverage = coverage
        self.crs = CRS.from_epsg(epsg)
        self.native_res = native_res
        self.kind = kind
        self.chunk_px = chunk_px
        self.mode = None  # remembered working request variant
        self._cov_checked = bool(coverage)
        self.last_error = None
        self._lock = threading.Lock()
        self.cache = os.path.join(CACHE_DIR, "wcs", sid)
        os.makedirs(self.cache, exist_ok=True)

    def _variants(self, w, h, factor):
        v = [
            ("scalesize", [("SCALESIZE", f"x({w}),y({h})")]),
            ("scalesize_EN", [("SCALESIZE", f"E({w}),N({h})")]),
            ("scalefactor", [("SCALEFACTOR", f"{factor:.6f}")]),
            ("native", []),
        ]
        if self.mode:
            v.sort(key=lambda t: t[0] != self.mode)
        return v

    def discover_coverage(self, client):
        """Read CoverageIds from GetCapabilities (used when no id is configured)."""
        if self._cov_checked:
            return self.coverage
        r = client.get(self.url, params={"SERVICE": "WCS", "VERSION": "2.0.1", "REQUEST": "GetCapabilities"})
        ids = re.findall(r"<(?:\w+:)?CoverageId>\s*([^<\s]+)\s*</", r.text)
        if not ids:
            raise SourceError(f"WCS {self.id}: keine CoverageId in GetCapabilities gefunden (HTTP {r.status_code})")
        # prefer an id that looks like the product, else the first one
        pick = next((i for i in ids if "dom" in i.lower() and "ndom" not in i.lower() and "tdom" not in i.lower()), ids[0]) \
            if "dom" in self.id else ids[0]
        log.info("WCS %s: CoverageIds %s -> verwende %s", self.id, ids, pick)
        self.coverage = pick
        self._cov_checked = True
        return pick

    def _fetch_chunk(self, x0, y0, x1, y1, res, client, dest=None):
        self.discover_coverage(client)
        w = max(1, int(round((x1 - x0) / res)))
        h = max(1, int(round((y1 - y0) / res)))
        key = hashlib.sha1(f"{self.url}|{self.coverage}|{x0}|{y0}|{x1}|{y1}|{res}".encode()).hexdigest()[:20]
        path = dest or os.path.join(self.cache, key + ".tif")
        if os.path.exists(path):
            return path
        factor = min(1.0, self.native_res / res)
        errors = []
        http_answers = 0  # scaling variants the server actually answered (with an error)
        for name, extra in self._variants(w, h, factor):
            if name == "native" and res > self.native_res * 1.01:
                native_px = (x1 - x0) * (y1 - y0) / self.native_res**2
                if native_px > (self.chunk_px * 1.01) ** 2:
                    if http_answers == 0:
                        self.last_error = "; ".join(errors)
                        raise SourceError(f"WCS {self.id} nicht erreichbar: {self.last_error}")
                    # chunk is sized for scaled requests -> caller re-chunks for native mode
                    self.last_error = "; ".join(errors)
                    raise _NeedNative()
            params = [
                ("SERVICE", "WCS"), ("VERSION", "2.0.1"), ("REQUEST", "GetCoverage"),
                ("COVERAGEID", self.coverage), ("FORMAT", "image/tiff"),
                ("SUBSET", f"x({x0},{x1})"), ("SUBSET", f"y({y0},{y1})"),
            ] + extra
            try:
                r = client.get(self.url, params=params)
            except httpx.HTTPError as e:
                errors.append(f"{name}: {e}")
                continue
            body = r.content
            if r.status_code == 200 and body[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
                tmp = path + ".part"
                with open(tmp, "wb") as f:
                    f.write(body)
                if name == "native" and res > self.native_res * 1.01:
                    self._downsample_file(tmp, res)
                os.replace(tmp, path)
                if self.mode != name:
                    log.info("WCS %s: using request variant %s", self.id, name)
                self.mode = name
                return path
            errors.append(f"{name}: HTTP {r.status_code} {body[:200]!r}")
            if name != "native" and r.status_code < 500:
                http_answers += 1
        self.last_error = "; ".join(errors)
        raise SourceError(f"WCS {self.id} lieferte keine Daten: {self.last_error}")

    @staticmethod
    def _downsample_file(path, res):
        with rasterio.open(path) as ds:
            l, b, r, t = ds.bounds
            w = max(1, int(round((r - l) / res)))
            h = max(1, int(round((t - b) / res)))
            data = ds.read(1, out_shape=(h, w), resampling=Resampling.average).astype(np.float32)
            prof = ds.profile
            prof.update(width=w, height=h, transform=from_origin(l, t, (r - l) / w, (t - b) / h), dtype="float32")
        with rasterio.open(path, "w", **prof) as out:
            out.write(data, 1)

    def read_direct(self, grid, resampling):  # legacy path without tile store
        try:
            return self._read(grid, resampling)
        except _NeedNative:
            log.warning("WCS %s: Server skaliert nicht serverseitig (%s) – lade native Auflösung", self.id, self.last_error)
            self.mode = "native"
            return self._read(grid, resampling)

    def fetch_tile(self, x0, y0, x1, y1, res, dest, client):
        """Fetch one area (source CRS) at resolution `res` into GeoTIFF `dest`.
        Falls back to native chunks + local downsampling if the server cannot scale."""
        if self.mode != "native":
            try:
                return self._fetch_chunk(x0, y0, x1, y1, res, client, dest=dest)
            except _NeedNative:
                log.warning("WCS %s: Server skaliert nicht (%s) – native Auflösung", self.id, self.last_error)
                self.mode = "native"
        step = self.native_res * self.chunk_px
        parts = []
        tmpdir = dest + ".parts"
        os.makedirs(tmpdir, exist_ok=True)
        x = x0
        while x < x1 - 1e-6:
            y = y0
            while y < y1 - 1e-6:
                pp = os.path.join(tmpdir, f"{int(x)}_{int(y)}.tif")
                parts.append(self._fetch_chunk(x, y, min(x + step, x1), min(y + step, y1), res, client, dest=pp))
                y += step
            x += step
        from rasterio.merge import merge
        dss = [rasterio.open(p) for p in parts]
        try:
            arr, tr = merge(dss, bounds=(x0, y0, x1, y1), res=res, nodata=np.nan, dtype="float32")
            prof = dss[0].profile
        finally:
            for d_ in dss:
                d_.close()
        write_geotiff(dest, arr[0], tr, self.crs)
        for p in parts:
            os.remove(p)
        os.rmdir(tmpdir)
        return dest

    # ---------------- local tile store (all reads go through it) ----------------
    @property
    def store_name(self):
        return f"{self.id}_{int(round(TILE_RES))}m"

    @property
    def store_dir(self):
        return os.path.join(DEM_DIR, self.store_name)

    def tile_path(self, x0, y0):
        return os.path.join(self.store_dir, f"{self.id}_{int(x0)}_{int(y0)}_{int(round(TILE_RES))}m.tif")

    def tile_state(self, x0, y0):
        p = self.tile_path(x0, y0)
        if os.path.exists(p):
            return "ok"
        if os.path.exists(p + ".empty"):
            return "empty"
        return "missing"

    def ensure_tile(self, x0, y0, client=None):
        """Download one store tile if needed. Returns path, or None for tiles without data."""
        p = self.tile_path(x0, y0)
        with _tile_lock(p):
            st = self.tile_state(x0, y0)
            if st == "ok":
                return p
            if st == "empty":
                return None
            os.makedirs(self.store_dir, exist_ok=True)
            own = client is None
            client = client or httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True)
            try:
                tmp = p + ".dl.tif"
                self.fetch_tile(x0, y0, x0 + TILE_M, y0 + TILE_M, TILE_RES, tmp, client)
                if recompress_tile(tmp):
                    os.replace(tmp, p)
                    return p
                open(p + ".empty", "w").close()
                return None
            finally:
                if own:
                    client.close()

    def tiles_for_bounds(self, b):
        x0 = math.floor(b[0] / TILE_M) * TILE_M
        y0 = math.floor(b[1] / TILE_M) * TILE_M
        out = []
        y = y0
        while y < b[3]:
            x = x0
            while x < b[2]:
                out.append((x, y))
                x += TILE_M
            y += TILE_M
        return out

    def read(self, grid, resampling):
        b = transform_bounds(WEBM, self.crs, *grid.bounds, densify_pts=21)
        tiles = self.tiles_for_bounds(b)
        missing = [t for t in tiles if self.tile_state(*t) == "missing"]
        if missing:
            log.info("WCS %s: lade %d fehlende Kacheln (%g m)", self.id, len(missing), TILE_RES)
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=TILE_WORKERS) as ex:
                list(ex.map(lambda t: self.ensure_tile(*t), missing))
        dst = np.full((grid.ny, grid.nx), np.nan, dtype=np.float32)
        for t in tiles:
            if self.tile_state(*t) == "ok":
                with rasterio.open(self.tile_path(*t)) as ds:
                    self._warp_into(dst, ds, grid, resampling)
        return dst

    def _read(self, grid, resampling):
        res = max(self.native_res, grid.true_res())
        b = transform_bounds(WEBM, self.crs, *grid.bounds, densify_pts=21)
        chunk_m = res * self.chunk_px
        if self.mode == "native":
            chunk_m = self.native_res * self.chunk_px
            total_mb = (b[2] - b[0]) * (b[3] - b[1]) / self.native_res**2 * 4 / 1e6
            if total_mb > MAX_NATIVE_MB:
                raise SourceError(
                    f"WCS {self.id} skaliert nicht serverseitig; native Daten wären ~{total_mb:.0f} MB "
                    f"(Limit RFP_WCS_MAX_NATIVE_MB={MAX_NATIVE_MB:.0f}). Radius verkleinern, Limit erhöhen "
                    f"oder Kacheln lokal in data/dem/ ablegen.")
        pad = 2 * res
        cx0 = math.floor((b[0] - pad) / chunk_m) * chunk_m
        cy0 = math.floor((b[1] - pad) / chunk_m) * chunk_m
        cx1 = math.ceil((b[2] + pad) / chunk_m) * chunk_m
        cy1 = math.ceil((b[3] + pad) / chunk_m) * chunk_m
        dst = np.full((grid.ny, grid.nx), np.nan, dtype=np.float32)
        with self._lock, httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            x = cx0
            while x < cx1 - 1e-6:
                y = cy0
                while y < cy1 - 1e-6:
                    p = self._fetch_chunk(round(x, 3), round(y, 3), round(x + chunk_m, 3), round(y + chunk_m, 3), res, client)
                    with rasterio.open(p) as ds:
                        self._warp_into(dst, ds, grid, resampling)
                    y += chunk_m
                x += chunk_m
        return dst


class CopernicusSource(BaseSource):
    id = "copernicus"
    label = "Copernicus GLO-30 DSM (30 m, Oberfläche, weltweit)"
    kind = "both"

    def __init__(self):
        self.cache = os.path.join(CACHE_DIR, "copernicus")
        os.makedirs(self.cache, exist_ok=True)
        self._lock = threading.Lock()

    @staticmethod
    def tile_name(lat, lon):
        ns = "N" if lat >= 0 else "S"
        ew = "E" if lon >= 0 else "W"
        return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"

    def _tile(self, lat, lon, client):
        name = self.tile_name(lat, lon)
        path = os.path.join(self.cache, name + ".tif")
        missing = path + ".missing"
        if os.path.exists(path):
            return path
        if os.path.exists(missing):
            return None
        url = f"{COP_BASE}/{name}/{name}.tif"
        log.info("Copernicus: lade %s", url)
        with client.stream("GET", url) as r:
            if r.status_code in (403, 404):
                open(missing, "w").close()  # ocean tiles do not exist
                return None
            if r.status_code != 200:
                raise SourceError(f"Copernicus {url}: HTTP {r.status_code}")
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        os.replace(tmp, path)
        return path

    def read(self, grid, resampling):
        l, b, r, t = transform_bounds(WEBM, CRS.from_epsg(4326), *grid.bounds, densify_pts=21)
        dst = np.full((grid.ny, grid.nx), np.nan, dtype=np.float32)
        with self._lock, httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            for lat in range(math.floor(b), math.floor(t) + 1):
                for lon in range(math.floor(l), math.floor(r) + 1):
                    p = self._tile(lat, lon, client)
                    if p:
                        with rasterio.open(p) as ds:
                            self._warp_into(dst, ds, grid, resampling)
        return dst


class LocalFolderSource(BaseSource):
    """All GeoTIFFs (*.tif, *.tiff, *.vrt, *.asc) below /data/dem/<name>."""

    kind = "both"

    def __init__(self, name):
        self.name = name
        self.id = f"local:{name}"
        self.label = f"Lokal: /data/dem/{name}"
        self.path = os.path.join(DEM_DIR, name)
        self._index = None
        self._index_sig = None
        self._lock = threading.Lock()

    def _files(self):
        out = []
        for ext in ("tif", "tiff", "TIF", "vrt", "asc"):
            out += glob.glob(os.path.join(self.path, "**", f"*.{ext}"), recursive=True)
        return sorted(f for f in set(out) if not f.endswith((".dl.tif", ".tmp.tif", ".part")) and ".parts" not in f)

    def index(self):
        files = self._files()
        sig = hashlib.sha1("|".join(f"{f}:{os.path.getmtime(f)}" for f in files).encode()).hexdigest()
        if self._index is not None and sig == self._index_sig:
            return self._index
        idx_path = os.path.join(CACHE_DIR, f"localindex_{hashlib.sha1(self.path.encode()).hexdigest()[:10]}.json")
        if os.path.exists(idx_path):
            with open(idx_path) as f:
                d = json.load(f)
            if d.get("sig") == sig:
                self._index, self._index_sig = d["files"], sig
                return self._index
        old = {}
        if os.path.exists(idx_path):
            try:
                with open(idx_path) as fh:
                    for e in json.load(fh).get("files", []):
                        old[(e["path"], e.get("mtime"))] = e
            except Exception:  # noqa: BLE001
                old = {}
        entries = []
        for f in files:
            mt = os.path.getmtime(f)
            if (f, mt) in old:
                entries.append(old[(f, mt)])
                continue
            try:
                with rasterio.open(f) as ds:
                    if ds.crs is None:
                        log.warning("%s hat kein CRS, übersprungen", f)
                        continue
                    wb = transform_bounds(ds.crs, WEBM, *ds.bounds, densify_pts=5)
                    entries.append({"path": f, "bounds": wb, "mtime": mt})
            except Exception as e:  # noqa: BLE001
                log.warning("Kann %s nicht lesen: %s", f, e)
        with open(idx_path, "w") as fh:
            json.dump({"sig": sig, "files": entries}, fh)
        self._index, self._index_sig = entries, sig
        return entries

    def read(self, grid, resampling):
        l, b, r, t = grid.bounds
        dst = np.full((grid.ny, grid.nx), np.nan, dtype=np.float32)
        with self._lock:
            hits = [e for e in self.index() if not (e["bounds"][2] < l or e["bounds"][0] > r or e["bounds"][3] < b or e["bounds"][1] > t)]
        for e in hits:
            with rasterio.open(e["path"]) as ds:
                self._warp_into(dst, ds, grid, resampling)
        return dst


_SOURCES: dict[str, BaseSource] = {}
_tile_locks: dict[str, threading.Lock] = {}
_tile_locks_guard = threading.Lock()


def _tile_lock(path):
    with _tile_locks_guard:
        lk = _tile_locks.get(path)
        if lk is None:
            lk = _tile_locks[path] = threading.Lock()
        return lk


def builtin_sources():
    if not _SOURCES:
        _SOURCES["nrw_dgm"] = WCSSource(
            "nrw_dgm", "NRW DGM (Geobasis NRW, Gelände, lokal gespeichert)", NRW_WCS_URL, NRW_WCS_COVERAGE, 25832, 1.0, kind="ground"
        )
        _SOURCES["nrw_dom"] = WCSSource(
            "nrw_dom", "NRW DOM1 (Geobasis NRW, Oberfläche, lokal gespeichert)", NRW_DOM_URL, NRW_DOM_COVERAGE,
            25832, 1.0, kind="surface")
        _SOURCES["copernicus"] = CopernicusSource()
    return _SOURCES


def list_sources():
    out = [{"id": s.id, "label": s.label, "kind": s.kind} for s in builtin_sources().values()]
    stores = {s.store_name for s in builtin_sources().values() if isinstance(s, WCSSource)}
    if os.path.isdir(DEM_DIR):
        for name in sorted(os.listdir(DEM_DIR)):
            if name in stores:
                continue
            if os.path.isdir(os.path.join(DEM_DIR, name)):
                out.append({"id": f"local:{name}", "label": f"Lokal: dem/{name}", "kind": "both"})
    return out


def get_source(sid: str) -> BaseSource | None:
    if not sid or sid == "none":
        return None
    srcs = builtin_sources()
    if sid in srcs:
        return srcs[sid]
    if sid.startswith("local:"):
        name = sid.split(":", 1)[1]
        if "/" in name or ".." in name:
            raise SourceError("ungültiger Ordnername")
        if sid not in _SOURCES:
            _SOURCES[sid] = LocalFolderSource(name)
        return _SOURCES[sid]
    raise SourceError(f"Unbekannte Terrain-Quelle: {sid}")


_mem_lock = threading.Lock()


def load_terrain(sid: str, grid: Grid, kind: str):
    """Returns float32 array (ny, nx) or None for 'none'. Cached on disk."""
    src = get_source(sid)
    if src is None:
        return None
    resampling = Resampling.bilinear if kind == "ground" else Resampling.max
    if isinstance(src, LocalFolderSource):
        src.index()
        sig = src._index_sig
    else:
        sig = "v1"
    key = hashlib.sha1(f"{sid}|{sig}|{grid.key()}|{kind}".encode()).hexdigest()[:24]
    path = os.path.join(CACHE_DIR, "terrain", key + ".npy")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        return np.load(path, mmap_mode="r")
    arr = src.read(grid, resampling)
    if float(np.isnan(arr).mean()) < 0.01:  # do not cache incomplete grids (failed tiles)
        np.save(path, arr)
    return arr


def fill_nans(arr, fallback):
    a = np.array(arr, dtype=np.float32, copy=True)
    m = np.isnan(a)
    if m.any():
        if fallback is not None:
            a[m] = np.asarray(fallback)[m]
        m = np.isnan(a)
        if m.any():
            a[m] = 0.0
    return a


def write_geotiff(path, arr, transform, crs):
    tmp = path + ".tmp.tif"
    h, w = arr.shape
    prof = dict(driver="GTiff", width=w, height=h, count=1, dtype="float32", crs=crs, transform=transform,
                nodata=np.nan, tiled=True, blockxsize=256, blockysize=256, compress="deflate", predictor=3)
    with rasterio.open(tmp, "w", **prof) as ds:
        ds.write(arr.astype(np.float32), 1)
    os.replace(tmp, path)


def recompress_tile(path):
    """Ensure a downloaded tile is a compressed, tiled float32 GeoTIFF; returns False if it holds no data."""
    with rasterio.open(path) as ds:
        arr = ds.read(1).astype(np.float32)
        nod = ds.nodata
        tr, crs = ds.transform, ds.crs
    if nod is not None and not np.isnan(nod):
        arr[arr == nod] = np.nan
    arr[(arr < -1000) | (arr > 9000)] = np.nan
    if np.isnan(arr).all():
        os.remove(path)
        return False
    write_geotiff(path, arr, tr, crs)
    return True


def cache_usage():
    out = {}
    for name in ("terrain", "coverage", "wcs", "copernicus", "numba"):
        p = os.path.join(CACHE_DIR, name)
        total = 0
        for root, _, files in os.walk(p):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        out[name] = total
    dem = {}
    if os.path.isdir(DEM_DIR):
        for name in sorted(os.listdir(DEM_DIR)):
            p = os.path.join(DEM_DIR, name)
            if os.path.isdir(p):
                size = n = 0
                for root, _, files in os.walk(p):
                    for f in files:
                        size += os.path.getsize(os.path.join(root, f))
                        n += 1
                dem[name] = {"bytes": size, "files": n}
    return {"cache": out, "dem": dem}


def clear_cache(parts):
    import shutil
    for name in parts:
        if name not in ("terrain", "coverage", "wcs", "copernicus"):
            continue
        p = os.path.join(CACHE_DIR, name)
        shutil.rmtree(p, ignore_errors=True)
        os.makedirs(p, exist_ok=True)
    for src in _SOURCES.values():
        if isinstance(src, WCSSource):
            os.makedirs(src.cache, exist_ok=True)
        elif isinstance(src, CopernicusSource):
            os.makedirs(src.cache, exist_ok=True)
