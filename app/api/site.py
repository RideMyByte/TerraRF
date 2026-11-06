"""The web app itself: index page, PWA manifest, imprint and privacy pages."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, Response

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
