"""Site administration (admin only): website/access settings, global model settings, elevation data,
caches, building footprints."""
from __future__ import annotations

import math
import time

import httpx
import numpy as np
from fastapi import APIRouter, Body
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ..model import buildings, coverage, settings, terrain
from ..sources import regions
from ..store import appconfig
from .deps import ADMIN, READ, fail

router = APIRouter()


# ---------------------------------------------------------------- website & access
@router.get("/api/admin/appconfig", dependencies=ADMIN)
def get_appconfig():
    return appconfig.get()


@router.put("/api/admin/appconfig", dependencies=ADMIN)
def put_appconfig(body: dict = Body(...)):
    try:
        return appconfig.update(body)
    except ValueError as e:
        fail(e)


# ---------------------------------------------------------------- global model settings (#30)
@router.get("/api/admin/settings", dependencies=ADMIN)
def get_settings():
    return {"values": settings.effective(), "changed": settings.stored(), "spec": settings.spec(),
            "model_version": coverage.MODEL_VERSION}


@router.put("/api/admin/settings", dependencies=ADMIN)
def put_settings(body: dict = Body(...)):
    try:
        return {"values": settings.save(body.get("values") or {}), "changed": settings.stored()}
    except ValueError as e:
        fail(e)


# ---------------------------------------------------------------- building footprints (#25)
@router.get("/api/admin/buildings", dependencies=ADMIN)
def buildings_status():
    return buildings.status()


@router.post("/api/admin/buildings/geojson", dependencies=ADMIN)
async def buildings_upload(body: dict = Body(...)):
    try:
        feats = await run_in_threadpool(buildings.parse_geojson, str(body.get("geojson") or ""))
        if not feats:
            raise ValueError("no polygons in the file")
        return await run_in_threadpool(buildings.add, feats, str(body.get("name") or "GeoJSON")[:80])
    except ValueError as e:
        fail(e)


@router.post("/api/admin/buildings/osm", dependencies=ADMIN)
async def buildings_osm(body: dict = Body(...)):
    try:
        return await run_in_threadpool(buildings.fetch_osm, body["bbox"])
    except (ValueError, KeyError, TypeError) as e:
        fail(e)
    except httpx.HTTPError as e:
        fail(e, 502)


@router.delete("/api/admin/buildings", dependencies=ADMIN)
def buildings_clear():
    buildings.clear()
    return buildings.status()


# ---------------------------------------------------------------- elevation data
@router.get("/api/terrain/test", dependencies=ADMIN)
async def terrain_test(source: str, lat: float = 51.57, lon: float = 6.98, res: float = 10.0):
    """Diagnostics: read a ~1 km area from one source."""
    def work():
        t0 = time.time()
        cs = res / math.cos(math.radians(lat))
        x, y = terrain.lonlat_to_merc(lon, lat)
        g = terrain.Grid.around(x, y, 500 / math.cos(math.radians(lat)), cs)
        src = terrain.get_source(source)
        if src is None:
            return {"ok": True, "note": "keine Quelle"}
        arr = src.read(g, terrain.Resampling.bilinear)
        valid = ~np.isnan(arr)
        out = {"ok": bool(valid.any()), "seconds": round(time.time() - t0, 2), "cells": int(arr.size),
               "valid_fraction": round(float(valid.mean()), 3)}
        if valid.any():
            out.update(min=round(float(np.nanmin(arr)), 1), max=round(float(np.nanmax(arr)), 1),
                       center=round(float(arr[arr.shape[0] // 2, arr.shape[1] // 2]), 1))
        if getattr(src, "mode", None):
            out["wcs_mode"] = src.mode
        return out
    try:
        return await run_in_threadpool(work)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)


@router.post("/api/regions/plan", dependencies=ADMIN)
def region_plan(body: dict = Body(...)):
    try:
        p = regions.plan(body["source"], body.get("area", "view"), float(body.get("res_m", 10)), body.get("bbox"))
        p.pop("tiles", None)
        return p
    except (ValueError, terrain.SourceError, KeyError) as e:
        fail(e)


@router.post("/api/regions/start", dependencies=ADMIN)
def region_start(body: dict = Body(...)):
    try:
        return regions.start(body["source"], body.get("area", "view"), float(body.get("res_m", 10)),
                             body.get("name") or "", body.get("bbox"))
    except (ValueError, terrain.SourceError, KeyError) as e:
        fail(e)


@router.get("/api/regions/jobs", dependencies=READ)
def region_jobs():
    return regions.list_jobs()


@router.post("/api/regions/cancel/{job_id}", dependencies=ADMIN)
def region_cancel(job_id: str):
    regions.cancel(job_id)
    return {"ok": True}


@router.get("/api/cache", dependencies=ADMIN)
def cache_info():
    return terrain.cache_usage()


@router.post("/api/cache/clear", dependencies=ADMIN)
def cache_clear(body: dict = Body(...)):
    terrain.clear_cache(body.get("parts") or ["terrain", "coverage"])
    coverage.clear_memory_caches()
    return terrain.cache_usage()
