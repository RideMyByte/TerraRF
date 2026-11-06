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
from ..store import appconfig, ingestkeys, measurements, projects
from .deps import MEMBER, fail, parse_bbox, project_for, station_of, visible_project

router = APIRouter()


@router.post("/api/ingest/meshmapper")
async def ingest_meshmapper(request: Request):
    """Target for MeshMapper → Settings → API Endpoints (custom API endpoint). Key in header X-API-Key.
    This URL is entered in phones – keep it stable. The key decides where the measurements go (#53): a project's
    key → that project, the key from Administration → the admin's own measurements."""
    got = request.headers.get("x-api-key", "")
    admin_key = appconfig.get().get("mm_api_key") or ""
    project_id = ingestkeys.project_for_key(got)
    if project_id is None and not (admin_key and hmac.compare_digest(got.encode(), admin_key.encode())):
        raise HTTPException(401, "ungültiger Schlüssel")
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "kein JSON")
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list) or len(items) > 500:
        raise HTTPException(400, "data muss eine Liste sein")
    rows = [r for it in items if isinstance(it, dict) for r in measurements.from_meshmapper(it)]
    stored = await run_in_threadpool(lambda: measurements.add(rows, project_id=project_id))
    return {"ok": True, "received": len(items), "stored": stored}


# ---------------------------------------------------------------- who sees which measurements (#53)
def scope_for(request, project_id, edit=False):
    """(scope, anonymous) for the measurements of a project.

    A project's measurements are its own plus – for projects of the admin – the admin's pool (everything from before
    0.5.5 and the admin key). Whoever may open the project sees them, so a public project shows its measurements to
    visitors, without time, device and notes. edit=True: the request must be allowed to change the project.
    Without a project only the admin gets a scope (everything)."""
    role = auth.role(request)
    if not project_id:
        if role != "admin":
            raise HTTPException(400, "Messungen gehören zu einem Projekt – bitte erst das Projekt speichern")
        return None, False
    project = visible_project(request, project_id)
    editable = projects.can_edit(role, project, auth.account_project(request))
    if edit and not editable:
        raise HTTPException(403, "Messungen dieses Projekts darf nur ändern, wer das Projekt bearbeiten darf")
    return measurements.Scope([project_id], pool=projects.owner(project) == "admin"), not editable


@router.get("/api/measurements")
def list_measurements(request: Request, project: str | None = None, bbox: str | None = None, pk: str | None = None,
                      days: float | None = None, series: int | None = None):
    scope, anonymous = scope_for(request, project)
    rows = measurements.query(parse_bbox(bbox), pk, days, public=anonymous, series=series, scope=scope)
    return {"rows": rows, "plan": measurements.plan(rows), "stats": measurements.stats(scope),
            "series": measurements.series_list(public=anonymous, scope=scope)}


@router.put("/api/measurements/series/{series_id}", dependencies=MEMBER)
def update_series(request: Request, series_id: int, body: dict = Body(...)):
    scope, _ = scope_for(request, body.get("project"), edit=True)
    if scope is not None and not measurements.series_in_scope(series_id, scope):
        raise HTTPException(404, "Messreihe nicht gefunden")
    try:
        changed = measurements.update_series(series_id, {k: v for k, v in body.items() if k != "project"})
    except ValueError as e:
        fail(e)
    if not changed:
        raise HTTPException(404, "Messreihe nicht gefunden")
    return {"ok": True}


@router.post("/api/measurements", dependencies=MEMBER)
def add_measurements(request: Request, body: dict = Body(...)):
    """Manual entry or CSV import into a project (#53; the admin may leave the project out → own pool)."""
    project_id = body.get("project") or None
    scope_for(request, project_id, edit=True)
    try:
        if body.get("csv"):
            rows, errors = measurements.from_csv(str(body["csv"]))
            series_key, series_values = f"csv|{time.time():.3f}", None   # one import = one series
        else:
            rows, errors = [measurements.manual(body, require_height=True)], []
            series_key, series_values = measurements.manual_series(body)
    except (ValueError, csv.Error) as e:
        fail(e)
    return {"stored": measurements.add(rows, skey=series_key, series_values=series_values, project_id=project_id),
            "errors": errors[:50]}


@router.delete("/api/measurements", dependencies=MEMBER)
def delete_measurements(request: Request, body: dict = Body(...)):
    scope, _ = scope_for(request, body.get("project"), edit=True)
    return {"deleted": measurements.delete(ids=[int(i) for i in body.get("ids") or []], src=body.get("src"),
                                           series=body.get("series"), scope=scope)}


def station_measurements(request, body):
    """Project, station and the measurements of its repeater that the request may see."""
    project = project_for(request, body)
    station = station_of(project, body.get("station_id"))
    if not station.get("corescope_pk"):
        raise HTTPException(400, "Station ist nicht mit einem Repeater verknüpft – Messungen werden über den Repeater zugeordnet")
    saved = project.get("id") if project.get("id") and projects.exists(project["id"]) else None
    if saved:
        scope, _ = scope_for(request, saved)
    elif auth.is_admin(request):
        scope = measurements.Scope(pool=True)          # unsaved project of the admin: the admin's own measurements
    else:
        scope = measurements.Scope()                   # unsaved project of somebody else: none yet
    return project, station, measurements.query(None, station["corescope_pk"], body.get("days"), scope=scope)


@router.post("/api/measurements/compare")
async def compare_measurements(request: Request, body: dict = Body(...)):
    project, station, rows = station_measurements(request, body)
    try:
        result = await run_in_threadpool(compare.compare_measurements, project, station["id"], rows,
                                         body.get("rx_gain_dbi"), measurements.series_map())
    except (ValueError, KeyError, terrain.SourceError) as e:
        fail(e)
    result["suggestion"] = compare.suggest_settings([result])
    return result
