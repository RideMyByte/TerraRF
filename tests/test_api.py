from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_and_config():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"]
    cfg = client.get("/api/config").json()
    ids = {s["id"] for s in cfg["sources"]}
    assert {"nrw_dgm", "nrw_dom", "local:tdgm", "local:tdom"} <= ids


def test_coverage_profile_and_layers(project):
    r = client.post("/api/coverage", json=project)
    assert r.status_code == 200, r.text
    run = r.json()
    st = run["stations"][0]
    assert st["area_km2"] > 0
    for layer in ("margin", "overlap", "server", "los", "station:a"):
        png = client.get(f"/api/run/{run['run_id']}/{layer}.png", params={"req": 5})
        assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    # second run comes from cache
    r2 = client.post("/api/coverage", json=project).json()
    assert r2["stations"][0]["cached"] is True
    insp = client.post("/api/inspect", json={"run_id": run["run_id"], "lat": 51.58, "lon": 7.03}).json()
    assert insp["stations"][0]["in_range"]
    prof = client.post("/api/profile", json={"project": project, "station_id": "a", "lat": 51.59, "lon": 7.0}).json()
    b = prof["budget"]
    assert b["total_loss_db"] >= b["fspl_db"]
    assert len(prof["d"]) == len(prof["ground"]) > 10


def test_disabled_station_not_computed(project, station):
    other = dict(station, id="b", lat=51.56, lon=7.05, enabled=False)
    project["stations"].append(other)
    run = client.post("/api/coverage", json=project).json()
    assert [s["id"] for s in run["stations"]] == ["a"]


def test_projects_crud(project):
    pid = client.get("/api/newid").json()["id"]
    assert client.put(f"/api/projects/{pid}", json=project).status_code == 200
    assert any(p["id"] == pid for p in client.get("/api/projects").json())
    assert client.get(f"/api/projects/{pid}").json()["name"] == "Test"
    client.delete(f"/api/projects/{pid}")
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert client.put("/api/projects/../evil", json=project).status_code in (400, 404)
