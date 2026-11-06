"""#15 Profil mit eigener Empfängerhöhe, #16 Rechtstexte/Kachel-Proxy/Cookies, #17 öffentliche Projekte vorrechnen."""
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.sources import tiles
from app.main import app


@pytest.fixture
def admin():
    with TestClient(app) as c:
        assert c.post("/api/login", json={"user": "admin", "password": "test-pw"}).status_code == 200
        yield c


def test_profile_rx_override_changes_only_profile(admin, project):
    b = {"project": project, "station_id": "a", "lat": 51.56, "lon": 7.06}
    low = admin.post("/api/profile", json={**b, "rx_height_m": 1.5}).json()
    high = admin.post("/api/profile", json={**b, "rx_height_m": 30, "rx_gain_dbi": 6}).json()
    assert high["rx_alt"] - low["rx_alt"] == pytest.approx(28.5, abs=0.2)
    assert high["budget"]["rx_gain_dbi"] == 6 and low["budget"]["rx_gain_dbi"] == 0
    assert high["budget"]["prx_dbm"] > low["budget"]["prx_dbm"]


def test_legal_pages_and_no_visitor_cookies():
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200 and "set-cookie" not in r.headers
        site = c.get("/api/config").json()["site"]
        assert site["imprint"] == "/impressum" and site["privacy"] == "/datenschutz"  # Links baut die Oberfläche
        imp = c.get("/impressum").text
        assert "Impressum" in imp and "Einstellungen" in imp  # Hinweis, solange nichts eingetragen ist
        ds = c.get("/datenschutz").text
        assert "terrarf_session" in ds and "localStorage" in ds
        assert c.get("/api/config").json()["tile_proxy"] is True


def test_login_cookie_is_session_cookie_unless_remember():
    with TestClient(app) as c:
        r = c.post("/api/login", json={"user": "admin", "password": "test-pw"})
        assert "max-age" not in r.headers["set-cookie"].lower()
        r = c.post("/api/login", json={"user": "admin", "password": "test-pw", "remember": True})
        assert "max-age" in r.headers["set-cookie"].lower()


def test_tile_proxy_caches_and_validates(monkeypatch):
    calls = []

    def handler(req):
        calls.append(str(req.url))
        assert req.headers["user-agent"].startswith("TerraRF/")
        return httpx.Response(200, content=b"\x89PNG-fake")
    monkeypatch.setattr(tiles, "_client", httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": tiles.UA}))
    with TestClient(app) as c:
        for _ in range(2):
            r = c.get("/tiles/osm/12/2127/1366.png")
            assert r.status_code == 200 and r.content == b"\x89PNG-fake"
        assert len(calls) == 1  # zweiter Abruf aus dem Cache
        assert c.get("/tiles/osm/12/99999/1.png").status_code == 400
        assert c.get("/tiles/evil/1/0/0.png").status_code == 404
        r = c.get("/tiles/wms/nrwdop", params={"SERVICE": "WMS", "REQUEST": "GetMap", "LAYERS": "x", "BBOX": "1,2,3,4",
                                              "WIDTH": 256, "HEIGHT": 256, "SRS": "EPSG:3857", "evil": "1"})
        assert r.status_code == 200
        assert "layers=nw_dop_rgb" in calls[-1] and "evil" not in calls[-1]


def test_public_project_is_precomputed_on_save(admin, project):
    project["public"] = True
    project["stations"][0]["height_m"] = 31.5  # eigener Cache-Schlüssel für diesen Test
    from app.model import coverage as cov
    before = set(cov._station_cache)
    assert admin.put("/api/projects/warm", json=project).status_code == 200
    for _ in range(120):  # Vorberechnung läuft im Hintergrund – ohne dass ein Besucher etwas anfragt
        if set(cov._station_cache) - before:
            break
        time.sleep(0.25)
    assert set(cov._station_cache) - before
    with TestClient(app) as anon:
        r = anon.post("/api/coverage", json={"project_id": "warm"}).json()
        assert r["stations"][0]["cached"] is True


def test_pwa_manifest_and_icons():
    with TestClient(app) as c:
        r = c.get("/manifest.webmanifest")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
        m = r.json()
        assert m["display"] == "standalone" and m["start_url"] == "/"
        purposes = {i["purpose"] for i in m["icons"]}
        assert {"any", "maskable"} <= purposes
        for i in m["icons"]:
            assert c.get(i["src"]).status_code == 200
        html = c.get("/").text
        assert 'rel="manifest"' in html and "apple-touch-icon" in html and "viewport-fit=cover" in html


def test_mobile_max_px_gives_coarser_image(admin, project):
    full = admin.post("/api/coverage", json={"project": project, "station_ids": ["a"]}).json()
    small = admin.post("/api/coverage", json={"project": project, "station_ids": ["a"], "max_px": 500}).json()
    assert small["factor"] >= full["factor"]
    assert max(small["stations"][0]["w"], small["stations"][0]["h"]) <= 500


def test_admin_reopens_last_project(admin, project):
    assert admin.put("/api/projects/zuletzt", json=project).status_code == 200
    admin.get("/api/projects/zuletzt")
    assert admin.get("/api/config").json()["last_project"] == "zuletzt"
    with TestClient(app) as anon:
        assert anon.get("/api/config").json()["last_project"] is None


def test_status_busy_and_resources(admin, project):
    import threading
    from app.site import sysstat
    r = admin.get("/api/status").json()
    assert "busy" in r and "cpu_pct" in r["res"] and r["res"]["mem_used"]
    with TestClient(app) as anon:
        assert "res" not in anon.get("/api/status").json()  # Auslastung nur für den Admin
    ev = threading.Event()

    def work():
        with sysstat.activity("Test-Rechnung"):
            ev.wait(2)
    t = threading.Thread(target=work); t.start()
    time.sleep(0.1)
    assert any(x["what"] == "Test-Rechnung" for x in admin.get("/api/status").json()["busy"])
    ev.set(); t.join()
    assert not any(x["what"] == "Test-Rechnung" for x in admin.get("/api/status").json()["busy"])


def test_user_role_and_site_settings(admin, project, station):
    from app.store import appconfig
    # Admin richtet den Benutzer ein (Klartext-Passwort sichtbar) und verlinkt das Impressum
    r = admin.put("/api/admin/appconfig", json={"user_enabled": True, "user_name": "gast", "user_password": "geheim123",
                                                "imprint_mode": "link", "imprint_url": "https://forum.example/impressum",
                                                "parent_name": "funk.nrw", "parent_url": "https://funk.nrw"})
    assert r.status_code == 200, r.text
    assert admin.get("/api/admin/appconfig").json()["user_password"] == "geheim123"
    assert admin.put("/api/admin/appconfig", json={"imprint_url": "javascript:alert(1)"}).status_code == 400
    project["public"] = True
    project["stations"][0]["height_m"] = 33.3
    assert admin.put("/api/projects/geteilt", json=project).status_code == 200
    with TestClient(app) as anon:
        site = anon.get("/api/config").json()["site"]
        assert site["imprint"] == "https://forum.example/impressum" and site["parent"]["name"] == "funk.nrw"
        r = anon.get("/impressum", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "https://forum.example/impressum"
        assert anon.get("/api/admin/appconfig").status_code == 401
    with TestClient(app) as u:
        assert u.post("/api/login", json={"user": "gast", "password": "geheim123"}).json()["role"] == "user"
        me = u.get("/api/me").json()
        assert me["role"] == "user" and me["admin"] is False
        assert u.get("/api/admin/appconfig").status_code == 401
        # eigene Station anlegen, fremde Station verändern -> nur die eigene wird gespeichert
        sent = {**project, "name": "umbenannt", "public": False,
                "stations": [{**project["stations"][0], "height_m": 99}, {**station, "id": "u1", "name": "Meine", "owner": "user"}]}
        r = u.put("/api/projects/geteilt", json=sent)
        assert r.status_code == 200, r.text
        saved = admin.get("/api/projects/geteilt").json()
        assert saved["name"] == project["name"] and saved["public"] is True
        assert [s["height_m"] for s in saved["stations"] if s["id"] == "a"] == [33.3]
        assert any(s["id"] == "u1" for s in saved["stations"])
        assert u.put("/api/projects/neu-vom-user", json=project).status_code == 403
        # rechnen mit ungespeichertem Stand ist erlaubt
        assert u.post("/api/coverage", json={"project": sent, "station_ids": ["u1"]}).status_code == 200
    # Passwort geändert -> alte Benutzer-Sitzung ungültig
    admin.put("/api/admin/appconfig", json={"user_password": "anderes123"})
    appconfig_reset = {"user_enabled": False, "imprint_mode": "generated", "parent_name": "", "parent_url": ""}
    admin.put("/api/admin/appconfig", json=appconfig_reset)
