"""Kachel-Proxy mit Festplatten-Cache: Der Browser lädt Kartenkacheln nur von TerraRF selbst.

Damit gehen keine IP-Adressen der Besucher an OpenStreetMap, OpenTopoMap oder Geobasis NRW.
Die Kartendienste sehen nur den Server; gecachte Kacheln entlasten ihre (spendenfinanzierten) Server.
Regeln der OSM Tile Usage Policy: eindeutiger User-Agent, Caching (mind. 7 Tage), keine Massenabrufe.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from urllib.parse import urlencode

import httpx

from . import terrain
from .version import VERSION

log = logging.getLogger("terrarf.tiles")

ENABLED = os.environ.get("RFP_TILE_PROXY", "true").strip().lower() not in ("0", "false", "no", "off")
MAX_AGE_S = 14 * 86400
CACHE_MB = float(os.environ.get("RFP_TILE_CACHE_MB", "2000") or 2000)
DIR = os.path.join(terrain.DATA_DIR, "tiles")
UA = f"TerraRF/{VERSION} tile proxy (+https://github.com/RideMyByte/TerraRF)"

# XYZ sources: id -> (URL template, max zoom)
XYZ = {
    "osm": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", 19),
    "otm": ("https://a.tile.opentopomap.org/{z}/{x}/{y}.png", 17),
}
# WMS (NRW DOP): only these parameters are passed through
WMS = {"nrwdop": ("https://www.wms.nrw.de/geobasis/wms_nw_dop", {"layers": "nw_dop_rgb", "format": "image/jpeg"})}
WMS_KEYS = ("bbox", "width", "height", "srs", "crs", "version", "styles", "transparent")

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
    """Älteste Kacheln löschen, wenn der Cache größer als RFP_TILE_CACHE_MB wird."""
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


def _fetch(src, key, url, params=None):
    p = _path(src, key)
    data = _cached(p)
    if data is not None:
        return data
    try:
        r = client().get(url, params=params)
        r.raise_for_status()
    except httpx.HTTPError:
        stale = p if os.path.exists(p) else None  # lieber alte Kachel als keine
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
