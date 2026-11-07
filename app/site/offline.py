"""Offline operation (#58): no connections to the internet.

With RFP_OFFLINE=true (or the switch in Administration → Data sources) every outgoing HTTP request of the server
to a public address is refused before it leaves the container. Addresses in the local network stay reachable –
a CoreScope instance or a tile server in the LAN keeps working. Everything else falls back to what is stored:
elevation tiles in data/dem, map tiles in the tile cache and offline map packages, building footprints, the
CoreScope cache.

The gate sits in httpx's network transport, so it covers every client in the code base (tiles, WCS, Copernicus,
Overpass, CoreScope) without each caller having to remember it. The test client does not use that transport.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
from functools import lru_cache

import httpx

log = logging.getLogger("terrarf.offline")

ENV = os.environ.get("RFP_OFFLINE", "").strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    if ENV:
        return True
    from ..store import appconfig   # late import: appconfig must not depend on this module
    return bool(appconfig.get().get("offline"))


def _private(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


@lru_cache(maxsize=256)
def _host_is_local(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    if not host:
        return False
    if host in ("localhost", "testserver") or "." not in host and not _looks_like_ip(host):
        return True          # docker service names, single-label LAN names
    if _private(host):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addrs = {i[4][0] for i in infos}
    return bool(addrs) and all(_private(a) for a in addrs)


def _looks_like_ip(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def allowed(url) -> bool:
    """May the server open this URL right now?"""
    if not enabled():
        return True
    return _host_is_local(httpx.URL(str(url)).host)


class OfflineError(httpx.ConnectError):
    pass


_original = httpx.HTTPTransport.handle_request


def _guarded(self, request):
    if not allowed(request.url):
        raise OfflineError(f"Offline-Betrieb: keine Verbindung zu {request.url.host}", request=request)
    return _original(self, request)


def install():
    if httpx.HTTPTransport.handle_request is not _guarded:
        httpx.HTTPTransport.handle_request = _guarded


install()
