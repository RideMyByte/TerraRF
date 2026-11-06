"""Results per station: key figures and the PDF report."""
from __future__ import annotations

import re
import time

from fastapi import APIRouter, Body, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from ..analysis import report
from ..model import terrain
from ..store import appconfig
from .deps import fail, project_for

router = APIRouter()


@router.post("/api/report/summary")
async def summary(request: Request, body: dict = Body(...)):
    project = project_for(request, body)
    try:
        return await run_in_threadpool(report.summary, project, body["station_id"])
    except (ValueError, KeyError, terrain.SourceError) as e:
        fail(e)


@router.post("/api/report/pdf")
async def pdf(request: Request, body: dict = Body(...)):
    project = project_for(request, body)
    parent = appconfig.public().get("parent")
    try:
        data = await run_in_threadpool(report.pdf, project, body["station_id"], body.get("cal"),
                                       parent and (parent.get("name") or parent.get("url")), body.get("lang") or "de")
    except (ValueError, KeyError, terrain.SourceError) as e:
        fail(e)
    station = next((x for x in project.get("stations") or [] if x["id"] == body["station_id"]), {})
    filename = re.sub(r"[^A-Za-z0-9_-]+", "_", station.get("name") or "station") + time.strftime("_%Y-%m-%d") + ".pdf"
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
