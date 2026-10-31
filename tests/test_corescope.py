"""CoreScope-Anbindung mit echten (gekürzten) Antworten von corescope.meshrheinland.de, ohne Netz."""
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from app import corescope
from app.main import app

D = os.path.join(os.path.dirname(__file__), "data")
REACH = json.load(open(os.path.join(D, "cs_reach.json")))
NB = json.load(open(os.path.join(D, "cs_neighbors.json")))
PK = REACH["node"]["pubkey"]


def _node(i, lat=51.5, lon=7.0, role="repeater"):
    return {"public_key": f"{i:064x}", "name": f"R{i}", "role": role, "lat": lat, "lon": lon,
            "last_seen": "2026-10-31T10:00:00Z"}


def _handler(req: httpx.Request):
    p, q = req.url.path, dict(req.url.params)
    assert req.headers["user-agent"].startswith("TerraRF/")
    if p == "/api/nodes":
        off, lim = int(q["offset"]), int(q["limit"])
        allnodes = [_node(i) for i in range(1, 451)] + [_node(999, 0, 0)] if q["role"] == "repeater" \
            else [_node(2000, role="room")]
        page = allnodes[off:off + lim]
        return httpx.Response(200, json={"nodes": page, "has_more": off + lim < len(allnodes), "counts": {"repeaters": 451}})
    if p == f"/api/nodes/{PK}/reach":
        return httpx.Response(200, json=REACH)
    if p == f"/api/nodes/{PK}/neighbors":
        return httpx.Response(200, json=NB)
    return httpx.Response(404)


@pytest.fixture
def mocked(monkeypatch):
    monkeypatch.setattr(corescope, "BASE", "https://cs.example")
    monkeypatch.setattr(corescope, "PAUSE_S", 0)
    monkeypatch.setattr(corescope, "_client", lambda: httpx.Client(base_url="https://cs.example", transport=httpx.MockTransport(_handler),
                                                                   headers={"User-Agent": corescope.UA}))
    for f in os.listdir(corescope.DIR):
        os.remove(os.path.join(corescope.DIR, f))


def test_merge_only_counts_snr_where_partner_is_observer():
    out = corescope.merge_links(PK, REACH, NB)
    meas = [l for l in out["links"] if l["snr"] is not None]
    obs = {o["pubkey"] for o in REACH["direct_observers"]}
    assert meas and {l["pk"] for l in meas} <= obs | {x["pubkey"] for x in NB["neighbors"] if x["pubkey"] in x["observers"]}
    # Nachbarn, die nur über andere Observer gesehen wurden, bekommen kein SNR
    indirect = {x["pubkey"] for x in NB["neighbors"] if x["pubkey"] not in x["observers"]}
    assert all(l["snr"] is None for l in out["links"] if l["pk"] in indirect)
    hemer = next(l for l in meas if l["name"] == "DE-NW-MK-Hemer-DH")
    assert hemer["distance_km"] > 50


def test_sync_pages_and_filters(mocked):
    corescope._sync()
    st = corescope.status()
    assert st["error"] is None and st["count"] == 451  # 450 Repeater + 1 Room, 0/0-Knoten verworfen
    assert len(corescope.nodes((51.4, 6.9, 51.6, 7.1))) == 451
    assert corescope.nodes((50, 6, 50.1, 6.1)) == []


def test_api_links_and_calibrate(mocked, project):
    with TestClient(app) as c:
        assert c.post("/api/corescope/sync").status_code == 401
        assert c.post("/api/login", json={"user": "admin", "password": "test-pw"}).status_code == 200
        r = c.get(f"/api/corescope/links/{PK}")
        assert r.status_code == 200, r.text
        assert any(l["snr"] is not None for l in r.json()["links"])
        assert c.get("/api/corescope/links/xyz").status_code == 400
        # Station mit Knoten verknüpfen; Partner in die Nähe legen, damit das Test-DGM reicht
        st = project["stations"][0]
        st["corescope_pk"] = PK
        L = corescope.links(PK)
        for i, l in enumerate(L["links"]):
            l["lat"], l["lon"] = 51.575 + 0.01 * ((i % 5) - 2), 7.02 + 0.015 * ((i % 3) + 1)
        json.dump(L, open(corescope._links_path(PK), "w"))
        r = c.post("/api/corescope/calibrate", json={"project": project, "station_id": "a", "rx_height_m": 10})
        assert r.status_code == 200, r.text
        res = r.json()
        rows = [x for x in res["rows"] if "delta_db" in x]
        assert rows and all(abs(x["measured_snr"] - x["predicted_snr"] - x["delta_db"]) < 0.11 for x in rows)
        assert res["noise_dbm"] < -110
