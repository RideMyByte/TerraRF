"""0.5.6: FM broadcast stations (#57), roof heights (#56)."""
import math

import pytest

from app.model import coverage as cov
from app.model import propagation as prop
from app.model import settings


@pytest.fixture
def admin():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        assert c.post("/api/login", json={"user": "admin", "password": "test-pw"}).status_code == 200
        yield c


def test_broadcast_conversions():
    assert prop.erp_w_to_eirp_dbm(1000) == pytest.approx(62.15, abs=1e-6)        # 1 kW ERP = 60 dBm + 2.15 dB
    # 54 dBµV/m at 100 MHz on an isotropic antenna: 54 − 77.2 − 40 = −63.2 dBm
    assert prop.field_to_dbm(54, 100, 0) == pytest.approx(-63.2, abs=1e-6)
    st = {"mode": "broadcast", "freq_mhz": 100, "emin_dbuvm": 54, "sens_override_dbm": -120}
    assert prop.sensitivity_dbm(st, 6, rx_gain_dbi=2, rx_cable_db=1) == pytest.approx(-62.2, abs=1e-6)


def test_broadcast_station_in_the_model(project):
    st = {**project["stations"][0], "mode": "broadcast", "freq_mhz": 100.0, "erp_w": 500, "emin_dbuvm": 54,
          "tx_power_dbm": 5, "cable_loss_db": 3, "antenna": {"type": "directional", "gain_dbi": 12, "azimuth_deg": 0}}
    m = cov.model_station(st)
    assert m["tx_power_dbm"] == pytest.approx(59.14, abs=0.01) and m["cable_loss_db"] == 0
    assert m["antenna"] == {"type": "omni", "gain_dbi": 0.0}
    pj = {**project, "stations": [st]}
    with settings.temporary({"clutter_mode": "off", "veg_mode": "off", "two_ray": False}):
        lat, lon = st["lat"] + 0.004, st["lon"]           # ~450 m north, open synthetic terrain
        b = cov.profile(pj, st["id"], lat, lon)["budget"]
        s = cov.merged_settings(pj)
    e = b["prx_dbm"] - float(s["rx_gain_dbi"]) + float(s["rx_cable_db"]) + float(s.get("rx_extra_loss_db", 0)) \
        + 77.2 + 20 * math.log10(100.0)
    # field strength from the free-space formula E = sqrt(30·EIRP)/d, minus the computed diffraction
    eirp_w = 10 ** ((m["tx_power_dbm"] - 30) / 10)
    d = 6371000 * math.radians(0.004)                        # metres north, haversine for a pure lat step
    e_fs = 20 * math.log10(math.sqrt(30 * eirp_w) / d * 1e6) - b["diffraction_db"] - b["obstacle_db"]
    assert e == pytest.approx(e_fs, abs=1.0), (e, e_fs, b)
    assert b["margin_db"] == pytest.approx(e - 54 - float(s.get("rx_extra_loss_db", 0)), abs=0.2)


# ---------------------------------------------------------------- #58 offline
def test_offline_gate_blocks_internet_but_not_lan(monkeypatch):
    import httpx
    from app.site import offline
    monkeypatch.setattr(offline, "ENV", True)
    assert offline.enabled()
    assert offline.allowed("http://127.0.0.1:9/x") and offline.allowed("http://192.168.1.5/api")
    assert offline.allowed("http://corescope:8080/")          # docker service name
    assert not offline.allowed("https://8.8.8.8/")
    with pytest.raises(offline.OfflineError):
        httpx.Client().get("https://8.8.8.8/")             # refused before any socket is opened
    monkeypatch.setattr(offline, "ENV", False)
    assert offline.allowed("https://8.8.8.8/")


def test_offline_switch_in_appconfig(admin):
    from app.site import offline
    assert not offline.enabled()
    admin.put("/api/admin/appconfig", json={"offline": True})
    try:
        assert offline.enabled() and admin.get("/api/config").json()["site"]["offline"]
        assert admin.get("/api/admin/offline").json()["offline"]
    finally:
        admin.put("/api/admin/appconfig", json={"offline": False})


def test_tile_ranges_and_packages(admin):
    from app.sources import tiles
    nrw = (50.32, 5.86, 52.53, 9.46)
    assert tiles.tile_range(nrw, 0) == (0, 0, 0, 0)
    assert tiles.count_tiles((51.5, 7.0, 51.51, 7.01), 17, 17) >= 4
    assert tiles.count_tiles(nrw, 17, 17) > tiles.MAX_PACK_TILES
    r = admin.post("/api/admin/offline/packages", json={"source": "osm", "bbox": [51.5, 7, 51.51, 7.01], "zmax": 15})
    assert r.status_code == 400                               # OSM may not be saved for offline use
    r = admin.post("/api/admin/offline/packages", json={"source": "nrwdop", "bbox": list(nrw), "zmax": 18})
    assert r.status_code == 400 and "höchstens" in r.json()["detail"]
    x0, y0, x1, y1 = tiles._xyz_bbox(1, 1, 0)
    assert x0 == pytest.approx(0) and y0 == pytest.approx(0) and x1 > 2e7 and y1 > 2e7


def test_mbtiles_base_map(admin, tmp_path):
    import os
    import sqlite3
    from app.sources import tiles
    os.makedirs(tiles.MBTILES_DIR, exist_ok=True)
    path = os.path.join(tiles.MBTILES_DIR, "testmap.mbtiles")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
        db.execute("CREATE TABLE tiles (zoom_level INT, tile_column INT, tile_row INT, tile_data BLOB)")
        db.executemany("INSERT INTO metadata VALUES (?, ?)", [("name", "Testkarte"), ("format", "png"), ("maxzoom", "3")])
        db.execute("INSERT INTO tiles VALUES (1, 1, 0, ?)", (b"PNGDATA",))   # TMS row 0 = XYZ y 1 at z1
    try:
        assert any(m["id"] == "testmap" for m in admin.get("/api/config").json()["mbtiles"])
        r = admin.get("/tiles/mb/testmap/1/1/1")
        assert r.status_code == 200 and r.content == b"PNGDATA" and r.headers["content-type"] == "image/png"
        assert admin.get("/tiles/mb/testmap/1/0/0").status_code == 404
        assert admin.get("/tiles/mb/..%2Fx/1/0/0").status_code == 404
    finally:
        os.remove(path)
