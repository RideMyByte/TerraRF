"""Kartenebenen aus den Höhendaten, mit denen gerechnet wird: Gelände, Oberfläche, Hindernishöhe.

Gerendert werden nur bereits lokal vorhandene Daten (vorgeladene NRW-Kacheln, eigene GeoTIFFs). Das Ansehen
der Karte löst keine Downloads aus. Ergebnisse werden als PNG-Kacheln auf der Platte zwischengespeichert.
"""
from __future__ import annotations

import io
import math
import os
import threading

import numpy as np
import rasterio
from PIL import Image
from rasterio.warp import Resampling, transform_bounds

from . import terrain

DIR = os.path.join(terrain.CACHE_DIR, "overlay")
MIN_Z, MAX_Z = 11, 18
WORLD = 2 * math.pi * 6378137.0
KINDS = ("relief", "surface", "ndom")
_lock = threading.Semaphore(2)  # höchstens zwei Kacheln gleichzeitig rendern
EMPTY = None


def _empty():
    global EMPTY
    if EMPTY is None:
        b = io.BytesIO()
        Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(b, "PNG")
        EMPTY = b.getvalue()
    return EMPTY


def _grid(z, x, y, pad=1):
    cs = WORLD / (256 * 2 ** z)
    ix0 = -128 * 2 ** z + 256 * x - pad
    iy0 = 256 * y - 128 * 2 ** z - pad
    return terrain.Grid(cs, ix0, iy0, 256 + 2 * pad, 256 + 2 * pad)


def _read_local(sid, grid):
    """Wie source.read(), aber ohne fehlende Kacheln nachzuladen."""
    src = terrain.get_source(sid)
    if src is None:
        return None
    if isinstance(src, terrain.WCSSource):
        b = transform_bounds(terrain.WEBM, src.crs, *grid.bounds, densify_pts=21)
        dst = np.full((grid.ny, grid.nx), np.nan, dtype=np.float32)
        hit = False
        for t in src.tiles_for_bounds(b):
            if src.tile_state(*t) == "ok":
                with rasterio.open(src.tile_path(*t)) as ds:
                    src._warp_into(dst, ds, grid, Resampling.bilinear)
                hit = True
        return dst if hit else None
    if isinstance(src, terrain.LocalFolderSource):
        a = src.read(grid, Resampling.bilinear)
        return a if np.isfinite(a).any() else None
    return None  # Copernicus o. ä.: würde nachladen


def _hillshade(z, cs_true, az=315.0, alt=45.0):
    gy, gx = np.gradient(z, cs_true)
    slope = np.arctan(np.hypot(gx, gy) * 1.0)
    aspect = np.arctan2(-gx, gy)
    a, h = math.radians(az), math.radians(alt)
    sh = np.sin(h) * np.cos(slope) + np.cos(h) * np.sin(slope) * np.cos(a - aspect)
    return np.clip(sh, 0, 1)


# Farbverläufe
_RELIEF = [(0, (38, 115, 77)), (60, (120, 170, 90)), (150, (215, 200, 120)), (300, (190, 140, 90)), (600, (150, 110, 90)), (900, (240, 240, 240))]
_NDOM = [(2, (255, 237, 160)), (6, (254, 178, 76)), (15, (240, 59, 32)), (30, (128, 0, 38))]


def _ramp(v, stops):
    xs = np.array([s[0] for s in stops], dtype=np.float32)
    out = np.zeros(v.shape + (3,), dtype=np.float32)
    for c in range(3):
        out[..., c] = np.interp(v, xs, [s[1][c] for s in stops])
    return out


def render(kind, z, x, y, ground_src, surface_src):
    if kind not in KINDS:
        raise KeyError(kind)
    if not (MIN_Z <= z <= MAX_Z and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        return _empty()
    key = f"{kind}_{ground_src}_{surface_src}".replace(":", "-").replace("/", "-")
    path = os.path.join(DIR, key, str(z), str(x), f"{y}.png")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    with _lock:
        g = _grid(z, x, y)
        lat = terrain.merc_to_lonlat(g.x0 + g.nx * g.cs / 2, g.y1 - g.ny * g.cs / 2)[1]
        cs_true = g.cs * math.cos(math.radians(lat))
        G = _read_local(ground_src, g) if kind in ("relief", "ndom") else None
        S = _read_local(surface_src, g) if kind in ("surface", "ndom") else None
        if (kind != "surface" and G is None) or (kind != "relief" and S is None):
            data = _empty()
        else:
            if kind == "relief":
                zz = np.nan_to_num(G, nan=np.nanmean(G) if np.isfinite(G).any() else 0)
                col = _ramp(zz, _RELIEF) * (0.45 + 0.55 * _hillshade(zz, cs_true))[..., None]
                alpha = np.where(np.isfinite(G), 205, 0)
            elif kind == "surface":
                zz = np.nan_to_num(S, nan=np.nanmean(S) if np.isfinite(S).any() else 0)
                shade = _hillshade(zz, cs_true, alt=40.0)
                col = np.repeat((60 + 195 * shade)[..., None], 3, axis=2)
                alpha = np.where(np.isfinite(S), 215, 0)
            else:
                nd = S - G
                col = _ramp(np.nan_to_num(nd, nan=0), _NDOM)
                alpha = np.where(np.isfinite(nd) & (nd >= 2.0), 190, 0)
            rgba = np.dstack([np.clip(col, 0, 255).astype(np.uint8), alpha.astype(np.uint8)])[1:-1, 1:-1]
            b = io.BytesIO()
            Image.fromarray(rgba, "RGBA").save(b, "PNG", optimize=False, compress_level=6)
            data = b.getvalue()
        if data is not _empty():  # leere Kacheln nicht merken: Daten können später vorgeladen werden
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        return data
