"""Drive tests: MeshMapper forwarding, manual entry, CSV, series, and the comparison with the model."""
from __future__ import annotations

import csv
import hmac
import time

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ..analysis import compare
from ..model import terrain
from ..site import auth
from ..store import appconfig, measurements
from .deps import ADMIN, MEMBER, fail, parse_bbox, project_for, station_of

router = APIRouter()


@router.post("/api/ingest/meshmapper")
async def ingest_meshmapper(request: Request):
    """Target for MeshMapper → Settings → API Endpoints (custom API endpoint). Key in header X-API-Key.
    This URL is entered in phones – keep it stable."""
    key = appconfig.get().get("mm_api_key") or ""
    got = request.headers.get("x-api-key", "")
    if not key or not hmac.compare_digest(got.encode(), key.encode()):
        raise HTTPException(401, "ungültiger Schlüssel")
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "kein JSON")
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list) or len(items) > 500:
        raise HTTPException(400, "data muss eine Liste sein")
    rows = [r for it in items if isinstance(it, dict) for r in measurements.from_meshmapper(it)]
    stored = await run_in_threadpool(measurements.add, rows)
    return {"ok": True, "received": len(items), "stored": stored}


def _require_view(request):
    if not (auth.can_edit_any(request) or appconfig.get().get("measurements_public")):
        raise HTTPException(401, "Messungen sind nur nach Anmeldung sichtbar")


@router.get("/api/measurements")
def list_measurements(request: Request, bbox: str | None = None, pk: str | None = None, days: float | None = None,
                      series: int | None = None):
    _require_view(request)
    public = not auth.can_edit_any(request)
    rows = measurements.query(parse_bbox(bbox), pk, days, public=public, series=series)
    return {"rows": rows, "plan": measurements.plan(rows), "stats": measurements.stats(),
            "series": measurements.series_list(public=public)}


@router.put("/api/measurements/series/{series_id}", dependencies=MEMBER)
def update_series(series_id: int, body: dict = Body(...)):
    try:
        changed = measurements.update_series(series_id, body)
    except ValueError as e:
        fail(e)
    if not changed:
        raise HTTPException(404, "Messreihe nicht gefunden")
    return {"ok": True}


@router.post("/api/measurements", dependencies=MEMBER)
def add_measurements(body: dict = Body(...)):
    try:
        if body.get("csv"):
            rows, errors = measurements.from_csv(str(body["csv"]))
            series_key, series_values = f"csv|{time.time():.3f}", None   # one import = one series
        else:
            rows, errors = [measurements.manual(body, require_height=True)], []
            series_key, series_values = measurements.manual_series(body)
    except (ValueError, csv.Error) as e:
        fail(e)
    return {"stored": measurements.add(rows, skey=series_key, series_values=series_values), "errors": errors[:50]}


@router.delete("/api/measurements", dependencies=ADMIN)
def delete_measurements(body: dict = Body(...)):
    return {"deleted": measurements.delete(ids=[int(i) for i in body.get("ids") or []], src=body.get("src"),
                                           series=body.get("series"))}


def _station_measurements(request, body):
    _require_view(request)
    project = project_for(request, body)
    station = station_of(project, body.get("station_id"))
    if not station.get("corescope_pk"):
        raise HTTPException(400, "Station ist nicht mit einem Repeater verknüpft – Messungen werden über den Repeater zugeordnet")
    return project, station, measurements.query(None, station["corescope_pk"], body.get("days"))


@router.post("/api/measurements/compare")
async def compare_measurements(request: Request, body: dict = Body(...)):
    project, station, rows = _station_measurements(request, body)
    try:
        result = await run_in_threadpool(compare.compare_measurements, project, station["id"], rows,
                                         body.get("rx_gain_dbi"), measurements.series_map())
    except (ValueError, KeyError, terrain.SourceError) as e:
        fail(e)
    result["suggestion"] = compare.suggest_settings([result])
    return result
