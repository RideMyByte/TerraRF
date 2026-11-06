"""Coverage orchestration: per-station computation, caching, compositing, PNGs, profiles."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import threading
import time
from collections import OrderedDict

import numpy as np
from PIL import Image

from . import buildings
from . import propagation as prop
from . import settings as settings_store
from . import terrain
from ..site import sysstat
from .terrain import CACHE_DIR, Grid, SourceError, fill_nans, get_source, load_terrain, lonlat_to_merc, merc_to_lonlat

log = logging.getLogger("terrarf.coverage")

RE_EARTH = 6371000.0
MAX_COMPOSITE_CELLS = int(os.environ.get("RFP_MAX_COMPOSITE_CELLS", "30000000"))
COV_DIR = os.path.join(CACHE_DIR, "coverage")
os.makedirs(COV_DIR, exist_ok=True)
MODEL_VERSION = "10"  # 9: fine near field; 10: building footprints (#25)

DEFAULT_SETTINGS = settings_store.DEFAULTS  # kept for older imports

MARGIN_COLORS = [(215, 48, 39, 255), (252, 141, 89, 255), (254, 224, 139, 255), (145, 207, 96, 255), (26, 150, 65, 255)]


def margin_bins(req):
    """Colour bins relative to the required reserve `req` (fade/location margin)."""
    req = float(req)
    if req > 0:
        lows = [0.0, req, req + 6.0, req + 12.0, req + 20.0]
    else:
        lows = [0.0, 6.0, 12.0, 20.0, 30.0]
    return list(zip(lows, MARGIN_COLORS))
OVERLAP_COLORS = [(215, 25, 28, 255), (253, 174, 97, 255), (166, 217, 106, 255), (26, 150, 65, 255)]
LOS_COLORS = {1: (255, 210, 0, 255), 2: (0, 170, 90, 255)}
VIS_COLOR = (127, 179, 255, 255)


def _model(s):
    return 1 if s.get("diffraction_model") == "deygout" else 0


def merged_settings(project=None):
    """Settings for a computation. Global since 0.5 (#30): the project argument is accepted for
    compatibility, its own ``settings`` are ignored."""
    return settings_store.effective()


# --------------------------------------------------------------------------
# Station computation
# --------------------------------------------------------------------------
class StationResult:
    def __init__(self, sid, grid, prx, vmain, sens, info, vis=None):
        self.vis = vis
        self.key = None
        self.sid = sid
        self.grid = grid
        self.prx = prx  # float32, NaN outside radius
        self.vmain = vmain  # float32
        self.sens = sens
        self.info = info

    @property
    def margin(self):
        return self.prx - self.sens

    def nbytes(self):
        return self.prx.nbytes + self.vmain.nbytes + (self.vis.nbytes if self.vis is not None else 0)


_station_cache: "OrderedDict[str, StationResult]" = OrderedDict()
_station_cache_lock = threading.Lock()
_STATION_CACHE_MAX = int(os.environ.get("RFP_STATION_CACHE", "16"))
# upper limit in bytes: large radii produce results of several hundred MB per station
_STATION_CACHE_BYTES = float(os.environ.get("RFP_STATION_CACHE_MB", "1200")) * 1e6
_compute_lock = threading.Lock()


def _station_key(st, s, cs):
    relevant = {k: st.get(k) for k in (
        "lat", "lon", "height_m", "height_ref", "tx_power_dbm", "freq_mhz", "cable_loss_db", "antenna", "radius_km")}
    rs = {k: s.get(k) for k in ("ground_source", "surface_source", "fallback_source", "rx_height_m", "rx_gain_dbi", "rx_cable_db",
                            "rx_extra_loss_db", "k_factor", "max_profile_points", "max_rays", "diffraction_model",
                            "clutter_mode", "clutter_floor", "clutter_exclude_m", "two_ray", "veg_mode", "near_m", "near_res_m",
                            "veg_gamma_db_m", "veg_max_db", "building_mode")}
    blob = json.dumps([MODEL_VERSION, relevant, rs, round(cs, 6), buildings.version()], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:24]


def terrain_pair(s, grid):
    """Ground and surface for the computation grid. Holes of the main source (e.g. outside NRW) are filled from the
    fallback source (default Copernicus GLO-30, 30 m), so near the border both are combined."""
    G = load_terrain(s["ground_source"], grid, "ground")
    surf_id = s.get("surface_source") or "none"
    S = load_terrain(surf_id, grid, "surface") if surf_id != "none" else None
    warn = []
    if G is None:
        raise ValueError("Keine Gelände-Quelle gewählt")
    for sid in {s["ground_source"], surf_id}:
        src = get_source(sid)
        if getattr(src, "failed_tiles", 0):
            warn.append(f"{src.label}: {src.failed_tiles} Kachel(n) nicht geladen (Netzfehler?) – dort Ersatzdaten")
    fb_id = s.get("fallback_source", "copernicus")
    holes = np.isnan(G).any() or (S is not None and np.isnan(S).any())
    if holes and fb_id not in ("", "none", s["ground_source"], surf_id):
        frac = float(np.isnan(G).mean())
        try:
            C = load_terrain(fb_id, grid, "surface")
        except Exception as e:  # noqa: BLE001 – network/proxy/file: carry on without fallback
            C = None
            warn.append(f"Ersatzquelle nicht verfügbar ({e}) – Lücken mit Nachbarwerten gefüllt")
        if C is not None:
            C = np.asarray(C, dtype=np.float32)
            G = np.where(np.isnan(G), C, G)
            if S is not None:
                S = np.where(np.isnan(S), C, S)
            if frac > 0.005:
                warn.append(f"{frac*100:.0f} % des Gebiets außerhalb der Hauptquelle: dort {get_source(fb_id).label} "
                            "(gröber, Oberflächenmodell auch als Gelände – Hindernisse dort kaum erkennbar)")
    nan_frac = float(np.isnan(G).mean())
    if nan_frac > 0.02:
        warn.append(f"Gelände: {nan_frac*100:.0f} % ohne Daten – dort Ersatzwerte")
    if S is not None and np.isnan(G).any():
        G = fill_nans(G, S)
    else:
        G = fill_nans(G, None)
    if S is None:
        S = G
    else:
        S = fill_nans(S, G)
        S = np.maximum(S, G)
    return G, S, warn


def drop_station_cache(st, s, cs):
    """Ergebnis einer Station verwerfen (erzwungene Neuberechnung)."""
    key = _station_key(st, s, cs)
    with _station_cache_lock:
        _station_cache.pop(key, None)
    for f in os.listdir(COV_DIR):
        if f.startswith(key):
            try:
                os.remove(os.path.join(COV_DIR, f))
            except OSError:
                pass
    with _png_lock:
        for k in [k for k in _png_cache if k.startswith(key + "@")]:
            _png_cache.pop(k, None)


def near_grid(s, x, y, kscale, cs, r_true):
    """Fine elevation grid around the station (#23), returned in the form the kernel expects. rnear = 0 means off
    (computation grid already fine enough, near field 0, or the data could not be loaded)."""
    near_res = float(s.get("near_res_m") or 5.0)
    rnear = min(float(s.get("near_m") or 0.0), r_true)
    dummy = np.zeros((2, 2), dtype=np.float32)
    no_mask = np.zeros((2, 2), dtype=np.uint8)
    if rnear <= 0.0 or cs * kscale <= near_res * 1.01:
        return dummy, dummy, 0.0, 0.0, cs, 0.0, no_mask
    cs2 = near_res / kscale
    g2 = Grid.around(x, y, rnear / kscale + 3 * cs2, cs2)
    try:
        G2, S2, _w = terrain_pair(s, g2)
    except Exception as e:  # noqa: BLE001 – carry on without near field
        log.warning("Nahfeld nicht geladen: %s", e)
        return dummy, dummy, 0.0, 0.0, cs, 0.0, no_mask
    if S2 is None:
        S2 = G2
    if np.isnan(G2).any() or np.isnan(S2).any():
        return dummy, dummy, 0.0, 0.0, cs, 0.0, no_mask
    S2 = np.maximum(S2, G2).astype(np.float32)
    return (G2.astype(np.float32), S2, (x - g2.x0) / cs2 - 0.5, (g2.y1 - y) / cs2 - 0.5, cs2, rnear,
            buildings.mask(g2))


def compute_station(st, s, cs, progress=None):
    """progress(frac, stage) is called while computing (frac 0…1)."""
    progress = progress or (lambda f, stage: None)
    key = _station_key(st, s, cs)
    with _station_cache_lock:
        if key in _station_cache:
            _station_cache.move_to_end(key)
            return _station_cache[key], True
    path = os.path.join(COV_DIR, key + ".npz")
    if os.path.exists(path):
        z = np.load(path, allow_pickle=False)
        grid = Grid(*[float(z["grid"][0])] + [int(v) for v in z["grid"][1:]])
        info = json.loads(str(z["info"]))
        res = StationResult(st["id"], grid, z["prx"], z["vmain"], 0.0, info, z["vis"])
        res.key = key
        _remember(key, res)
        return res, True

    t0 = time.time()
    lat, lon = float(st["lat"]), float(st["lon"])
    x, y = lonlat_to_merc(lon, lat)
    kscale = math.cos(math.radians(lat))
    r_true = float(st.get("radius_km", 10.0)) * 1000.0
    res_true = cs * kscale
    grid = Grid.around(x, y, r_true / kscale + 3 * cs, cs)
    progress(0.02, "Höhendaten")
    G, S, warn = terrain_pair(s, grid)
    B = buildings.mask(grid)
    G2, S2, fx2, fy2, cs2, rnear, B2 = near_grid(s, x, y, kscale, cs, r_true)
    building_mode = 1 if s.get("building_mode") == "diffraction" else 0
    progress(0.15, "Strahlen")
    t_terrain = time.time() - t0

    fx = (x - grid.x0) / cs - 0.5
    fy = (grid.y1 - y) / cs - 0.5
    g0 = float(prop.sample(G2 if rnear > 0 else G, fx2 if rnear > 0 else fx, fy2 if rnear > 0 else fy))
    s0 = float(prop.sample(S2 if rnear > 0 else S, fx2 if rnear > 0 else fx, fy2 if rnear > 0 else fy))
    base = s0 if st.get("height_ref") == "surface" else g0
    ztx = base + float(st.get("height_m", 10.0))

    max_pts = int(s["max_profile_points"])
    step = max(res_true, r_true / max_pts)
    n_bins = int(math.ceil(r_true / step))
    sub = int(min(16, max(1, math.ceil(step / res_true * 1.5))))
    n_rays = int(min(int(s["max_rays"]), max(360, math.ceil(2 * math.pi * r_true / res_true))))
    f_mhz = float(st["freq_mhz"])
    lam = prop.C / (f_mhz * 1e6)
    re = RE_EARTH * float(s["k_factor"])
    cm, excl, clR, clT, flR, flT = prop.clutter_params(s)
    vg, vam = prop.veg_params(s, f_mhz)
    t1 = time.time()
    parts = []
    nblk = 20 if n_rays >= 2000 else 5
    edges = np.linspace(0, n_rays, nblk + 1).astype(int)
    for bi_ in range(nblk):  # in blocks so that the progress becomes visible
        parts.append(prop.coverage_rays(
            G, S, fx, fy, cs, kscale, ztx, ztx - g0, float(s["rx_height_m"]), lam, f_mhz, n_rays, n_bins, step, sub,
            re, _model(s), cm, excl, clR, clT, flR, flT, bool(s.get("two_ray", True)), vg, vam,
            int(edges[bi_]), int(edges[bi_ + 1]), G2, S2, fx2, fy2, cs2, rnear, B, B2, building_mode))
        progress(0.15 + 0.75 * (bi_ + 1) / nblk, "Strahlen")
    loss, vmain, dz, lclut, lpe, lveg, vis_rays = (np.concatenate([p_[i] for p_ in parts]) for i in range(7))
    progress(0.92, "Raster")
    t_kernel = time.time() - t1
    loss = loss + lclut + lpe + lveg
    vis_rays[:, 0] = 1
    d = np.arange(n_bins + 1, dtype=np.float64) * step
    d[0] = step * 0.5
    d3 = np.sqrt(d[None, :] ** 2 + dz.astype(np.float64) ** 2)
    elev = np.degrees(np.arctan2(dz, d[None, :]))
    az = (np.arange(n_rays) * 360.0 / n_rays)[:, None]
    gain = prop.antenna_gain(az, elev, st.get("antenna") or {})
    prx_rays = (float(st["tx_power_dbm"]) - float(st.get("cable_loss_db", 0.0)) + gain
                - prop.fspl_db(d3, float(st["freq_mhz"])) - loss
                + float(s["rx_gain_dbi"]) - float(s["rx_cable_db"])
                - float(s.get("rx_extra_loss_db", 0.0))).astype(np.float32)
    prx_rays[:, 0] = prx_rays[:, 1]
    vmain[:, 0] = -1e9

    # rasterise rays onto the grid (nearest ray / bin)
    cols = (np.arange(grid.nx) + 0.5) * cs + grid.x0
    rows = grid.y1 - (np.arange(grid.ny) + 0.5) * cs
    dxm = ((cols - x) * kscale)[None, :]
    dym = ((rows - y) * kscale)[:, None]
    dist = np.hypot(dxm, dym)
    th = np.mod(np.arctan2(dxm, dym), 2 * math.pi)
    ri = np.rint(th / (2 * math.pi) * n_rays).astype(np.int32) % n_rays
    bi = np.clip(np.rint(dist / step).astype(np.int32), 0, n_bins)
    prx = prx_rays[ri, bi]
    vm = vmain[ri, bi].astype(np.float32)
    vis = vis_rays[ri, bi].astype(np.int8)
    outside = dist > r_true
    vis[outside] = 0
    prx[outside] = np.nan
    vm[outside] = np.nan

    info = {
        "ground_m": round(g0, 1), "surface_m": round(s0, 1), "antenna_alt_m": round(ztx, 1),
        "rays": n_rays, "bins": n_bins, "step_m": round(step, 1), "res_m": round(res_true, 1),
        "t_terrain_s": round(t_terrain, 2), "t_kernel_s": round(t_kernel, 2), "t_total_s": round(time.time() - t0, 2), "warnings": warn,
    }
    res = StationResult(st["id"], grid, prx.astype(np.float32), vm, 0.0, info, vis)
    res.key = key
    np.savez(path, grid=np.array([grid.cs, grid.ix0, grid.iy0, grid.nx, grid.ny], dtype=np.float64),
             prx=res.prx, vmain=res.vmain, vis=vis, info=json.dumps(info))
    _remember(key, res)
    return res, False


def _remember(key, res):
    with _station_cache_lock:
        _station_cache[key] = res
        _station_cache.move_to_end(key)
        while len(_station_cache) > 1 and (len(_station_cache) > _STATION_CACHE_MAX or
                                           sum(r.nbytes() for r in _station_cache.values()) > _STATION_CACHE_BYTES):
            _station_cache.popitem(last=False)


# --------------------------------------------------------------------------
# Runs (composite of all stations)
# --------------------------------------------------------------------------
GRID_LAT = float(os.environ.get("RFP_GRID_LAT", "51.5"))  # fixed reference latitude of the calculation grid


def project_cs(project, s):
    """Cell size of the common grid. Deliberately independent of the stations: if it followed their mean
    latitude, adding a far-away station could change it and invalidate the cache of all other stations."""
    return float(s["res_m"]) / math.cos(math.radians(GRID_LAT))


MAX_DIM = int(os.environ.get("RFP_MAX_LAYER_PX", "3000"))  # max. edge length of one station image


def run_coverage(project, station_ids=None, max_dim=None, force=None):
    """Compute (or fetch from cache) all requested stations. No server-side compositing:
    the browser stacks the per-station data images itself."""
    s = merged_settings(project)
    stations = [st for st in project.get("stations") or []
                if (st["id"] in station_ids if station_ids is not None else st.get("enabled", True))]
    if not stations:
        raise ValueError("Keine aktive Station")
    cs = project_cs(project, s)
    t0 = time.time()
    results = []
    for st in stations:
        if st["id"] in (force or ()):
            drop_station_cache(st, s, cs)
    for st in stations:
        with sysstat.activity(f"Abdeckung {st.get('name') or st['id']}", station=st["id"], progress=0.0, stage="wartet") as h:
            with _compute_lock:  # one heavy computation at a time
                def prog(f, stage, h=h):
                    h["progress"], h["stage"] = f, stage
                r, cached = compute_station(st, s, cs, progress=prog)
        results.append((st, r, cached))
    # one common downsampling factor keeps all station images on the same pixel grid
    md = max_dim or MAX_DIM
    f = max(1, max(math.ceil(max(r.grid.nx, r.grid.ny) / md) for _, r, _ in results))
    out = []
    for st, r, cached in results:
        g = r.grid
        dg = _down_grid(g, f)
        kscale = math.cos(math.radians(float(st["lat"])))
        sens = prop.sensitivity_dbm(st, s.get("noise_rise_db", 0.0))
        cell_area = (g.cs * kscale) ** 2
        covered = np.nan_to_num(r.prx - sens, nan=-999) >= float(s["min_margin_db"])
        out.append({
            "id": st["id"], "name": st.get("name"), "key": r.key, "sensitivity_dbm": round(sens, 1),
            "png": f"/api/station/{r.key}/{f}.png",
            "gx0": dg.ix0, "gy0": dg.iy0, "w": dg.nx, "h": dg.ny, "bounds": dg.latlon_bounds(),
            "area_km2": round(float(covered.sum()) * cell_area / 1e6, 2),
            "los_area_km2": round(float((np.nan_to_num(r.vmain, nan=1e9) <= 0).sum()) * cell_area / 1e6, 2),
            "cached": cached, **r.info,
        })
    return {
        "factor": f, "cell_m": round(cs * f, 3), "stations": out, "t_total_s": round(time.time() - t0, 2),
        "legend": {
            "margin": [{"from": b, "color": "#%02x%02x%02x" % c[:3]} for b, c in margin_bins(s["min_margin_db"])],
            "overlap": [{"n": i + 1, "color": "#%02x%02x%02x" % c[:3]} for i, c in enumerate(OVERLAP_COLORS)],
            "los": [{"cls": 3, "label": "Dach/Baumkrone sichtbar", "color": "#7fb3ff"},
                    {"cls": 1, "label": "Empfänger: Sichtlinie, Fresnelzone verletzt", "color": "#ffd200"},
                    {"cls": 2, "label": "Empfänger: Fresnelzone ≥ 60 % frei", "color": "#00aa5a"}],
        },
    }


def _down_grid(g, f):
    if f == 1:
        return g
    ix0 = -(-g.ix0 // f)  # ceil
    iy0 = -(-g.iy0 // f)
    nx = (g.ix0 + g.nx - 1) // f - ix0 + 1
    ny = (g.iy0 + g.ny - 1) // f - iy0 + 1
    return Grid(g.cs * f, ix0, iy0, max(nx, 1), max(ny, 1))


_png_cache: "OrderedDict[str, bytes]" = OrderedDict()
_png_lock = threading.Lock()
_png_gen_lock = threading.Lock()  # render one image at a time (memory)


def station_png(key, f):
    """Data image of one station: R = level (dBm + 200, 0 = outside), G = sight class | roof visible << 2."""
    ck = f"{key}@{f}"
    with _png_lock:
        if ck in _png_cache:
            _png_cache.move_to_end(ck)
            return _png_cache[ck]
    disk = os.path.join(COV_DIR, f"{key}_{f}.png")
    if os.path.exists(disk):
        with open(disk, "rb") as fh:
            return fh.read()
    with _png_gen_lock:
        if os.path.exists(disk):  # created meanwhile by a parallel request
            with open(disk, "rb") as fh:
                return fh.read()
        data = _make_png(key, f)
        tmp = disk + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, disk)
    with _png_lock:
        _png_cache[ck] = data
        while len(_png_cache) > 64:
            _png_cache.popitem(last=False)
    return data


def _make_png(key, f):
    r = load_station(key)
    if r is None:
        raise KeyError(key)
    g = r.grid
    if f > 1:
        dg = _down_grid(g, f)
        rows = dg.iy0 * f - g.iy0 + np.arange(dg.ny) * f
        cols = dg.ix0 * f - g.ix0 + np.arange(dg.nx) * f
        rows = np.clip(rows, 0, g.ny - 1)
        cols = np.clip(cols, 0, g.nx - 1)
        prx = r.prx[np.ix_(rows, cols)]
        vm = r.vmain[np.ix_(rows, cols)]
        vis = r.vis[np.ix_(rows, cols)] if r.vis is not None else np.zeros_like(prx, dtype=np.int8)
    else:
        prx, vm = r.prx, r.vmain
        vis = r.vis if r.vis is not None else np.zeros_like(prx, dtype=np.int8)
    valid = ~np.isnan(prx)
    R = np.where(valid, np.clip(np.rint(np.nan_to_num(prx) + 200), 1, 255), 0).astype(np.uint8)
    los = prop.los_class(np.nan_to_num(vm, nan=1e9)).astype(np.uint8)
    G = np.where(valid, los | (vis.astype(np.uint8) << 2), 0).astype(np.uint8)
    rgba = np.dstack([R, G, np.zeros_like(R), np.full_like(R, 255)])
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def load_station(key):
    with _station_cache_lock:
        if key in _station_cache:
            return _station_cache[key]
    if not all(c in "0123456789abcdef" for c in key):
        return None
    path = os.path.join(COV_DIR, key + ".npz")
    if not os.path.exists(path):
        return None
    z = np.load(path, allow_pickle=False)
    grid = Grid(*[float(z["grid"][0])] + [int(v) for v in z["grid"][1:]])
    res = StationResult("?", grid, z["prx"], z["vmain"], 0.0, json.loads(str(z["info"])), z["vis"])
    res.key = key
    _remember(key, res)
    return res


def profile(project, station_id, lat, lon, rx_height=None, rx_gain=None, rx_station_id=None, rx_cable=None,
            rx_extra_loss=None):
    """Terrain profile and link budget station -> point.
    rx_station_id: the far end is a station of the project -> its height, antenna (pattern towards the
    transmitter) and cable loss replace the client values (point-to-point and repeater links)."""
    s = merged_settings(project)
    if rx_gain not in (None, ""):
        s = {**s, "rx_gain_dbi": float(rx_gain)}
    if rx_cable not in (None, ""):
        s = {**s, "rx_cable_db": float(rx_cable)}
    if rx_extra_loss not in (None, ""):
        s = {**s, "rx_extra_loss_db": float(rx_extra_loss)}
    st = next((x for x in project.get("stations") or [] if x["id"] == station_id), None)
    if st is None:
        raise KeyError("Station nicht gefunden")
    rx_st = None
    if rx_station_id:
        rx_st = next((x for x in project.get("stations") or [] if x["id"] == rx_station_id), None)
        if rx_st is None:
            raise KeyError("Gegenstation nicht gefunden")
        if rx_st["id"] == st["id"]:
            raise ValueError("Gegenstelle ist dieselbe Station")
        lat, lon = float(rx_st["lat"]), float(rx_st["lon"])
        rx_height = float(rx_st.get("height_m", 10))
        s = {**s, "rx_cable_db": float(rx_st.get("cable_loss_db", 0) or 0), "rx_extra_loss_db": 0.0}
    cs = project_cs(project, s)
    lat0, lon0 = float(st["lat"]), float(st["lon"])
    x0, y0 = lonlat_to_merc(lon0, lat0)
    x1, y1 = lonlat_to_merc(lon, lat)
    kscale = math.cos(math.radians((lat0 + lat) / 2))
    D = math.hypot(x1 - x0, y1 - y0) * kscale
    if D < 1:
        raise ValueError("Punkt liegt auf der Station")
    pad = 4 * cs
    ix0 = int(math.floor((min(x0, x1) - pad) / cs))
    ix1 = int(math.ceil((max(x0, x1) + pad) / cs))
    iy0 = int(math.floor(-(max(y0, y1) + pad) / cs))
    iy1 = int(math.ceil(-(min(y0, y1) - pad) / cs))
    grid = Grid(cs, ix0, iy0, ix1 - ix0, iy1 - iy0)
    G, S, warn = terrain_pair(s, grid)
    B = buildings.mask(grid)
    res_true = cs * kscale
    n = int(min(6000, max(50, math.ceil(D / (res_true / 2)))))
    if float(s.get("near_m") or 0) > 0 and res_true > float(s.get("near_res_m") or 5.0) * 1.01:
        n = int(min(6000, max(n, math.ceil(D / (float(s.get("near_res_m") or 5.0) / 2)))))  # fine near field (#23)
    t = np.linspace(0, 1, n + 1)
    fxs = ((x0 + (x1 - x0) * t) - grid.x0) / cs - 0.5
    fys = (grid.y1 - (y0 + (y1 - y0) * t)) / cs - 0.5
    ground = np.array([prop.sample(G, a, b) for a, b in zip(fxs, fys)])
    surf = np.array([prop.sample(S, a, b) for a, b in zip(fxs, fys)])
    bld = np.array([prop.mask_at(B, a, b) for a, b in zip(fxs, fys)], dtype=np.int8)
    d = t * D
    # sample the station's near field finely (#23), as in the area computation
    G2, S2, fx2, fy2, cs2, rnear, B2 = near_grid(s, x0, y0, math.cos(math.radians(lat0)), cs, D)
    if rnear > 0:
        k0 = math.cos(math.radians(lat0))
        for i in np.nonzero(d < rnear)[0]:
            dd = d[i] / k0 / cs2
            ux, uy = (x1 - x0) / (D / kscale), (y1 - y0) / (D / kscale)
            ground[i] = prop.sample(G2, fx2 + ux * dd, fy2 - uy * dd)
            surf[i] = prop.sample(S2, fx2 + ux * dd, fy2 - uy * dd)
            bld[i] = prop.mask_at(B2, fx2 + ux * dd, fy2 - uy * dd)
    # the antenna's own cell counts as free (as in the area computation) – otherwise interpolation at the house
    # wall decides about 20 dB more or less
    own = 0.6 * (float(s.get("near_res_m") or 5.0) if rnear > 0 else res_true)
    surf = np.where(d < own, ground, surf)
    surf = np.maximum(surf, ground)
    re = RE_EARTH * float(s["k_factor"])
    bulge = d * (D - d) / (2 * re)  # bulge relative to the chord, for display
    base = surf[0] if st.get("height_ref") == "surface" else ground[0]
    ztx = base + float(st.get("height_m", 10))
    rxh = float(s["rx_height_m"] if rx_height is None else rx_height)
    if rx_st is not None and rx_st.get("height_ref") == "surface":
        rxh += float(surf[-1] - ground[-1])  # height above roof -> height above ground
    zrx = ground[-1] + rxh
    # for the loss computation: earth curvature relative to the tx tangent
    curv = d * d / (2 * re)
    z = (surf - curv).astype(np.float64)
    zg = (ground - curv).astype(np.float64)
    nd = (surf - ground).astype(np.float64)
    z[0] = ztx
    nd[0] = 0.0
    gsum = np.concatenate([[0.0], np.cumsum(ground[1:])])
    f_mhz = float(st["freq_mhz"])
    lam = prop.C / (f_mhz * 1e6)
    cm, excl, clR, clT, flR, flT = prop.clutter_params(s)
    vg, vam = prop.veg_params(s, f_mhz)
    loss_diff, v1, lclut, lpe, R_cl, lveg, dveg = prop.endpoint_terms(
        d.astype(np.float64), z, z.copy(), zg, nd, gsum, n, ztx, ztx - ground[0], rxh, lam, f_mhz, _model(s),
        cm, excl, clR, clT, flR, flT, bool(s.get("two_ray", True)), vg, vam, bld,
        1 if s.get("building_mode") == "diffraction" else 0)
    dz = (zrx - curv[-1]) - ztx
    d3 = math.hypot(D, dz)
    elev = math.degrees(math.atan2(dz, D))
    az = (math.degrees(math.atan2(x1 - x0, y1 - y0)) + 360) % 360
    gtx = float(prop.antenna_gain(np.array([az]), np.array([elev]), st.get("antenna") or {})[0])
    if rx_st is not None:  # gain of the far end towards the transmitter (reverse direction, elevation inverted)
        grx = float(prop.antenna_gain(np.array([(az + 180.0) % 360.0]), np.array([-elev]), rx_st.get("antenna") or {})[0])
        s = {**s, "rx_gain_dbi": grx}
    fspl = float(prop.fspl_db(np.array([d3]), f_mhz)[0])
    extra = float(s.get("rx_extra_loss_db", 0.0))
    total_loss = fspl + loss_diff + lclut + lpe + lveg
    prx = (float(st["tx_power_dbm"]) - float(st.get("cable_loss_db", 0)) + gtx - total_loss
           + float(s["rx_gain_dbi"]) - float(s["rx_cable_db"]) - extra)
    sens = prop.sensitivity_dbm(st, s.get("noise_rise_db", 0.0))
    hb_eff = max(ztx - ground[0], ztx - float(np.mean(ground)))
    hata = {e: round(prop.hata_db(D, f_mhz, hb_eff, rxh, e), 1) for e in ("urban", "suburban", "open")}
    # display: terrain with bulge, straight LOS line, first Fresnel zone
    los_line = ztx + (zrx - ztx) * t
    fr = np.sqrt(np.maximum(lam * d * (D - d) / D, 0))
    k = max(1, n // 700)
    sl = slice(None, None, k)
    idx = np.arange(n + 1)[sl]
    if idx[-1] != n:
        idx = np.append(idx, n)
    clearance = (los_line - (surf + bulge)) / np.where(fr > 0, fr, np.inf)
    worst = int(np.argmin(clearance[1:-1])) + 1 if n > 2 else 0
    return {
        "station": st.get("name"), "distance_m": round(D, 1), "azimuth_deg": round(az, 1),
        "elevation_deg": round(elev, 2),
        "d": np.round(d[idx], 1).tolist(),
        "ground": np.round((ground + bulge)[idx], 2).tolist(),
        "surface": np.round((surf + bulge)[idx], 2).tolist(),
        # 1 where the surface at a sample is a known building footprint (drawn grey, #25)
        "building": [int(v) for v in np.maximum.reduceat(np.where(surf - ground > 1.0, bld, 0), idx)] if len(idx) else [],
        "los": np.round(los_line[idx], 2).tolist(),
        "fresnel": np.round(fr[idx], 2).tolist(),
        "tx_alt": round(ztx, 1), "rx_alt": round(zrx, 1),
        "worst_clearance_ratio": round(float(clearance[worst]), 2) if worst else None,
        "worst_at_m": round(float(d[worst]), 0) if worst else None,
        "los_class": int(prop.los_class(v1)),
        "budget": {
            "tx_power_dbm": float(st["tx_power_dbm"]), "tx_cable_db": float(st.get("cable_loss_db", 0)),
            "tx_gain_dbi": round(gtx, 2), "fspl_db": round(fspl, 1), "diffraction_db": round(float(loss_diff), 1),
            "reflection_db": round(float(lpe), 1), "clutter_db": round(float(lclut), 1),
            "obstacle_db": round(float(lveg), 1), "obstacle_len_m": round(float(dveg), 0),
            "clutter_height_m": round(float(R_cl), 1),
            "rx_gain_dbi": round(float(s["rx_gain_dbi"]), 2), "rx_cable_db": float(s["rx_cable_db"]), "rx_extra_db": extra,
            "rx_station": rx_st.get("name") if rx_st is not None else None, "rx_height_m": round(rxh, 1),
            "total_loss_db": round(total_loss, 1),
            "prx_dbm": round(prx, 1), "sensitivity_dbm": round(sens, 1), "margin_db": round(prx - sens, 1),
            "required_margin_db": float(s["min_margin_db"]),
        },
        "hata": {"loss_db": hata, "hb_eff_m": round(hb_eff, 1),
                 "valid": bool(150 <= f_mhz <= 1500 and 1000 <= D <= 20000 and 30 <= hb_eff <= 200 and 1 <= rxh <= 10)},
        "warnings": warn,
    }


# #43: a repeater whose GPS position lies on or right next to a building is assumed to stand on its roof.
MAST_SEARCH_M = 8.0       # how far from the position to look for a roof (consumer GPS is off by a few metres)
MIN_BUILDING_M = 2.5      # objects lower than this are not taken as a building (cars, hedges, noise)
ABOVE_ROOF_M = 1.0        # antenna this much above the highest roof point nearby (wish in #43)


def point_elevation(project, lat, lon, radius_m=0.0):
    """Ground and surface height at a point. With radius_m also the highest object (surface − ground) within that
    radius on the fine near-field grid, and a mast height guess for a repeater there (#43)."""
    s = merged_settings(project)
    cs = project_cs(project, s)
    x, y = lonlat_to_merc(lon, lat)
    grid = Grid.around(x, y, 3 * cs, cs)
    G, S, _ = terrain_pair(s, grid)
    fx = (x - grid.x0) / cs - 0.5
    fy = (grid.y1 - y) / cs - 0.5
    out = {"ground_m": round(float(prop.sample(G, fx, fy)), 1), "surface_m": round(float(prop.sample(S, fx, fy)), 1)}
    if radius_m > 0:
        fine = min(cs, float(s.get("near_res_m") or 5.0))
        g = Grid.around(x, y, radius_m + 2 * fine, fine)
        G2, S2, _ = terrain_pair(s, g)
        xs = g.x0 + (np.arange(G2.shape[1]) + 0.5) * fine
        ys = g.y1 - (np.arange(G2.shape[0]) + 0.5) * fine
        inside = ((xs[None, :] - x) ** 2 + (ys[:, None] - y) ** 2) <= (radius_m + fine / 2) ** 2
        heights = np.where(inside & np.isfinite(S2 - G2), S2 - G2, 0.0)
        highest = float(heights.max()) if heights.size else 0.0
        out["object_max_m"] = round(highest, 1)
        out["mast_guess_m"] = round(highest + ABOVE_ROOF_M, 1) if highest >= MIN_BUILDING_M else None
    return out


def mast_height_guess(project, lat, lon):
    """Antenna height above ground for a repeater that sits on a roof (#43), or None if no building is near."""
    try:
        return point_elevation(project, lat, lon, MAST_SEARCH_M).get("mast_guess_m")
    except Exception:  # noqa: BLE001 – no elevation data there: no guess
        return None


def clear_memory_caches():
    """Forget computed stations and rendered PNGs held in RAM (after the disk cache was cleared)."""
    with _station_cache_lock:
        _station_cache.clear()
    with _png_lock:
        _png_cache.clear()


__all__ = ["run_coverage", "station_png", "profile", "point_elevation", "DEFAULT_SETTINGS"]
