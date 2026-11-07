"""TerraRF web application: assembles the API routers, starts background work, serves the frontend.

Areas (see docs/architecture.md):
  plan     – stations, coverage, profiles           api/coverage.py, model/
  network  – MeshCore repeaters via CoreScope       api/network.py, sources/corescope.py
  measure  – drive tests (MeshMapper, manual, CSV)  api/measurements.py, store/measurements.py
  compare  – model vs. reality                      analysis/compare.py
  report   – key figures and PDF                    api/reports.py, analysis/report.py
  admin    – access, website, model, data, catalogs api/admin.py, api/catalog.py
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import admin, catalog, coverage, maps, measurements, network, projects, reports, session, site
from .model import propagation
from .site import auth, offline  # noqa: F401 – offline installs the network gate (#58)
from .sources import corescope, regions
from .store import measurements as measurement_store
from .version import VERSION

logging.basicConfig(level=os.environ.get("RFP_LOGLEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app):
    auth.init()
    measurement_store.init()
    threading.Thread(target=propagation.warmup, daemon=True).start()      # compile the numba kernels early
    if os.environ.get("RFP_PRELOAD", "nrw_dgm,nrw_dom").strip() and not offline.enabled():
        threading.Thread(target=regions.start_preload, daemon=True).start()
    threading.Thread(target=corescope.auto_sync_loop, daemon=True).start()
    yield


app = FastAPI(title="TerraRF", version=VERSION, lifespan=lifespan)
for module in (session, projects, coverage, network, measurements, reports, admin, catalog, maps, site):
    app.include_router(module.router)
app.mount("/static", StaticFiles(directory=site.STATIC), name="static")


@app.middleware("http")
async def revalidate_frontend(request, call_next):
    """ES modules import each other without the ?v= version tag, so the browser must revalidate them
    (ETag, cheap 304) instead of keeping an old module after an update."""
    response = await call_next(request)
    if request.url.path.startswith(("/static/js/", "/static/locales/", "/static/style.css")):
        response.headers["Cache-Control"] = "no-cache"
    return response
