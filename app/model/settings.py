"""Model and computation settings – one global set, maintained by the admin (issue #30).

Since 0.5 every project computes with the same settings: the admin tunes them under
*Administration → Model*, and the change applies to all projects (cached results are keyed by
the settings, so nothing stale is served). Settings stored inside old project files are ignored.

``SPEC`` describes every setting for the admin page: type, unit, limits, choices and the source
(ITU-R recommendation or reasoning) behind the default. Labels and help texts live in the
frontend locale files under ``settings.<key>``.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading

from ..paths import DATA_DIR

PATH = os.path.join(DATA_DIR, "settings.json")
_lock = threading.Lock()

# name: (default, type, unit, min, max, choices, group, reference)
SPEC = {
    # elevation data
    "ground_source": ("nrw_dgm", "source", None, None, None, None, "data", "Terrain model (DGM), bare ground"),
    "surface_source": ("nrw_dom", "source", None, None, None, None, "data", "Surface model (DOM): ground + buildings + trees"),
    "fallback_source": ("copernicus", "source", None, None, None, None, "data",
                        "Fills holes outside NRW (Copernicus GLO-30)"),
    # computation
    "res_m": (10.0, "number", "m", 2, 100, None, "compute", "Grid cell size of the coverage raster"),
    "k_factor": (1.3333, "number", None, 0.5, 4, None, "compute", "Effective earth radius factor, ITU-R P.453 (standard 4/3)"),
    "max_profile_points": (500, "int", None, 100, 2000, None, "compute", "Samples per ray"),
    "max_rays": (7200, "int", None, 360, 20000, None, "compute", "Upper limit of rays per station"),
    "near_m": (150.0, "number", "m", 0, 500, None, "compute",
               "Radius around each station that is sampled at near_res_m (buildings right at the antenna)"),
    "near_res_m": (5.0, "number", "m", 1, 20, None, "compute", "Resolution of the near field (native NRW tile size)"),
    # propagation model
    "diffraction_model": ("deygout", "choice", None, None, None, ("deygout", "bullington"), "model",
                          "ITU-R P.526: Deygout (3 edges) or Bullington"),
    "veg_mode": ("auto", "choice", None, None, None, ("auto", "custom", "off"), "model",
                 "ITU-R P.833-10 excess attenuation where the direct ray runs through objects"),
    "veg_gamma_db_m": (None, "number", "dB/m", 0, 2, None, "model", "Specific attenuation for veg_mode = custom"),
    "veg_max_db": (None, "number", "dB", 0, 60, None, "model", "Maximum attenuation for veg_mode = custom"),
    "building_mode": ("vegetation", "choice", None, None, None, ("vegetation", "diffraction"), "model",
                      "Known buildings: like vegetation (P.833) or only by diffraction over the roof (P.526)"),
    "clutter_mode": ("auto", "choice", None, None, None, ("auto", "off", "suburban", "urban", "dense", "trees"), "model",
                     "ITU-R P.2108 §3.1 terminal clutter at the receiver"),
    "clutter_floor": ("none", "choice", None, None, None, ("none", "suburban", "urban", "dense", "trees"), "model",
                      "Minimum clutter class when clutter_mode = auto"),
    "clutter_exclude_m": (60.0, "number", "m", 0, 500, None, "model",
                          "Receiver surroundings handled by P.2108 instead of diffraction (at most half the path)"),
    "two_ray": (True, "bool", None, None, None, None, "model", "Ground reflection (two-ray) on line-of-sight paths"),
    # default receiver (handheld) for the coverage map
    "rx_height_m": (1.5, "number", "m", 0, 300, None, "receiver", "Receiver antenna height above ground"),
    "rx_gain_dbi": (0.0, "number", "dBi", -10, 40, None, "receiver", "Receiver antenna gain"),
    "rx_cable_db": (0.0, "number", "dB", 0, 30, None, "receiver", "Receiver cable loss"),
    "rx_extra_loss_db": (0.0, "number", "dB", 0, 40, None, "receiver", "Body / building loss at the receiver"),
    "noise_rise_db": (0.0, "number", "dB", 0, 40, None, "receiver", "Noise above thermal (local interference)"),
    "min_margin_db": (10.0, "number", "dB", -10, 40, None, "receiver", "Fade/location margin counted as covered"),
}
DEFAULTS = {k: v[0] for k, v in SPEC.items()}


def spec():
    """SPEC as JSON-friendly records for the admin page."""
    out = []
    for k, (default, typ, unit, lo, hi, choices, group, ref) in SPEC.items():
        out.append({"key": k, "default": default, "type": typ, "unit": unit, "min": lo, "max": hi,
                    "choices": list(choices) if choices else None, "group": group, "reference": ref})
    return out


_cached = None


def stored():
    """Values the admin changed (only those differ from DEFAULTS)."""
    global _cached
    if _cached is None:
        try:
            with open(PATH) as f:
                _cached = {k: v for k, v in json.load(f).items() if k in SPEC}
        except (OSError, ValueError):
            _cached = {}
    return dict(_cached)


def effective():
    """The settings every computation uses: defaults overlaid with the admin's values."""
    s = dict(DEFAULTS)
    s.update(stored())
    if _override is not None:
        s.update(_override)
    return s


def _validate(key, value):
    default, typ, _unit, lo, hi, choices, _g, _r = SPEC[key]
    if value is None or value == "":
        if typ in ("number",) and default is None:
            return None
        return default
    if typ == "bool":
        return bool(value)
    if typ in ("number", "int"):
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key}: not a number") from None
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            raise ValueError(f"{key}: must be between {lo} and {hi}")
        return int(v) if typ == "int" else v
    if typ == "choice":
        if value not in choices:
            raise ValueError(f"{key}: one of {', '.join(choices)}")
        return value
    v = str(value).strip()
    if not v or len(v) > 64:
        raise ValueError(f"{key}: invalid")
    return v


def save(values):
    """Store the admin's values. Keys set to their default are dropped, so later default changes still apply."""
    global _cached
    clean = {}
    for k, v in (values or {}).items():
        if k not in SPEC:
            continue
        v = _validate(k, v)
        if v != SPEC[k][0]:
            clean[k] = v
    with _lock:
        tmp = PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(clean, f, indent=1)
        os.replace(tmp, PATH)
        _cached = clean
    return effective()


_override = None


@contextlib.contextmanager
def temporary(values):
    """Compute with other settings for a moment (tests, what-if comparisons). Not thread-safe by design:
    only for single-threaded use."""
    global _override
    before = _override
    _override = {**(before or {}), **values}
    try:
        yield
    finally:
        _override = before
