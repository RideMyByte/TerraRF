"""Antennas, receiving devices and measurement placements (#28, #29). Read: everybody; edit: admin."""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from ..store import catalog
from .deps import ADMIN, READ, fail

router = APIRouter()


@router.get("/api/catalog", dependencies=READ)
def get_catalog():
    return catalog.all_items()


@router.put("/api/catalog/{kind}/{item_id}", dependencies=ADMIN)
def put_item(kind: str, item_id: str, body: dict = Body(...)):
    try:
        return catalog.put(kind, item_id, body)
    except ValueError as e:
        fail(e)


@router.delete("/api/catalog/{kind}/{item_id}", dependencies=ADMIN)
def delete_item(kind: str, item_id: str):
    try:
        if not catalog.delete(kind, item_id):
            raise HTTPException(404, "nicht gefunden")
    except ValueError as e:
        fail(e)
    return {"ok": True}
