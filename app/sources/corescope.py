"""CoreScope (MeshCore analyzer) as a data source: repeater locations, links, measured SNR.

Read-only GET requests, cached on the server (the instances' CORS does not allow browser access).
Polite: own User-Agent, pause between pages, links refreshed at most every LINKS_TTL_S.

Endpoints used (CoreScope OpenAPI):
  /api/nodes?role=repeater&limit=&offset=   -> nodes, page by page until has_more is false
  /api/nodes/{pk}/reach                     -> links (we_hear/they_hear) + direct_observers with avg_snr
  /api/nodes/{pk}/neighbors                 -> neighbours with avg_snr and observers

SNR rating: an avg_snr only counts as a measurement of the link node -> partner when the partner itself is an
observer (listed in its own observers) or appears in direct_observers. Otherwise it is the SNR of the last
hop to some observer and says nothing about the link.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

import httpx

from .. import paths
from ..model import terrain
from ..site import offline
from ..version import VERSION

log = logging.getLogger("terrarf.corescope")

BASE = os.environ.get("RFP_CORESCOPE_URL", "").strip().rstrip("/")
SYNC_H = float(os.environ.get("RFP_CORESCOPE_SYNC_H", "24") or 0)
LINKS_TTL_S = 6 * 3600
PAGE = 200
MAX_PAGES = 100
PAUSE_S = 0.3
UA = f"TerraRF/{VERSION} (+https://github.com/RideMyByte/TerraRF)"
DIR = os.path.join(paths.DATA_DIR, "corescope")
os.makedirs(DIR, exist_ok=True)
NODES_FILE = os.path.join(DIR, "nodes.json")
PK = re.compile(r"^[0-9a-f]{64}$")
ROLES = ("repeater", "room")
LINKS_VERSION = 4
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
    with _req_lock:  # at most one request at a time, with a minimum gap
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
    except Exception as e:  # noqa: BLE001 – status for the interface
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
    """Sync at start-up when nothing or only stale data is there, then every SYNC_H hours."""
    if not enabled() or SYNC_H <= 0:
        return
    while True:
        try:
            age = time.time() - os.path.getmtime(NODES_FILE)
        except OSError:
            age = 1e12
        if age >= SYNC_H * 3600 and not offline.allowed(BASE):
            time.sleep(600)            # #58: offline and CoreScope is not in the local network – try again later
            continue
        if age >= SYNC_H * 3600:
            try:
                start_sync()
            except ValueError:
                return
            threading.Thread(target=record_station_links, daemon=True).start()
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
    try:
        from . import linkhistory
        linkhistory.record(out)          # #52: keep the state for scatter and time course
    except Exception as e:  # noqa: BLE001 – history is optional
        log.warning("link history: %s", e)
    return out


def record_station_links(max_repeaters=40):
    """After a sync: fetch the links of repeaters that are stations of a project, so their history grows even when
    nobody looks (#52). At most max_repeaters, slowly, one after the other."""
    from ..store import projects
    pks = []
    for item in projects.list_all("admin"):
        try:
            for st in projects.load(item["id"]).get("stations") or []:
                pk = str(st.get("corescope_pk") or "").lower()
                if PK.match(pk) and pk not in pks:
                    pks.append(pk)
        except Exception:  # noqa: BLE001
            continue
    for pk in pks[:max_repeaters]:
        try:
            links(pk, refresh=True)
        except Exception as e:  # noqa: BLE001
            log.warning("links %s: %s", pk[:8], e)
        time.sleep(2)


def merge_links(pk, reach, nb, reports=None):
    """Merge links from reach + neighbors; SNR only where it measures the link itself."""
    me = reach.get("node") or {}
    L = {}
    for x in reach.get("links") or []:
        q = str(x.get("pubkey") or "").lower()
        if not PK.match(q) or x.get("lat") is None or x.get("lon") is None:
            continue
        L[q] = {"pk": q, "name": x.get("name"), "role": x.get("role"), "lat": x["lat"], "lon": x["lon"],
                "last_seen": x.get("last_seen") or x.get("last_heard"),
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
        if e is None:  # only known from /neighbors -> location from the node list
            kn = node(q)
            if kn:
                e = L[q] = {"pk": q, "name": x.get("name") or kn["name"], "role": x.get("role") or kn.get("role"),
                            "lat": kn["lat"], "lon": kn["lon"], "we_hear": 0, "they_hear": 0, "bidir": False,
                            "distance_km": x.get("distance_km"), "snr": None, "snr_n": 0}
        if e is not None:
            e["count"] = int(x.get("count") or 0)
            if x.get("avg_snr") is not None:
                e["snr_any"] = round(float(x["avg_snr"]), 2)  # average over all observers – not the link
        if x.get("avg_snr") is not None and q in [str(v).lower() for v in x.get("observers") or []]:
            meas[q] = (float(x["avg_snr"]), int(x.get("count") or 0), x)  # newer / more complete than reach
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
    # path-derived SNR is uncertain (observers only hear the last hop, short prefixes are ambiguous):
    # shown as a hint only. Only the neighbour tables the nodes report themselves count as measurements.
    for e in L.values():
        e.setdefault("count", 0)
        if e.get("snr") is not None and e.get("snr_any") is None:
            e["snr_any"] = e["snr"]
        e["snr"], e["snr_n"], e["snr_src"] = None, 0, None
    try:
        rep = (reports or observer_reports())
    except Exception:  # noqa: BLE001
        rep = {"by_neighbor": {}}
    for x in rep.get("by_neighbor", {}).get(pk, []):  # observer x heard our node directly: pk -> x
        e = L.get(x["observer"])
        if e is None:
            kn = node(x["observer"])
            if not kn:
                continue
            e = L[x["observer"]] = {"pk": x["observer"], "name": x.get("name") or kn["name"], "role": kn.get("role"),
                                    "lat": kn["lat"], "lon": kn["lon"], "we_hear": 0, "they_hear": 0, "bidir": False,
                                    "distance_km": None, "count": 0, "snr_any": None}
        e["snr"], e["snr_n"], e["snr_src"] = round(x["snr"], 2), 1, "neighbor_report"
        e["heard_at"] = x.get("heard_at")   # #26: when the partner last heard this node directly
    mine = {}
    for q, lst in rep.get("by_neighbor", {}).items():  # unser Knoten meldet selbst: q -> pk
        for x in lst:
            if x["observer"] == pk:
                mine[q] = x
    for q, v in mine.items():
        if q in L:
            L[q]["snr_rev"] = round(v["snr"], 2)
            L[q]["heard_rev_at"] = v.get("heard_at")
    return {"v": LINKS_VERSION, "pk": pk, "name": me.get("name"), "lat": me.get("lat"), "lon": me.get("lon"),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "window_days": (reach.get("window") or {}).get("days"), "links": list(L.values())}


# ---------------------------------------------------------------- own values per repeater (without a station)
def overrides():
    try:
        with open(OVERRIDES_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def set_override(pk, data):
    """Height/gain/cable of a repeater that is not a station of the project (used by the comparison)."""
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


# ---------------------------------------------------------------- Metadaten eines Knotens
NODE_TTL_S = 3600
RF_TTL_S = 6 * 3600
_rf_cache = {"t": 0, "data": None}
META_KEYS = ("name", "role", "last_seen", "first_seen", "advert_count", "battery_mv", "temperature_c",
             "relay_count_1h", "relay_count_24h", "flood_advert_count_7d", "usefulness_score", "traffic_share_score",
             "bridge_score", "declared_regions")


def _rf_params(c):
    if time.time() - _rf_cache["t"] < RF_TTL_S:
        return _rf_cache["data"]
    try:
        d = _get(c, "/api/rf-params")
    except httpx.HTTPError:
        d = None
    _rf_cache.update(t=time.time(), data=d)
    return d


def _rf_for(d, pk):
    """The response format of /api/rf-params is not specified – search defensively."""
    if not isinstance(d, dict) or d.get("enabled") is False:
        return None
    for k in ("nodes", "params", "rf", "data"):
        v = d.get(k)
        if isinstance(v, dict) and isinstance(v.get(pk), dict):
            return v[pk]
    v = d.get(pk)
    return v if isinstance(v, dict) else None


def node_meta(pk):
    """What CoreScope knows about a node. MeshCore does not announce height, antenna and transmit power."""
    if not PK.match(pk):
        raise ValueError("ungültiger Schlüssel")
    p = os.path.join(DIR, f"node_{pk}.json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < NODE_TTL_S:
        with open(p) as f:
            return {**json.load(f), "override": overrides().get(pk)}
    if not enabled():
        raise ValueError("RFP_CORESCOPE_URL ist nicht gesetzt")
    with _client() as c:
        d = _get(c, f"/api/nodes/{pk}")
        rf = _rf_for(_rf_params(c), pk)
    n = d.get("node") if isinstance(d, dict) and isinstance(d.get("node"), dict) else d
    out = {"pk": pk, "node": {k: n.get(k) for k in META_KEYS if n.get(k) is not None}, "rf": rf,
           "adverts": len(d.get("recentAdverts") or []) if isinstance(d, dict) else 0}
    with open(p, "w") as f:
        json.dump(out, f)
    return {**out, "override": overrides().get(pk)}



# ---------------------------------------------------------------- direct measurements: the observers' neighbour tables
REPORTS_FILE = os.path.join(DIR, "observer_reports.json")
REPORTS_TTL_S = 6 * 3600


def _items(d, *keys):
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in keys:
            if isinstance(d.get(k), list):
                return d[k]
    return []


def observer_reports(refresh=False):
    """Neighbour tables the observers report themselves (/api/observers/{id} -> neighbor_report): SNR of directly
    heard adverts, so certainly direct links. Result: {neighbour_pk: [{observer, name, snr, heard_secs_ago}]}."""
    if not refresh and os.path.exists(REPORTS_FILE) and time.time() - os.path.getmtime(REPORTS_FILE) < REPORTS_TTL_S:
        with open(REPORTS_FILE) as f:
            return json.load(f)
    if not enabled():
        return {"by_neighbor": {}, "observers": {}}
    idx, obs = {}, {}
    with _client() as c:
        lst = _items(_get(c, "/api/observers"), "observers", "data", "items")
        for o in lst:
            if not isinstance(o, dict) or not (o.get("neighbors_report_at") or o.get("neighbors_count")):
                continue
            oid = str(o.get("id") or o.get("pubkey") or o.get("public_key") or "")
            if not oid:
                continue
            try:
                d = _get(c, f"/api/observers/{oid}")
            except httpx.HTTPError:
                continue
            d = d.get("observer", d) if isinstance(d, dict) else {}
            rep = d.get("neighbor_report") or {}
            opk = str(d.get("pubkey") or d.get("public_key") or oid).lower()
            obs[opk] = {"name": d.get("name") or o.get("name"), "reported_at": rep.get("reported_at") or rep.get("received_at")}
            for nb in rep.get("neighbors") or []:
                q = str(nb.get("pubkey") or "").lower()
                if PK.match(q) and nb.get("snr") is not None:
                    ago = nb.get("heard_secs_ago")
                    idx.setdefault(q, []).append({"observer": opk, "name": obs[opk]["name"], "snr": float(nb["snr"]),
                                                  "heard_secs_ago": ago,
                                                  # absolute time, so the age stays right while the cache is reused
                                                  "heard_at": int(time.time() - float(ago)) if isinstance(ago, (int, float)) else None})
    out = {"by_neighbor": idx, "observers": obs, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(REPORTS_FILE, "w") as f:
        json.dump(out, f)
    return out
