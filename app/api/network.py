"""The real network: MeshCore repeaters and their neighbours from CoreScope, and the link comparison."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ..analysis import compare
from ..model import terrain
from ..site import auth
from ..sources import corescope, linkhistory
from .deps import ADMIN, READ, fail, parse_bbox, project_for, station_of

router = APIRouter()


@router.get("/api/corescope/status", dependencies=READ)
def status():
    return corescope.status()


@router.post("/api/corescope/sync", dependencies=ADMIN)
def sync():
    try:
        return {"started": corescope.start_sync()}
    except ValueError as e:
        fail(e)


@router.get("/api/corescope/nodes", dependencies=READ)
def nodes(bbox: str | None = None):
    return {"nodes": corescope.nodes(parse_bbox(bbox)), **corescope.status()}


@router.get("/api/corescope/links/{pk}", dependencies=READ)
async def links(request: Request, pk: str, refresh: bool = False):
    """Neighbours of one repeater: counts both ways, directly reported SNR, path-derived SNR, last heard."""
    try:
        return await run_in_threadpool(corescope.links, pk.lower(), refresh and auth.is_admin(request))
    except Exception as e:  # noqa: BLE001
        fail(e, 502 if not isinstance(e, ValueError) else 400)


@router.get("/api/corescope/links/{pk}/history/{partner}", dependencies=READ)
def link_history(pk: str, partner: str):
    """What TerraRF recorded about one link over time (#52): scatter and course of the directly reported SNR."""
    if not (corescope.PK.match(pk.lower()) and corescope.PK.match(partner.lower())):
        raise HTTPException(400, "ungültiger Schlüssel")
    return linkhistory.history(pk.lower(), partner.lower())


@router.get("/api/corescope/node/{pk}", dependencies=READ)
async def node(pk: str):
    try:
        return await run_in_threadpool(corescope.node_meta, pk.lower())
    except ValueError as e:
        fail(e)
    except httpx.HTTPError as e:
        fail(e, 502)


@router.get("/api/corescope/overrides", dependencies=READ)
def overrides():
    return corescope.overrides()


@router.put("/api/corescope/overrides/{pk}", dependencies=ADMIN)
def set_override(pk: str, body: dict = Body(...)):
    try:
        return {"pk": pk.lower(), "values": corescope.set_override(pk.lower(), body)}
    except (ValueError, TypeError) as e:
        fail(e)


@router.post("/api/corescope/calibrate")
async def calibrate(request: Request, body: dict = Body(...)):
    """Model vs. directly reported SNR for the station's repeater (zero hop)."""
    project = project_for(request, body)
    station = station_of(project, body.get("station_id"))
    if not station.get("corescope_pk"):
        raise HTTPException(400, "Station ist nicht mit einem CoreScope-Knoten verknüpft")

    def work():
        found = corescope.links(str(station["corescope_pk"]).lower())
        partners = [x for x in found["links"] if x.get("snr") is not None]
        wanted = body.get("partners")  # only those the current map filters show
        if wanted is not None:
            wanted = {str(x).lower() for x in wanted}
            partners = [x for x in partners if x["pk"] in wanted]
        result = compare.calibrate(project, station["id"], partners, float(body.get("rx_height_m", 10)),
                                   float(body.get("rx_gain_dbi", 3)), float(body.get("rx_cable_db", 1)),
                                   corescope.overrides())
        result["window_days"] = found.get("window_days")
        return result
    try:
        return await run_in_threadpool(work)
    except (ValueError, KeyError, terrain.SourceError, httpx.HTTPError) as e:
        fail(e)
