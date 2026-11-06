"""Projects: list, load, save, delete."""
from __future__ import annotations

import logging
import threading
import uuid

from fastapi import APIRouter, Body, HTTPException, Request

from ..model import coverage
from ..site import auth
from ..store import projects
from .deps import MEMBER, READ, visible_project

router = APIRouter()
log = logging.getLogger("terrarf.api.projects")


@router.get("/api/projects", dependencies=READ)
def list_projects(request: Request):
    return projects.list_all(auth.role(request))


@router.get("/api/projects/{project_id}")
def get_project(request: Request, project_id: str):
    p = visible_project(request, project_id)
    if auth.is_admin(request):
        projects.remember_last(project_id)
    return p


@router.put("/api/projects/{project_id}", dependencies=MEMBER)
def save_project(request: Request, project_id: str, project: dict = Body(...)):
    """Create or replace a project. The owner is set by the server: whoever creates a project owns it (#32)."""
    if not projects.valid_id(project_id):
        raise HTTPException(400, "ungültige Projekt-ID")
    role = auth.role(request)
    if projects.exists(project_id):
        stored = projects.load(project_id)
        if not projects.can_edit(role, stored):
            raise HTTPException(403, "Dieses Projekt darf nur sein Ersteller oder der Admin ändern")
        project["owner"] = projects.owner(stored)
    else:
        project["owner"] = role
    saved = projects.save(project_id, project)
    if saved.get("public"):
        threading.Thread(target=_precompute, args=(saved,), daemon=True).start()
    return {"ok": True, "id": project_id, "updated": saved["updated"], "project": saved}


def _precompute(project):
    """Compute public projects right after saving, so visitors get results from the cache (a first computation
    can take longer than a reverse proxy's timeout)."""
    try:
        if any(s.get("enabled", True) for s in project.get("stations") or []):
            coverage.run_coverage(project)
    except Exception as e:  # noqa: BLE001
        log.warning("precomputing %s failed: %s", project.get("id"), e)


@router.delete("/api/projects/{project_id}", dependencies=MEMBER)
def delete_project(request: Request, project_id: str):
    try:
        if not projects.can_edit(auth.role(request), projects.load(project_id)):
            raise HTTPException(403, "Dieses Projekt darf nur sein Ersteller oder der Admin löschen")
        projects.delete(project_id)
    except projects.NotFound:
        raise HTTPException(404, "Projekt nicht gefunden")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.get("/api/newid", dependencies=MEMBER)
def new_id():
    return {"id": uuid.uuid4().hex[:12]}
