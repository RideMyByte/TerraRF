"""Messfahrten: MeshMapper-Weiterleitung, Eingabe von Hand/CSV, Messplan, Abgleich mit der Abdeckung."""
import pytest
from fastapi.testclient import TestClient

from app import measurements
from app.main import app

KEY = "testkey_1234567890abcdef"
REP = "ab" * 32


@pytest.fixture
def admin():
    with TestClient(app) as c:
        c.post("/api/login", json={"user": "admin", "password": "test-pw"})
        assert c.put("/api/admin/appconfig", json={"mm_api_key": KEY}).status_code == 200
        measurements.delete(src="meshmapper"); measurements.delete(src="manual"); measurements.delete(src="csv")
        yield c


def test_meshmapper_ingest_and_query(admin):
    batch = {"data": [
        {"type": "DISC", "lat": 51.575, "lon": 7.03, "timestamp": 1762000000, "repeater_id": "ABAB", "public_key": REP.upper(),
         "local_snr": -3.5, "local_rssi": -110, "remote_snr": -6, "noisefloor": -118, "power": "0.3w", "radio_freq": "869.618,62.5,8,8"},
        {"type": "TX", "lat": 51.576, "lon": 7.031, "timestamp": 1762000030, "heard_repeats": "ABAB(2.25),CDEF(-11)"},
        {"type": "DISC", "lat": 51.5, "lon": 7.0, "timestamp": 1762000060, "repeater_id": "None"},   # fehlgeschlagen
        {"type": "DEFER", "lat": 51.5, "lon": 7.0, "timestamp": 1762000090, "held": "tx"},
        {"type": "RX", "lat": 51.576, "lon": 7.031, "timestamp": 1762000033, "heard_repeats": "ABAB(2.25)"},  # dasselbe Echo noch mal
        {"type": "RX", "lat": 51.576, "lon": 7.031, "timestamp": 1762000040, "heard_repeats": "ABAB(null)"},  # eigener CARpeater
        {"type": "SCOPES", "lat": 51.5, "lon": 7.0, "timestamp": 1762000090, "public_key": REP, "scopes": []},
    ]}
    with TestClient(app) as anon:
        assert anon.post("/api/ingest/meshmapper", json=batch).status_code == 401
        assert anon.post("/api/ingest/meshmapper", json=batch, headers={"X-API-Key": "falsch"}).status_code == 401
        r = anon.post("/api/ingest/meshmapper", json=batch, headers={"X-API-Key": KEY})
        assert r.status_code == 200 and r.json()["stored"] == 3
        assert anon.post("/api/ingest/meshmapper", json=batch, headers={"X-API-Key": KEY}).json()["stored"] == 0  # doppelt
        assert anon.get("/api/measurements").status_code == 401   # Besucher: nicht freigegeben
    d = admin.get("/api/measurements").json()
    assert d["stats"]["count"] == 3
    disc = next(x for x in d["rows"] if x["kind"] == "DISC")
    assert disc["rep_pk"] == REP and disc["snr_down"] == -3.5 and disc["snr_up"] == -6
    assert {x["rep_prefix"] for x in d["rows"] if x["kind"] == "TX"} == {"abab", "cdef"}
    assert len(admin.get("/api/measurements", params={"pk": REP}).json()["rows"]) == 2   # DISC + TX-Echo (Präfix)


def test_manual_csv_plan_and_compare(admin, project):
    for snr in (-2, -1, 0):  # drei stimmige Messungen in einer Zelle -> „genug“
        assert admin.post("/api/measurements", json={"lat": 51.5651, "lon": 7.0451, "rep": REP, "snr": snr, "rssi": -100 + snr, "ts": 1762000000 + snr}).status_code == 200
    csv_text = "lat;lon;rep;snr;note\n51,560;7,050;" + REP + ";-12;Feld\n51.59;7.01;" + REP + ";4;\nkaputt;;;;\n"
    r = admin.post("/api/measurements", json={"csv": csv_text}).json()
    assert r["stored"] == 2 and len(r["errors"]) == 1
    d = admin.get("/api/measurements", params={"pk": REP}).json()
    assert any(c["done"] for c in d["plan"]) and any(not c["done"] for c in d["plan"])
    project["stations"][0]["corescope_pk"] = REP
    c = admin.post("/api/measurements/compare", json={"project": project, "station_id": "a"})
    assert c.status_code == 200, c.text
    res = c.json()
    rows = [x for x in res["rows"] if x["delta_db"] is not None]
    usable = [x for x in rows if not x["saturated"]]
    assert len(rows) >= 4 and (res["stats"] or {"n": 0})["n"] == len(usable)
    # gesättigt heißt: Messung oder Modell am oberen SNR-Ende
    assert all(x["saturated"] == (x["measured_snr"] >= res["sat_db"] or x["predicted_snr"] >= res["sat_db"]) for x in rows)
    assert all(abs(x["measured_snr"] - x["predicted_snr"] - x["delta_db"]) < 0.11 for x in rows)
    assert res["rssi_stats"] and res["rssi_stats"]["n"] >= 1
    assert admin.post("/api/measurements", json={"lat": 1, "lon": 2, "rep": "xyz", "snr": 1}).status_code == 400


def test_series_settings_change_model_and_pdf(admin, project):
    import io
    pypdf = pytest.importorskip("pypdf")
    t0 = 1762100000
    batch = {"data": [{"type": "DISC", "lat": 51.565 + i * 0.002, "lon": 7.045, "timestamp": t0 + i * 60, "repeater_id": "ABAB",
                       "public_key": REP, "local_snr": -5.0 - i, "local_rssi": -105 - i, "remote_snr": -6} for i in range(4)]
             + [{"type": "DISC", "lat": 51.57, "lon": 7.03, "timestamp": t0 + 7200, "repeater_id": "ABAB",   # 2 h später: neue Reihe
                 "public_key": REP, "local_snr": -4.0, "local_rssi": -104, "remote_snr": -6}]}
    with TestClient(app) as anon:
        assert anon.post("/api/ingest/meshmapper", json=batch, headers={"X-API-Key": KEY}).json()["stored"] == 5
    d = admin.get("/api/measurements", params={"pk": REP}).json()
    mm = [s for s in d["series"] if s["src"] == "meshmapper"]
    assert sorted(s["n"] for s in mm) == [1, 4]
    big = next(s for s in mm if s["n"] == 4)
    project["stations"][0]["corescope_pk"] = REP
    body = {"project": project, "station_id": "a"}
    before = admin.post("/api/measurements/compare", json=body).json()
    assert admin.put(f"/api/measurements/series/{big['id']}", json={"rx_gain_dbi": 6, "rx_h": 2.5, "name": "Auto Dachantenne"}).json()["ok"]
    assert admin.put(f"/api/measurements/series/{big['id']}", json={"rx_h": 9999}).status_code == 400
    after = admin.post("/api/measurements/compare", json=body).json()
    b = {x["id"]: x for x in before["rows"]}
    moved = [x for x in after["rows"] if x["series_id"] == big["id"] and x["predicted_snr"] is not None]
    assert moved and all(x["rx_height_m"] == 2.5 and x["rx_gain_dbi"] == 6 for x in moved)
    assert all(x["predicted_snr"] > b[x["id"]]["predicted_snr"] + 5 for x in moved)   # +6 dBi und höher
    assert 2.5 in after["rx_heights"]
    pdf = admin.post("/api/report/pdf", json=body)
    assert pdf.status_code == 200
    text = " ".join(" ".join(pg.extract_text() for pg in pypdf.PdfReader(io.BytesIO(pdf.content)).pages).split())
    assert "Messfahrten" in text and "nicht möglich" not in text and "Auto Dachantenne" in text
    assert admin.request("DELETE", "/api/measurements", json={"series": big["id"]}).json()["deleted"] == 4
    assert all(s["id"] != big["id"] for s in admin.get("/api/measurements").json()["series"])
