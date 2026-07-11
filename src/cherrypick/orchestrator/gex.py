"""Bridge to the optional cherrypick-gex module.

The umbrella surfaces a live GEX section without importing the GEX module's internals: it drives the
module in place via subprocess — `python run.py gex --symbol <sym> --json` — exactly as it drives every
other module (install/status/earnings). The module reads MEIC's stream cache read-only, so this bridge
stays broker-free and network-free; a failure here is reported as a not-ok payload and never raises into
the dashboard render.

GEX is deliberately configured under a top-level `gex` block, NOT under `modules` — `modules` is the
paper-pipeline registry that report/calibrate/watchdog iterate, and the GEX viewer has no paper DB.
"""

from __future__ import annotations

import subprocess
from typing import Any

from . import config as cfgmod
from .util import first_json

_DEFAULT_JSON_ARGV = ["run.py", "gex", "--json"]


def config(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("gex", {}) or {}


def is_enabled(cfg: dict[str, Any]) -> bool:
    return bool(config(cfg).get("enabled"))


def default_symbol(cfg: dict[str, Any]) -> str:
    return str(config(cfg).get("default_symbol", "SPX")).upper()


def refresh_seconds(cfg: dict[str, Any]) -> int:
    return int(config(cfg).get("refresh_seconds", 15))


def fetch(cfg: dict[str, Any], symbol: str | None = None, timeout: int = 30) -> dict[str, Any]:
    """Run the GEX module's `gex --json` for `symbol` and return its payload.

    Returns the module's `{ok, ...}` payload on success, or `{ok: False, error}` when the module is
    disabled, missing, times out, or emits no parseable JSON — the caller renders that inline.
    """
    gcfg = config(cfg)
    if not gcfg.get("enabled"):
        return {"ok": False, "error": "GEX module not enabled (set gex.enabled = true)"}
    sym = (symbol or default_symbol(cfg)).strip().upper()
    root = cfgmod.module_root(gcfg, "gex")
    if not root.exists():
        return {"ok": False, "error": f"GEX module checkout not found at {root}"}
    argv = list(gcfg.get("json_argv") or _DEFAULT_JSON_ARGV) + ["--symbol", sym]
    try:
        proc = subprocess.run(
            [cfgmod.python_exe(), *argv],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "symbol": sym, "error": f"GEX module timed out after {timeout}s"}
    except OSError as exc:
        return {"ok": False, "symbol": sym, "error": f"could not launch GEX module: {exc}"}
    payload = first_json(proc.stdout)
    if not payload:
        err = (proc.stderr or "").strip() or "GEX module returned no JSON"
        return {"ok": False, "symbol": sym, "error": err[:400]}
    return payload
