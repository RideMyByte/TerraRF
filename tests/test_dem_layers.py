"""Kartenebenen aus den Höhendaten (Gelände, Hindernishöhe)."""
import io
import math

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


def _tile(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def test_relief_and_ndom_tiles():
    x, y = _tile(51.575, 7.02, 14)
    with TestClient(app) as c:
        r = c.get(f"/api/overlay/relief/14/{x}/{y}.png", params={"g": "local:tdgm", "s": "local:tdom"})
        assert r.status_code == 200, r.text
        im = np.array(Image.open(io.BytesIO(r.content)))
        assert im.shape == (256, 256, 4) and (im[..., 3] > 0).mean() > 0.9
        assert im[..., :3].std() > 2  # Schummerung/Farbverlauf vorhanden
        # Waldstreifen im Test-DOM: Zeilen 300-340, Spalten 200-600 bei 20 m -> irgendwo sichtbar auf z13
        found = False
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                xx, yy = _tile(51.575, 7.02, 13)
                rr = c.get(f"/api/overlay/ndom/13/{xx + dx}/{yy + dy}.png", params={"g": "local:tdgm", "s": "local:tdom"})
                assert rr.status_code == 200
                if (np.array(Image.open(io.BytesIO(rr.content)))[..., 3] > 0).any():
                    found = True
        assert found
        z5 = c.get("/api/overlay/relief/5/0/0.png")                     # zu weit herausgezoomt: leere Kachel
        assert z5.status_code == 200 and np.array(Image.open(io.BytesIO(z5.content))).size <= 4
        assert c.get(f"/api/overlay/foo/14/{x}/{y}.png").status_code == 404
        assert c.get(f"/api/overlay/relief/14/{x}/{y}.png", params={"g": "../etc"}).status_code == 400
        s = c.get(f"/api/overlay/surface/14/{x}/{y}.png", params={"g": "local:tdgm", "s": "local:tdom"})
        assert s.status_code == 200
