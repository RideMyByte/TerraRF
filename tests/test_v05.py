"""0.5: global model settings (#30), catalog (#28/#29), building footprints (#25), receiver profiles in the
drive-test comparison."""
import json

import pytest
from fastapi.testclient import TestClient

from app.analysis import compare
from app.main import app
from app.model import buildings, settings
from app.store import catalog, measurements

from conftest import TEST_SETTINGS


@pytest.fixture
def admin():
    with TestClient(app) as c:
        assert c.post("/api/login", json={"user": "admin", "password": "test-pw"}).status_code == 200
        yield c


@pytest.fixture
def user(admin):
    assert admin.put("/api/admin/appconfig", json={"user_enabled": True, "user_name": "helfer", "user_password": "user-pass-123"}).status_code == 200
    with TestClient(app) as c:
        assert c.post("/api/login", json={"user": "helfer", "password": "user-pass-123"}).json()["role"] == "user"
        yield c


# ---------------------------------------------------------------- #30 global settings
def test_settings_are_global_and_admin_only(admin, user, project):
    spec = admin.get("/api/admin/settings").json()
    keys = {x["key"] for x in spec["spec"]}
    assert {"k_factor", "diffraction_model", "building_mode", "near_m"} <= keys
    assert user.get("/api/admin/settings").status_code in (401, 403)
    assert admin.put("/api/admin/settings", json={"values": {"k_factor": 99}}).status_code == 400
    r = admin.put("/api/admin/settings", json={"values": {**TEST_SETTINGS, "min_margin_db": 7}})
    assert r.status_code == 200 and r.json()["values"]["min_margin_db"] == 7
    assert admin.get("/api/config").json()["defaults"]["min_margin_db"] == 7
    # settings inside a project are ignored
    project["settings"] = {"min_margin_db": 30}
    budget = admin.post("/api/profile", json={"project": project, "station_id": "a", "lat": 51.585, "lon": 7.03}).json()["budget"]
    assert budget["required_margin_db"] == 7
    settings.save(TEST_SETTINGS)


def test_old_project_file_is_migrated(admin, project):
    project["settings"] = {"res_m": 5}
    project["stations"][0]["antenna"] = {"preset": "omni5", "type": "omni", "gain_dbi": 5.8}
    saved = admin.put("/api/projects/migr", json=project).json()["project"]
    assert "settings" not in saved and saved["stations"][0]["antenna"]["catalog_id"] == "collinear-5.8"


# ---------------------------------------------------------------- #28/#29 catalog
def test_catalog_read_all_edit_admin(admin, user):
    cat = user.get("/api/catalog").json()
    ids = {a["id"] for a in cat["antennas"]}
    assert {"interline-horizon-868", "interline-horizon-city-868", "alfa-aoa-868-5acm", "sirio-gp-868c"} <= ids
    assert {p["id"] for p in cat["placements"]} >= {"car-roof", "jacket-pocket", "bicycle"}
    assert user.put("/api/catalog/antennas/x1", json={"name": "X"}).status_code in (401, 403)
    assert admin.put("/api/catalog/antennas/Bad Id", json={"name": "X"}).status_code == 400
    assert admin.put("/api/catalog/antennas/test-yagi", json={"name": "Test Yagi", "type": "directional",
                                                               "gain_dbi": "11", "hbw_deg": 40}).json()["gain_dbi"] == 11
    assert admin.delete("/api/catalog/antennas/test-yagi").json()["ok"]
    assert admin.delete("/api/catalog/antennas/test-yagi").status_code == 404


def test_receiver_from_device_and_placement():
    defaults = {"rx_height_m": 1.5, "rx_gain_dbi": 0.0, "rx_cable_db": 0.0}
    h, g, loss = compare.receiver_for_measurement({}, {"device_id": "t1000-e", "placement_id": "jacket-pocket"}, defaults)
    assert (h, g, loss) == (1.0, 1.0, 4.0) or (h, g, loss) == (1.5, 1.0, 4.0)   # 1.25 m rounds to half metres
    # a value on the series wins over the profile, a value on the measurement wins over both
    h, g, _ = compare.receiver_for_measurement({"rx_h": 3}, {"rx_h": 2, "rx_gain_dbi": 5, "placement_id": "car-roof"}, defaults)
    assert (h, g) == (3.0, 5.0)
    h, g, loss = compare.receiver_for_measurement({}, {"device_id": "handheld-gizont", "placement_id": "car-roof"}, defaults)
    assert (h, g, loss) == (1.5, 3.5, 0.0)       # gain from the device's catalog antenna, 1.7 m -> 1.5


def test_series_accepts_only_catalog_profiles(admin):
    measurements.add([measurements.manual({"lat": 51.57, "lon": 7.03, "rep": "cd" * 32, "snr": -5})])
    sid = admin.get("/api/measurements").json()["series"][0]["id"]
    assert admin.put(f"/api/measurements/series/{sid}", json={"device_id": "nope"}).status_code == 400
    assert admin.put(f"/api/measurements/series/{sid}", json={"device_id": "t1000-e", "placement_id": "bicycle"}).json()["ok"]
    s = next(x for x in admin.get("/api/measurements").json()["series"] if x["id"] == sid)
    assert s["device_id"] == "t1000-e" and s["placement_id"] == "bicycle"


# ---------------------------------------------------------------- #25 buildings
def _square(lat, lon, d=0.0008):
    return [[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]


def test_buildings_import_mask_and_profile(admin, project):
    admin.delete("/api/admin/buildings")
    # the synthetic "forest" band of the test terrain: declare one block of it a building
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"@id": "way/1", "building": "yes", "building:levels": "3"},
         "geometry": {"type": "Polygon", "coordinates": [_square(51.5835, 7.0)]}},
        {"type": "Feature", "properties": {"@id": "way/2"}, "geometry": {"type": "Point", "coordinates": [7, 51]}}]}
    assert admin.post("/api/admin/buildings/geojson", json={"geojson": "kein json"}).status_code == 400
    r = admin.post("/api/admin/buildings/geojson", json={"geojson": json.dumps(fc), "name": "Test"}).json()
    assert r == {"received": 1, "new": 1, "total": 1}
    st = admin.get("/api/config").json()["buildings"]
    assert st["count"] == 1 and "OpenStreetMap" in st["attribution"]
    shown = admin.get("/api/buildings", params={"bbox": "51.5,6.9,51.7,7.1"}).json()
    assert len(shown["features"]) == 1
    prof = admin.post("/api/profile", json={"project": project, "station_id": "a", "lat": 51.5835, "lon": 7.0}).json()
    assert any(prof["building"]) and len(prof["building"]) == len(prof["d"])
    # buildings like vegetation (default) vs. only diffraction: the P.833 part through the block disappears
    with settings.temporary({"building_mode": "diffraction"}):
        prof_d = admin.post("/api/profile", json={"project": project, "station_id": "a", "lat": 51.5835, "lon": 7.0}).json()
    assert prof_d["budget"]["obstacle_db"] <= prof["budget"]["obstacle_db"]
    admin.delete("/api/admin/buildings")
    assert admin.get("/api/admin/buildings").json()["count"] == 0


def test_overpass_parser():
    data = {"elements": [
        {"type": "way", "id": 5, "tags": {"building": "house"},
         "geometry": [{"lat": 51.0, "lon": 7.0}, {"lat": 51.0, "lon": 7.001}, {"lat": 51.001, "lon": 7.001}, {"lat": 51.0, "lon": 7.0}]},
        {"type": "relation", "id": 6, "tags": {"building": "yes"}, "members": [
            {"role": "outer", "geometry": [{"lat": 51.0, "lon": 7.0}, {"lat": 51.0, "lon": 7.001}, {"lat": 51.001, "lon": 7.001},
                                          {"lat": 51.0, "lon": 7.0}]},
            {"role": "inner", "geometry": []}]}]}
    feats = buildings.parse_overpass(data)
    assert [f["id"] for f in feats] == ["way/5", "relation/6#0"]
    with pytest.raises(ValueError):
        buildings.fetch_osm((51.0, 7.0, 51.5, 7.5))     # far larger than the allowed area


def test_suggestion_needs_enough_values():
    assert compare.suggest_settings([{"rows": [{"delta_db": -9, "saturated": False}] * 5}])["advice"] == "too_few"
    out = compare.suggest_settings([{"rows": [{"delta_db": -9 + (i % 3), "saturated": False} for i in range(20)]}])
    assert out["advice"] == "offset" and out["suggested_extra_loss_db"] >= 7
    assert compare.suggest_settings([{"rows": [{"delta_db": (-1) ** i * 15, "saturated": False} for i in range(20)]}])["advice"] in ("fits", "scatter")
    # #40: model too pessimistic and no extra loss to remove -> no button, a hint instead
    out = compare.suggest_settings([{"rows": [{"delta_db": 20 + (i % 3), "saturated": False} for i in range(20)]}])
    assert out["advice"] == "pessimistic" and "suggested_extra_loss_db" not in out
