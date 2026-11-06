"""Projects: list, load, save, delete, and the collaborator accounts of a project."""
from __future__ import annotations

import logging
import threading
import uuid

from fastapi import APIRouter, Body, HTTPException, Request

from ..model import coverage
from ..site import auth
from ..store import accounts, appconfig, ingestkeys, measurements, projects
from .deps import ADMIN, MEMBER, READ, visible_project

router = APIRouter()
log = logging.getLogger("terrarf.api.projects")


@router.get("/api/projects", dependencies=READ)
def list_projects(request: Request):
    return projects.list_all(auth.role(request), auth.account_project(request))


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
        if not projects.can_edit(role, stored, auth.account_project(request)):
            raise HTTPException(403, "Dieses Projekt darf nur sein Ersteller oder der Admin ändern")
        project["owner"] = projects.owner(stored)
        if role == "project":
            project["public"] = bool(stored.get("public"))     # collaborators do not publish
    elif role == "project":
        raise HTTPException(403, "Mit diesem Zugang lässt sich nur das freigegebene Projekt bearbeiten")
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
        if auth.role(request) == "project" or not projects.can_edit(auth.role(request), projects.load(project_id)):
            raise HTTPException(403, "Dieses Projekt darf nur sein Ersteller oder der Admin löschen")
        projects.delete(project_id)
        accounts.delete_project(project_id)
        ingestkeys.delete(project_id)
        measurements.reassign_project(project_id)   # measurements are kept, as the admin's own
    except projects.NotFound:
        raise HTTPException(404, "Projekt nicht gefunden")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.get("/api/newid", dependencies=MEMBER)
def new_id(request: Request):
    if auth.role(request) == "project":
        raise HTTPException(403, "Mit diesem Zugang lassen sich keine Projekte anlegen")
    return {"id": uuid.uuid4().hex[:12]}


# ---------------------------------------------------------------- MeshMapper key per project (#53)
def _editable(request, project_id):
    _existing(project_id)
    project = projects.load(project_id)
    if not projects.can_edit(auth.role(request), project, auth.account_project(request)):
        raise HTTPException(403, "Nur wer das Projekt bearbeiten darf")
    return project


@router.get("/api/projects/{project_id}/ingest-key", dependencies=MEMBER)
def get_ingest_key(request: Request, project_id: str):
    """The project's MeshMapper key – for everybody who may edit the project, so collaborators can measure too."""
    _editable(request, project_id)
    return {"key": ingestkeys.get(project_id)}


@router.post("/api/projects/{project_id}/ingest-key", dependencies=MEMBER)
def new_ingest_key(request: Request, project_id: str):
    """Create or replace the key (the old one stops working). Not for collaborators."""
    _editable(request, project_id)
    if auth.role(request) == "project":
        raise HTTPException(403, "Den Schlüssel erneuert der Ersteller des Projekts oder der Admin")
    return {"key": ingestkeys.generate(project_id)}


# ---------------------------------------------------------------- collaborator accounts (admin)
def _existing(project_id):
    if not projects.valid_id(project_id) or not projects.exists(project_id):
        raise HTTPException(404, "Projekt nicht gefunden – erst speichern")


@router.get("/api/projects/{project_id}/members", dependencies=ADMIN)
def list_members(project_id: str):
    _existing(project_id)
    return accounts.for_project(project_id)


@router.post("/api/projects/{project_id}/members", dependencies=ADMIN)
def add_member(project_id: str, body: dict = Body(...)):
    """New login for this project. The password (generated if empty) is returned once and never again."""
    _existing(project_id)
    try:
        password = accounts.create(body.get("name"), project_id, body.get("password") or None, body.get("note", ""),
                                   reserved=(auth.ADMIN_USER, appconfig.get()["user_name"]))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"name": body.get("name").strip(), "password": password}


@router.post("/api/projects/{project_id}/members/{name}/password", dependencies=ADMIN)
def reset_member_password(project_id: str, name: str, body: dict = Body(default={})):
    _existing(project_id)
    if accounts.project_of(name) != project_id:
        raise HTTPException(404, "Zugang nicht gefunden")
    try:
        return {"name": name, "password": accounts.set_password(name, (body or {}).get("password") or None)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/api/projects/{project_id}/members/{name}", dependencies=ADMIN)
def delete_member(project_id: str, name: str):
    if accounts.project_of(name) != project_id:
        raise HTTPException(404, "Zugang nicht gefunden")
    accounts.delete(name)
    return {"ok": True}
