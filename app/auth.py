"""Logins: ein Admin (alles) und optional ein Benutzer „user“ (lesen + eigene Stationen anlegen).
Alle anderen sehen öffentliche Projekte nur lesend. Der Benutzer wird in den Einstellungen gepflegt.

Config (environment):
  RFP_ADMIN_USER       default "admin"
  RFP_ADMIN_PASSWORD   if empty, a random password is generated once and stored in
                       <data>/admin_password.txt (also printed to the log on first start)
  RFP_PUBLIC_READ      "true" (default): visitors may view public projects without login;
                       "false": everything requires login
Sessions are HMAC-signed cookies (secret in <data>/secret.key), no server-side state.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time

from fastapi import HTTPException, Request

from .terrain import DATA_DIR

log = logging.getLogger("terrarf.auth")

COOKIE = "terrarf_session"
SESSION_S = int(os.environ.get("RFP_SESSION_DAYS", "30")) * 86400
ADMIN_USER = os.environ.get("RFP_ADMIN_USER", "admin") or "admin"
PUBLIC_READ = os.environ.get("RFP_PUBLIC_READ", "true").strip().lower() not in ("0", "false", "no", "nein")

_secret: bytes | None = None
_password: str | None = None
_fail: dict[str, list[float]] = {}
_lock = threading.Lock()


def _read_or_create(path, factory):
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    data = factory()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return data


def init():
    global _secret, _password
    _secret = _read_or_create(os.path.join(DATA_DIR, "secret.key"), lambda: secrets.token_bytes(32))
    env_pw = os.environ.get("RFP_ADMIN_PASSWORD", "")
    if env_pw:
        _password = env_pw
    else:
        path = os.path.join(DATA_DIR, "admin_password.txt")
        new = not os.path.exists(path)
        _password = _read_or_create(path, lambda: secrets.token_urlsafe(12).encode()).decode().strip()
        if new:
            log.warning("Kein RFP_ADMIN_PASSWORD gesetzt – erzeugtes Admin-Passwort für '%s': %s "
                        "(gespeichert in %s)", ADMIN_USER, _password, path)
        else:
            log.info("Admin-Passwort aus %s (RFP_ADMIN_PASSWORD setzen, um es zu ändern)", path)


def _sign(payload: bytes) -> str:
    return hmac.new(_secret, payload, hashlib.sha256).hexdigest()


def _pwtag(role):
    """Kurzer Fingerabdruck des aktuellen Passworts: ändert der Admin es, werden alte Sitzungen ungültig."""
    from . import appconfig
    pw = _password if role == "admin" else appconfig.get()["user_password"]
    return hashlib.sha256((pw or "").encode()).hexdigest()[:10]


def make_token(user: str, role: str = "admin") -> str:
    payload = f"{user}|{int(time.time()) + SESSION_S}|{role}|{_pwtag(role)}".encode()
    return base64.urlsafe_b64encode(payload).decode() + "." + _sign(payload)


def verify(token: str | None) -> str | None:
    if not token or "." not in token or _secret is None:
        return None
    b64, sig = token.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(b64.encode())
    except Exception:  # noqa: BLE001
        return None
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    parts = payload.decode(errors="replace").split("|")
    if len(parts) == 2:          # Sitzung aus älteren Versionen: nur Admin
        parts += ["admin", _pwtag("admin")]
    if len(parts) != 4:
        return None
    user, exp, role, tag = parts
    try:
        if int(exp) < time.time():
            return None
    except ValueError:
        return None
    if not hmac.compare_digest(tag, _pwtag(role)):
        return None
    if role == "admin" and user == ADMIN_USER:
        return "admin"
    if role == "user":
        from . import appconfig
        c = appconfig.get()
        if c["user_enabled"] and c["user_password"] and user == c["user_name"]:
            return "user"
    return None


def role(request: Request) -> str | None:
    return verify(request.cookies.get(COOKIE))


def is_admin(request: Request) -> bool:
    return role(request) == "admin"


def can_edit_any(request: Request) -> bool:
    """Admin oder Benutzer: darf rechnen, ungespeicherte Projekte schicken, Stationen anlegen."""
    return role(request) in ("admin", "user")


def require_admin(request: Request):
    if not is_admin(request):
        raise HTTPException(401, "Anmeldung erforderlich")


def require_member(request: Request):
    if not can_edit_any(request):
        raise HTTPException(401, "Anmeldung erforderlich")


def require_read(request: Request):
    if not PUBLIC_READ and not can_edit_any(request):
        raise HTTPException(401, "Anmeldung erforderlich")


def check_login(request: Request, user: str, password: str) -> str | None:
    """Gibt die Rolle zurück ("admin"/"user") oder None."""
    ip = request.client.host if request.client else "?"
    now = time.time()
    with _lock:
        recent = [t for t in _fail.get(ip, []) if now - t < 600]
        _fail[ip] = recent
        if len(recent) >= 10:
            raise HTTPException(429, "Zu viele Fehlversuche, bitte später erneut versuchen")
    from . import appconfig
    c = appconfig.get()
    got = None
    if hmac.compare_digest(user.encode(), ADMIN_USER.encode()) & hmac.compare_digest(
            (password or "").encode(), (_password or "").encode()):
        got = "admin"
    elif c["user_enabled"] and c["user_password"] and hmac.compare_digest(user.encode(), c["user_name"].encode()) \
            & hmac.compare_digest((password or "").encode(), c["user_password"].encode()):
        got = "user"
    if not got:
        with _lock:
            _fail.setdefault(ip, []).append(now)
        time.sleep(0.8)
    return got


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
