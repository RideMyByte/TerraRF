import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


@pytest.fixture
def anon():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin():
    with TestClient(app) as c:
        r = c.post("/api/login", json={"user": "admin", "password": "test-pw"})
        assert r.status_code == 200
        yield c


def test_health_and_config(anon):
    assert anon.get("/api/health").json()["ok"]
    cfg = anon.get("/api/config").json()
    assert cfg["auth"]["admin"] is False and cfg["auth"]["public_read"] is True
    ids = {s["id"] for s in cfg["sources"]}
    assert {"nrw_dgm", "nrw_dom", "local:tdgm", "local:tdom"} <= ids


def test_login_rejects_wrong_password(anon):
    assert anon.post("/api/login", json={"user": "admin", "password": "falsch"}).status_code == 401
    assert anon.get("/api/me").json()["admin"] is False


def test_writes_need_login(anon, project):
    assert anon.put("/api/projects/abc", json=project).status_code == 401
    assert anon.post("/api/coverage", json={"project": project}).status_code == 401
    assert anon.post("/api/regions/start", json={"source": "nrw_dgm"}).status_code == 401
    assert anon.post("/api/cache/clear", json={}).status_code == 401


def test_coverage_station_image_and_cache(admin, project):
    r = admin.post("/api/coverage", json={"project": project, "station_ids": ["a"]})
    assert r.status_code == 200, r.text
    run = r.json()
    st = run["stations"][0]
    assert st["area_km2"] > 0 and st["w"] > 10 and st["h"] > 10
    png = admin.get(st["png"])
    assert png.status_code == 200
    img = np.array(Image.open(io.BytesIO(png.content)))
    assert img.shape == (st["h"], st["w"], 4)
    lvl = img[..., 0]
    assert (lvl > 0).any()                         # some cells with signal
    prx = lvl[lvl > 0].astype(int) - 200
    assert prx.max() < 30 and prx.min() > -199     # plausible dBm range
    r2 = admin.post("/api/coverage", json={"project": project, "station_ids": ["a"]}).json()
    assert r2["stations"][0]["cached"] is True and r2["stations"][0]["key"] == st["key"]
    prof = admin.post("/api/profile", json={"project": project, "station_id": "a", "lat": 51.59, "lon": 7.0}).json()
    b = prof["budget"]
    assert b["total_loss_db"] >= b["fspl_db"]


def test_disabled_station_not_computed(admin, project, station):
    project["stations"].append(dict(station, id="b", lat=51.56, lon=7.05, enabled=False))
    run = admin.post("/api/coverage", json={"project": project}).json()
    assert [s["id"] for s in run["stations"]] == ["a"]


def test_public_readonly_and_private_hidden(admin, anon, project):
    pub = admin.get("/api/newid").json()["id"]
    priv = admin.get("/api/newid").json()["id"]
    assert admin.put(f"/api/projects/{pub}", json=dict(project, public=True)).status_code == 200
    assert admin.put(f"/api/projects/{priv}", json=dict(project, name="Geheim", public=False)).status_code == 200
    listed = {p["id"] for p in anon.get("/api/projects").json()}
    assert pub in listed and priv not in listed
    assert anon.get(f"/api/projects/{priv}").status_code == 404
    assert anon.get(f"/api/projects/{pub}").json()["name"] == "Test"
    r = anon.post("/api/coverage", json={"project_id": pub, "station_ids": ["a"]})
    assert r.status_code == 200, r.text
    assert anon.post("/api/coverage", json={"project_id": priv}).status_code == 404
    prof = anon.post("/api/profile", json={"project_id": pub, "station_id": "a", "lat": 51.59, "lon": 7.0})
    assert prof.status_code == 200
    # visitors cannot inject their own stations via a public project id
    r = anon.post("/api/coverage", json={"project_id": pub, "station_ids": ["evil"]})
    assert r.status_code == 400
    admin.delete(f"/api/projects/{pub}")
    admin.delete(f"/api/projects/{priv}")


def test_projects_crud(admin, project):
    pid = admin.get("/api/newid").json()["id"]
    assert admin.put(f"/api/projects/{pid}", json=project).status_code == 200
    assert any(p["id"] == pid for p in admin.get("/api/projects").json())
    admin.delete(f"/api/projects/{pid}")
    assert admin.get(f"/api/projects/{pid}").status_code == 404
    assert admin.put("/api/projects/../evil", json=project).status_code in (400, 404)


def test_logout(admin):
    admin.post("/api/logout")
    assert admin.get("/api/me").json()["admin"] is False


def test_adding_station_keeps_cache_of_others(admin, project, station):
    k1 = admin.post("/api/coverage", json={"project": project, "station_ids": ["a"]}).json()["stations"][0]["key"]
    far = {**station, "id": "b", "lat": 52.4, "lon": 7.02}  # weit nördlich: verschob früher das Rechengitter
    project["stations"].append(far)
    r = admin.post("/api/coverage", json={"project": project, "station_ids": ["a"]}).json()["stations"][0]
    assert r["key"] == k1 and r["cached"] is True
