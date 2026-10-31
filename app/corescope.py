"""CoreScope (MeshCore-Analyzer) als Datenquelle: Repeater-Standorte, Verbindungen, gemessene SNR.

Nur lesende GET-Abfragen, serverseitig mit Cache (CORS der Instanzen erlaubt keinen Browserzugriff).
Höflich: eigene User-Agent-Kennung, Pause zwischen Seiten, Verbindungen höchstens alle LINKS_TTL_S neu.

Verwendete Endpunkte (CoreScope OpenAPI):
  /api/nodes?role=repeater&limit=&offset=   -> Knoten, seitenweise bis has_more false
  /api/nodes/{pk}/reach                     -> Verbindungen (we_hear/they_hear) + direct_observers mit avg_snr
  /api/nodes/{pk}/neighbors                 -> Nachbarn mit avg_snr und observers

SNR-Bewertung: Ein avg_snr gilt nur dann als Messung der Strecke Knoten -> Partner, wenn der Partner selbst
Observer ist (steht in seiner eigenen observers-Liste) bzw. in direct_observers auftaucht. Sonst ist es
das SNR des letzten Hops zu irgendeinem Observer und sagt über die Strecke nichts.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

import httpx

from . import terrain
from .version import VERSION

log = logging.getLogger("terrarf.corescope")

BASE = os.environ.get("RFP_CORESCOPE_URL", "").strip().rstrip("/")
SYNC_H = float(os.environ.get("RFP_CORESCOPE_SYNC_H", "24") or 0)
LINKS_TTL_S = 6 * 3600
PAGE = 200
MAX_PAGES = 100
PAUSE_S = 0.3
UA = f"TerraRF/{VERSION} (+https://github.com/RideMyByte/TerraRF)"
DIR = os.path.join(terrain.DATA_DIR, "corescope")
os.makedirs(DIR, exist_ok=True)
NODES_FILE = os.path.join(DIR, "nodes.json")
PK = re.compile(r"^[0-9a-f]{64}$")
ROLES = ("repeater", "room")
LINKS_VERSION = 2
OVERRIDES_FILE = os.path.join(DIR, "overrides.json")
_ovr_lock = threading.Lock()

_state = {"syncing": False, "error": None, "progress": None}
_lock = threading.Lock()
_req_lock = threading.Lock()
_last_req = [0.0]
_nodes_cache = {"mtime": None, "data": None}


def enabled():
    return bool(BASE)


def _client():
    return httpx.Client(base_url=BASE, timeout=30, follow_redirects=True, headers={"User-Agent": UA})


def _get(client, path, params=None):
    with _req_lock:  # höchstens eine Anfrage gleichzeitig, mit Mindestabstand
        wait = PAUSE_S - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = client.get(path, params=params)
        finally:
            _last_req[0] = time.time()
    r.raise_for_status()
    return r.json()


def _slim(n):
    try:
        lat, lon = float(n.get("lat")), float(n.get("lon"))
    except (TypeError, ValueError):
        return None
    pk = str(n.get("public_key") or "").lower()
    if not PK.match(pk) or (abs(lat) < 0.01 and abs(lon) < 0.01) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {"pk": pk, "name": n.get("name") or pk[:8], "role": n.get("role"), "lat": round(lat, 6), "lon": round(lon, 6),
            "last_seen": n.get("last_seen") or n.get("last_heard"), "first_seen": n.get("first_seen"),
            "relay_24h": n.get("relay_count_24h")}


def _read_nodes():
    try:
        m = os.path.getmtime(NODES_FILE)
    except OSError:
        return None
    if _nodes_cache["mtime"] != m:
        with open(NODES_FILE) as f:
            _nodes_cache.update(mtime=m, data=json.load(f))
    return _nodes_cache["data"]


def status():
    d = _read_nodes()
    return {"enabled": enabled(), "url": BASE or None, "fetched_at": d and d.get("fetched_at"),
            "count": len(d["nodes"]) if d else 0, "total_remote": d and d.get("total_remote"),
            "syncing": _state["syncing"], "progress": _state["progress"], "error": _state["error"]}


def _sync():
    nodes, total_remote = {}, {}
    try:
        with _client() as c:
            for role in ROLES:
                for page in range(MAX_PAGES):
                    _state["progress"] = f"{role}: Seite {page + 1}"
                    d = _get(c, "/api/nodes", {"role": role, "limit": PAGE, "offset": page * PAGE})
                    for n in d.get("nodes") or []:
                        s = _slim(n)
                        if s:
                            nodes[s["pk"]] = s
                    total_remote = d.get("counts") or total_remote
                    if not d.get("has_more") or not d.get("nodes"):
                        break
        out = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "url": BASE,
               "total_remote": total_remote, "nodes": list(nodes.values())}
        tmp = NODES_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(out, f)
        os.replace(tmp, NODES_FILE)
        _state["error"] = None
        log.info("CoreScope: %d Knoten mit Standort übernommen", len(nodes))
    except Exception as e:  # noqa: BLE001 – Status für die Oberfläche
        _state["error"] = f"{type(e).__name__}: {e}"
        log.warning("CoreScope-Sync fehlgeschlagen: %s", e)
    finally:
        _state["syncing"] = False
        _state["progress"] = None


def start_sync():
    if not enabled():
        raise ValueError("RFP_CORESCOPE_URL ist nicht gesetzt")
    with _lock:
        if _state["syncing"]:
            return False
        _state["syncing"] = True
    threading.Thread(target=_sync, daemon=True).start()
    return True


def auto_sync_loop():
    """Beim Start synchronisieren, wenn nichts oder Veraltetes da ist, danach alle SYNC_H Stunden."""
    if not enabled() or SYNC_H <= 0:
        return
    while True:
        try:
            age = time.time() - os.path.getmtime(NODES_FILE)
        except OSError:
            age = 1e12
        if age >= SYNC_H * 3600:
            try:
                start_sync()
            except ValueError:
                return
            age = 0
        time.sleep(max(600, SYNC_H * 3600 - age))


def nodes(bbox=None):
    d = _read_nodes()
    if not d:
        return []
    out = d["nodes"]
    if bbox:
        s, w, n, e = bbox
        out = [x for x in out if s <= x["lat"] <= n and w <= x["lon"] <= e]
    return out


def node(pk):
    d = _read_nodes()
    return next((x for x in (d or {}).get("nodes", []) if x["pk"] == pk), None)


def _links_path(pk):
    return os.path.join(DIR, f"links_{pk}.json")


def links(pk, refresh=False):
    if not PK.match(pk):
        raise ValueError("ungültiger Schlüssel")
    p = _links_path(pk)
    if not refresh and os.path.exists(p) and time.time() - os.path.getmtime(p) < LINKS_TTL_S:
        with open(p) as f:
            d = json.load(f)
        if d.get("v") == LINKS_VERSION:
            return d
    if not enabled():
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
        raise ValueError("RFP_CORESCOPE_URL ist nicht gesetzt")
    with _client() as c:
        reach = _get(c, f"/api/nodes/{pk}/reach")
        try:
            nb = _get(c, f"/api/nodes/{pk}/neighbors")
        except httpx.HTTPError:
            nb = {"neighbors": []}
    out = merge_links(pk, reach, nb)
    with open(p, "w") as f:
        json.dump(out, f)
    return out


def merge_links(pk, reach, nb):
    """Verbindungen aus reach + neighbors zusammenführen; SNR nur dort, wo es die Strecke misst."""
    me = reach.get("node") or {}
    L = {}
    for x in reach.get("links") or []:
        q = str(x.get("pubkey") or "").lower()
        if not PK.match(q) or x.get("lat") is None or x.get("lon") is None:
            continue
        L[q] = {"pk": q, "name": x.get("name"), "role": x.get("role"), "lat": x["lat"], "lon": x["lon"],
                "we_hear": int(x.get("we_hear") or 0), "they_hear": int(x.get("they_hear") or 0),
                "bidir": bool(x.get("bidir")), "distance_km": x.get("distance_km"), "snr": None, "snr_n": 0}
    meas, where = {}, {}
    for o in reach.get("direct_observers") or []:
        q0 = str(o.get("pubkey") or "").lower()
        if o.get("lat") is not None and o.get("lon") is not None:
            where[q0] = (o["lat"], o["lon"], o.get("distance_km"))
        q = str(o.get("pubkey") or "").lower()
        if PK.match(q) and o.get("avg_snr") is not None:
            meas[q] = (float(o["avg_snr"]), int(o.get("count") or 0), o)
    for x in nb.get("neighbors") or []:
        q = str(x.get("pubkey") or "").lower()
        if not PK.match(q):
            continue
        e = L.get(q)
        if e is None:  # nur aus /neighbors bekannt -> Standort aus der Knotenliste
            kn = node(q)
            if kn:
                e = L[q] = {"pk": q, "name": x.get("name") or kn["name"], "role": x.get("role") or kn.get("role"),
                            "lat": kn["lat"], "lon": kn["lon"], "we_hear": 0, "they_hear": 0, "bidir": False,
                            "distance_km": x.get("distance_km"), "snr": None, "snr_n": 0}
        if e is not None:
            e["count"] = int(x.get("count") or 0)
            if x.get("avg_snr") is not None:
                e["snr_any"] = round(float(x["avg_snr"]), 2)  # Mittel über alle Observer – nicht die Strecke
        if x.get("avg_snr") is not None and q in [str(v).lower() for v in x.get("observers") or []]:
            meas[q] = (float(x["avg_snr"]), int(x.get("count") or 0), x)  # neuer/vollständiger als reach
    for q, (snr, cnt, src) in meas.items():
        e = L.get(q)
        if e is None:
            lat, lon, dist = where.get(q, (None, None, src.get("distance_km")))
            if lat is None or lon is None:
                kn = node(q)
                if not kn:
                    continue
                lat, lon = kn["lat"], kn["lon"]
            e = L[q] = {"pk": q, "name": src.get("name"), "role": src.get("role") or "observer", "lat": lat, "lon": lon,
                        "we_hear": 0, "they_hear": 0, "bidir": False, "distance_km": dist or src.get("distance_km")}
        e["snr"] = round(snr, 2)
        e["snr_n"] = cnt
    for e in L.values():
        e.setdefault("count", 0)
        e.setdefault("snr_any", e.get("snr"))
    return {"v": LINKS_VERSION, "pk": pk, "name": me.get("name"), "lat": me.get("lat"), "lon": me.get("lon"),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "window_days": (reach.get("window") or {}).get("days"), "links": list(L.values())}


# ---------------------------------------------------------------- eigene Angaben je Repeater (ohne Station)
def overrides():
    try:
        with open(OVERRIDES_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def set_override(pk, data):
    """Höhe/Gewinn/Kabel für einen Repeater, der nicht als Station angelegt ist (für den Abgleich)."""
    if not PK.match(pk):
        raise ValueError("ungültiger Schlüssel")
    clean = {}
    for k, lo, hi in (("height_m", 0, 400), ("gain_dbi", -10, 40), ("cable_db", 0, 20)):
        v = data.get(k)
        if v not in (None, ""):
            v = float(v)
            if not lo <= v <= hi:
                raise ValueError(f"{k} außerhalb {lo}…{hi}")
            clean[k] = v
    with _ovr_lock:
        o = overrides()
        if clean:
            o[pk] = clean
        else:
            o.pop(pk, None)
        tmp = OVERRIDES_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(o, f)
        os.replace(tmp, OVERRIDES_FILE)
    return clean
