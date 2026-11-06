"""0.5.1: projects per creator (#32), station noise floor (#37), manual entries with receiver (#38),
new placements for existing catalogs (#39)."""
import json

import pytest
from fastapi.testclient import TestClient

from app.analysis import compare
from app.main import app
from app.model import propagation as prop
from app.store import catalog, measurements


@pytest.fixture
def admin():
    with TestClient(app) as c:
        assert c.post("/api/login", json={"user": "admin", "password": "test-pw"}).status_code == 200
        yield c


# ---------------------------------------------------------------- #39 catalog seeds reach old catalogs
def test_new_seed_placements_are_added_once(tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    old = {k: [x for x in catalog.SEED[k] if "since" not in x] for k in catalog.KINDS}   # a 0.5.0 file
    old["placements"] = [p for p in old["placements"] if p["id"] != "bicycle"]             # the admin deleted one
    path.write_text(json.dumps(old))
    monkeypatch.setattr(catalog, "PATH", str(path))
    monkeypatch.setattr(catalog, "_data", None)
    ids = {p["id"] for p in catalog.all_items()["placements"]}
    assert {"in-car", "trouser-pocket", "backpack-inside"} <= ids
    assert "bicycle" not in ids                       # deleted seeds stay deleted
    stored = json.loads(path.read_text())
    assert "in-car" in stored["_seeded"] and all("since" not in p for p in stored["placements"])
    # the admin deletes a new one -> it does not come back on the next start
    catalog.delete("placements", "in-car")
    monkeypatch.setattr(catalog, "_data", None)
    assert "in-car" not in {p["id"] for p in catalog.all_items()["placements"]}
    assert "_seeded" not in catalog.all_items()


# ---------------------------------------------------------------- #37 measured noise floor
def test_noise_floor_replaces_thermal_noise():
    st = {"mode": "lora", "sf": 8, "bw_khz": 62.5, "nf_db": 6}
    thermal = prop.noise_floor_dbm(st, 2.0)
    assert thermal == pytest.approx(-174 + 10 * 4.79588 + 6 + 2, abs=0.01)
    assert prop.noise_floor_dbm({**st, "noise_floor_dbm": -110}, 2.0) == -110      # no noise rise on top
    assert prop.sensitivity_dbm({**st, "noise_floor_dbm": -110}) == pytest.approx(-110 + prop.LORA_SNR[8])
    # the transmitting station's own noise does not change the SNR at somebody else's receiver
    assert compare.noise_floor_dbm({**st, "noise_floor_dbm": -100}, {}) == pytest.approx(prop.noise_floor_dbm(st))


def test_calibration_uses_noise_of_receiving_station(admin, project):
    b = {**project["stations"][0], "id": "b", "name": "B", "lat": 51.585, "lon": 7.03, "corescope_pk": "bb" * 32,
         "noise_floor_dbm": -105}
    project = {**project, "stations": [project["stations"][0], b]}
    partner = {"pk": "bb" * 32, "name": "B", "lat": b["lat"], "lon": b["lon"], "snr": 5}
    with_noise = compare.calibrate(project, "a", [partner])["rows"][0]
    without = compare.calibrate({**project, "stations": [project["stations"][0], {**b, "noise_floor_dbm": None}]}, "a", [partner])["rows"][0]
    assert with_noise["noise_dbm"] == -105
    assert with_noise["predicted_snr"] < without["predicted_snr"]


# ---------------------------------------------------------------- #38 manual entry
def test_manual_entry_needs_height_and_keeps_the_receiver(admin):
    base = {"lat": 51.6, "lon": 7.0, "rep": "a3f1", "snr_down": 4.5}
    r = admin.post("/api/measurements", json=base)
    assert r.status_code == 400 and "Antennenhöhe" in r.json()["detail"]
    r = admin.post("/api/measurements", json={**base, "rx_h": 1.5, "device_id": "t1000-e", "placement_id": "in-car",
                                             "rx_gain_dbi": 1.0, "rx_cable_db": 9.0, "power": 22})
    assert r.status_code == 200 and r.json()["stored"] == 1
    row = next(m for m in measurements.query() if m.get("note") is None and m["rx_h"] == 1.5 and m["snr_down"] == 4.5)
    assert row["power"] == "22 dBm"
    series = {s["id"]: s for s in measurements.series_list()}[row["series_id"]]
    assert series["device_id"] == "t1000-e" and series["placement_id"] == "in-car" and series["rx_cable_db"] == 9.0
    h, g, loss = compare.receiver_for_measurement(row, series, {"rx_height_m": 9, "rx_gain_dbi": 9, "rx_cable_db": 9})
    assert (h, g, loss) == (1.5, 1.0, 9.0)
    assert admin.post("/api/measurements", json={**base, "rx_h": 1.5, "placement_id": "nope"}).status_code == 400
    # CSV may still leave the height out
    assert admin.post("/api/measurements", json={"csv": "lat;lon;rep;snr_down\n51.61;7.01;a3f1;3"}).json()["stored"] == 1
