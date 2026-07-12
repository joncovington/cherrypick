"""Generic dashboard-section bridge.

The umbrella surfaces a live card for any module that speaks the `cherrypick.core.viz` section contract:
it subprocesses the module for a declarative payload and renders it generically (in `dashboard.py` via
`core.viz`). It never imports a module's internals — it drives the module in place by config-declared
argv, exactly like install/status/earnings — so a new module gets a dashboard card "for free" by
emitting the schema. This replaces the hand-wired GEX bridge.

Sections are declared under `dashboard.sections` (a list), deliberately NOT under `modules` (that
registry is the paper-pipeline one). Each section: `{id, title, enabled, path|repo, fetch_argv,
default_symbol?, refresh_seconds?}`. `fetch_argv` may contain `{name}` tokens filled from request params
(e.g. `{symbol}`).
"""

from __future__ import annotations

import subprocess
from typing import Any

from . import config as cfgmod
from .util import first_json


def enabled_sections(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    secs = cfg.get("dashboard", {}).get("sections", []) or []
    return [s for s in secs if s.get("enabled") and s.get("id")]


def by_id(cfg: dict[str, Any], section_id: str) -> dict[str, Any] | None:
    return next((s for s in enabled_sections(cfg) if s.get("id") == section_id), None)


def refresh_seconds(section_cfg: dict[str, Any]) -> int:
    return int(section_cfg.get("refresh_seconds", 15))


def _argv(section_cfg: dict[str, Any], params: dict[str, str]) -> list[str]:
    out = []
    for tok in section_cfg.get("fetch_argv") or []:
        for k, v in params.items():
            tok = tok.replace("{" + k + "}", str(v))
        out.append(tok)
    return out


def fetch(section_cfg: dict[str, Any], params: dict[str, str] | None = None, timeout: int = 30) -> dict:
    """Run the section module's fetch_argv and return its `core.viz` payload.

    Returns the module's `{ok, ...}` payload, or `{ok: False, error}` when the module is missing, times
    out, or emits no parseable JSON — the generic renderer shows that inline. Read-only and best-effort:
    the module only reads files, so this stays broker-free/network-free.
    """
    params = dict(params or {})
    if "symbol" not in params and section_cfg.get("default_symbol"):
        params["symbol"] = str(section_cfg["default_symbol"]).upper()
    root = cfgmod.module_root(section_cfg, section_cfg.get("id"))
    if not root.exists():
        return {"ok": False, "error": f"section module checkout not found at {root}"}
    argv = _argv(section_cfg, params)
    if not argv:
        return {"ok": False, "error": "section has no fetch_argv"}
    try:
        proc = subprocess.run(
            [cfgmod.python_exe(), *argv], cwd=str(root),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"section timed out after {timeout}s"}
    except OSError as exc:
        return {"ok": False, "error": f"could not launch section module: {exc}"}
    payload = first_json(proc.stdout)
    if not payload:
        err = (proc.stderr or "").strip() or "section module returned no JSON"
        return {"ok": False, "error": err[:400]}
    return payload
