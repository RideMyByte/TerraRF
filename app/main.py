from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager

import httpx
import numpy as np
from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import appconfig, auth, corescope, legal, overlay, report, regions, sysstat, terrain, tiles
from . import coverage as cov
from . import propagation as prop
from .version import VERSION

logging.basicConfig(level=os.environ.get("RFP_LOGLEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("terrarf")

PROJECT_DIR = os.path.join(terrain.DATA_DIR, "projects")
os.makedirs(PROJECT_DIR, exist_ok=True)
STATIC = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(_app):
    auth.init()
    threading.Thread(target=prop.warmup, daemon=True).start()
    if os.environ.get("RFP_PRELOAD", "nrw_dgm,nrw_dom").strip():
        threading.Thread(target=regions.start_preload, daemon=True).start()
    threading.Thread(target=corescope.auto_sync_loop, daemon=True).start()
    yield


app = FastAPI(title="TerraRF", version=VERSION, lifespan=lifespan)
ADMIN = [Depends(auth.require_admin)]
MEMBER = [Depends(auth.require_member)]
READ = [Depends(auth.require_read)]


def _err(e: Exception, code=400):
    log.exception("Fehler")
    raise HTTPException(status_code=code, detail=str(e))


# ---------------------------------------------------------------- basics + auth
@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION}


@app.get("/api/me")
def me(request: Request):
    role = auth.role(request)
    name = auth.ADMIN_USER if role == "admin" else appconfig.get()["user_name"] if role == "user" else None
    return {"admin": role == "admin", "role": role, "user": name, "public_read": auth.PUBLIC_READ}


@app.get("/api/admin/appconfig", dependencies=ADMIN)
def get_appconfig():
    return appconfig.get()


@app.put("/api/admin/appconfig", dependencies=ADMIN)
def put_appconfig(body: dict = Body(...)):
    try:
        return appconfig.update(body)
    except ValueError as e:
        _err(e)


@app.get("/api/status")
def status(request: Request):
    """Läuft gerade etwas? Für alle; Auslastung des Containers nur für den Admin."""
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


@app.post("/api/login")
def login(request: Request, body: dict = Body(...)):
    role = auth.check_login(request, str(body.get("user", "")), str(body.get("password", "")))
    if not role:
        raise HTTPException(401, "Benutzer oder Passwort falsch")
    resp = JSONResponse({"ok": True, "admin": role == "admin", "role": role})
    # Standard: Sitzungscookie (endet mit dem Browser); "angemeldet bleiben" nur auf ausdrücklichen Wunsch
    resp.set_cookie(auth.COOKIE, auth.make_token(str(body.get("user", "")), role),
                    max_age=auth.SESSION_S if body.get("remember") else None, httponly=True,
                    samesite="lax", secure=auth.is_https(request), path="/")
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/api/config")
def config(request: Request):
    return {
        "version": VERSION,
        "sources": terrain.list_sources(),
        "defaults": cov.DEFAULT_SETTINGS,
        "tile_res": terrain.TILE_RES,
        "auth": me(request),
        "corescope": {"enabled": corescope.enabled(), "url": corescope.BASE or None},
        "tile_proxy": tiles.ENABLED,
        "legal": {"imprint": legal.configured()},
        "site": appconfig.public(),
        "last_project": _last_project() if auth.is_admin(request) else None,
    }


# ---------------------------------------------------------------- projects (storage)
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _ppath(pid):
    if not _ID.match(pid or ""):
        raise HTTPException(400, "ungültige Projekt-ID")
    return os.path.join(PROJECT_DIR, pid + ".json")


def _load_project(pid):
    p = _ppath(pid)
    if not os.path.exists(p):
        raise HTTPException(404, "Projekt nicht gefunden")
    with open(p) as f:
        return json.load(f)


def _visible_project(request: Request, pid):
    """Admin sees everything; visitors only public projects."""
    p = _load_project(pid)
    if not auth.is_admin(request):
        auth.require_read(request)
        if not p.get("public"):
            raise HTTPException(404, "Projekt nicht gefunden")
    return p


def _project_for(request: Request, body: dict):
    """Admin may send an unsaved project; visitors only reference a saved public one."""
    if auth.can_edit_any(request) and body.get("project"):
        return body["project"]
    if auth.can_edit_any(request) and "stations" in body:
        return body
    pid = body.get("project_id")
    if not pid:
        raise HTTPException(401, "Anmeldung erforderlich")
    return _visible_project(request, pid)


@app.get("/api/projects", dependencies=READ)
def list_projects(request: Request):
    admin = auth.is_admin(request)
    out = []
    for fn in sorted(os.listdir(PROJECT_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(PROJECT_DIR, fn)) as f:
                p = json.load(f)
        except Exception:  # noqa: BLE001
            continue
        if not admin and not p.get("public"):
            continue
        out.append({"id": p.get("id"), "name": p.get("name"), "stations": len(p.get("stations") or []),
                    "updated": p.get("updated"), "public": bool(p.get("public"))})
    out.sort(key=lambda p: p.get("updated") or "", reverse=True)
    return out


LAST_FILE = os.path.join(terrain.DATA_DIR, "last_project")


@app.get("/api/projects/{pid}")
def get_project(request: Request, pid: str):
    p = _visible_project(request, pid)
    if auth.is_admin(request):  # beim nächsten Start (z. B. als App vom Startbildschirm) wieder öffnen
        try:
            with open(LAST_FILE, "w") as f:
                f.write(pid)
        except OSError:
            pass
    return p


def _last_project():
    try:
        with open(LAST_FILE) as f:
            pid = f.read().strip()
        return pid if _ID.match(pid) and os.path.exists(_ppath(pid)) else None
    except OSError:
        return None


def _merge_user_changes(stored, sent):
    """Benutzer „user“: nur eigene Stationen (owner = "user") anlegen, ändern, löschen. Alles andere bleibt,
    wie es gespeichert ist – auch wenn der Browser etwas anderes schickt."""
    own = [s_ for s_ in sent.get("stations") or [] if s_.get("owner") == "user"]
    foreign_ids = {s_["id"] for s_ in stored.get("stations") or [] if s_.get("owner") != "user"}
    own = [s_ for s_ in own if s_.get("id") not in foreign_ids]
    merged = dict(stored)
    merged["stations"] = [s_ for s_ in stored.get("stations") or [] if s_.get("owner") != "user"] + own
    return merged


@app.put("/api/projects/{pid}", dependencies=MEMBER)
def save_project(request: Request, pid: str, project: dict = Body(...)):
    p = _ppath(pid)
    if not auth.is_admin(request):
        if not os.path.exists(p):
            raise HTTPException(403, "Neue Projekte darf nur der Admin anlegen")
        stored = _load_project(pid)
        if not stored.get("public"):
            raise HTTPException(404, "Projekt nicht gefunden")
        project = _merge_user_changes(stored, project)
    project["id"] = pid
    project["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(project, f, indent=1, ensure_ascii=False)
    os.replace(tmp, p)
    if project.get("public"):
        threading.Thread(target=_warm, args=(project,), daemon=True).start()
    return {"ok": True, "id": pid, "updated": project["updated"], "project": project}


def _warm(project):
    """Öffentliche Projekte nach dem Speichern vorrechnen, damit Besucher sofort Ergebnisse aus dem Cache bekommen
    (eine Erstberechnung kann länger dauern als das Zeitlimit eines Reverse-Proxys)."""
    try:
        if any(s.get("enabled", True) for s in project.get("stations") or []):
            cov.run_coverage(project)
    except Exception as e:  # noqa: BLE001
        log.warning("Vorberechnung für %s fehlgeschlagen: %s", project.get("id"), e)


@app.delete("/api/projects/{pid}", dependencies=ADMIN)
def delete_project(pid: str):
    p = _ppath(pid)
    if os.path.exists(p):
        os.remove(p)
    return {"ok": True}


@app.get("/api/newid", dependencies=ADMIN)
def new_id():
    return {"id": uuid.uuid4().hex[:12]}


# ---------------------------------------------------------------- computation
@app.post("/api/coverage")
async def coverage(request: Request, body: dict = Body(...)):
    project = _project_for(request, body)
    ids = body.get("station_ids")
    if ids is not None:
        known = {s["id"] for s in project.get("stations") or []}
        ids = [i for i in ids if i in known]
    try:
        max_px = body.get("max_px")
        max_px = int(min(max(int(max_px), 500), cov.MAX_DIM)) if max_px else None
        force = body.get("force") or []
        if force and not auth.can_edit_any(request):
            force = []  # Besucher dürfen keine Neuberechnung erzwingen
        return await run_in_threadpool(cov.run_coverage, project, ids, max_px, force)
    except (ValueError, terrain.SourceError, KeyError) as e:
        _err(e)


@app.get("/api/station/{key}/{factor}.png", dependencies=READ)
def station_png(key: str, factor: int):
    if not re.fullmatch(r"[0-9a-f]{8,64}", key) or not 1 <= factor <= 64:
        raise HTTPException(400, "ungültig")
    try:
        data = cov.station_png(key, factor)
    except KeyError:
        raise HTTPException(404, "Berechnung nicht gefunden – bitte neu berechnen")
    return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.post("/api/profile")
async def profile(request: Request, body: dict = Body(...)):
    project = _project_for(request, body)
    try:
        return await run_in_threadpool(cov.profile, project, body["station_id"], float(body.get("lat") or 0),
                                       float(body.get("lon") or 0), body.get("rx_height_m"), body.get("rx_gain_dbi"),
                                       body.get("rx_station_id"), body.get("rx_cable_db"))
    except (ValueError, terrain.SourceError, KeyError) as e:
        _err(e)


# ---------------------------------------------------------------- CoreScope (MeshCore-Netz)
@app.get("/api/corescope/status", dependencies=READ)
def cs_status():
    return corescope.status()


@app.post("/api/corescope/sync", dependencies=ADMIN)
def cs_sync():
    try:
        return {"started": corescope.start_sync()}
    except ValueError as e:
        _err(e)


@app.get("/api/corescope/nodes", dependencies=READ)
def cs_nodes(bbox: str | None = None):
    bb = None
    if bbox:
        try:
            bb = [float(v) for v in bbox.split(",")]
            assert len(bb) == 4
        except (ValueError, AssertionError):
            raise HTTPException(400, "bbox = minLat,minLon,maxLat,maxLon")
    return {"nodes": corescope.nodes(bb), **corescope.status()}


@app.get("/api/corescope/links/{pk}", dependencies=READ)
async def cs_links(request: Request, pk: str, refresh: bool = False):
    try:
        return await run_in_threadpool(corescope.links, pk.lower(), refresh and auth.is_admin(request))
    except Exception as e:  # noqa: BLE001
        _err(e, 502 if not isinstance(e, ValueError) else 400)


@app.get("/api/corescope/node/{pk}", dependencies=READ)
async def cs_node(pk: str):
    try:
        return await run_in_threadpool(corescope.node_meta, pk.lower())
    except ValueError as e:
        _err(e)
    except httpx.HTTPError as e:
        _err(e, 502)


@app.get("/api/corescope/overrides", dependencies=READ)
def cs_overrides():
    return corescope.overrides()


@app.put("/api/corescope/overrides/{pk}", dependencies=ADMIN)
def cs_set_override(pk: str, body: dict = Body(...)):
    try:
        return {"pk": pk.lower(), "values": corescope.set_override(pk.lower(), body)}
    except (ValueError, TypeError) as e:
        _err(e)


@app.post("/api/corescope/calibrate")
async def cs_calibrate(request: Request, body: dict = Body(...)):
    project = _project_for(request, body)
    st = next((x for x in project.get("stations") or [] if x["id"] == body.get("station_id")), None)
    if st is None or not st.get("corescope_pk"):
        raise HTTPException(400, "Station ist nicht mit einem CoreScope-Knoten verknüpft")

    def work():
        L = corescope.links(str(st["corescope_pk"]).lower())
        pts = [x for x in L["links"] if x.get("snr") is not None]
        want = body.get("partners")  # nur die Partner, die mit den aktuellen Filtern auch auf der Karte stehen
        if want is not None:
            want = {str(x).lower() for x in want}
            pts = [x for x in pts if x["pk"] in want]
        res = cov.calibrate(project, st["id"], pts, float(body.get("rx_height_m", 10)), float(body.get("rx_gain_dbi", 3)),
                            float(body.get("rx_cable_db", 1)), corescope.overrides())
        res["window_days"] = L.get("window_days")
        return res
    try:
        return await run_in_threadpool(work)
    except (ValueError, KeyError, terrain.SourceError, httpx.HTTPError) as e:
        _err(e)


@app.post("/api/report/summary")
async def report_summary(request: Request, body: dict = Body(...)):
    project = _project_for(request, body)
    try:
        return await run_in_threadpool(report.summary, project, body["station_id"])
    except (ValueError, KeyError, terrain.SourceError) as e:
        _err(e)


@app.post("/api/report/pdf")
async def report_pdf(request: Request, body: dict = Body(...)):
    project = _project_for(request, body)
    site = appconfig.public().get("parent")
    try:
        data = await run_in_threadpool(report.pdf, project, body["station_id"], body.get("cal"),
                                       site and (site.get("name") or site.get("url")))
    except (ValueError, KeyError, terrain.SourceError) as e:
        _err(e)
    st = next((x for x in project.get("stations") or [] if x["id"] == body["station_id"]), {})
    fn = re.sub(r"[^A-Za-z0-9_-]+", "_", st.get("name") or "station") + time.strftime("_%Y-%m-%d") + ".pdf"
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@app.post("/api/elevation", dependencies=MEMBER)
async def elevation(body: dict = Body(...)):
    try:
        return await run_in_threadpool(cov.point_elevation, body["project"], float(body["lat"]), float(body["lon"]))
    except (ValueError, terrain.SourceError, KeyError) as e:
        _err(e)


@app.get("/api/terrain/test", dependencies=ADMIN)
async def terrain_test(source: str, lat: float = 51.57, lon: float = 6.98, res: float = 10.0):
    """Diagnostics: fetch a ~1 km area from one source."""
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
        mode = getattr(src, "mode", None)
        if mode:
            out["wcs_mode"] = mode
        return out
    try:
        return await run_in_threadpool(work)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)


# ---------------------------------------------------------------- regions + cache (admin)
@app.post("/api/regions/plan", dependencies=ADMIN)
def region_plan(body: dict = Body(...)):
    try:
        p = regions.plan(body["source"], body.get("area", "view"), float(body.get("res_m", 10)), body.get("bbox"))
        p.pop("tiles", None)
        return p
    except (ValueError, terrain.SourceError, KeyError) as e:
        _err(e)


@app.post("/api/regions/start", dependencies=ADMIN)
def region_start(body: dict = Body(...)):
    try:
        return regions.start(body["source"], body.get("area", "view"), float(body.get("res_m", 10)),
                             body.get("name") or "", body.get("bbox"))
    except (ValueError, terrain.SourceError, KeyError) as e:
        _err(e)


@app.get("/api/regions/jobs", dependencies=READ)
def region_jobs():
    return regions.list_jobs()


@app.post("/api/regions/cancel/{jid}", dependencies=ADMIN)
def region_cancel(jid: str):
    regions.cancel(jid)
    return {"ok": True}


@app.get("/api/cache", dependencies=ADMIN)
def cache_info():
    return terrain.cache_usage()


@app.post("/api/cache/clear", dependencies=ADMIN)
def cache_clear(body: dict = Body(...)):
    terrain.clear_cache(body.get("parts") or ["terrain", "coverage"])
    with cov._station_cache_lock:
        cov._station_cache.clear()
    with cov._png_lock:
        cov._png_cache.clear()
    return terrain.cache_usage()


# ---------------------------------------------------------------- frontend
# ---------------------------------------------------------------- Kachel-Proxy, Rechtstexte
TILE_HEADERS = {"Cache-Control": "public, max-age=604800"}


@app.get("/tiles/{src}/{z}/{x}/{y}.png")
async def tile(src: str, z: int, x: int, y: int):
    if not tiles.ENABLED:
        raise HTTPException(404, "Kachel-Proxy ist ausgeschaltet")
    try:
        data = await run_in_threadpool(tiles.xyz, src, z, x, y)
    except KeyError:
        raise HTTPException(404, "unbekannte Quelle")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Kartendienst nicht erreichbar: {e}")
    return Response(data, media_type="image/png", headers=TILE_HEADERS)


@app.get("/tiles/wms/{src}")
async def tile_wms(src: str, request: Request):
    if not tiles.ENABLED:
        raise HTTPException(404, "Kachel-Proxy ist ausgeschaltet")
    try:
        data = await run_in_threadpool(tiles.wms, src, dict(request.query_params))
    except KeyError:
        raise HTTPException(404, "unbekannte Quelle")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Kartendienst nicht erreichbar: {e}")
    return Response(data, media_type="image/jpeg", headers=TILE_HEADERS)


MANIFEST = {
    "name": "TerraRF – Funkplanung", "short_name": "TerraRF", "id": "/", "start_url": "/", "scope": "/",
    "display": "standalone", "orientation": "any", "lang": "de",
    "description": "Funkabdeckung, Sichtlinie und Linkbudget für MeshCore, LoRa und Amateurfunk planen",
    "background_color": "#f6f7f9", "theme_color": "#0d2236", "categories": ["utilities", "navigation"],
    "icons": [
        {"src": "/static/pwa/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/static/pwa/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/static/pwa/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        {"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
    ],
}


@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse(MANIFEST, media_type="application/manifest+json")


@app.get("/api/overlay/{kind}/{z}/{x}/{y}.png", dependencies=READ)
async def overlay_tile(kind: str, z: int, x: int, y: int, g: str = "nrw_dgm", s: str = "nrw_dom"):
    if not re.fullmatch(r"[a-z0-9_:\-]{1,64}", g) or not re.fullmatch(r"[a-z0-9_:\-]{1,64}", s):
        raise HTTPException(400, "ungültige Quelle")
    try:
        data = await run_in_threadpool(overlay.render, kind, z, x, y, g, s)
    except KeyError:
        raise HTTPException(404, "unbekannte Ebene")
    return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/impressum")
def impressum():
    c = appconfig.get()
    if c["imprint_mode"] == "off":
        raise HTTPException(404, "Kein Impressum hinterlegt")
    if c["imprint_mode"] == "link" and c["imprint_url"]:
        return RedirectResponse(c["imprint_url"], status_code=302)
    return Response(legal.page("impressum"), media_type="text/html")


@app.get("/datenschutz")
def datenschutz():
    c = appconfig.get()
    if c["privacy_mode"] == "link" and c["privacy_url"]:
        return RedirectResponse(c["privacy_url"], status_code=302)
    return Response(legal.page("datenschutz"), media_type="text/html")


_INDEX = None


@app.get("/")
def index():
    global _INDEX
    if _INDEX is None:
        with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
            _INDEX = f.read().replace("__VERSION__", VERSION)
    return Response(_INDEX, media_type="text/html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
