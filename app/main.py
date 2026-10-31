from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool

from . import coverage as cov
from . import propagation as prop
from . import regions, terrain
from .version import VERSION

logging.basicConfig(level=os.environ.get("RFP_LOGLEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rfplanner")

PROJECT_DIR = os.path.join(terrain.DATA_DIR, "projects")
os.makedirs(PROJECT_DIR, exist_ok=True)
STATIC = os.path.join(os.path.dirname(__file__), "static")

@asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=prop.warmup, daemon=True).start()
    if os.environ.get("RFP_PRELOAD", "nrw_dgm,nrw_dom").strip():
        threading.Thread(target=regions.start_preload, daemon=True).start()
    yield


app = FastAPI(title="RF-Planer", version=VERSION, lifespan=lifespan)


def _err(e: Exception, code=400):
    log.exception("Fehler")
    raise HTTPException(status_code=code, detail=str(e))


@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION}


@app.get("/api/config")
def config():
    return {
        "version": VERSION,
        "sources": terrain.list_sources(),
        "defaults": cov.DEFAULT_SETTINGS,
        "dem_dir": terrain.DEM_DIR,
        "tile_res": terrain.TILE_RES,
    }


@app.post("/api/coverage")
async def coverage(project: dict = Body(...)):
    try:
        return await run_in_threadpool(cov.run_coverage, project)
    except (ValueError, terrain.SourceError, KeyError) as e:
        _err(e)


@app.get("/api/run/{run_id}/{layer}.png")
def layer_png(run_id: str, layer: str, req: float | None = None):
    run = cov.get_run(run_id)
    if not run:
        raise HTTPException(404, "Berechnung nicht mehr im Speicher – bitte neu berechnen")
    try:
        data = cov.render_layer(run, layer, req)
    except KeyError:
        raise HTTPException(404, "Layer unbekannt")
    return Response(data, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.post("/api/inspect")
def inspect(body: dict = Body(...)):
    run = cov.get_run(body.get("run_id", ""))
    if not run:
        raise HTTPException(404, "Berechnung nicht mehr im Speicher – bitte neu berechnen")
    return {"stations": cov.inspect_point(run, float(body["lat"]), float(body["lon"]))}


@app.post("/api/profile")
async def profile(body: dict = Body(...)):
    try:
        return await run_in_threadpool(cov.profile, body["project"], body["station_id"], float(body["lat"]),
                                       float(body["lon"]), body.get("rx_height_m"))
    except (ValueError, terrain.SourceError, KeyError) as e:
        _err(e)


@app.post("/api/elevation")
async def elevation(body: dict = Body(...)):
    try:
        return await run_in_threadpool(cov.point_elevation, body["project"], float(body["lat"]), float(body["lon"]))
    except (ValueError, terrain.SourceError, KeyError) as e:
        _err(e)


@app.get("/api/terrain/test")
async def terrain_test(source: str, lat: float = 51.57, lon: float = 6.98, res: float = 10.0):
    """Diagnostics: fetch a ~1 km area from one source."""
    def work():
        t0 = time.time()
        import math
        cs = res / math.cos(math.radians(lat))
        x, y = terrain.lonlat_to_merc(lon, lat)
        g = terrain.Grid.around(x, y, 500 / math.cos(math.radians(lat)), cs)
        src = terrain.get_source(source)
        if src is None:
            return {"ok": True, "note": "keine Quelle"}
        import numpy as np
        arr = src.read(g, terrain.Resampling.bilinear)
        valid = ~np.isnan(arr)
        out = {"ok": bool(valid.any()), "seconds": round(time.time() - t0, 2), "cells": int(arr.size),
               "valid_fraction": round(float(valid.mean()), 3)}
        if valid.any():
            out.update(min=round(float(np.nanmin(arr)), 1), max=round(float(np.nanmax(arr)), 1),
                       center=round(float(arr[arr.shape[0] // 2, arr.shape[1] // 2]), 1))
        mode = getattr(src, "mode", None)
        if mode:
            out["wcs_mode"] = mode
        return out
    try:
        return await run_in_threadpool(work)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)


# ---------------------------------------------------------------- regions + cache
@app.post("/api/regions/plan")
def region_plan(body: dict = Body(...)):
    try:
        p = regions.plan(body["source"], body.get("area", "view"), float(body.get("res_m", 10)), body.get("bbox"))
        p.pop("tiles", None)
        return p
    except (ValueError, terrain.SourceError, KeyError) as e:
        _err(e)


@app.post("/api/regions/start")
def region_start(body: dict = Body(...)):
    try:
        return regions.start(body["source"], body.get("area", "view"), float(body.get("res_m", 10)),
                             body.get("name") or "", body.get("bbox"))
    except (ValueError, terrain.SourceError, KeyError) as e:
        _err(e)


@app.get("/api/regions/jobs")
def region_jobs():
    return regions.list_jobs()


@app.post("/api/regions/cancel/{jid}")
def region_cancel(jid: str):
    regions.cancel(jid)
    return {"ok": True}


@app.get("/api/cache")
def cache_info():
    return terrain.cache_usage()


@app.post("/api/cache/clear")
def cache_clear(body: dict = Body(...)):
    terrain.clear_cache(body.get("parts") or ["terrain", "coverage"])
    with cov._station_cache_lock:
        cov._station_cache.clear()
    return terrain.cache_usage()


# ---------------------------------------------------------------- projects
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _ppath(pid):
    if not _ID.match(pid):
        raise HTTPException(400, "ungültige Projekt-ID")
    return os.path.join(PROJECT_DIR, pid + ".json")


@app.get("/api/projects")
def list_projects():
    out = []
    for fn in sorted(os.listdir(PROJECT_DIR)):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(PROJECT_DIR, fn)) as f:
                    p = json.load(f)
                out.append({"id": p.get("id"), "name": p.get("name"), "stations": len(p.get("stations") or []),
                            "updated": p.get("updated")})
            except Exception:  # noqa: BLE001
                continue
    out.sort(key=lambda p: p.get("updated") or "", reverse=True)
    return out


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    p = _ppath(pid)
    if not os.path.exists(p):
        raise HTTPException(404, "Projekt nicht gefunden")
    with open(p) as f:
        return json.load(f)


@app.put("/api/projects/{pid}")
def save_project(pid: str, project: dict = Body(...)):
    p = _ppath(pid)
    project["id"] = pid
    project["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(project, f, indent=1, ensure_ascii=False)
    os.replace(tmp, p)
    return {"ok": True, "id": pid, "updated": project["updated"]}


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    p = _ppath(pid)
    if os.path.exists(p):
        os.remove(p)
    return {"ok": True}


@app.get("/api/newid")
def new_id():
    return {"id": uuid.uuid4().hex[:12]}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
