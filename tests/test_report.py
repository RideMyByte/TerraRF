"""Auswertung je Station und PDF-Bericht."""
from fastapi.testclient import TestClient

from app.main import app


def test_summary_and_pdf(project):
    with TestClient(app) as c:
        c.post("/api/login", json={"user": "admin", "password": "test-pw"})
        r = c.post("/api/report/summary", json={"project": project, "station_id": "a"})
        assert r.status_code == 200, r.text
        sm = r.json()
        assert sm["area_total_km2"] > 0 and 0 <= sm["covered_pct"] <= 100
        assert abs(sum(x["km2"] for x in sm["classes"]) - sm["area_total_km2"]) < 0.5
        assert sm["rings"] and sm["rings"][0]["from_km"] == 0
        p = c.post("/api/report/pdf", json={"project": project, "station_id": "a"})
        assert p.status_code == 200, p.text[:300]
        assert p.headers["content-type"] == "application/pdf" and p.content[:5] == b"%PDF-"
        assert "attachment" in p.headers["content-disposition"]
    with TestClient(app) as anon:  # Besucher nur für gespeicherte, öffentliche Projekte
        assert anon.post("/api/report/pdf", json={"project": project, "station_id": "a"}).status_code == 401
