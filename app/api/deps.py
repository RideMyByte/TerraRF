"""Shared pieces for the API routers: access levels, error mapping and project lookup."""
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request

from ..site import auth
from ..store import projects

log = logging.getLogger("terrarf.api")

ADMIN = [Depends(auth.require_admin)]     # site administration, catalogs, global settings
MEMBER = [Depends(auth.require_member)]   # admin or user: save stations, enter measurements
READ = [Depends(auth.require_read)]       # anyone allowed to look (public read may be switched off)


def fail(error: Exception, status: int = 400):
    """Log and turn an exception into an HTTP error with its message."""
    log.warning("request failed: %s", error)
    raise HTTPException(status_code=status, detail=str(error))


def visible_project(request: Request, project_id: str):
    """Admin sees every project, the user their own and public ones, visitors only public ones (#32)."""
    try:
        p = projects.load(project_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except projects.NotFound:
        raise HTTPException(404, "Projekt nicht gefunden")
    if not projects.can_view(auth.role(request), p):
        auth.require_read(request)
        raise HTTPException(404, "Projekt nicht gefunden")
    return p


def project_for(request: Request, body: dict):
    """The project a computation runs on. Members may send an unsaved project (the editor's current state);
    visitors only reference a saved public one by id."""
    if auth.can_edit_any(request) and body.get("project"):
        return projects.migrate(dict(body["project"]))
    if auth.can_edit_any(request) and "stations" in body:
        return projects.migrate(dict(body))
    project_id = body.get("project_id")
    if not project_id:
        raise HTTPException(401, "Anmeldung erforderlich")
    return visible_project(request, project_id)


def station_of(project, station_id):
    station = next((x for x in project.get("stations") or [] if x["id"] == station_id), None)
    if station is None:
        raise HTTPException(400, "Station nicht gefunden")
    return station


def parse_bbox(text):
    """'minLat,minLon,maxLat,maxLon' -> list of 4 floats."""
    if not text:
        return None
    try:
        bb = [float(v) for v in text.split(",")]
    except ValueError:
        bb = []
    if len(bb) != 4:
        raise HTTPException(400, "bbox = minLat,minLon,maxLat,maxLon")
    return bb
