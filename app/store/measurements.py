"""Ground measurements (drive tests): MeshMapper forwarding (custom API endpoint), manual entry, CSV.

One row = one repeater heard at one place (or that heard us there).
  snr_down  SNR at the measuring device (repeater -> us)   – basis of the comparison with the coverage
  snr_up    SNR at the repeater (us -> repeater), DISC/TRACE only
All MeshMapper kinds that name a repeater are direct receptions (DISC/TRACE without hop; TX echoes and RX are
the repeater's directly heard retransmission).

Rows are grouped into series (one drive with the same receiver); see ``series_list`` and ``update_series``.
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

from .. import paths
from . import catalog
from ..sources import corescope

DB = os.path.join(paths.DATA_DIR, "measurements.db")
_lock = threading.Lock()
GRID_LAT, GRID_LON = 0.0027, 0.00384  # grid cell as in MeshMapper (~300 m)
PLAN_MIN_N, PLAN_MAX_SPREAD_DB = 3, 6.0
HEX = re.compile(r"^[0-9a-fA-F]{2,64}$")
_REP = re.compile(r"([0-9A-Fa-f]{2,64})\s*\(\s*(-?\d+(?:\.\d+)?|null|None)?\s*\)")

COLS = ("ts", "src", "kind", "lat", "lon", "alt", "rep_prefix", "rep_pk", "snr_down", "snr_up", "rssi_down",
        "noise", "power", "ext_ant", "radio", "contact", "rx_h", "note")
SERIES_GAP_S = 1800   # new series after a 30 min break
SERIES_EDIT = ("name", "rx_h", "rx_gain_dbi", "rx_cable_db", "ext_ant", "note", "device_id", "placement_id")


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
        # series (#22): continuous drives sharing the same receiving antenna
        c.execute("""CREATE TABLE IF NOT EXISTS series (id INTEGER PRIMARY KEY, src TEXT, skey TEXT, t0 INTEGER, t1 INTEGER,
            name TEXT, rx_h REAL, rx_gain_dbi REAL, rx_cable_db REAL, ext_ant INTEGER, note TEXT)""")
        if "series_id" not in [r[1] for r in c.execute("PRAGMA table_info(m)")]:
            c.execute("ALTER TABLE m ADD COLUMN series_id INTEGER")
        have = [r[1] for r in c.execute("PRAGMA table_info(series)")]
        for col in ("device_id", "placement_id"):   # 0.5: catalog profiles (#29)
            if col not in have:
                c.execute(f"ALTER TABLE series ADD COLUMN {col} TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS m_series ON m(series_id)")
        todo = c.execute("SELECT id, ts, src, contact, power, ext_ant FROM m WHERE series_id IS NULL ORDER BY ts").fetchall()
        for r in todo:  # put old data into series afterwards
            c.execute("UPDATE m SET series_id = ? WHERE id = ?", (_series_for(c, dict(r)), r["id"]))


def _f(v):
    try:
        if v in (None, "", "null", "None"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def resolve(prefix, lat, lon, max_km=80.0):
    """Short repeater id (2–6 hex) -> full key if unique in the area, else None."""
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
    """One ping object of the MeshMapper forwarding -> measurement rows (one per repeater heard)."""
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
    # in MeshMapper 0 means “not readable” – for DISC/TRACE store no value then
    for r in out:
        if r["kind"] in ("DISC", "TRACE") and r["snr_down"] == 0 and r["rssi_down"] == 0:
            r["snr_down"] = r["rssi_down"] = None
    return [r for r in out if r["snr_down"] is not None or r["snr_up"] is not None]


def _skey(r):
    if r.get("src") == "meshmapper":  # one series per device/power/antenna
        return "|".join(str(r.get(k) or "") for k in ("src", "contact", "power", "ext_ant"))
    return str(r.get("src") or "manual")


def _series_for(c, r, skey=None):
    """Matching series (same source/device, at most 30 min apart) or a new one."""
    skey = skey or _skey(r)
    ts = int(r.get("ts") or time.time())
    hit = c.execute("SELECT id, t0, t1 FROM series WHERE skey = ? AND t0 - ? <= ? AND t1 + ? >= ? ORDER BY t1 DESC LIMIT 1",
                    (skey, SERIES_GAP_S, ts, SERIES_GAP_S, ts)).fetchone()
    if hit:
        c.execute("UPDATE series SET t0 = MIN(t0, ?), t1 = MAX(t1, ?) WHERE id = ?", (ts, ts, hit[0]))
        return hit[0]
    ext = 1 if r.get("ext_ant") else None
    cur = c.execute("INSERT INTO series (src, skey, t0, t1, ext_ant) VALUES (?,?,?,?,?)", (r.get("src"), skey, ts, ts, ext))
    return cur.lastrowid


def add(rows, skey=None):
    """skey: put all rows into one series of their own (e.g. a CSV import)."""
    if not rows:
        return 0
    n = 0
    with _lock, _db() as c:
        for r in rows:
            if r["kind"] in ("TX", "RX") and r.get("snr_down") is not None and c.execute(
                    # MeshMapper reports an echo in the TX entry and again as its own RX – count it once
                    "SELECT 1 FROM m WHERE kind IN ('TX','RX') AND rep_prefix = ? AND snr_down = ? AND ABS(ts - ?) <= 30 "
                    "AND ABS(lat - ?) < 0.0005 AND ABS(lon - ?) < 0.0008 LIMIT 1",
                    (r["rep_prefix"], r["snr_down"], r["ts"], r["lat"], r["lon"])).fetchone():
                continue
            if c.execute("SELECT 1 FROM m WHERE uniq = ?", (r["uniq"],)).fetchone():
                continue
            sid = _series_for(c, r, skey)
            cur = c.execute(f"INSERT OR IGNORE INTO m ({','.join(COLS)},uniq,series_id) VALUES ({','.join('?' * (len(COLS) + 2))})",
                            [r.get(k) for k in COLS] + [r["uniq"], sid])
            n += cur.rowcount
    return n


def manual(d):
    """Manual entry: lat, lon, repeater (key/prefix) and snr_down are required; the rest is optional."""
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
    """CSV with header: lat,lon,rep,snr[,snr_up,rssi,rx_h,ts,note] (separator , or ;)."""
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


def query(bbox=None, pk=None, days=None, public=False, series=None):
    sql, args = "SELECT * FROM m WHERE 1=1", []
    if series:
        sql += " AND series_id = ?"
        args.append(int(series))
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
        if public:  # visitors: no time, no device
            for k in ("ts", "contact", "note", "power"):
                r.pop(k, None)
    return rows


def delete(ids=None, src=None, series=None):
    n = 0
    with _lock, _db() as c:
        if ids:
            n = c.execute(f"DELETE FROM m WHERE id IN ({','.join('?' * len(ids))})", list(ids)).rowcount
        elif series:
            n = c.execute("DELETE FROM m WHERE series_id = ?", (int(series),)).rowcount
        elif src:
            n = c.execute("DELETE FROM m WHERE src = ?", (src,)).rowcount
        c.execute("DELETE FROM series WHERE id NOT IN (SELECT DISTINCT series_id FROM m WHERE series_id IS NOT NULL)")
    return n


def series_list(public=False):
    """Series with counts, period and the receiving antenna."""
    with _db() as c:
        out = []
        for r in c.execute("""SELECT s.*, COUNT(m.id) AS n, COUNT(DISTINCT COALESCE(m.rep_pk, m.rep_prefix)) AS repeaters,
                MAX(m.ext_ant) AS ext_seen, MAX(m.power) AS power_seen, MAX(m.radio) AS radio_seen, MIN(m.lat) AS s_, MAX(m.lat) AS n_,
                MIN(m.lon) AS w_, MAX(m.lon) AS e_
                FROM series s JOIN m ON m.series_id = s.id GROUP BY s.id ORDER BY s.t1 DESC"""):
            d = dict(r)
            d["bounds"] = [d.pop("s_"), d.pop("w_"), d.pop("n_"), d.pop("e_")]
            d.pop("skey", None)
            if public:
                for k in ("t0", "t1", "note", "power_seen"):
                    d.pop(k, None)
            out.append(d)
    return out


def series_map():
    with _db() as c:
        return {r["id"]: dict(r) for r in c.execute("SELECT * FROM series")}


def update_series(sid, d):
    vals = {}
    for k in SERIES_EDIT:
        if k not in d:
            continue
        v = d[k]
        if k in ("name", "note"):
            vals[k] = (str(v or "").strip()[:120 if k == "name" else 500]) or None
        elif k in ("device_id", "placement_id"):
            v = str(v or "").strip()
            kind = "devices" if k == "device_id" else "placements"
            if v and catalog.get(kind, v) is None:
                raise ValueError(f"{k}: not in the catalog")
            vals[k] = v or None
        elif k == "ext_ant":
            vals[k] = None if v in (None, "") else (1 if v else 0)
        else:
            f = _f(v)
            if f is not None and not (-50 <= f <= 500):
                raise ValueError(f"{k}: unplausibler Wert")
            vals[k] = f
    if not vals:
        return 0
    with _lock, _db() as c:
        return c.execute(f"UPDATE series SET {', '.join(k + ' = ?' for k in vals)} WHERE id = ?", [*vals.values(), int(sid)]).rowcount


def stats():
    with _db() as c:
        r = c.execute("SELECT COUNT(*), MIN(ts), MAX(ts), COUNT(DISTINCT rep_pk) FROM m").fetchone()
        by = {s: n for s, n in c.execute("SELECT src, COUNT(*) FROM m GROUP BY src")}
    return {"count": r[0], "first": r[1], "last": r[2], "repeaters": r[3], "by_source": by}


def cell_of(lat, lon):
    return int(math.floor(lat / GRID_LAT)), int(math.floor(lon / GRID_LON))


def plan(rows):
    """Measurement plan: a ~300 m cell is “enough” once one repeater has ≥ 3 measurements there with ≤ 6 dB scatter."""
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
