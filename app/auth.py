"""Single admin login. Everyone else gets read-only access to public projects.

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


def make_token(user: str) -> str:
    payload = f"{user}|{int(time.time()) + SESSION_S}".encode()
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
    try:
        user, exp = payload.decode().split("|")
    except ValueError:
        return None
    if int(exp) < time.time() or user != ADMIN_USER:
        return None
    return user


def is_admin(request: Request) -> bool:
    return verify(request.cookies.get(COOKIE)) is not None


def require_admin(request: Request):
    if not is_admin(request):
        raise HTTPException(401, "Anmeldung erforderlich")


def require_read(request: Request):
    if not PUBLIC_READ and not is_admin(request):
        raise HTTPException(401, "Anmeldung erforderlich")


def check_login(request: Request, user: str, password: str) -> bool:
    ip = request.client.host if request.client else "?"
    now = time.time()
    with _lock:
        recent = [t for t in _fail.get(ip, []) if now - t < 600]
        _fail[ip] = recent
        if len(recent) >= 10:
            raise HTTPException(429, "Zu viele Fehlversuche, bitte später erneut versuchen")
    ok = hmac.compare_digest(user.encode(), ADMIN_USER.encode()) & hmac.compare_digest(
        (password or "").encode(), (_password or "").encode())
    if not ok:
        with _lock:
            _fail.setdefault(ip, []).append(now)
        time.sleep(0.8)
    return bool(ok)


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
