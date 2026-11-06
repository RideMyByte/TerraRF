"""Planning computations: coverage per station, station rasters, profiles, point elevation."""
from __future__ import annotations

import re

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from ..model import coverage as cov
from ..model import terrain
from ..site import auth
from .deps import MEMBER, READ, fail, project_for

router = APIRouter()


@router.post("/api/coverage")
async def run_coverage(request: Request, body: dict = Body(...)):
    project = project_for(request, body)
    ids = body.get("station_ids")
    if ids is not None:
        known = {s["id"] for s in project.get("stations") or []}
        ids = [i for i in ids if i in known]
    try:
        max_px = body.get("max_px")
        max_px = int(min(max(int(max_px), 500), cov.MAX_DIM)) if max_px else None
        force = body.get("force") or []
        if force and not auth.can_edit_any(request):
            force = []  # visitors may not force a recomputation
        return await run_in_threadpool(cov.run_coverage, project, ids, max_px, force)
    except (ValueError, terrain.SourceError, KeyError) as e:
        fail(e)


@router.get("/api/station/{key}/{factor}.png", dependencies=READ)
def station_png(key: str, factor: int):
    """Data image of one station (R = dBm + 200, G = line-of-sight class | roof visibility << 2)."""
    if not re.fullmatch(r"[0-9a-f]{8,64}", key) or not 1 <= factor <= 64:
        raise HTTPException(400, "ungültig")
    try:
        data = cov.station_png(key, factor)
    except KeyError:
        raise HTTPException(404, "Berechnung nicht gefunden – bitte neu berechnen")
    return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.post("/api/profile")
async def profile(request: Request, body: dict = Body(...)):
    project = project_for(request, body)
    try:
        return await run_in_threadpool(cov.profile, project, body["station_id"], float(body.get("lat") or 0),
                                       float(body.get("lon") or 0), body.get("rx_height_m"), body.get("rx_gain_dbi"),
                                       body.get("rx_station_id"), body.get("rx_cable_db"))
    except (ValueError, terrain.SourceError, KeyError) as e:
        fail(e)


@router.post("/api/elevation", dependencies=MEMBER)
async def elevation(body: dict = Body(...)):
    try:
        radius = min(30.0, max(0.0, float(body.get("radius_m") or 0)))
        return await run_in_threadpool(cov.point_elevation, body.get("project") or {}, float(body["lat"]), float(body["lon"]), radius)
    except (ValueError, terrain.SourceError, KeyError) as e:
        fail(e)
