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
    if p == f"/api/nodes/{PK}":
        return httpx.Response(200, json={"node": {"public_key": PK, "name": "DE-NW-GE-HaldeScholven", "role": "repeater",
                                                  "battery_mv": 4010, "last_seen": "2026-10-31T18:00:00Z"}, "recentAdverts": [{}, {}]})
    if p == "/api/rf-params":
        return httpx.Response(404)
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


def test_links_have_avg_snr_and_overrides(mocked):
    corescope._sync()
    out = corescope.merge_links(PK, REACH, NB)
    assert out["v"] == corescope.LINKS_VERSION
    assert any(l.get("snr_any") is not None and l["snr"] is None for l in out["links"])  # indirekt, nicht gemessen
    with TestClient(app) as c:
        assert c.put(f"/api/corescope/overrides/{PK}", json={"height_m": 35}).status_code == 401
        c.post("/api/login", json={"user": "admin", "password": "test-pw"})
        assert c.put(f"/api/corescope/overrides/{PK}", json={"height_m": 35, "gain_dbi": 5.8}).status_code == 200
        assert c.get("/api/corescope/overrides").json()[PK] == {"height_m": 35.0, "gain_dbi": 5.8}
        assert c.put(f"/api/corescope/overrides/{PK}", json={"height_m": 9999}).status_code == 400
        assert c.put(f"/api/corescope/overrides/{PK}", json={}).status_code == 200
        assert PK not in c.get("/api/corescope/overrides").json()


def test_calibrate_uses_partner_station_and_override_and_filter(mocked, project, station):
    with TestClient(app) as c:
        c.post("/api/login", json={"user": "admin", "password": "test-pw"})
        st = project["stations"][0]
        st["corescope_pk"] = PK
        L = corescope.links(PK)
        meas = [l for l in L["links"] if l["snr"] is not None][:3]
        for i, l in enumerate(meas):
            l["lat"], l["lon"] = 51.575 + 0.01 * i, 7.06  # Partner 0 genau östlich der Station
        json.dump(L, open(corescope._links_path(PK), "w"))
        # Partner 0 ist als Station eingetragen (Richtantenne zurück), Partner 1 hat eine eigene Höhe
        project["stations"].append({**station, "id": "p0", "name": "P0", "lat": meas[0]["lat"], "lon": meas[0]["lon"],
                                    "height_m": 25, "corescope_pk": meas[0]["pk"],
                                    "antenna": {"type": "directional", "gain_dbi": 14, "hbw_deg": 30, "azimuth_deg": 270}})
        c.put(f"/api/corescope/overrides/{meas[1]['pk']}", json={"height_m": 40})
        r = c.post("/api/corescope/calibrate", json={"project": project, "station_id": "a",
                                                     "partners": [m["pk"] for m in meas[:2]]}).json()
        rows = {x["pk"]: x for x in r["rows"]}
        assert set(rows) == {meas[0]["pk"], meas[1]["pk"]}          # Filter greift
        assert rows[meas[0]["pk"]]["rx_src"] == "station" and rows[meas[0]["pk"]]["rx_gain_dbi"] > 10
        assert rows[meas[1]["pk"]]["rx_src"] == "angabe" and rows[meas[1]["pk"]]["rx_height_m"] == 40
        c.put(f"/api/corescope/overrides/{meas[1]['pk']}", json={})


def test_profile_to_station_uses_far_end_antenna(project, station):
    with TestClient(app) as c:
        c.post("/api/login", json={"user": "admin", "password": "test-pw"})
        b = {**station, "id": "b", "name": "B", "lat": 51.575, "lon": 7.07, "height_m": 20,
             "antenna": {"type": "directional", "gain_dbi": 18, "hbw_deg": 20, "azimuth_deg": 270}}
        project["stations"].append(b)
        towards = c.post("/api/profile", json={"project": project, "station_id": "a", "rx_station_id": "b"}).json()
        b["antenna"]["azimuth_deg"] = 90  # Gegenstelle schaut weg
        away = c.post("/api/profile", json={"project": project, "station_id": "a", "rx_station_id": "b"}).json()
        assert towards["budget"]["rx_station"] == "B" and towards["budget"]["rx_gain_dbi"] > 17
        assert away["budget"]["rx_gain_dbi"] < 0
        assert towards["budget"]["prx_dbm"] - away["budget"]["prx_dbm"] > 15


def test_node_meta_without_height(mocked):
    with TestClient(app) as c:
        r = c.get(f"/api/corescope/node/{PK}")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["node"]["battery_mv"] == 4010 and d["adverts"] == 2 and d["rf"] is None
        assert "height_m" not in d["node"]  # MeshCore sendet keine Höhe/Antenne/Leistung
