"""Accounts that belong to one project (“collaborators”): the admin creates them to share a project with someone
who may work on exactly that project – change it, compute, enter measurements – and nothing else.

Stored in data/accounts.json (mode 0600). Passwords are kept only as PBKDF2-HMAC-SHA256 hashes with a random
salt (200 000 iterations); logins are additionally rate limited per IP (auth.check_login).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

from ..paths import DATA_DIR

PATH = os.path.join(DATA_DIR, "accounts.json")
ITERATIONS = 200_000
NAME = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
_lock = threading.Lock()


def _read():
    try:
        with open(PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write(data):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, PATH)


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()


def for_project(project_id):
    """Accounts of one project, without secrets."""
    return [{"name": n, "created": a.get("created"), "note": a.get("note", "")}
            for n, a in sorted(_read().items()) if a.get("project") == project_id]


def create(name, project_id, password=None, note="", reserved=()):
    """New account for a project. Returns the password (generated if none is given) – it is shown only once."""
    name = (name or "").strip()
    if not NAME.match(name):
        raise ValueError("Name: 3–32 Zeichen, Buchstaben, Ziffern, . _ -")
    if name.lower() in {r.lower() for r in reserved if r}:
        raise ValueError("Dieser Name ist schon vergeben")
    password = password or secrets.token_urlsafe(9)
    if len(password) < 8:
        raise ValueError("Passwort: mindestens 8 Zeichen")
    with _lock:
        data = _read()
        if any(n.lower() == name.lower() for n in data):
            raise ValueError("Dieser Name ist schon vergeben")
        salt = secrets.token_hex(16)
        data[name] = {"project": project_id, "salt": salt, "hash": _hash(password, salt),
                      "created": int(time.time()), "note": str(note or "")[:200]}
        _write(data)
    return password


def set_password(name, password=None):
    password = password or secrets.token_urlsafe(9)
    if len(password) < 8:
        raise ValueError("Passwort: mindestens 8 Zeichen")
    with _lock:
        data = _read()
        if name not in data:
            raise KeyError(name)
        data[name]["salt"] = secrets.token_hex(16)
        data[name]["hash"] = _hash(password, data[name]["salt"])
        _write(data)
    return password


def delete(name):
    with _lock:
        data = _read()
        if data.pop(name, None) is not None:
            _write(data)


def delete_project(project_id):
    """Remove every account of a deleted project."""
    with _lock:
        data = _read()
        kept = {n: a for n, a in data.items() if a.get("project") != project_id}
        if len(kept) != len(data):
            _write(kept)


def check(name, password):
    """Project id if name and password match, else None."""
    a = _read().get(name)
    if not a:
        _hash(password or "x", "00" * 16)       # same time with or without account
        return None
    return a["project"] if hmac.compare_digest(_hash(password or "", a["salt"]), a["hash"]) else None


def project_of(name):
    a = _read().get(name)
    return a.get("project") if a else None


def tag(name):
    """Short fingerprint of the stored hash: a new password or a deleted account ends old sessions."""
    a = _read().get(name)
    return a["hash"][:10] if a else None
