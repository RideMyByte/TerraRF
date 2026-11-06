"""Who is logged in, login/logout, and the start-up configuration the browser loads first."""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from ..model import buildings, settings, terrain
from ..site import auth, legal, sysstat
from ..sources import corescope, regions, tiles
from ..store import appconfig, projects
from ..version import VERSION

router = APIRouter()


@router.get("/api/health")
def health():
    return {"ok": True, "version": VERSION}


@router.get("/api/me")
def me(request: Request):
    role = auth.role(request)
    name = auth.ADMIN_USER if role == "admin" else appconfig.get()["user_name"] if role == "user" else None
    return {"admin": role == "admin", "role": role, "user": name, "public_read": auth.PUBLIC_READ}


@router.post("/api/login")
def login(request: Request, body: dict = Body(...)):
    role = auth.check_login(request, str(body.get("user", "")), str(body.get("password", "")))
    if not role:
        raise HTTPException(401, "Benutzer oder Passwort falsch")
    resp = JSONResponse({"ok": True, "admin": role == "admin", "role": role})
    # session cookie by default (ends with the browser); "stay logged in" only on request
    resp.set_cookie(auth.COOKIE, auth.make_token(str(body.get("user", "")), role),
                    max_age=auth.SESSION_S if body.get("remember") else None, httponly=True,
                    samesite="lax", secure=auth.is_https(request), path="/")
    return resp


@router.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@router.get("/api/config")
def config(request: Request):
    """Everything the frontend needs before it draws anything."""
    return {
        "version": VERSION,
        "sources": terrain.list_sources(),
        "defaults": settings.effective(),
        "tile_res": terrain.TILE_RES,
        "auth": me(request),
        "corescope": {"enabled": corescope.enabled(), "url": corescope.BASE or None},
        "tile_proxy": tiles.ENABLED,
        "legal": {"imprint": legal.configured()},
        "site": appconfig.public(),
        "buildings": buildings.status(),
        "last_project": projects.last() if auth.is_admin(request) else None,
    }


@router.get("/api/status")
def status(request: Request):
    """Is something running? For everybody; container load only for the admin."""
    items = sysstat.busy()
    for j in regions.list_jobs():
        if j.get("state") in ("running", "queued"):
            items.append({"what": f"Höhendaten laden ({j.get('name') or j.get('source')}, {j.get('done', 0)}/{j.get('total', '?')})",
                          "seconds": j.get("elapsed_s")})
    cs = corescope.status()
    if cs.get("syncing"):
        items.append({"what": f"MeshCore-Sync ({cs.get('progress') or '…'})", "seconds": None})
    out = {"busy": items}
    if auth.is_admin(request):
        out["res"] = sysstat.resources()
    return out
