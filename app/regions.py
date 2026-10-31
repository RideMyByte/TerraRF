"""Offline region downloads and the boot-time NRW preload.

NRW sources (WCS) keep a local tile store (data/dem/<source>_<res>m, 10 km tiles, 5 m by default).
Every computation reads from that store and downloads missing tiles on demand; the preload job
fills the whole of NRW in the background, nearest tiles first. Jobs are resumable: existing
tiles are skipped.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from rasterio.crs import CRS
from rasterio.warp import transform_bounds

from . import terrain

log = logging.getLogger("rfplanner.regions")

NRW_BBOX_25832 = tuple(float(v) for v in os.environ.get(
    "RFP_PRELOAD_BBOX", "278000,5560000,542000,5832000").split(","))
WORKERS = int(os.environ.get("RFP_REGION_WORKERS", "2"))
PRELOAD = [x.strip() for x in os.environ.get("RFP_PRELOAD", "nrw_dgm,nrw_dom").split(",") if x.strip()]
PRELOAD_CENTER = tuple(float(v) for v in os.environ.get("RFP_PRELOAD_CENTER", "51.48,7.55").split(","))

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _tiles_for(bbox_src, tile_m):
    x0 = math.floor(bbox_src[0] / tile_m) * tile_m
    y0 = math.floor(bbox_src[1] / tile_m) * tile_m
    out = []
    y = y0
    while y < bbox_src[3]:
        x = x0
        while x < bbox_src[2]:
            out.append((x, y, x + tile_m, y + tile_m))
            x += tile_m
        y += tile_m
    return out


def plan(source_id, area, res_m, bbox_lonlat=None):
    src = terrain.get_source(source_id)
    if src is None:
        raise ValueError("Keine Quelle gewählt")
    if isinstance(src, terrain.CopernicusSource):
        if area == "nrw":
            bbox_lonlat = transform_bounds(CRS.from_epsg(25832), CRS.from_epsg(4326), *NRW_BBOX_25832)
        w, s, e, n = bbox_lonlat
        tiles = [(lat, lon) for lat in range(math.floor(s), math.floor(n) + 1)
                 for lon in range(math.floor(w), math.floor(e) + 1)]
        return {"kind": "copernicus", "tiles": tiles, "n_tiles": len(tiles), "est_mb": len(tiles) * 30,
                "note": "1°-Kacheln, je ca. 20–40 MB (Schätzung)"}
    if not isinstance(src, terrain.WCSSource):
        raise ValueError("Diese Quelle kann nicht vorab geladen werden")
    bbox = NRW_BBOX_25832 if area == "nrw" else transform_bounds(CRS.from_epsg(4326), src.crs, *bbox_lonlat,
                                                                densify_pts=21)
    tiles = _tiles_for(bbox, terrain.TILE_M)
    px = (terrain.TILE_M / res_m) ** 2
    est_mb = len(tiles) * px * 4 * 0.5 / 1e6
    if area == "nrw":
        est_mb *= 0.55
    store = abs(res_m - terrain.TILE_RES) < 1e-6
    have = sum(1 for t in tiles if src.tile_state(t[0], t[1]) != "missing") if store else 0
    return {"kind": "wcs", "tiles": tiles, "n_tiles": len(tiles), "present": have, "est_mb": round(est_mb),
            "tile_km": terrain.TILE_M / 1000, "store": store,
            "note": ("geht in den gemeinsamen NRW-Speicher; " if store else "eigener Ordner; ")
            + "Schätzung, leere Kacheln (außerhalb NRW) werden verworfen"}


def start(source_id, area, res_m, name, bbox_lonlat=None, order_center=None, preload=False):
    src = terrain.get_source(source_id)
    res_m = float(res_m)
    use_store = isinstance(src, terrain.WCSSource) and abs(res_m - terrain.TILE_RES) < 1e-6
    if use_store:
        name = src.store_name
    name = "".join(c for c in name if c.isalnum() or c in "-_") or f"{source_id}_{int(res_m)}m"
    with _lock:
        for j in _jobs.values():
            if j["state"] == "running" and j["name"] == name and j["source"] == source_id:
                return public(j)
    p = plan(source_id, area, res_m, bbox_lonlat)
    if order_center and p["kind"] == "wcs":
        cx, cy = _to_src(src, order_center)
        p["tiles"].sort(key=lambda t: ((t[0] + t[2]) / 2 - cx) ** 2 + ((t[1] + t[3]) / 2 - cy) ** 2)
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "source": source_id, "name": name, "res_m": res_m, "area": area, "preload": preload,
           "total": p["n_tiles"], "done": 0, "skipped": 0, "empty": 0, "failed": 0, "bytes": 0,
           "state": "running", "message": "", "started": time.time(), "errors": [], "cancel": False}
    with _lock:
        _jobs[jid] = job
    threading.Thread(target=_run, args=(job, p, use_store), daemon=True).start()
    return public(job)


def _to_src(src, latlon):
    from rasterio.warp import transform as tr
    xs, ys = tr(CRS.from_epsg(4326), src.crs, [latlon[1]], [latlon[0]])
    return xs[0], ys[0]


def public(job):
    return {k: v for k, v in job.items() if k != "cancel"} | {"elapsed_s": round(time.time() - job["started"], 1)}


def list_jobs():
    with _lock:
        return [public(j) for j in sorted(_jobs.values(), key=lambda j: -j["started"])]


def cancel(jid):
    with _lock:
        if jid in _jobs:
            _jobs[jid]["cancel"] = True


def _run(job, p, use_store):
    src = terrain.get_source(job["source"])
    out_dir = src.store_dir if use_store else os.path.join(terrain.DEM_DIR, job["name"])
    os.makedirs(out_dir, exist_ok=True)
    try:
        if p["kind"] == "copernicus":
            with httpx.Client(timeout=terrain.HTTP_TIMEOUT, follow_redirects=True) as client:
                for lat, lon in p["tiles"]:
                    if job["cancel"]:
                        break
                    path = src._tile(lat, lon, client)
                    job["done"] += 1
                    if path:
                        job["bytes"] += os.path.getsize(path)
            job["message"] = "Copernicus-Kacheln liegen im Cache und werden automatisch genutzt."
        else:
            def one(t):
                x0, y0, x1, y1 = t
                if use_store:
                    st = src.tile_state(x0, y0)
                    if st == "ok":
                        return "skipped", os.path.getsize(src.tile_path(x0, y0))
                    if st == "empty":
                        return "empty", 0
                    path = src.ensure_tile(x0, y0)
                    return ("done", os.path.getsize(path)) if path else ("empty", 0)
                dest = os.path.join(out_dir, f"{job['source']}_{int(x0)}_{int(y0)}_{int(job['res_m'])}m.tif")
                if os.path.exists(dest):
                    return "skipped", os.path.getsize(dest)
                if os.path.exists(dest + ".empty"):
                    return "empty", 0
                with httpx.Client(timeout=terrain.HTTP_TIMEOUT, follow_redirects=True) as client:
                    tmp = dest + ".dl.tif"
                    src.fetch_tile(x0, y0, x1, y1, job["res_m"], tmp, client)
                    if terrain.recompress_tile(tmp):
                        os.replace(tmp, dest)
                        return "done", os.path.getsize(dest)
                    open(dest + ".empty", "w").close()
                    return "empty", 0

            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                futs = {}
                it = iter(p["tiles"])
                for _ in range(WORKERS * 2):
                    t = next(it, None)
                    if t is None:
                        break
                    futs[ex.submit(one, t)] = t
                while futs:
                    for f in as_completed(list(futs)):
                        t = futs.pop(f)
                        try:
                            kind, size = f.result()
                            job[kind] += 1
                            job["bytes"] += size
                        except Exception as e:  # noqa: BLE001
                            job["failed"] += 1
                            if len(job["errors"]) < 5:
                                job["errors"].append(f"{t}: {e}"[:300])
                            log.warning("Kachel %s fehlgeschlagen: %s", t, e)
                        if job["preload"] and getattr(src, "mode", None) == "native":
                            job["cancel"] = True
                            job["message"] = ("Server skaliert nicht serverseitig – ganz NRW in 1 m wäre zu groß. "
                                              "Vorladen gestoppt; Kacheln werden bei Bedarf geladen.")
                        if not job["cancel"]:
                            nt = next(it, None)
                            if nt is not None:
                                futs[ex.submit(one, nt)] = nt
                        break
        job["state"] = "cancelled" if job["cancel"] else ("done" if job["failed"] == 0 else "done_with_errors")
    except Exception as e:  # noqa: BLE001
        log.exception("Region-Download fehlgeschlagen")
        job["state"] = "error"
        job["message"] = str(e)[:500]


def start_preload():
    """Called at startup: fill the NRW tile stores (resumable, nearest tiles first)."""
    for sid in PRELOAD:
        try:
            src = terrain.get_source(sid)
            if not isinstance(src, terrain.WCSSource):
                continue
            j = start(sid, "nrw", terrain.TILE_RES, src.store_name, order_center=PRELOAD_CENTER, preload=True)
            log.info("Vorladen %s gestartet: %s Kacheln", sid, j["total"])
        except Exception as e:  # noqa: BLE001
            log.warning("Vorladen %s nicht gestartet: %s", sid, e)
