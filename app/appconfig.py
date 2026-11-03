"""Einstellungen der Instanz, die der Admin in der Oberfläche ändert (data/appconfig.json).

Umgebungsvariablen liefern nur die Startwerte; was im Dialog gespeichert wird, gilt danach.
"""
from __future__ import annotations

import json
import os
import re
import threading

from .terrain import DATA_DIR

PATH = os.path.join(DATA_DIR, "appconfig.json")
_lock = threading.Lock()

DEFAULTS = {
    # zweiter Zugang: lesen + eigene Stationen anlegen
    "user_enabled": False,
    "user_name": os.environ.get("RFP_USER_NAME", "user"),
    "user_password": os.environ.get("RFP_USER_PASSWORD", ""),
    # Impressum: generated (aus den Angaben unten) | link (externe Seite) | off
    "imprint_mode": os.environ.get("RFP_IMPRINT_MODE", "generated"),
    "imprint_url": os.environ.get("RFP_IMPRINT_URL", ""),
    "imprint_name": os.environ.get("RFP_IMPRINT_NAME", ""),
    "imprint_address": os.environ.get("RFP_IMPRINT_ADDRESS", ""),
    "imprint_email": os.environ.get("RFP_IMPRINT_EMAIL", ""),
    "imprint_extra": os.environ.get("RFP_IMPRINT_EXTRA", ""),
    # Datenschutz: generated | link
    "privacy_mode": os.environ.get("RFP_PRIVACY_MODE", "generated"),
    "privacy_url": os.environ.get("RFP_PRIVACY_URL", ""),
    "privacy_extra": os.environ.get("RFP_PRIVACY_EXTRA", ""),
    # Zugehörigkeit: „Diese Seite gehört zu …“
    "parent_name": os.environ.get("RFP_PARENT_NAME", ""),
    "parent_url": os.environ.get("RFP_PARENT_URL", ""),
    "show_github": os.environ.get("RFP_SHOW_GITHUB", "true").strip().lower() not in ("0", "false", "no"),
    # Messungen: Schlüssel für die MeshMapper-Weiterleitung, Sichtbarkeit für Besucher
    "mm_api_key": os.environ.get("RFP_MEASURE_KEY", ""),
    "measurements_public": False,
}
if DEFAULTS["user_password"]:
    DEFAULTS["user_enabled"] = True

MODES = {"imprint_mode": ("generated", "link", "off"), "privacy_mode": ("generated", "link")}
GITHUB_URL = "https://github.com/RideMyByte/TerraRF"


def get():
    cfg = dict(DEFAULTS)
    try:
        with open(PATH) as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return cfg


def update(data: dict):
    cur = get()
    for k, v in data.items():
        if k not in DEFAULTS:
            continue
        if isinstance(DEFAULTS[k], bool):
            v = bool(v)
        else:
            v = str(v or "").strip()[:2000]
        if k in MODES and v not in MODES[k]:
            raise ValueError(f"{k}: erlaubt sind {', '.join(MODES[k])}")
        if k == "mm_api_key" and v and (len(v) < 16 or not re.fullmatch(r"[A-Za-z0-9_\-]+", v)):
            raise ValueError("Messungs-Schlüssel: mindestens 16 Zeichen, nur Buchstaben, Ziffern, _ und -")
        if k.endswith("_url") and v and not v.startswith(("https://", "http://")):
            raise ValueError(f"{k}: muss mit https:// oder http:// beginnen")
        cur[k] = v
    if cur["user_enabled"] and len(cur["user_password"]) < 6:
        raise ValueError("Benutzer-Passwort: mindestens 6 Zeichen")
    with _lock:
        tmp = PATH + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # enthält ein Klartext-Passwort
        with os.fdopen(fd, "w") as f:
            json.dump(cur, f, indent=1, ensure_ascii=False)
        os.replace(tmp, PATH)
    return cur


def public():
    """Was jeder Besucher sehen darf (Fußzeile)."""
    c = get()
    return {
        "imprint": None if c["imprint_mode"] == "off" else (c["imprint_url"] if c["imprint_mode"] == "link" and c["imprint_url"] else "/impressum"),
        "privacy": c["privacy_url"] if c["privacy_mode"] == "link" and c["privacy_url"] else "/datenschutz",
        "parent": {"name": c["parent_name"], "url": c["parent_url"]} if c["parent_name"] or c["parent_url"] else None,
        "github": GITHUB_URL if c["show_github"] else None,
        "user_enabled": bool(c["user_enabled"]),
        "measurements_public": bool(c["measurements_public"]),
    }
