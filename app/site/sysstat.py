"""Was gerade gerechnet wird und wie ausgelastet der Container ist (cgroup v2, Rückfall v1/Host)."""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager

_lock = threading.Lock()
_active = {}  # id -> {"what":..., "since":...}
_next = [0]
_cpu_last = {"t": None, "usage": None}


@contextmanager
def activity(what, **extra):
    """Laufende Arbeit melden. Das zurückgegebene dict kann aktualisiert werden (progress, stage)."""
    with _lock:
        _next[0] += 1
        i = _next[0]
        h = _active[i] = {"what": what, "since": time.time(), **extra}
    try:
        yield h
    finally:
        with _lock:
            _active.pop(i, None)


def busy():
    now = time.time()
    with _lock:
        items = sorted(_active.values(), key=lambda x: x["since"])
    out = []
    for x in items:
        o = {"what": x["what"], "seconds": round(now - x["since"], 1)}
        for k in ("station", "progress", "stage"):
            if x.get(k) is not None:
                o[k] = round(x[k], 3) if k == "progress" else x[k]
        out.append(o)
    return out


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _cpu_usage_s():
    v = _read("/sys/fs/cgroup/cpu.stat")
    if v:
        for line in v.splitlines():
            k, _, n = line.partition(" ")
            if k == "usage_usec":
                return int(n) / 1e6
    v = _read("/sys/fs/cgroup/cpuacct/cpuacct.usage")
    if v:
        return int(v) / 1e9
    t = os.times()
    return t.user + t.system  # nur dieser Prozess


def _cpu_limit():
    v = _read("/sys/fs/cgroup/cpu.max")
    if v and not v.startswith("max"):
        q, p = v.split()
        return max(0.01, int(q) / int(p))
    try:
        return float(len(os.sched_getaffinity(0)))
    except AttributeError:
        return float(os.cpu_count() or 1)


def _mem():
    used = _read("/sys/fs/cgroup/memory.current") or _read("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    limit = _read("/sys/fs/cgroup/memory.max") or _read("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    total = None
    mi = _read("/proc/meminfo")
    if mi:
        for line in mi.splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) * 1024
    used = int(used) if used and used.isdigit() else None
    lim = int(limit) if limit and limit.isdigit() else None
    if lim is None or (total and lim > total):  # "max" bzw. absurd hoher Wert = keine Grenze
        lim = total
    if used is None:
        rss = _read("/proc/self/statm")
        used = int(rss.split()[1]) * os.sysconf("SC_PAGE_SIZE") if rss else None
    return used, lim


def resources():
    now = time.time()
    usage = _cpu_usage_s()
    cpu = None
    with _lock:
        if _cpu_last["t"] is not None and now - _cpu_last["t"] > 0.2:
            cpu = (usage - _cpu_last["usage"]) / (now - _cpu_last["t"]) / _cpu_limit() * 100
        if _cpu_last["t"] is None or now - _cpu_last["t"] > 0.2:
            _cpu_last.update(t=now, usage=usage)
    used, lim = _mem()
    return {"cpu_pct": round(max(0.0, min(100.0, cpu)), 0) if cpu is not None else None, "cpus": round(_cpu_limit(), 1),
            "mem_used": used, "mem_limit": lim}
