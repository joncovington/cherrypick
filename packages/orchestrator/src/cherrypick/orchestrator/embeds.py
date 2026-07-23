"""Embedded module dashboards for `dashboard --serve`.

Each module ships its *own* full dashboard; this embeds them, one iframe per module, on the orchestrator's
live page — a single pane for the whole suite. Two delivery kinds, both driven by config-declared argv
(the orchestrator never imports a module's internals — same contract as `install`/`sections`):

  - "server": the module runs its own localhost HTTP dashboard (e.g. cherrypick-meic `src/dashboard.py`).
    The orchestrator ensures it's up — launching it if the port is down, exactly like it starts the
    streamer/Dolt — and the iframe points at the module's port.
  - "static": the module regenerates a self-contained HTML file (e.g. cherrypick-earnings
    `src/strategy_dashboard.py`, which inlines its charts as base64). The orchestrator runs the generator on
    demand (throttled) and serves the file from a local route.

Serve-only and loopback-only: launching/generating happens only under `dashboard --serve` (human-
attended), never on the static auto-regen path or the watchdog reliability path. PAPER mode is forced
through the config-declared argv (`--mode paper`) so an embedded view never surfaces live/broker data.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import config as cfgmod

# Reuse the reliability-side primitives: a stdlib socket reachability probe and the benign detached
# (no-window) launcher the watchdog already uses for the streamer. An embedded module server is the
# same shape of process — a python script launched from the module root, left running.
from .watchdog import _dolt_reachable as _port_reachable
from .watchdog import _start_streamer as _launch_detached

_last_build: dict[str, float] = {}  # embed id -> monotonic ts of last static regen (throttle)
_build_lock = threading.Lock()
_building: set[str] = set()  # embed ids with a background regen in flight (single-flight guard)


def _output_path(embed_cfg: dict[str, Any], root: Path) -> Path | None:
    """Resolve a static embed's `output`. `~` and `$VARS` are expanded and an absolute path is used
    as-is, so an embed can point at the module's generated file in the shared home
    (e.g. `~/.cherrypick/data/earnings/reports/strategy_dashboard.html`); a relative path resolves
    against the module checkout root (the historical default)."""
    out_rel = embed_cfg.get("output")
    if not out_rel:
        return None
    p = Path(os.path.expandvars(os.path.expanduser(str(out_rel))))
    return p if p.is_absolute() else (root / p)


def enabled_embeds(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    embeds = cfg.get("dashboard", {}).get("embeds", []) or []
    return [e for e in embeds if e.get("enabled") and e.get("id")]


def by_id(cfg: dict[str, Any], embed_id: str) -> dict[str, Any] | None:
    return next((e for e in enabled_embeds(cfg) if e.get("id") == embed_id), None)


def _subst(argv: list[str] | None, params: dict[str, Any]) -> list[str]:
    out = []
    for tok in argv or []:
        for k, v in params.items():
            tok = tok.replace("{" + k + "}", str(v))
        out.append(tok)
    return out


def server_url(embed_cfg: dict[str, Any]) -> str:
    host = embed_cfg.get("host", "127.0.0.1")
    return f"http://{host}:{int(embed_cfg.get('port', 0))}/"


def ensure_server(embed_cfg: dict[str, Any], wait_seconds: float = 6.0) -> dict[str, Any]:
    """Make sure a "server"-kind embed's dashboard is listening; launch it (PAPER, via `serve_argv`) if
    the port is down, then briefly wait for it to bind so the iframe redirect lands on a ready server."""
    host = embed_cfg.get("host", "127.0.0.1")
    port = int(embed_cfg.get("port", 0))
    url = server_url(embed_cfg)
    if _port_reachable(host, port):
        return {"ok": True, "running": True, "url": url, "detail": "already up"}
    root = cfgmod.module_root(embed_cfg, embed_cfg.get("id"))
    if not root.exists():
        return {"ok": False, "running": False, "url": url, "detail": f"module checkout not found at {root}"}
    argv = _subst(embed_cfg.get("serve_argv"), {"port": port})
    if not argv:
        return {"ok": False, "running": False, "url": url, "detail": "embed has no serve_argv"}
    if not _launch_detached(root, argv):
        return {"ok": False, "running": False, "url": url, "detail": "launch failed"}
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while time.monotonic() < deadline:
        if _port_reachable(host, port):
            return {"ok": True, "running": True, "url": url, "detail": "started"}
        time.sleep(0.25)
    return {"ok": True, "running": False, "url": url, "detail": "launched (still starting)"}


def _run_build(embed_cfg: dict[str, Any], root: Path, out_path: Path | None) -> dict[str, Any]:
    """Actually run the embed's `build_argv` generator (a module dashboard subprocess, e.g. matplotlib →
    base64 HTML). Blocking; callers decide whether to run it inline or on a background thread."""
    argv = _subst(embed_cfg.get("build_argv"), {})
    if not argv:
        return {"ok": False, "detail": "embed has no build_argv"}
    try:
        proc = subprocess.run(
            [cfgmod.python_exe(), *argv],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=int(embed_cfg.get("build_timeout", 120)),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "static build timed out"}
    except OSError as exc:
        return {"ok": False, "detail": f"could not launch static build: {exc}"}
    if proc.returncode != 0:
        return {"ok": False, "detail": ((proc.stderr or proc.stdout) or "build failed").strip()[:300]}
    if out_path and not out_path.exists():
        return {"ok": False, "detail": f"build ran but output missing: {out_path}"}
    return {"ok": True, "path": str(out_path) if out_path else None, "detail": "built"}


def _spawn_rebuild(embed_cfg: dict[str, Any], eid: str, root: Path, out_path: Path | None) -> bool:
    """Kick off a background regen for `eid`, single-flight (skip if one is already running). Marks the
    build time immediately so the throttle holds even before the subprocess finishes. Returns whether a
    build was started."""
    with _build_lock:
        if eid in _building:
            return False
        _building.add(eid)
        _last_build[eid] = time.monotonic()

    def _work() -> None:
        try:
            _run_build(embed_cfg, root, out_path)
        finally:
            with _build_lock:
                _building.discard(eid)

    threading.Thread(target=_work, name=f"embed-build-{eid}", daemon=True).start()
    return True


def build_static(embed_cfg: dict[str, Any]) -> dict[str, Any]:
    """Ensure a "static"-kind embed's HTML is available, without blocking the request on a *stale* regen.

    - **File exists (the common case):** serve it immediately. If it's older than `refresh_seconds`, a
      regen runs on a **background** thread so the next load is fresh — the iframe never waits on the
      matplotlib generator. This is what fixes the recurring slowness (a stale reload used to block on
      the ~seconds-long subprocess every `refresh_seconds`).
    - **No file yet, a build already in flight** (e.g. `prewarm` at startup): report `building` so the
      caller can show an auto-refreshing placeholder instead of double-running the generator.
    - **No file yet and nothing building** (first ever, pre-warm skipped/failed): build **once
      synchronously** so the first view is correct and a generator error surfaces on the request rather
      than hiding behind a perpetual placeholder.

    Returns the output path, a `building` flag, or a failure `detail`."""
    eid = embed_cfg["id"]
    root = cfgmod.module_root(embed_cfg, eid)
    if not root.exists():
        return {"ok": False, "detail": f"module checkout not found at {root}"}
    out_path = _output_path(embed_cfg, root)
    interval = float(embed_cfg.get("refresh_seconds", 30))
    now = time.monotonic()

    if out_path and out_path.exists():
        fresh = (now - _last_build.get(eid, 0.0)) < interval
        if not fresh:
            _spawn_rebuild(embed_cfg, eid, root, out_path)  # revalidate in the background
        return {"ok": True, "path": str(out_path), "detail": "cached" if fresh else "revalidating"}

    with _build_lock:
        building = eid in _building
    if building:
        return {"ok": False, "building": True, "detail": "building"}

    # Nothing to serve and nothing in flight — build once, inline, so errors surface.
    _last_build[eid] = now
    return _run_build(embed_cfg, root, out_path)


def prewarm(cfg: dict[str, Any]) -> None:
    """Best-effort: kick off a background build of every enabled static embed so a file already exists by
    the time the user opens the dashboard. Called once when `dashboard --serve` starts."""
    for emb in enabled_embeds(cfg):
        if emb.get("kind") == "static":
            root = cfgmod.module_root(emb, emb["id"])
            if root.exists():
                _spawn_rebuild(emb, emb["id"], root, _output_path(emb, root))


def _recycle_port(host: str, port: int) -> bool:
    """Best-effort terminate whatever is listening on `port` — a stale embed child left over from a PRIOR
    `dashboard --serve` session. ensure_server reuses any process already on the port, so without this a
    days-old orphan (old code, a retired cache pointer) keeps being served into the iframe across suite
    restarts. Platform-dispatched, no third-party deps; never raises."""
    try:
        if os.name == "nt":
            ps = (
                f"$c=Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue;"
                "if($c){$c|Select-Object -Expand OwningProcess -Unique|"
                "ForEach-Object{Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue}}"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, timeout=15,
            )
        else:
            r = subprocess.run(["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True, timeout=10)
            for pid in r.stdout.split():
                subprocess.run(["kill", "-TERM", pid], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def recycle_servers(cfg: dict[str, Any]) -> list[str]:
    """At `dashboard --serve` startup, terminate any lingering "server"-kind embed from a PRIOR session so
    the next iframe load spawns a fresh child on the current code + config. Returns the ids recycled.

    ensure_server treats a reachable port as "already up" and reuses it — which meant a stale orphan (the
    07-21 gex embed still pointing at the retired meic cache) survived every suite restart and kept being
    framed. Recycling at startup, before this session spawns anything of its own, is safe: it can only hit
    a prior session's process."""
    recycled = []
    for emb in enabled_embeds(cfg):
        if emb.get("kind") != "server":
            continue
        host = emb.get("host", "127.0.0.1")
        port = int(emb.get("port", 0))
        if port and _port_reachable(host, port) and _recycle_port(host, port):
            recycled.append(emb["id"])
    return recycled


def read_static(embed_cfg: dict[str, Any]) -> bytes | None:
    root = cfgmod.module_root(embed_cfg, embed_cfg.get("id"))
    out_path = _output_path(embed_cfg, root)
    if out_path is None:
        return None
    try:
        return out_path.read_bytes()
    except OSError:
        return None
