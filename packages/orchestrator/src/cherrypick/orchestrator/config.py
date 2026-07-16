"""Config loading and path resolution for cherrypick.

All paths are derived from this file's location or from config values — never hardcoded
absolute paths (a portability guardrail inherited from both sibling modules).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# The orchestrator package bootstraps src/_core onto sys.path in its __init__, which runs before this
# submodule's body — so the shared home resolver is importable here.
from cherrypick.core import home as _home

# cherrypick runtime root — where config.json, logs/, and state/ live. In a source checkout that is the
# repo root; this module sits at <root>/src/cherrypick/orchestrator/config.py, so the root is 3 parents
# up. An installed copy (no repo root) sets CHERRYPICK_HOME to its runtime dir instead.
# The per-user runtime home for an installed copy (config.json, logs/, state/, dashboard.html, modules/).
_USER_HOME = Path.home() / ".cherrypick"


def _source_root() -> Path:
    """The **source anchor** for resolving a module's relative `path` in config (e.g. `../meic`) — the
    orchestrator checkout dir (this file sits at <root>/src/cherrypick/orchestrator/config.py, so it is
    3 parents up). This stays tied to the checkout even though runtime files (config/state/dashboard/
    logs) now live under the per-user home, so an in-place `path: ../meic` keeps resolving into the repo
    regardless of where config.json physically lives. CHERRYPICK_HOME overrides it for an installed copy
    (where modules come from MODULES_HOME, not a relative checkout)."""
    env = os.environ.get("CHERRYPICK_HOME")
    if env:
        return Path(env)
    repo_root = Path(__file__).resolve().parents[3]
    if (repo_root / "run.py").exists() or (repo_root / "pyproject.toml").exists():
        return repo_root
    return _USER_HOME


def _logs_home() -> Path:
    """Where cherrypick writes its logs. Always the per-user home (~/.cherrypick/logs), independent of
    ROOT — so log output never lands inside a source checkout and its location is stable and
    user-scoped regardless of how cherrypick is launched. Delegates to the suite-wide resolver, so
    CHERRYPICK_HOME relocates it uniformly with every other package's logs."""
    return _home.logs_dir()


# ROOT is the source anchor for relative module paths (see _source_root); the runtime files themselves
# — config.json, state/, dashboard.html, logs/ — all live under the per-user home now, so nothing runtime
# is written into the checkout.
ROOT = _source_root()
CONFIG_PATH = _home.config_path()
LOGS_DIR = _logs_home()
STATE_DIR = _home.state_dir()

# Where `cherrypick install` materializes module checkouts when a module declares no explicit `path`.
# Precedence: CHERRYPICK_MODULES_HOME (test/override) → CHERRYPICK_HOME/modules → ~/.cherrypick/modules.
# Kept independent of ROOT (via the shared resolver) so a source checkout still parks modules in the user
# dir rather than nesting them (and their runtime data — e.g. Earnings' multi-GB Dolt store) in the repo.
MODULES_HOME = _home.modules_dir()


LEGACY_CONFIG_PATH = ROOT / "config.json"


def effective_config_path() -> Path:
    """The config file to read: the per-user home config (`~/.cherrypick/config.json`) once it exists,
    otherwise a legacy in-repo `config.json` (a source checkout that predates the move). A pure lookup —
    it never writes, so importing/reading has no side effects and test runs can't pollute the real home;
    the actual file move into the home is an explicit step (`cherrypick migrate-home`). Falls back to the
    home path for the 'not found' message when neither exists."""
    if CONFIG_PATH.exists():
        return CONFIG_PATH
    return LEGACY_CONFIG_PATH if LEGACY_CONFIG_PATH.exists() else CONFIG_PATH


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load and lightly validate the cherrypick config (home config, or a legacy in-repo one until
    migrated — see :func:`effective_config_path`)."""
    cfg_path = path or effective_config_path()
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"cherrypick config not found at {cfg_path}. Copy config.example.json there to create it."
        )
    with cfg_path.open("r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    if "modules" not in cfg:
        raise ValueError("config.json missing 'modules' section")
    return cfg


def module_dirname(module_cfg: dict[str, Any], name: str | None = None) -> str:
    """Checkout directory name under MODULES_HOME: the repo basename
    (…/cherrypick-meic.git → cherrypick-meic) when a 'repo' is configured, else the module's key."""
    repo = module_cfg.get("repo")
    if repo:
        stem = str(repo).rstrip("/").rsplit("/", 1)[-1]
        return stem[:-4] if stem.endswith(".git") else stem
    if name:
        return name
    raise ValueError("module config needs a 'repo', a 'path', or a name to locate its checkout")


def module_root(module_cfg: dict[str, Any], name: str | None = None) -> Path:
    """Resolve a module's on-disk root.

    An explicit 'path' (absolute, or relative to cherrypick ROOT) always wins — the dev override for a
    working checkout. With no 'path', the module lives at its managed install location
    MODULES_HOME/<dirname> (see module_dirname), which is where `cherrypick install` clones it.
    """
    raw = module_cfg.get("path")
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = ROOT / p
        return p.resolve()
    return (MODULES_HOME / module_dirname(module_cfg, name)).resolve()


def paper_db_path(module_cfg: dict[str, Any], name: str | None = None) -> Path:
    """Resolve a module's paper-trades DB file. `paper.paper_db` may be:
      - absolute (used as-is);
      - `~`- or env-prefixed — expanded, so a module whose data lives in the managed home can be pointed
        at e.g. `~/.cherrypick/data/meic/paper_trades.db` without a hardcoded machine path; or
      - relative — resolved against the module checkout root (the historical default).
    Mirrors `dolt_service.data_dir` resolution. One source of truth so every read surface (report,
    reconcile, calibrate, dashboard) and the watchdog freshness check agree on which file the module
    actually writes — a mismatch silently blinds the orchestrator to a module's paper data.
    """
    rel = (module_cfg.get("paper", {}) or {}).get("paper_db", "data/paper_trades.db")
    p = Path(os.path.expandvars(os.path.expanduser(str(rel))))
    if p.is_absolute():
        return p.resolve()
    return (module_root(module_cfg, name) / p).resolve()


def module_logs_dir(name: str) -> Path:
    """A module's logs home: `LOGS_DIR/<name>` (e.g. ~/.cherrypick/logs/meic) — the location the module's
    own `paths.logs_dir()` writes to by the shared convention. Used to find each module's log tails and
    the per-module `paper-eod-<day>.md` files the EOD digest links to. Both sides derive it the same way
    from the (CHERRYPICK_HOME-aware) logs home, so they agree without the orchestrator importing the
    module. A module-private override (MEIC_LOGS_DIR/EARNINGS_LOGS_DIR) is a test/machine escape hatch
    only; in normal operation the convention holds."""
    return LOGS_DIR / name


def enabled_modules(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return {name: module_cfg} for modules with enabled=true."""
    return {name: mcfg for name, mcfg in cfg.get("modules", {}).items() if mcfg.get("enabled", False)}


def enabled_services(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Long-running background daemons the orchestrator keeps alive (top-level `services`): started at
    `install`, kept up by the watchdog, and located by `path`/`repo` like modules. Each declares
    `status_argv` (prints `{"running": bool}`), `start_argv`, and `auto_restart`. Distinct from the
    `modules` registry (paper pipelines) — a service has no paper DB or schedule of its own, e.g. the gex
    spot-trail recorder that runs alongside the streamer."""
    return [s for s in (cfg.get("services") or []) if s.get("enabled") and s.get("id")]


def eod_digest_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolved suite end-of-day-digest scheduling. ON by default (opt out with
    `"eod_digest": {"enabled": false}`), with a default task name and daily time — so a config
    predating the feature still gets the digest scheduled at `install`. The time is the box's local
    clock (assumed ET, like the modules' entry_time/exit_time)."""
    ed = cfg.get("eod_digest", {}) or {}
    return {
        "enabled": ed.get("enabled", True),
        "task_name": ed.get("task_name", "cherrypick-eod-digest"),
        "at": ed.get("at", "16:15"),
    }


def python_exe() -> str:
    """The interpreter to run module scripts with (same env as cherrypick)."""
    return sys.executable


def pythonw_exe() -> str:
    """A windowless interpreter for scheduled tasks (falls back to python if absent)."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else str(exe)


def ensure_dirs() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def state_file(name: str) -> Path:
    ensure_dirs()
    return STATE_DIR / name


def log_file(name: str) -> Path:
    ensure_dirs()
    return LOGS_DIR / name
