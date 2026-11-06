"""Catalog of antennas, receiving devices and measurement placements (issues #28, #29).

Only the admin edits the catalog; everybody can read it. Stations pick an antenna from it, and a
measurement series picks a device and a placement, which together give the receiver height, antenna
gain and extra loss used when measurements are compared with the model.

Stored in ``data/catalog.json``. The seed entries below are written once, on first start; after that
the file belongs to the admin (deleted seeds stay deleted).

Sources of the seed values are given per entry (``source``): manufacturer data sheets or the
Mesh Rheinland antenna list (community experience, https://www.meshrheinland.de/hardware/antennen).
"""
from __future__ import annotations

import json
import os
import re
import threading

from ..paths import DATA_DIR

PATH = os.path.join(DATA_DIR, "catalog.json")
KINDS = ("antennas", "devices", "placements")
_lock = threading.Lock()
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")

MR = "Mesh Rheinland antenna list"
SEED = {
    "antennas": [
        # generic types (were fixed presets before 0.5)
        {"id": "dipole", "name": "Dipole / whip 2.15 dBi", "type": "omni", "gain_dbi": 2.15, "source": "λ/2 dipole, textbook value"},
        {"id": "collinear-5.8", "name": "Collinear 5.8 dBi", "type": "omni", "gain_dbi": 5.8, "source": "generic"},
        {"id": "omni-8", "name": "Omni 8 dBi", "type": "omni", "gain_dbi": 8.0, "source": "generic"},
        {"id": "sector-90", "name": "Sector 90° / 14 dBi", "type": "directional", "gain_dbi": 14, "hbw_deg": 90, "vbw_deg": 8,
         "fb_db": 25, "source": "generic"},
        {"id": "sector-120", "name": "Sector 120° / 13 dBi", "type": "directional", "gain_dbi": 13, "hbw_deg": 120, "vbw_deg": 8,
         "fb_db": 22, "source": "generic"},
        {"id": "yagi-9", "name": "Yagi 9 dBi", "type": "directional", "gain_dbi": 9, "hbw_deg": 50, "vbw_deg": 50, "fb_db": 18,
         "source": "generic"},
        {"id": "panel-14", "name": "Panel 14 dBi", "type": "directional", "gain_dbi": 14, "hbw_deg": 30, "vbw_deg": 30, "fb_db": 25,
         "source": "generic"},
        {"id": "dish-24", "name": "Dish 24 dBi", "type": "directional", "gain_dbi": 24, "hbw_deg": 8, "vbw_deg": 8, "fb_db": 30,
         "source": "generic"},
        # community favourites (issue #28)
        {"id": "interline-horizon-city-868", "name": "Interline HORIZON HELIUM City 868", "maker": "Interline", "type": "omni",
         "gain_dbi": 6.0, "vbw_deg": 30, "connector": "N female", "freq_min_mhz": 860, "freq_max_mhz": 880,
         "note": "built-in band-pass filter", "source": "manufacturer data (IH-G06-F8688-V)",
         "url": "https://interline.pl/antennas/HORIZON-HELIUM-CITY-868"},
        {"id": "interline-horizon-868", "name": "Interline HORIZON 868 HELIUM 8 dBi", "maker": "Interline", "type": "omni",
         "gain_dbi": 8.0, "vbw_deg": 15, "tilt_deg": 10, "connector": "N female", "freq_min_mhz": 860, "freq_max_mhz": 880,
         "note": "10° electrical downtilt, built-in band-pass filter", "source": "manufacturer data (IH-G08-F8688-V2)",
         "url": "https://interline.pl/antennas/HORIZON-868-HELIUM"},
        {"id": "interline-panel-868", "name": "Interline Panel 868 8 dBi", "maker": "Interline", "type": "directional",
         "gain_dbi": 8.0, "hbw_deg": 70, "connector": "N female", "freq_min_mhz": 860, "freq_max_mhz": 880, "fb_db": 20,
         "source": MR + " (opening 70°)"},
        {"id": "alfa-aoa-868-5acm", "name": "ALFA Network AOA-868-5ACM", "maker": "ALFA Network", "type": "omni", "gain_dbi": 5.0,
         "connector": "N male", "freq_min_mhz": 863, "freq_max_mhz": 870, "source": "manufacturer data",
         "url": "https://www.alfa.com.tw/products/aoa-868-5acm"},
        {"id": "gizont-whip", "name": "Gizont whip 16 cm", "maker": "Gizont", "type": "omni", "gain_dbi": 3.5, "connector": "SMA",
         "note": "portable; variants differ in tuning", "source": MR},
        {"id": "eightwood-868", "name": "Eightwood 868 MHz", "maker": "Eightwood", "type": "omni", "gain_dbi": 3.5,
         "connector": "RP-SMA", "note": "portable", "source": MR},
        {"id": "mikrotik-868-omni", "name": "MikroTik 868 Omni", "maker": "MikroTik", "type": "omni", "gain_dbi": 6.5,
         "connector": "SMA, 1 m cable", "freq_min_mhz": 862, "freq_max_mhz": 876, "source": MR},
        {"id": "ziisor-8", "name": "Ziisor 8 dBi", "maker": "Ziisor", "type": "omni", "gain_dbi": 8.0, "connector": "N male",
         "note": "buy the 868 MHz version", "source": MR},
        {"id": "sirio-gp-868c", "name": "Sirio GP 868C", "maker": "Sirio", "type": "omni", "gain_dbi": 5.14, "connector": "N female",
         "freq_min_mhz": 835, "freq_max_mhz": 900, "note": "ground-plane base antenna", "source": MR},
    ],
    "devices": [
        {"id": "t1000-e", "name": "SenseCAP T1000-E", "antenna_gain_dbi": 1.0, "note": "built-in antenna",
         "source": "assumption from issue #29 – no manufacturer gain found"},
        {"id": "handheld-stub", "name": "Handheld with stock stub antenna", "antenna_gain_dbi": 0.0,
         "source": "assumption; stock stubs are often poorly tuned (Mesh Rheinland)"},
        {"id": "handheld-gizont", "name": "Handheld with Gizont whip", "antenna_id": "gizont-whip", "source": MR},
    ],
    "placements": [
        {"id": "car-roof", "name": "Car roof", "height_m": 1.7, "extra_loss_db": 0.0, "gain_bonus_db": 0.0,
         "note": "the roof acts as ground plane; the model can only express that as extra gain", "source": "issue #29"},
        {"id": "jacket-pocket", "name": "Jacket pocket", "height_m": 1.25, "extra_loss_db": 4.0, "gain_bonus_db": 0.0,
         "note": "body loss: rule of thumb 3–6 dB for a handheld worn on the body, not a standardised value",
         "source": "issue #29 (height 1–1.5 m)"},
        {"id": "bicycle", "name": "Bicycle, outside", "height_m": 1.0, "extra_loss_db": 0.0, "gain_bonus_db": 0.0,
         "source": "issue #29"},
        {"id": "handheld-raised", "name": "In the hand, raised", "height_m": 1.8, "extra_loss_db": 0.0, "gain_bonus_db": 0.0,
         "source": "typical"},
    ],
}

# allowed fields per kind: name -> type
FIELDS = {
    "antennas": {"name": str, "maker": str, "type": ("omni", "directional"), "gain_dbi": float, "hbw_deg": float,
                 "vbw_deg": float, "tilt_deg": float, "fb_db": float, "freq_min_mhz": float, "freq_max_mhz": float,
                 "connector": str, "note": str, "url": str, "source": str},
    "devices": {"name": str, "antenna_gain_dbi": float, "antenna_id": str, "cable_db": float, "note": str, "source": str},
    "placements": {"name": str, "height_m": float, "extra_loss_db": float, "gain_bonus_db": float, "note": str, "source": str},
}

_data = None


def _load():
    global _data
    if _data is None:
        try:
            with open(PATH) as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = json.loads(json.dumps(SEED))
            _write(d)
        for k in KINDS:
            d.setdefault(k, [])
        _data = d
    return _data


def _write(d):
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(tmp, PATH)


def all_items():
    with _lock:
        return json.loads(json.dumps(_load()))


def get(kind, item_id):
    if not item_id:
        return None
    return next((x for x in all_items().get(kind, []) if x["id"] == item_id), None)


def _clean(kind, item):
    if kind not in KINDS:
        raise ValueError("unknown catalog")
    out = {}
    for k, typ in FIELDS[kind].items():
        v = item.get(k)
        if v is None or v == "":
            continue
        if typ is float:
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k}: not a number") from None
            if not -100 <= v <= 10000:
                raise ValueError(f"{k}: out of range")
        elif isinstance(typ, tuple):
            if v not in typ:
                raise ValueError(f"{k}: one of {', '.join(typ)}")
        else:
            v = str(v).strip()[:300]
        out[k] = v
    if not out.get("name"):
        raise ValueError("name is required")
    if kind == "antennas":
        out.setdefault("type", "omni")
        out.setdefault("gain_dbi", 0.0)
    return out


def put(kind, item_id, item):
    """Create or replace one entry."""
    if not _ID.match(item_id or ""):
        raise ValueError("id: lowercase letters, digits, - and _")
    clean = _clean(kind, item)
    clean["id"] = item_id
    with _lock:
        d = _load()
        lst = d[kind]
        idx = next((i for i, x in enumerate(lst) if x["id"] == item_id), None)
        if idx is None:
            lst.append(clean)
        else:
            lst[idx] = clean
        _write(d)
    return clean


def delete(kind, item_id):
    if kind not in KINDS:
        raise ValueError("unknown catalog")
    with _lock:
        d = _load()
        before = len(d[kind])
        d[kind] = [x for x in d[kind] if x["id"] != item_id]
        _write(d)
        return before - len(d[kind])


def receiver_for(device_id=None, placement_id=None):
    """Receiver values a device + placement imply: height (m), antenna gain (dBi) and extra loss (dB).
    Missing parts are None, so the caller falls back to its own defaults."""
    dev = get("devices", device_id) or {}
    plc = get("placements", placement_id) or {}
    gain = dev.get("antenna_gain_dbi")
    if gain is None and dev.get("antenna_id"):
        ant = get("antennas", dev["antenna_id"])
        gain = ant.get("gain_dbi") if ant else None
    if gain is not None and plc.get("gain_bonus_db"):
        gain += plc["gain_bonus_db"]
    loss = None
    if dev.get("cable_db") is not None or plc.get("extra_loss_db") is not None:
        loss = (dev.get("cable_db") or 0.0) + (plc.get("extra_loss_db") or 0.0)
    return {"height_m": plc.get("height_m"), "gain_dbi": gain, "loss_db": loss}
