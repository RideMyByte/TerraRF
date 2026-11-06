"""MeshMapper keys per project (#53): measurements sent with a project's key belong to that project.
The key in Administration → Data sources stays the admin's own key (measurements without project).

Stored in data/ingest_keys.json (mode 0600) as {project_id: key}.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import threading

from ..paths import DATA_DIR

PATH = os.path.join(DATA_DIR, "ingest_keys.json")
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


def get(project_id):
    return _read().get(project_id)


def generate(project_id):
    """New key for a project (an old one stops working)."""
    key = secrets.token_urlsafe(24)
    with _lock:
        data = _read()
        data[project_id] = key
        _write(data)
    return key


def delete(project_id):
    with _lock:
        data = _read()
        if data.pop(project_id, None) is not None:
            _write(data)


def project_for_key(key):
    """Project id of a key, or None. Compares in constant time against every stored key."""
    found = None
    for project_id, stored in _read().items():
        if key and hmac.compare_digest(key.encode(), stored.encode()):
            found = project_id
    return found
