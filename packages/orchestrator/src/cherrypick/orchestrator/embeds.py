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


def build_static(embed_cfg: dict[str, Any]) -> dict[str, Any]:
    """Regenerate a "static"-kind embed's HTML via its `build_argv`, throttled to `refresh_seconds` so a
    reloaded iframe doesn't re-run the generator on every request. Returns the output file path."""
    eid = embed_cfg["id"]
    root = cfgmod.module_root(embed_cfg, eid)
    if not root.exists():
        return {"ok": False, "detail": f"module checkout not found at {root}"}
    out_path = _output_path(embed_cfg, root)
    interval = float(embed_cfg.get("refresh_seconds", 30))
    now = time.monotonic()
    if out_path and out_path.exists() and (now - _last_build.get(eid, 0.0)) < interval:
        return {"ok": True, "path": str(out_path), "detail": "cached"}
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
    _last_build[eid] = now
    if proc.returncode != 0:
        return {"ok": False, "detail": ((proc.stderr or proc.stdout) or "build failed").strip()[:300]}
    if out_path and not out_path.exists():
        return {"ok": False, "detail": f"build ran but output missing: {out_path}"}
    return {"ok": True, "path": str(out_path), "detail": "built"}


def read_static(embed_cfg: dict[str, Any]) -> bytes | None:
    root = cfgmod.module_root(embed_cfg, embed_cfg.get("id"))
    out_path = _output_path(embed_cfg, root)
    if out_path is None:
        return None
    try:
        return out_path.read_bytes()
    except OSError:
        return None
