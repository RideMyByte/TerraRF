"""Tile proxy with disk cache: the browser loads map tiles only from TerraRF itself.

So no visitor IP addresses reach OpenStreetMap, OpenTopoMap or Geobasis NRW; the map services only see the
server, and cached tiles relieve their (donation-funded) servers.
OSM Tile Usage Policy: unique User-Agent, caching (at least 7 days), no bulk downloads – and therefore no offline
packages of OSM or OpenTopoMap tiles (#58). Offline maps come from the open NRW aerial photo (Geobasis NRW,
dl-de/zero-2-0), saved per area as a package, or from raster MBTiles files the admin puts into data/tiles/mbtiles.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import sqlite3
import threading
import time
from urllib.parse import urlencode

import httpx

from .. import paths
from ..model import terrain
from ..site import offline
from ..version import VERSION

log = logging.getLogger("terrarf.tiles")

ENABLED = os.environ.get("RFP_TILE_PROXY", "true").strip().lower() not in ("0", "false", "no", "off")
MAX_AGE_S = 14 * 86400
CACHE_MB = float(os.environ.get("RFP_TILE_CACHE_MB", "2000") or 2000)
DIR = os.path.join(paths.DATA_DIR, "tiles")
PACK_DIR = os.path.join(paths.DATA_DIR, "tiles_offline")       # saved areas: never trimmed, never expire
MBTILES_DIR = os.path.join(DIR, "mbtiles")
UA = f"TerraRF/{VERSION} tile proxy (+https://github.com/RideMyByte/TerraRF)"

# XYZ sources: id -> (URL template, max zoom)
XYZ = {
    "osm": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", 19),
    "otm": ("https://a.tile.opentopomap.org/{z}/{x}/{y}.png", 17),
}
# WMS (NRW DOP): only these parameters are passed through
WMS = {"nrwdop": ("https://www.wms.nrw.de/geobasis/wms_nw_dop", {"layers": "nw_dop_rgb", "format": "image/jpeg"})}
WMS_KEYS = ("bbox", "width", "height", "srs", "crs", "version", "styles", "transparent")
# WMS sources that are also served as XYZ tiles (cache key z/x/y, so an area can be saved for offline use)
WMS_XYZ = {"nrwdop": 20}
# only these may be saved for offline use: open data whose licence allows it (not OSM/OpenTopoMap, see above)
PACKABLE = {"nrwdop": "Luftbild NRW (DOP, © Geobasis NRW, dl-de/zero-2-0)"}
MAX_PACK_TILES = int(os.environ.get("RFP_OFFLINE_MAX_TILES", "60000") or 60000)

_client = None
_client_lock = threading.Lock()
_writes = [0]


def client():
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": UA},
                                   limits=httpx.Limits(max_connections=4))
        return _client


def _path(src, key):
    h = hashlib.sha1(key.encode()).hexdigest()
    return os.path.join(DIR, src, h[:2], h + ".bin")


def _cached(p):
    try:
        if time.time() - os.path.getmtime(p) < MAX_AGE_S:
            with open(p, "rb") as f:
                return f.read()
    except OSError:
        pass
    return None


def _store(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, p)
    _writes[0] += 1
    if _writes[0] % 500 == 0:
        threading.Thread(target=_trim, daemon=True).start()


def _trim():
    """Delete the oldest tiles once the cache grows beyond RFP_TILE_CACHE_MB (not while offline: then every cached
    tile is all there is)."""
    if offline.enabled():
        return
    files = []
    for root, _d, fs in os.walk(DIR):
        for f in fs:
            p = os.path.join(root, f)
            try:
                st = os.stat(p)
                files.append((st.st_mtime, st.st_size, p))
            except OSError:
                pass
    total = sum(s for _, s, _ in files)
    limit = CACHE_MB * 1e6
    if total <= limit:
        return
    files.sort()
    for _m, s, p in files:
        if total <= limit * 0.8:
            break
        try:
            os.remove(p)
            total -= s
        except OSError:
            pass
    log.info("Kachel-Cache auf %.0f MB gekürzt", total / 1e6)


def _pack_path(src, key):
    h = hashlib.sha1(key.encode()).hexdigest()
    return os.path.join(PACK_DIR, src, h[:2], h + ".bin")


def _read(p):
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None


def _fetch(src, key, url, params=None):
    p = _path(src, key)
    data = _cached(p)
    if data is not None:
        return data
    packed = _read(_pack_path(src, key))
    if packed is not None:
        return packed
    try:
        r = client().get(url, params=params)
        r.raise_for_status()
    except httpx.HTTPError:
        stale = p if os.path.exists(p) else None  # an old tile is better than none
        if stale:
            with open(stale, "rb") as f:
                return f.read()
        raise
    _store(p, r.content)
    return r.content


def xyz(src, z, x, y):
    if src not in XYZ:
        raise KeyError(src)
    tmpl, zmax = XYZ[src]
    if not (0 <= z <= zmax and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise ValueError("Kachel außerhalb")
    return _fetch(src, f"{z}/{x}/{y}", tmpl.format(z=z, x=x, y=y))


def wms(src, query: dict):
    if src not in WMS:
        raise KeyError(src)
    base, fixed = WMS[src]
    q = {k.lower(): v for k, v in query.items()}
    params = {"service": "WMS", "request": "GetMap", **fixed}
    for k in WMS_KEYS:
        if k in q:
            params[k] = q[k]
    w, h = int(params.get("width", 256)), int(params.get("height", 256))
    if not (16 <= w <= 1024 and 16 <= h <= 1024) or "bbox" not in params:
        raise ValueError("ungültige Anfrage")
    key = urlencode(sorted(params.items()))
    return _fetch(src, key, base, params)


# ---------------------------------------------------------------- WMS as XYZ (#58)
def _xyz_bbox(z, x, y):
    """EPSG:3857 bounds of an XYZ tile."""
    w = 20037508.342789244            # half the EPSG:3857 world width
    size = 2 * w / 2 ** z
    x0 = -w + x * size
    y1 = w - y * size
    return x0, y1 - size, x0 + size, y1


def wms_tile(src, z, x, y):
    if src not in WMS_XYZ:
        raise KeyError(src)
    if not (0 <= z <= WMS_XYZ[src] and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise ValueError("Kachel außerhalb")
    base, fixed = WMS[src]
    x0, y0, x1, y1 = _xyz_bbox(z, x, y)
    params = {"service": "WMS", "request": "GetMap", "version": "1.1.1", "srs": "EPSG:3857", "styles": "",
              "width": 256, "height": 256, "bbox": f"{x0:.3f},{y0:.3f},{x1:.3f},{y1:.3f}", **fixed}
    return _fetch(src, f"xyz/{z}/{x}/{y}", base, params)


def tile_range(bbox, z):
    """XYZ tile indices covering bbox = (south, west, north, east) at zoom z."""
    s, w, n, e = bbox
    def tx(lon):
        return int(math.floor((lon + 180.0) / 360.0 * 2 ** z))
    def ty(lat):
        lat = max(-85.05, min(85.05, lat))
        r = math.radians(lat)
        return int(math.floor((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2 ** z))
    x0, x1 = sorted((tx(w), tx(e)))
    y0, y1 = sorted((ty(n), ty(s)))
    return max(0, x0), min(2 ** z - 1, x1), max(0, y0), min(2 ** z - 1, y1)


def count_tiles(bbox, zmin, zmax):
    n = 0
    for z in range(zmin, zmax + 1):
        x0, x1, y0, y1 = tile_range(bbox, z)
        n += (x1 - x0 + 1) * (y1 - y0 + 1)
    return n


# ---------------------------------------------------------------- offline packages (#58)
_pack = {"state": "idle"}
_pack_lock = threading.Lock()


def pack_status():
    with _pack_lock:
        st = dict(_pack)
    st["sources"] = PACKABLE
    st["max_tiles"] = MAX_PACK_TILES
    st["size_mb"] = round(_dir_size(PACK_DIR) / 1e6, 1)
    return st


def _dir_size(d):
    total = 0
    for root, _d, fs in os.walk(d):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def start_pack(src, bbox, zmin, zmax):
    """Save every tile of the area for offline use, politely one after the other."""
    if src not in PACKABLE:
        raise ValueError("Diese Karte darf nicht offline gespeichert werden")
    zmin, zmax = int(zmin), int(zmax)
    if not (0 <= zmin <= zmax <= WMS_XYZ.get(src, 18)):
        raise ValueError("ungültige Zoomstufen")
    total = count_tiles(bbox, zmin, zmax)
    if total > MAX_PACK_TILES:
        raise ValueError(f"{total} Kacheln – höchstens {MAX_PACK_TILES} je Paket (kleineren Ausschnitt oder "
                         "niedrigere Zoomstufe wählen)")
    if offline.enabled():
        raise ValueError("Im Offline-Betrieb können keine Karten geladen werden")
    with _pack_lock:
        if _pack.get("state") == "running":
            raise ValueError("Es läuft schon ein Kartenpaket")
        _pack.clear()
        _pack.update({"state": "running", "src": src, "bbox": list(bbox), "zmin": zmin, "zmax": zmax,
                      "total": total, "done": 0, "failed": 0, "started": time.time()})
    threading.Thread(target=_run_pack, args=(src, bbox, zmin, zmax), daemon=True).start()
    return pack_status()


def cancel_pack():
    with _pack_lock:
        if _pack.get("state") == "running":
            _pack["state"] = "cancelling"
    return pack_status()


def _run_pack(src, bbox, zmin, zmax):
    for z in range(zmin, zmax + 1):
        x0, x1, y0, y1 = tile_range(bbox, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                with _pack_lock:
                    if _pack["state"] != "running":
                        _pack["state"] = "cancelled"
                        return
                key = f"xyz/{z}/{x}/{y}"
                dest = _pack_path(src, key)
                ok = os.path.exists(dest)
                if not ok:
                    try:
                        data = wms_tile(src, z, x, y)
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with open(dest + ".tmp", "wb") as f:
                            f.write(data)
                        os.replace(dest + ".tmp", dest)
                        ok = True
                    except (httpx.HTTPError, OSError, ValueError):
                        ok = False
                    time.sleep(0.05)       # one request at a time, gently
                with _pack_lock:
                    _pack["done" if ok else "failed"] += 1
    with _pack_lock:
        _pack["state"] = "done"
        _pack["finished"] = time.time()
    log.info("Kartenpaket %s fertig: %s Kacheln, %s fehlgeschlagen", src, _pack["done"], _pack["failed"])


def delete_packs():
    import shutil
    with _pack_lock:
        if _pack.get("state") == "running":
            raise ValueError("Es läuft gerade ein Kartenpaket")
    shutil.rmtree(PACK_DIR, ignore_errors=True)
    return pack_status()


# ---------------------------------------------------------------- raster MBTiles (#58)
def mbtiles_list():
    """Raster MBTiles files in data/tiles/mbtiles: [{id, name, format, minzoom, maxzoom, attribution}]."""
    out = []
    try:
        names = sorted(f for f in os.listdir(MBTILES_DIR) if f.endswith(".mbtiles"))
    except OSError:
        return out
    for f in names:
        meta = {}
        try:
            with sqlite3.connect(f"file:{os.path.join(MBTILES_DIR, f)}?mode=ro", uri=True) as db:
                meta = dict(db.execute("SELECT name, value FROM metadata").fetchall())
        except sqlite3.Error:
            continue
        fmt = (meta.get("format") or "png").lower()
        if fmt not in ("png", "jpg", "jpeg", "webp"):
            continue                       # vector tiles (pbf) need a vector renderer
        out.append({"id": f[:-8], "name": meta.get("name") or f[:-8], "format": fmt,
                    "minzoom": int(meta.get("minzoom") or 0), "maxzoom": int(meta.get("maxzoom") or 18),
                    "attribution": meta.get("attribution") or ""})
    return out


def mbtiles_tile(name, z, x, y):
    """One tile; MBTiles stores rows in TMS order (y counted from the south)."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise KeyError(name)
    path = os.path.join(MBTILES_DIR, name + ".mbtiles")
    if not os.path.isfile(path):
        raise KeyError(name)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        row = db.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                         (z, x, 2 ** z - 1 - y)).fetchone()
    if not row:
        raise FileNotFoundError("Kachel fehlt")
    return row[0]
