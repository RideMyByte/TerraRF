"""Projects on disk: one JSON file per project in ``data/projects/``.

A project is a name, a list of stations and a ``public`` flag. Model settings are global since 0.5, so a
``settings`` block in an older file is dropped when it is loaded (``migrate``).
"""
from __future__ import annotations

import json
import os
import re
import time

from ..paths import DATA_DIR

PROJECT_DIR = os.path.join(DATA_DIR, "projects")
LAST_FILE = os.path.join(DATA_DIR, "last_project")
os.makedirs(PROJECT_DIR, exist_ok=True)

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class NotFound(KeyError):
    pass


def valid_id(project_id):
    return bool(_ID.match(project_id or ""))


def path(project_id):
    if not valid_id(project_id):
        raise ValueError("invalid project id")
    return os.path.join(PROJECT_DIR, project_id + ".json")


def migrate(project):
    """Bring an older project up to date (in memory)."""
    project.pop("settings", None)          # 0.5: model settings are global (#30)
    for st in project.get("stations") or []:
        ant = st.get("antenna") or {}
        if ant.get("preset") and not ant.get("catalog_id"):
            # 0.5: fixed antenna presets became catalog entries with the same values
            ant["catalog_id"] = {"omni2": "dipole", "omni5": "collinear-5.8", "omni8": "omni-8", "sector90": "sector-90",
                                 "sector120": "sector-120", "yagi": "yagi-9", "panel": "panel-14",
                                 "dish": "dish-24"}.get(ant["preset"])
            ant.pop("preset", None)
    return project


def load(project_id):
    p = path(project_id)
    if not os.path.exists(p):
        raise NotFound("project not found")
    with open(p) as f:
        return migrate(json.load(f))


def exists(project_id):
    return valid_id(project_id) and os.path.exists(path(project_id))


def list_all(role):
    """Projects the role may see (see ``can_view``), newest first."""
    out = []
    for fn in sorted(os.listdir(PROJECT_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(PROJECT_DIR, fn)) as f:
                p = json.load(f)
        except (OSError, ValueError):
            continue
        if not can_view(role, p):
            continue
        out.append({"id": p.get("id"), "name": p.get("name"), "stations": len(p.get("stations") or []),
                    "updated": p.get("updated"), "public": bool(p.get("public")), "owner": owner(p),
                    "editable": can_edit(role, p)})
    out.sort(key=lambda p: p.get("updated") or "", reverse=True)
    return out


def save(project_id, project):
    project = migrate(dict(project))
    project["id"] = project_id
    project["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    p = path(project_id)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(project, f, indent=1, ensure_ascii=False)
    os.replace(tmp, p)
    return project


def delete(project_id):
    p = path(project_id)
    if os.path.exists(p):
        os.remove(p)


def owner(project):
    """Who created a project: "admin" or "user". Projects from before 0.5.1 belong to the admin."""
    return "user" if project.get("owner") == "user" else "admin"


def can_view(role, project):
    """#32: admin sees everything, the user their own projects and public ones, visitors public ones."""
    return role == "admin" or bool(project.get("public")) or (role == "user" and owner(project) == "user")


def can_edit(role, project):
    """#32: a project – public or not – is changed only by the one who created it, and by the admin."""
    return role == "admin" or (role == "user" and owner(project) == "user")


def remember_last(project_id):
    """The admin's last opened project is reopened at the next start (e.g. as home-screen app)."""
    try:
        with open(LAST_FILE, "w") as f:
            f.write(project_id)
    except OSError:
        pass


def last():
    try:
        with open(LAST_FILE) as f:
            pid = f.read().strip()
        return pid if exists(pid) else None
    except OSError:
        return None
