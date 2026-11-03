"""Messungen am Boden: MeshMapper-Weiterleitung (Custom API Endpoint), Eingabe von Hand, CSV.

Eine Zeile = ein Repeater, der an einem Ort gehört wurde (bzw. uns gehört hat).
  snr_down  SNR beim Messgerät (Repeater -> wir)      – Grundlage für den Abgleich mit der Abdeckung
  snr_up    SNR beim Repeater (wir -> Repeater), nur DISC/TRACE
Alle MeshMapper-Arten mit Repeater-Angabe sind direkte Empfänge (DISC/TRACE ohne Hop, TX-Echos und RX
sind die direkt gehörte Wiederholung des Repeaters).
"""
from __future__ import annotations

import csv
import io
import math
import os
import re
import sqlite3
import threading
import time

from . import corescope, terrain

DB = os.path.join(terrain.DATA_DIR, "measurements.db")
_lock = threading.Lock()
GRID_LAT, GRID_LON = 0.0027, 0.00384  # Rasterzelle wie MeshMapper (~300 m)
PLAN_MIN_N, PLAN_MAX_SPREAD_DB = 3, 6.0
HEX = re.compile(r"^[0-9a-fA-F]{2,64}$")
_REP = re.compile(r"([0-9A-Fa-f]{2,64})\s*\(\s*(-?\d+(?:\.\d+)?|null|None)?\s*\)")

COLS = ("ts", "src", "kind", "lat", "lon", "alt", "rep_prefix", "rep_pk", "snr_down", "snr_up", "rssi_down",
        "noise", "power", "ext_ant", "radio", "contact", "rx_h", "note")


def _db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS m (id INTEGER PRIMARY KEY, ts INTEGER, src TEXT, kind TEXT, lat REAL, lon REAL,
            alt REAL, rep_prefix TEXT, rep_pk TEXT, snr_down REAL, snr_up REAL, rssi_down REAL, noise REAL, power TEXT,
            ext_ant INTEGER, radio TEXT, contact TEXT, rx_h REAL, note TEXT, uniq TEXT UNIQUE)""")
        c.execute("CREATE INDEX IF NOT EXISTS m_pk ON m(rep_pk)")
        c.execute("CREATE INDEX IF NOT EXISTS m_ll ON m(lat, lon)")


def _f(v):
    try:
        if v in (None, "", "null", "None"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def resolve(prefix, lat, lon, max_km=80.0):
    """Kurze Repeater-ID (2–6 Hex) -> voller Schlüssel, wenn im Umkreis eindeutig. Sonst None."""
    p = (prefix or "").lower()
    if len(p) == 64:
        return p
    cand = [n for n in corescope.nodes() if n["pk"].startswith(p)]
    if lat is not None and lon is not None:
        cand = [n for n in cand if _km(lat, lon, n["lat"], n["lon"]) <= max_km]
    return cand[0]["pk"] if len(cand) == 1 else None


def _km(a, b, c, d):
    r = math.pi / 180
    x = (d - b) * r * math.cos((a + c) / 2 * r)
    return 6371.0 * math.hypot(x, (c - a) * r)


def _row(**kw):
    r = {k: kw.get(k) for k in COLS}
    if r["rep_prefix"]:
        r["rep_prefix"] = r["rep_prefix"].lower()
        r["rep_pk"] = (r["rep_pk"] or "").lower() or resolve(r["rep_prefix"], r["lat"], r["lon"])
    r["uniq"] = "|".join(str(r[k]) for k in ("src", "ts", "kind", "lat", "lon", "rep_prefix"))
    return r


def from_meshmapper(item):
    """Ein Ping-Objekt der MeshMapper-Weiterleitung -> Messzeilen (eine je gehörtem Repeater)."""
    kind = str(item.get("type") or "").upper()
    lat, lon = _f(item.get("lat")), _f(item.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return []
    base = dict(ts=int(_f(item.get("timestamp")) or time.time()), src="meshmapper", kind=kind, lat=lat, lon=lon,
                alt=_f(item.get("altitude")), noise=_f(item.get("noisefloor")), power=str(item.get("power") or "") or None,
                ext_ant=1 if item.get("external_antenna") else 0, radio=item.get("radio_freq"), contact=item.get("contact"))
    out = []
    if kind in ("DISC", "TRACE"):
        rid = str(item.get("repeater_id") or "")
        if not HEX.match(rid):
            return []  # fehlgeschlagene Erkennung
        pk = str(item.get("public_key") or "").lower()
        out.append(_row(**base, rep_prefix=rid, rep_pk=pk if len(pk) == 64 else None,
                        snr_down=_f(item.get("local_snr")), snr_up=_f(item.get("remote_snr")), rssi_down=_f(item.get("local_rssi"))))
    elif kind in ("TX", "RX"):
        for rid, snr in _REP.findall(str(item.get("heard_repeats") or "")):
            out.append(_row(**base, rep_prefix=rid, snr_down=_f(snr)))
    # 0 bedeutet bei MeshMapper „nicht lesbar“ – bei DISC/TRACE dann lieber ohne Wert
    for r in out:
        if r["kind"] in ("DISC", "TRACE") and r["snr_down"] == 0 and r["rssi_down"] == 0:
            r["snr_down"] = r["rssi_down"] = None
    return [r for r in out if r["snr_down"] is not None or r["snr_up"] is not None]


def add(rows):
    if not rows:
        return 0
    n = 0
    with _lock, _db() as c:
        for r in rows:
            if r["kind"] in ("TX", "RX") and r.get("snr_down") is not None and c.execute(
                    # MeshMapper meldet ein Echo im TX-Eintrag und zusätzlich als eigenes RX – nur einmal zählen
                    "SELECT 1 FROM m WHERE kind IN ('TX','RX') AND rep_prefix = ? AND snr_down = ? AND ABS(ts - ?) <= 30 "
                    "AND ABS(lat - ?) < 0.0005 AND ABS(lon - ?) < 0.0008 LIMIT 1",
                    (r["rep_prefix"], r["snr_down"], r["ts"], r["lat"], r["lon"])).fetchone():
                continue
            cur = c.execute(f"INSERT OR IGNORE INTO m ({','.join(COLS)},uniq) VALUES ({','.join('?' * (len(COLS) + 1))})",
                            [r.get(k) for k in COLS] + [r["uniq"]])
            n += cur.rowcount
    return n


def manual(d):
    """Eingabe von Hand: lat, lon, Repeater (Schlüssel/Präfix), snr_down Pflicht; Rest optional."""
    lat, lon, rep = _f(d.get("lat")), _f(d.get("lon")), str(d.get("rep") or d.get("rep_pk") or d.get("rep_prefix") or "").strip()
    if lat is None or lon is None or not HEX.match(rep):
        raise ValueError("Ort und Repeater (Hex-Schlüssel oder -Präfix) sind nötig")
    snr = _f(d.get("snr_down") if d.get("snr_down") is not None else d.get("snr"))
    if snr is None and _f(d.get("snr_up")) is None:
        raise ValueError("SNR fehlt")
    return _row(ts=int(_f(d.get("ts")) or time.time()), src=d.get("src") or "manual", kind=d.get("kind") or "MANUAL", lat=lat,
                lon=lon, alt=_f(d.get("alt")), rep_prefix=rep, rep_pk=rep.lower() if len(rep) == 64 else None, snr_down=snr,
                snr_up=_f(d.get("snr_up")), rssi_down=_f(d.get("rssi")), power=d.get("power"), rx_h=_f(d.get("rx_h")),
                note=(str(d.get("note") or "")[:300] or None))


def from_csv(text):
    """CSV mit Kopfzeile: lat,lon,rep,snr[,snr_up,rssi,rx_h,ts,note] (Trennzeichen , oder ;)."""
    dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t")
    rows, errs = [], []
    for i, d in enumerate(csv.DictReader(io.StringIO(text), dialect=dialect), start=2):
        d = {k.strip().lower(): (v or "").strip().replace(",", ".") if k and k.strip().lower() not in ("note", "rep") else (v or "").strip()
             for k, v in d.items() if k}
        try:
            rows.append(manual({**d, "src": "csv", "kind": "CSV"}))
        except ValueError as e:
            errs.append(f"Zeile {i}: {e}")
    return rows, errs


def query(bbox=None, pk=None, days=None, public=False):
    sql, args = "SELECT * FROM m WHERE 1=1", []
    if bbox:
        sql += " AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"
        args += [bbox[0], bbox[2], bbox[1], bbox[3]]
    if pk:
        sql += " AND (rep_pk = ? OR (rep_pk IS NULL AND ? LIKE rep_prefix || '%'))"
        args += [pk.lower(), pk.lower()]
    if days:
        sql += " AND ts >= ?"
        args.append(int(time.time() - float(days) * 86400))
    sql += " ORDER BY ts DESC LIMIT 20000"
    with _db() as c:
        rows = [dict(r) for r in c.execute(sql, args)]
    for r in rows:
        r.pop("uniq", None)
        if public:  # Besucher: keine Zeit, kein Gerät
            for k in ("ts", "contact", "note", "power"):
                r.pop(k, None)
    return rows


def delete(ids=None, src=None):
    with _lock, _db() as c:
        if ids:
            return c.execute(f"DELETE FROM m WHERE id IN ({','.join('?' * len(ids))})", list(ids)).rowcount
        if src:
            return c.execute("DELETE FROM m WHERE src = ?", (src,)).rowcount
    return 0


def stats():
    with _db() as c:
        r = c.execute("SELECT COUNT(*), MIN(ts), MAX(ts), COUNT(DISTINCT rep_pk) FROM m").fetchone()
        by = {s: n for s, n in c.execute("SELECT src, COUNT(*) FROM m GROUP BY src")}
    return {"count": r[0], "first": r[1], "last": r[2], "repeaters": r[3], "by_source": by}


def cell_of(lat, lon):
    return int(math.floor(lat / GRID_LAT)), int(math.floor(lon / GRID_LON))


def plan(rows):
    """Messplan: je ~300-m-Zelle „genug“, wenn ein Repeater dort ≥ 3 Messungen mit Streuung ≤ 6 dB hat."""
    cells = {}
    for r in rows:
        if r.get("snr_down") is None:
            continue
        k = cell_of(r["lat"], r["lon"])
        cells.setdefault(k, {}).setdefault(r.get("rep_pk") or r.get("rep_prefix"), []).append(r["snr_down"])
    out = []
    for (i, j), reps in cells.items():
        best = None
        for rep, v in reps.items():
            ok = len(v) >= PLAN_MIN_N and (max(v) - min(v)) <= PLAN_MAX_SPREAD_DB
            if ok and (best is None or len(v) > best[1]):
                best = (rep, len(v))
        out.append({"s": i * GRID_LAT, "w": j * GRID_LON, "n": (i + 1) * GRID_LAT, "e": (j + 1) * GRID_LON,
                    "done": best is not None, "count": sum(len(v) for v in reps.values()), "repeaters": len(reps)})
    return out
