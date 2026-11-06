"""The web app itself: index page, PWA manifest, imprint and privacy pages."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from ..site import legal
from ..store import appconfig
from ..version import VERSION

router = APIRouter()
STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")

MANIFEST = {
    "name": "TerraRF – coverage hub", "short_name": "TerraRF", "id": "/", "start_url": "/", "scope": "/",
    "display": "standalone", "orientation": "any",
    "description": "Plan, observe, measure and compare radio coverage for MeshCore, LoRa and amateur radio",
    "background_color": "#f6f7f9", "theme_color": "#0d2236", "categories": ["utilities", "navigation"],
    "icons": [
        {"src": "/static/pwa/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/static/pwa/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/static/pwa/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        {"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
    ],
}


@router.get("/assets/{version}/{folder}/{path:path}")
def versioned_asset(version: str, folder: str, path: str):
    """Frontend modules and translations under a path that contains the version.

    ES modules import each other by relative URL, so a version tag in the query string of main.js does not reach
    them. With the version in the path every release gets new URLs: no browser, reverse proxy or CDN can mix
    modules of two versions (which breaks the page with "module does not provide an export"). Files of the
    current version may be cached for good; a request for another version gets the current files uncached.
    """
    if folder not in ("js", "locales"):
        raise HTTPException(404)
    base = os.path.realpath(os.path.join(STATIC, folder))
    target = os.path.realpath(os.path.join(base, path))
    if not target.startswith(base + os.sep) or not os.path.isfile(target):
        raise HTTPException(404)
    cache = "public, max-age=31536000, immutable" if version == VERSION and VERSION != "dev" else "no-cache"
    return FileResponse(target, headers={"Cache-Control": cache})


@router.get("/manifest.webmanifest")
def manifest():
    return JSONResponse(MANIFEST, media_type="application/manifest+json")


@router.get("/impressum")
def imprint():
    c = appconfig.get()
    if c["imprint_mode"] == "off":
        raise HTTPException(404, "Kein Impressum hinterlegt")
    if c["imprint_mode"] == "link" and c["imprint_url"]:
        return RedirectResponse(c["imprint_url"], status_code=302)
    return Response(legal.page("impressum"), media_type="text/html")


@router.get("/datenschutz")
def privacy():
    c = appconfig.get()
    if c["privacy_mode"] == "link" and c["privacy_url"]:
        return RedirectResponse(c["privacy_url"], status_code=302)
    return Response(legal.page("datenschutz"), media_type="text/html")


_index_html = None


@router.get("/")
def index():
    global _index_html
    if _index_html is None:
        with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
            _index_html = f.read().replace("__VERSION__", VERSION)
    return Response(_index_html, media_type="text/html", headers={"Cache-Control": "no-cache"})
