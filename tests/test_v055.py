"""0.5.5: measurements per project (#53)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import measurements

REP = "cd" * 32
ADMIN_KEY = "adminkey_1234567890abcdef"


def _ping(lat, snr, ts):
    return {"data": [{"type": "DISC", "lat": lat, "lon": 7.03, "timestamp": ts, "repeater_id": "CDCD",
                      "public_key": REP, "local_snr": snr, "local_rssi": -100, "remote_snr": -5}]}


@pytest.fixture
def admin():
    with TestClient(app) as c:
        assert c.post("/api/login", json={"user": "admin", "password": "test-pw"}).status_code == 200
        c.put("/api/admin/appconfig", json={"mm_api_key": ADMIN_KEY, "user_enabled": True, "user_name": "helfer",
                                            "user_password": "user-pass-123"})
        yield c


def test_measurements_belong_to_projects(admin, project):
    admin_project = {**project, "id": "adm-proj", "public": False}
    assert admin.put("/api/projects/adm-proj", json=admin_project).status_code == 200
    with TestClient(app) as u:
        u.post("/api/login", json={"user": "helfer", "password": "user-pass-123"})
        assert u.put("/api/projects/user-proj", json={**project, "id": "user-proj", "public": False}).status_code == 200
        key = u.post("/api/projects/user-proj/ingest-key").json()["key"]
        assert u.get("/api/projects/user-proj/ingest-key").json()["key"] == key
        assert u.get("/api/projects/adm-proj/ingest-key").status_code in (403, 404)
        with TestClient(app) as phone:
            assert phone.post("/api/ingest/meshmapper", json=_ping(51.57, -3, 1762300000), headers={"X-API-Key": key}).json()["stored"] == 1
            assert phone.post("/api/ingest/meshmapper", json=_ping(51.58, -7, 1762300100), headers={"X-API-Key": ADMIN_KEY}).json()["stored"] == 1
            assert phone.post("/api/ingest/meshmapper", json=_ping(51.59, 1, 1762300200), headers={"X-API-Key": "nope"}).status_code == 401
        mine = u.get("/api/measurements", params={"project": "user-proj"}).json()
        assert [r["snr_down"] for r in mine["rows"] if r["rep_pk"] == REP] == [-3] and mine["stats"]["count"] == 1
        assert u.get("/api/measurements", params={"project": "adm-proj"}).status_code == 404       # not visible
        # manual entry goes into the project; somebody else's project is refused
        entry = {"project": "user-proj", "lat": 51.6, "lon": 7.0, "rep": "cdcd", "snr_down": 2, "rx_h": 1.5}
        assert u.post("/api/measurements", json=entry).status_code == 200
        assert u.post("/api/measurements", json={**entry, "project": "adm-proj"}).status_code in (403, 404)
        assert u.post("/api/measurements", json={**entry, "project": None}).status_code == 400
    # the admin's project shows the admin pool (old measurements + admin key), not the user's
    adm = admin.get("/api/measurements", params={"project": "adm-proj"}).json()
    snrs = {r["snr_down"] for r in adm["rows"] if r["rep_pk"] == REP}
    assert -7 in snrs and -3 not in snrs
    # visitors: nothing while private; once public, the measurements without time and device
    with TestClient(app) as anon:
        assert anon.get("/api/measurements", params={"project": "user-proj"}).status_code in (401, 404)
        admin.put("/api/projects/user-proj", json={**project, "id": "user-proj", "public": True})
        rows = anon.get("/api/measurements", params={"project": "user-proj"}).json()["rows"]
        assert rows and all("ts" not in r and "power" not in r for r in rows)
        assert anon.request("DELETE", "/api/measurements", json={"project": "user-proj", "src": "meshmapper"}).status_code == 401
    # the admin deletes the user's project: its measurements stay, as the admin's own
    assert admin.delete("/api/projects/user-proj").status_code == 200
    pool = admin.get("/api/measurements", params={"project": "adm-proj"}).json()
    assert -3 in {r["snr_down"] for r in pool["rows"] if r["rep_pk"] == REP}


def test_link_history_keeps_changes_only():
    """#52: a link state is stored only when it changed; repeated reports count once in the statistics."""
    from app.sources import linkhistory
    a, b = "aa" * 32, "bb" * 32
    state = {"pk": a, "window_days": 7, "links": [{"pk": b, "snr": 4.0, "snr_rev": None, "snr_any": 1.0, "they_hear": 10,
                                                   "we_hear": 5, "heard_at": 1000, "heard_rev_at": None}]}
    assert linkhistory.record(state, now=2000) == 1
    assert linkhistory.record(state, now=3000) == 0                       # nothing changed
    state["links"][0].update(they_hear=12)                                # counts changed, same report
    assert linkhistory.record(state, now=4000) == 1
    state["links"][0].update(snr=-2.0, heard_at=3900)                     # a new report
    assert linkhistory.record(state, now=5000) == 1
    h = linkhistory.history(a, b)
    assert len(h["points"]) == 3 and h["since"] == 2000
    assert h["snr"]["n"] == 2 and h["snr"]["mean"] == 1.0 and h["snr"]["std"] == 3.0
    assert h["snr_series"] == [(1000, 4.0), (3900, -2.0)] or h["snr_series"] == [[1000, 4.0], [3900, -2.0]]
