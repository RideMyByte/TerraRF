"""Building footprints (issue #25).

The surface model (DOM) already contains every building as height, but it cannot tell a house from a tree.
Footprints add that distinction: the model can treat buildings differently from vegetation
(``settings.building_mode``), the profile draws them grey, and the map can show them.

Footprints come from a GeoJSON file the admin uploads (e.g. an overpass-turbo export with ``building``) or
from an Overpass API request for a map area. The *height* still comes from the DOM – most OpenStreetMap
buildings have no height tag. OpenStreetMap data is © OpenStreetMap contributors, ODbL.

Stored in ``data/buildings/footprints.json`` (WGS84 rings). For a computation the footprints that touch the
grid are rasterised onto it (``mask``).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
import time

import httpx
import numpy as np
from rasterio.features import rasterize
from rasterio.transform import from_origin

from ..paths import DATA_DIR
from .terrain import lonlat_to_merc

log = logging.getLogger("terrarf.buildings")

DIR = os.path.join(DATA_DIR, "buildings")
PATH = os.path.join(DIR, "footprints.json")
OVERPASS_URL = os.environ.get("RFP_OVERPASS_URL", "https://overpass-api.de/api/interpreter")
MAX_OSM_AREA_KM2 = float(os.environ.get("RFP_OSM_MAX_KM2", "30"))
MAX_FEATURES_RESPONSE = 6000
os.makedirs(DIR, exist_ok=True)

_lock = threading.Lock()
_state = None   # {"features": [...], "sources": [...], "index": [(minx, miny, maxx, maxy, merc_rings)], "version": str}


def _load():
    global _state
    if _state is None:
        try:
            with open(PATH) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {"features": [], "sources": []}
        _state = _build_index(data)
    return _state


def _build_index(data):
    index = []
    for feat in data["features"]:
        rings = [[lonlat_to_merc(lon, lat) for lon, lat in ring] for ring in feat["rings"]]
        xs = [p[0] for p in rings[0]]
        ys = [p[1] for p in rings[0]]
        index.append((min(xs), min(ys), max(xs), max(ys), rings))
    raw = json.dumps([f["id"] for f in data["features"]]).encode()
    data["index"] = index
    data["version"] = hashlib.sha1(raw).hexdigest()[:10] if data["features"] else "none"
    return data


def _save(data):
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"features": data["features"], "sources": data["sources"]}, f)
    os.replace(tmp, PATH)


def version():
    """Changes whenever the footprints change; part of the coverage cache key."""
    return _load()["version"]


def status():
    s = _load()
    return {"count": len(s["features"]), "sources": s["sources"], "version": s["version"],
            "attribution": "© OpenStreetMap contributors (ODbL)" if s["features"] else None}


# ---------------------------------------------------------------- import
def _rings_from_geometry(geom):
    """GeoJSON Polygon / MultiPolygon -> list of polygons, each a list of rings [[lon, lat], …]."""
    if not isinstance(geom, dict):
        return []
    if geom.get("type") == "Polygon":
        return [geom.get("coordinates") or []]
    if geom.get("type") == "MultiPolygon":
        return list(geom.get("coordinates") or [])
    return []


def _feature(fid, rings, props):
    rings = [[[round(float(p[0]), 7), round(float(p[1]), 7)] for p in ring] for ring in rings if len(ring) >= 4]
    if not rings:
        return None
    out = {"id": str(fid), "rings": rings}
    levels = props.get("building:levels")
    try:
        if levels is not None:
            out["levels"] = float(str(levels).replace(",", "."))
    except ValueError:
        pass
    if props.get("height"):
        out["height"] = props.get("height")
    return out


def parse_geojson(text):
    """Footprints from a GeoJSON FeatureCollection. Every polygon counts as a building (an overpass-turbo
    export with ``building`` contains nothing else); features without geometry are skipped."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ValueError(f"not valid JSON: {e}") from None
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise ValueError("expected a GeoJSON FeatureCollection")
    out = []
    for n, f in enumerate(data.get("features") or []):
        props = f.get("properties") or {}
        fid = props.get("@id") or f.get("id") or f"feature/{n}"
        for k, poly in enumerate(_rings_from_geometry(f.get("geometry"))):
            feat = _feature(fid if k == 0 else f"{fid}#{k}", poly, props)
            if feat:
                out.append(feat)
    return out


def parse_overpass(data):
    """Footprints from an Overpass JSON response (``out geom``): ways and multipolygon relations."""
    out = []
    for el in data.get("elements") or []:
        tags = el.get("tags") or {}
        if el.get("type") == "way" and el.get("geometry"):
            ring = [[p["lon"], p["lat"]] for p in el["geometry"]]
            feat = _feature(f"way/{el['id']}", [ring], tags)
            if feat:
                out.append(feat)
        elif el.get("type") == "relation":
            outers = [[[p["lon"], p["lat"]] for p in m.get("geometry") or []]
                      for m in el.get("members") or [] if m.get("role") == "outer" and m.get("geometry")]
            for k, ring in enumerate(outers):   # inner rings (courtyards) are ignored: a simplification
                feat = _feature(f"relation/{el['id']}#{k}", [ring], tags)
                if feat:
                    out.append(feat)
    return out


def add(features, source_name):
    """Merge footprints; an id that exists already is replaced."""
    global _state
    with _lock:
        s = _load()
        by_id = {f["id"]: f for f in s["features"]}
        new = sum(1 for f in features if f["id"] not in by_id)
        by_id.update({f["id"]: f for f in features})
        lons = [p[0] for f in features for p in f["rings"][0]]
        lats = [p[1] for f in features for p in f["rings"][0]]
        bbox = [min(lats), min(lons), max(lats), max(lons)] if lons else None
        data = {"features": list(by_id.values()),
                "sources": s["sources"] + [{"name": source_name, "count": len(features), "new": new, "bbox": bbox,
                                            "imported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}]}
        _save(data)
        _state = _build_index(data)
    return {"received": len(features), "new": new, "total": len(by_id)}


def clear():
    global _state
    with _lock:
        data = {"features": [], "sources": []}
        _save(data)
        _state = _build_index(data)


def fetch_osm(bbox):
    """Load footprints for bbox = (min_lat, min_lon, max_lat, max_lon) from the Overpass API."""
    min_lat, min_lon, max_lat, max_lon = (float(v) for v in bbox)
    km2 = (max_lat - min_lat) * 111.2 * (max_lon - min_lon) * 111.2 * math.cos(math.radians((min_lat + max_lat) / 2))
    if not 0 < km2 <= MAX_OSM_AREA_KM2:
        raise ValueError(f"area must be between 0 and {MAX_OSM_AREA_KM2:g} km² (is {km2:.1f})")
    query = (f'[out:json][timeout:90];(way["building"]({min_lat},{min_lon},{max_lat},{max_lon});'
             f'relation["building"]({min_lat},{min_lon},{max_lat},{max_lon}););out geom;')
    with httpx.Client(timeout=120, headers={"User-Agent": "TerraRF (self-hosted coverage planner)"}) as c:
        r = c.post(OVERPASS_URL, data={"data": query})
        r.raise_for_status()
    feats = parse_overpass(r.json())
    return add(feats, f"OpenStreetMap {min_lat:.4f},{min_lon:.4f} – {max_lat:.4f},{max_lon:.4f}")


# ---------------------------------------------------------------- use
def mask(grid):
    """0/1 raster (uint8) of footprints on an EPSG:3857 grid; all zeros when none touch it."""
    out_shape = (grid.ny, grid.nx)
    s = _load()
    if not s["index"]:
        return np.zeros(out_shape, dtype=np.uint8)
    x0, y1 = grid.x0, grid.y1
    x1, y0 = x0 + grid.nx * grid.cs, y1 - grid.ny * grid.cs
    shapes = [({"type": "Polygon", "coordinates": rings}, 1) for (a, b, c, d, rings) in s["index"]
              if c >= x0 and a <= x1 and d >= y0 and b <= y1]
    if not shapes:
        return np.zeros(out_shape, dtype=np.uint8)
    return rasterize(shapes, out_shape=out_shape, transform=from_origin(x0, y1, grid.cs, grid.cs),
                     fill=0, dtype="uint8")


def features_in(bbox, limit=MAX_FEATURES_RESPONSE):
    """GeoJSON of the footprints in bbox = (min_lat, min_lon, max_lat, max_lon), for the map layer."""
    min_lat, min_lon, max_lat, max_lon = (float(v) for v in bbox)
    feats = []
    for f in _load()["features"]:
        ring = f["rings"][0]
        lon0, lat0 = ring[0]
        if min_lat <= lat0 <= max_lat and min_lon <= lon0 <= max_lon:
            feats.append({"type": "Feature", "properties": {"id": f["id"], "levels": f.get("levels")},
                          "geometry": {"type": "Polygon", "coordinates": f["rings"]}})
            if len(feats) >= limit:
                break
    return {"type": "FeatureCollection", "features": feats, "truncated": len(feats) >= limit}
