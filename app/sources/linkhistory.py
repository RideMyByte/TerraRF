"""History of the links between repeaters (#52).

CoreScope only answers with the current state: counts over its data window and the last directly reported SNR.
To show scatter and a time course, TerraRF keeps every new state it fetched in a small SQLite table
(data/corescope/history.db): one row per link and fetch, but only when something changed – a newly heard
neighbour report, another SNR or other counts. Links are fetched when somebody looks at them and, for repeaters
that are stations of a project, once with every CoreScope sync (see corescope.record_station_links).
"""
from __future__ import annotations

import math
import os
import sqlite3
import statistics
import threading
import time

from ..paths import DATA_DIR

DB = os.path.join(DATA_DIR, "corescope", "history.db")
KEEP_DAYS = 400
_lock = threading.Lock()


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS link_obs (centre TEXT, partner TEXT, ts INTEGER, snr REAL, snr_rev REAL,
        snr_any REAL, they_hear INTEGER, we_hear INTEGER, heard_at INTEGER, heard_rev_at INTEGER, window_days REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS link_obs_pair ON link_obs(centre, partner, ts)")
    return c


FIELDS = ("snr", "snr_rev", "snr_any", "they_hear", "we_hear", "heard_at", "heard_rev_at")


def record(links_result, now=None):
    """Store the links of one fetched result whose values differ from the last stored row of that link."""
    centre = links_result.get("pk")
    if not centre:
        return 0
    now = int(now or time.time())
    stored = 0
    with _lock, _db() as c:
        for link in links_result.get("links") or []:
            last = c.execute(f"SELECT {','.join(FIELDS)} FROM link_obs WHERE centre = ? AND partner = ? ORDER BY ts DESC LIMIT 1",
                             (centre, link.get("pk"))).fetchone()
            values = tuple(link.get(k) for k in FIELDS)
            if last is not None and tuple(last) == values:
                continue
            c.execute("INSERT INTO link_obs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (centre, link.get("pk"), now, *values, links_result.get("window_days")))
            stored += 1
        c.execute("DELETE FROM link_obs WHERE ts < ?", (now - KEEP_DAYS * 86400,))
    return stored


def _stats(values):
    values = [v for v in values if v is not None and not math.isnan(v)]
    if not values:
        return None
    return {"n": len(values), "mean": round(statistics.fmean(values), 2), "median": round(statistics.median(values), 2),
            "std": round(statistics.pstdev(values), 2) if len(values) > 1 else 0.0, "min": min(values), "max": max(values)}


def history(centre, partner):
    """Everything recorded for one link, oldest first, with statistics of the directly reported SNR.

    A directly reported SNR counts once per report (same heard_at = same report, fetched again)."""
    with _db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM link_obs WHERE centre = ? AND partner = ? ORDER BY ts",
                                           (centre, partner))]
    direct = {r["heard_at"]: r["snr"] for r in rows if r["snr"] is not None and r["heard_at"]}
    reverse = {r["heard_rev_at"]: r["snr_rev"] for r in rows if r["snr_rev"] is not None and r["heard_rev_at"]}
    return {
        "since": rows[0]["ts"] if rows else None,
        "points": [{k: r[k] for k in ("ts", *FIELDS)} for r in rows],
        "snr": _stats(list(direct.values())),
        "snr_rev": _stats(list(reverse.values())),
        "snr_series": sorted(direct.items()),
        "snr_rev_series": sorted(reverse.items()),
    }
