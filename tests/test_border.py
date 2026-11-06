"""Rand der Hauptquelle: WCS außerhalb des Gebiets -> leere Kachel statt Fehler; Lücken aus Ersatzquelle."""
import os

import httpx
import numpy as np
import rasterio
from rasterio.transform import from_origin

from app.model import terrain
from app.model import coverage as cov


def test_wcs_4xx_marks_tile_empty(tmp_path, monkeypatch):
    src = terrain.WCSSource("t_wcs", "Test", "https://wcs.example/x", "cov", 25832, 1.0, bbox=(0, 0, 1e9, 1e9))
    monkeypatch.setattr(terrain, "DEM_DIR", str(tmp_path))
    body = b'<?xml version="1.0"?><ows:ExceptionReport/>'
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400, content=body)))
    assert src.ensure_tile(300000, 5700000, client=client) is None
    assert src.tile_state(300000, 5700000) == "empty"
    # außerhalb der bbox: gar keine Anfrage
    calls = []
    src2 = terrain.WCSSource("t_wcs2", "Test", "https://wcs.example/x", "cov", 25832, 1.0, bbox=(0, 0, 10, 10))
    c2 = httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(500)))
    assert src2.ensure_tile(300000, 5700000, client=c2) is None and not calls


def test_holes_filled_from_fallback(project):
    # Ersatzquelle: großflächig konstant 77 m
    d = os.path.join(terrain.DATA_DIR, "dem", "tfb")
    os.makedirs(d, exist_ok=True)
    with rasterio.open(os.path.join(d, "tfb.tif"), "w", driver="GTiff", width=200, height=200, count=1, dtype="float32",
                       crs="EPSG:25832", transform=from_origin(330000, 5750000, 300, 300), nodata=-9999) as ds:
        ds.write(np.full((200, 200), 77, np.float32), 1)
    terrain._SOURCES.pop("local:tfb", None)
    s = cov.merged_settings({**project, "settings": {**project["settings"], "fallback_source": "local:tfb"}})
    # Gitter, das zur Hälfte über den Rand des Test-DGM hinausragt
    x, y = terrain.lonlat_to_merc(6.99, 51.575)
    g = terrain.Grid.around(x - 10000, y, 4000, 40.0)
    G, S, warn = cov.terrain_pair(s, g)
    assert not np.isnan(G).any()
    assert (np.isclose(G, 77)).mean() > 0.2          # Lücken aus der Ersatzquelle, nicht 0 m
    assert (G == 0).mean() < 0.01
    assert any("außerhalb der Hauptquelle" in w for w in warn)
