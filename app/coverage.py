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

from . import propagation as prop
from .terrain import CACHE_DIR, Grid, fill_nans, load_terrain, lonlat_to_merc, merc_to_lonlat

log = logging.getLogger("rfplanner.coverage")

RE_EARTH = 6371000.0
MAX_COMPOSITE_CELLS = int(os.environ.get("RFP_MAX_COMPOSITE_CELLS", "30000000"))
COV_DIR = os.path.join(CACHE_DIR, "coverage")
os.makedirs(COV_DIR, exist_ok=True)
MODEL_VERSION = "7"

DEFAULT_SETTINGS = {
    "ground_source": "nrw_dgm",
    "surface_source": "nrw_dom",
    "res_m": 10.0,
    "rx_height_m": 1.5,
    "rx_gain_dbi": 0.0,
    "rx_cable_db": 0.0,
    "rx_extra_loss_db": 0.0,
    "noise_rise_db": 0.0,
    "k_factor": 1.3333,
    "min_margin_db": 10.0,
    "max_profile_points": 500,
    "max_rays": 7200,
    "diffraction_model": "deygout",
    "clutter_mode": "auto",
    "clutter_floor": "none",
    "clutter_exclude_m": 60.0,
    "two_ray": True,
    "veg_mode": "auto",
    "veg_gamma_db_m": None,
    "veg_max_db": None,
}

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


def merged_settings(project):
    s = dict(DEFAULT_SETTINGS)
    s.update({k: v for k, v in (project.get("settings") or {}).items() if v is not None})
    return s


def hex_rgba(h, a=255):
    h = (h or "#3388ff").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)
    except ValueError:
        return (51, 136, 255, a)


# --------------------------------------------------------------------------
# Station computation
# --------------------------------------------------------------------------
class StationResult:
    def __init__(self, sid, grid, prx, vmain, sens, info, vis=None):
        self.vis = vis
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
        return self.prx.nbytes + self.vmain.nbytes


_station_cache: "OrderedDict[str, StationResult]" = OrderedDict()
_station_cache_lock = threading.Lock()
_STATION_CACHE_MAX = int(os.environ.get("RFP_STATION_CACHE", "16"))
_compute_lock = threading.Lock()


def _station_key(st, s, cs):
    relevant = {k: st.get(k) for k in (
        "lat", "lon", "height_m", "height_ref", "tx_power_dbm", "freq_mhz", "cable_loss_db", "antenna", "radius_km")}
    rs = {k: s[k] for k in ("ground_source", "surface_source", "rx_height_m", "rx_gain_dbi", "rx_cable_db",
                            "rx_extra_loss_db", "k_factor", "max_profile_points", "max_rays", "diffraction_model",
                            "clutter_mode", "clutter_floor", "clutter_exclude_m", "two_ray", "veg_mode",
                            "veg_gamma_db_m", "veg_max_db")}
    blob = json.dumps([MODEL_VERSION, relevant, rs, round(cs, 6)], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:24]


def terrain_pair(s, grid):
    G = load_terrain(s["ground_source"], grid, "ground")
    surf_id = s.get("surface_source") or "none"
    S = load_terrain(surf_id, grid, "surface") if surf_id != "none" else None
    warn = []
    if G is None:
        raise ValueError("Keine Gelände-Quelle gewählt")
    nan_frac = float(np.isnan(G).mean())
    if nan_frac > 0.02:
        warn.append(f"Gelände: {nan_frac*100:.0f} % ohne Daten (außerhalb der Quelle?) – dort Ersatzwerte")
    if S is not None and np.isnan(G).any():
        G = fill_nans(G, S)  # e.g. outside NRW: use surface model as ground
    else:
        G = fill_nans(G, None)
    if S is None:
        S = G
    else:
        S = fill_nans(S, G)
        S = np.maximum(S, G)
    return G, S, warn


def compute_station(st, s, cs):
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
        _remember(key, res)
        return res, True

    t0 = time.time()
    lat, lon = float(st["lat"]), float(st["lon"])
    x, y = lonlat_to_merc(lon, lat)
    kscale = math.cos(math.radians(lat))
    r_true = float(st.get("radius_km", 10.0)) * 1000.0
    res_true = cs * kscale
    grid = Grid.around(x, y, r_true / kscale + 3 * cs, cs)
    G, S, warn = terrain_pair(s, grid)
    t_terrain = time.time() - t0

    fx = (x - grid.x0) / cs - 0.5
    fy = (grid.y1 - y) / cs - 0.5
    g0 = float(prop.sample(G, fx, fy))
    s0 = float(prop.sample(S, fx, fy))
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
    loss, vmain, dz, lclut, lpe, lveg, vis_rays = prop.coverage_rays(
        G, S, fx, fy, cs, kscale, ztx, ztx - g0, float(s["rx_height_m"]), lam, f_mhz, n_rays, n_bins, step, sub,
        re, _model(s), cm, excl, clR, clT, flR, flT, bool(s.get("two_ray", True)), vg, vam)
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
    np.savez(path, grid=np.array([grid.cs, grid.ix0, grid.iy0, grid.nx, grid.ny], dtype=np.float64),
             prx=res.prx, vmain=res.vmain, vis=vis, info=json.dumps(info))
    _remember(key, res)
    return res, False


def _remember(key, res):
    with _station_cache_lock:
        _station_cache[key] = res
        _station_cache.move_to_end(key)
        while len(_station_cache) > _STATION_CACHE_MAX:
            _station_cache.popitem(last=False)


# --------------------------------------------------------------------------
# Runs (composite of all stations)
# --------------------------------------------------------------------------
_runs: "OrderedDict[str, dict]" = OrderedDict()
_runs_lock = threading.Lock()


def project_cs(project, s):
    sts = project.get("stations") or []
    lat_ref = np.mean([float(x["lat"]) for x in sts]) if sts else 51.5
    lat_ref = round(float(lat_ref) * 2) / 2  # quantise so the grid stays stable
    return float(s["res_m"]) / math.cos(math.radians(lat_ref))


def run_coverage(project):
    s = merged_settings(project)
    stations = [st for st in project.get("stations") or [] if st.get("enabled", True)]
    if not stations:
        raise ValueError("Keine aktive Station")
    cs = project_cs(project, s)
    t0 = time.time()
    results = []
    with _compute_lock:  # one heavy computation at a time
        for st in stations:
            r, cached = compute_station(st, s, cs)
            r.sens = prop.sensitivity_dbm(st, s.get("noise_rise_db", 0.0))
            r.sid = st["id"]
            results.append((st, r, cached))

    U = Grid.union([r.grid for _, r, _ in results])
    f = max(1, math.ceil(math.sqrt(U.nx * U.ny / MAX_COMPOSITE_CELLS)))
    C = Grid(U.cs * f, U.ix0 // f, U.iy0 // f, math.ceil((U.ix0 + U.nx) / f) - U.ix0 // f,
             math.ceil((U.iy0 + U.ny) / f) - U.iy0 // f)

    best = np.full((C.ny, C.nx), -np.inf, dtype=np.float32)
    count = np.zeros((C.ny, C.nx), dtype=np.int16)
    best_idx = np.full((C.ny, C.nx), -1, dtype=np.int16)
    los = np.zeros((C.ny, C.nx), dtype=np.int8)
    vis = np.zeros((C.ny, C.nx), dtype=np.int8)
    per_station = []  # (slices, margin float16) for live re-thresholding
    min_margin = float(s["min_margin_db"])
    stats = []
    for k, (st, r, cached) in enumerate(results):
        g = r.grid
        # map composite cell centres to station grid indices
        cx = ((C.ix0 + np.arange(C.nx) + 0.5) * C.cs - g.x0) / g.cs
        cy = ((C.iy0 + np.arange(C.ny) + 0.5) * C.cs - g.iy0 * g.cs) / g.cs
        ci = np.floor(cx).astype(np.int64)
        ri = np.floor(cy).astype(np.int64)
        okc = np.where((ci >= 0) & (ci < g.nx))[0]
        okr = np.where((ri >= 0) & (ri < g.ny))[0]
        if len(okc) == 0 or len(okr) == 0:
            continue
        sub_m = (r.prx[np.ix_(ri[okr], ci[okc])] - r.sens).astype(np.float32)
        sub_v = r.vmain[np.ix_(ri[okr], ci[okc])]
        valid = ~np.isnan(sub_m)
        sl = np.ix_(okr, okc)
        bm = best[sl]
        bidx = best_idx[sl]
        better = valid & (sub_m > bm)
        bm[better] = sub_m[better]
        bidx[better] = k
        best[sl] = bm
        best_idx[sl] = bidx
        cnt = count[sl]
        cnt[valid & (sub_m >= min_margin)] += 1
        count[sl] = cnt
        lc = np.where(valid, prop.los_class(np.nan_to_num(sub_v, nan=1e9)), 0).astype(np.int8)
        los[sl] = np.maximum(los[sl], lc)
        if r.vis is not None:
            vis[sl] = np.maximum(vis[sl], r.vis[np.ix_(ri[okr], ci[okc])])
        per_station.append((okr, okc, sub_m.astype(np.float16)))

        kscale = math.cos(math.radians(float(st["lat"])))
        m_full = r.prx - r.sens
        covered = np.nan_to_num(m_full, nan=-999) >= min_margin
        cell_area = (g.cs * kscale) ** 2
        stats.append({
            "id": st["id"], "name": st.get("name"), "sensitivity_dbm": round(r.sens, 1),
            "area_km2": round(float(covered.sum()) * cell_area / 1e6, 2),
            "los_area_km2": round(float((np.nan_to_num(r.vmain, nan=1e9) <= 0).sum()) * cell_area / 1e6, 2),
            "cached": cached, **r.info,
        })

    run_id = hashlib.sha1(json.dumps([project.get("stations"), s, time.time()], sort_keys=True, default=str)
                          .encode()).hexdigest()[:16]
    run = {
        "id": run_id, "grid": C, "best": best, "count": count, "best_idx": best_idx, "los": los, "vis": vis,
        "per_station": per_station,
        "stations": results, "settings": s, "colors": [hex_rgba(st.get("color")) for st, _, _ in results],
        "png": {},
    }
    with _runs_lock:
        _runs[run_id] = run
        while len(_runs) > 4:
            _runs.popitem(last=False)
    layers = [
        {"id": "margin", "label": "Bester Pegel (Reserve über Empfindlichkeit)"},
        {"id": "overlap", "label": "Überlappung (Anzahl Stationen)"},
        {"id": "server", "label": "Bester Server (Stationsfarbe)"},
        {"id": "los", "label": "Sichtlinie / Fresnelzone"},
    ]
    return {
        "run_id": run_id,
        "bounds": C.latlon_bounds(),
        "downsample": f,
        "layers": layers,
        "stations": stats,
        "station_bounds": {st["id"]: r.grid.latlon_bounds() for st, r, _ in results},
        "t_total_s": round(time.time() - t0, 2),
        "legend": {
            "margin": [{"from": b, "color": "#%02x%02x%02x" % c[:3]} for b, c in margin_bins(s["min_margin_db"])],
            "required_margin_db": float(s["min_margin_db"]),
            "overlap": [{"n": i + 1, "color": "#%02x%02x%02x" % c[:3]} for i, c in enumerate(OVERLAP_COLORS)],
            "los": [{"cls": 3, "label": "Dach/Baumkrone sichtbar", "color": "#7fb3ff"},
                    {"cls": 1, "label": "Empfänger: Sichtlinie, Fresnelzone verletzt", "color": "#ffd200"},
                    {"cls": 2, "label": "Empfänger: Fresnelzone ≥ 60 % frei", "color": "#00aa5a"}],
        },
    }


def get_run(run_id):
    with _runs_lock:
        return _runs.get(run_id)


def _margin_rgba(m, req=0.0):
    out = np.zeros(m.shape + (4,), dtype=np.uint8)
    m = np.nan_to_num(m, nan=-np.inf, neginf=-np.inf)
    for lo, col in margin_bins(req):
        out[m >= lo] = col
    return out


def render_layer(run, layer, req=None):
    req = float(run["settings"]["min_margin_db"] if req is None else req)
    key = f"{layer}|{req:.1f}"
    if key in run["png"]:
        return run["png"][key]
    C = run["grid"]
    if layer == "margin":
        rgba = _margin_rgba(run["best"], req)
    elif layer in ("overlap", "server"):
        if layer == "overlap":
            c = np.zeros((C.ny, C.nx), dtype=np.int16)
            for okr, okc, m in run["per_station"]:
                sl = np.ix_(okr, okc)
                c[sl] += (np.nan_to_num(m.astype(np.float32), nan=-999) >= req)
            rgba = np.zeros(c.shape + (4,), dtype=np.uint8)
            for i, col in enumerate(OVERLAP_COLORS):
                rgba[c >= i + 1] = col
        else:
            idx = run["best_idx"]
            ok = run["best"] >= req
            rgba = np.zeros(idx.shape + (4,), dtype=np.uint8)
            for k, col in enumerate(run["colors"]):
                rgba[(idx == k) & ok] = col
    elif layer == "los":
        l = run["los"]
        rgba = np.zeros(l.shape + (4,), dtype=np.uint8)
        rgba[run["vis"] > 0] = VIS_COLOR
        for cls, col in LOS_COLORS.items():
            rgba[l == cls] = col
    elif layer.startswith("station:"):
        sid = layer.split(":", 1)[1]
        for st, r, _ in run["stations"]:
            if st["id"] == sid:
                rgba = _margin_rgba(r.prx - r.sens, req)
                break
        else:
            raise KeyError(layer)
    else:
        raise KeyError(layer)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=False, compress_level=3)
    data = buf.getvalue()
    if len(run["png"]) > 40:
        run["png"].clear()
    run["png"][key] = data
    return data


# --------------------------------------------------------------------------
# Inspect + profile
# --------------------------------------------------------------------------
def inspect_point(run, lat, lon):
    x, y = lonlat_to_merc(lon, lat)
    out = []
    for st, r, _ in run["stations"]:
        g = r.grid
        c = int(math.floor((x - g.x0) / g.cs))
        rr = int(math.floor((g.y1 - y) / g.cs))
        entry = {"id": st["id"], "name": st.get("name"), "color": st.get("color")}
        if 0 <= c < g.nx and 0 <= rr < g.ny and not np.isnan(r.prx[rr, c]):
            p = float(r.prx[rr, c])
            v = float(r.vmain[rr, c])
            entry.update({"prx_dbm": round(p, 1), "margin_db": round(p - r.sens, 1),
                          "los": int(prop.los_class(v)), "in_range": True,
                          "roof_visible": bool(r.vis[rr, c]) if r.vis is not None else None})
        else:
            entry["in_range"] = False
        sx, sy = lonlat_to_merc(float(st["lon"]), float(st["lat"]))
        entry["distance_km"] = round(math.hypot(x - sx, y - sy) * math.cos(math.radians(lat)) / 1000, 2)
        out.append(entry)
    return out


def profile(project, station_id, lat, lon, rx_height=None):
    s = merged_settings(project)
    st = next((x for x in project.get("stations") or [] if x["id"] == station_id), None)
    if st is None:
        raise KeyError("Station nicht gefunden")
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
    res_true = cs * kscale
    n = int(min(6000, max(50, math.ceil(D / (res_true / 2)))))
    t = np.linspace(0, 1, n + 1)
    fxs = ((x0 + (x1 - x0) * t) - grid.x0) / cs - 0.5
    fys = (grid.y1 - (y0 + (y1 - y0) * t)) / cs - 0.5
    ground = np.array([prop.sample(G, a, b) for a, b in zip(fxs, fys)])
    surf = np.array([prop.sample(S, a, b) for a, b in zip(fxs, fys)])
    surf = np.maximum(surf, ground)
    d = t * D
    re = RE_EARTH * float(s["k_factor"])
    bulge = d * (D - d) / (2 * re)  # bulge relative to the chord, for display
    base = surf[0] if st.get("height_ref") == "surface" else ground[0]
    ztx = base + float(st.get("height_m", 10))
    rxh = float(s["rx_height_m"] if rx_height is None else rx_height)
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
        cm, excl, clR, clT, flR, flT, bool(s.get("two_ray", True)), vg, vam)
    dz = (zrx - curv[-1]) - ztx
    d3 = math.hypot(D, dz)
    elev = math.degrees(math.atan2(dz, D))
    az = (math.degrees(math.atan2(x1 - x0, y1 - y0)) + 360) % 360
    gtx = float(prop.antenna_gain(np.array([az]), np.array([elev]), st.get("antenna") or {})[0])
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
            "rx_gain_dbi": float(s["rx_gain_dbi"]), "rx_cable_db": float(s["rx_cable_db"]), "rx_extra_db": extra,
            "total_loss_db": round(total_loss, 1),
            "prx_dbm": round(prx, 1), "sensitivity_dbm": round(sens, 1), "margin_db": round(prx - sens, 1),
            "required_margin_db": float(s["min_margin_db"]),
        },
        "hata": {"loss_db": hata, "hb_eff_m": round(hb_eff, 1),
                 "valid": bool(150 <= f_mhz <= 1500 and 1000 <= D <= 20000 and 30 <= hb_eff <= 200 and 1 <= rxh <= 10)},
        "warnings": warn,
    }


def point_elevation(project, lat, lon):
    s = merged_settings(project)
    cs = project_cs(project, s)
    x, y = lonlat_to_merc(lon, lat)
    grid = Grid.around(x, y, 3 * cs, cs)
    G, S, _ = terrain_pair(s, grid)
    fx = (x - grid.x0) / cs - 0.5
    fy = (grid.y1 - y) / cs - 0.5
    return {"ground_m": round(float(prop.sample(G, fx, fy)), 1), "surface_m": round(float(prop.sample(S, fx, fy)), 1)}


__all__ = ["run_coverage", "get_run", "render_layer", "inspect_point", "profile", "point_elevation",
           "DEFAULT_SETTINGS", "merc_to_lonlat"]
