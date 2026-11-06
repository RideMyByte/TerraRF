"""Map tiles: proxied base maps, elevation-data overlays and building footprints for display."""
from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from ..model import buildings
from ..sources import overlay, tiles
from .deps import READ, parse_bbox

router = APIRouter()
TILE_HEADERS = {"Cache-Control": "public, max-age=604800"}


async def _proxied(fn, *args, media_type):
    if not tiles.ENABLED:
        raise HTTPException(404, "Kachel-Proxy ist ausgeschaltet")
    try:
        data = await run_in_threadpool(fn, *args)
    except KeyError:
        raise HTTPException(404, "unbekannte Quelle")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Kartendienst nicht erreichbar: {e}")
    return Response(data, media_type=media_type, headers=TILE_HEADERS)


@router.get("/tiles/{src}/{z}/{x}/{y}.png")
async def tile(src: str, z: int, x: int, y: int):
    return await _proxied(tiles.xyz, src, z, x, y, media_type="image/png")


@router.get("/tiles/wms/{src}")
async def tile_wms(src: str, request: Request):
    return await _proxied(tiles.wms, src, dict(request.query_params), media_type="image/jpeg")


@router.get("/api/overlay/{kind}/{z}/{x}/{y}.png", dependencies=READ)
async def overlay_tile(kind: str, z: int, x: int, y: int, g: str = "nrw_dgm", s: str = "nrw_dom"):
    if not re.fullmatch(r"[a-z0-9_:\-]{1,64}", g) or not re.fullmatch(r"[a-z0-9_:\-]{1,64}", s):
        raise HTTPException(400, "ungültige Quelle")
    try:
        data = await run_in_threadpool(overlay.render, kind, z, x, y, g, s)
    except KeyError:
        raise HTTPException(404, "unbekannte Ebene")
    return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/buildings", dependencies=READ)
def building_features(bbox: str):
    """Footprints in the visible area (GeoJSON), for the map layer."""
    return buildings.features_in(parse_bbox(bbox))
