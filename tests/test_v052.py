"""0.5.2: collaborator logins per project, versioned asset paths."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import accounts


@pytest.fixture
def admin():
    with TestClient(app) as c:
        assert c.post("/api/login", json={"user": "admin", "password": "test-pw"}).status_code == 200
        yield c


def _login(name, password):
    c = TestClient(app)
    r = c.post("/api/login", json={"user": name, "password": password})
    return c, r


def test_collaborator_works_on_its_project_only(admin, project):
    shared = {**project, "id": "geteilt2", "name": "Geteilt", "public": False}
    other = {**project, "id": "anderes", "name": "Anderes", "public": False}
    assert admin.put("/api/projects/geteilt2", json=shared).status_code == 200
    assert admin.put("/api/projects/anderes", json=other).status_code == 200
    assert admin.post("/api/projects/nope/members", json={"name": "anna"}).status_code == 404
    assert admin.post("/api/projects/geteilt2/members", json={"name": "admin"}).status_code == 400   # reserved
    r = admin.post("/api/projects/geteilt2/members", json={"name": "anna", "note": "Messfahrten"})
    assert r.status_code == 200
    password = r.json()["password"]
    assert len(password) >= 8 and password not in open(accounts.PATH).read()        # only the hash is stored
    assert admin.post("/api/projects/anderes/members", json={"name": "Anna"}).status_code == 400   # unique
    assert [m["name"] for m in admin.get("/api/projects/geteilt2/members").json()] == ["anna"]

    c, r = _login("anna", "wrong-password")
    assert r.status_code == 401
    c, r = _login("anna", password)
    assert r.json()["role"] == "project" and r.json()["project"] == "geteilt2"
    with c:
        assert c.get("/api/me").json()["project"] == "geteilt2"
        listed = {p["id"]: p for p in c.get("/api/projects").json()}
        assert listed["geteilt2"]["editable"] and "anderes" not in listed
        assert c.get("/api/projects/anderes").status_code == 404
        saved = c.put("/api/projects/geteilt2", json={**shared, "name": "von Anna", "public": True}).json()["project"]
        assert saved["name"] == "von Anna" and saved["public"] is False and saved["owner"] == "admin"
        assert c.put("/api/projects/anderes", json=other).status_code == 403
        assert c.put("/api/projects/neu-von-anna", json={**shared, "id": "neu-von-anna"}).status_code == 403
        assert c.get("/api/newid").status_code == 403
        assert c.delete("/api/projects/geteilt2").status_code == 403
        assert c.post("/api/projects/geteilt2/members", json={"name": "bob"}).status_code in (401, 403)
        assert c.get("/api/admin/settings").status_code in (401, 403)
        # a new password ends the session
        new = admin.post("/api/projects/geteilt2/members/anna/password", json={}).json()["password"]
        assert c.get("/api/me").json()["role"] is None
    c, r = _login("anna", new)
    assert r.status_code == 200
    with c:
        assert admin.delete("/api/projects/geteilt2/members/anna").status_code == 200
        assert c.get("/api/me").json()["role"] is None                                # deleted login: logged out
    # deleting the project removes its logins
    admin.post("/api/projects/geteilt2/members", json={"name": "carla"})
    assert admin.delete("/api/projects/geteilt2").status_code == 200
    assert accounts.project_of("carla") is None
