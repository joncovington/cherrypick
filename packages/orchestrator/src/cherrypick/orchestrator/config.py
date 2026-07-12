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

# cherrypick runtime root — where config.json, logs/, and state/ live. In a source checkout that is the
# repo root; this module sits at <root>/src/cherrypick/orchestrator/config.py, so the root is 3 parents
# up. An installed copy (no repo root) sets CHERRYPICK_HOME to its runtime dir instead.
# The per-user runtime home for an installed copy (config.json, logs/, state/, dashboard.html, modules/).
_USER_HOME = Path.home() / ".cherrypick"


def _default_root() -> Path:
    """cherrypick's runtime home (holds config.json, logs/, state/, dashboard.html).

    CHERRYPICK_HOME always wins. Otherwise a *source checkout* keeps everything in the repo root
    (convenient for dev, matches historical behavior); an *installed copy* — where this file lives under
    site-packages, so the repo-root guess is meaningless/unwritable — falls back to the per-user
    ~/.cherrypick so the `cherrypick` console script has a real, writable home.
    """
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
    user-scoped regardless of how cherrypick is launched. CHERRYPICK_HOME overrides the home."""
    env = os.environ.get("CHERRYPICK_HOME")
    return (Path(env) if env else _USER_HOME) / "logs"


ROOT = _default_root()
CONFIG_PATH = ROOT / "config.json"
# Logs live under the user home by default (see _logs_home); dashboard.html and state/ stay under ROOT.
LOGS_DIR = _logs_home()
STATE_DIR = ROOT / "state"

# Where `cherrypick install` materializes module checkouts when a module declares no explicit `path`.
# Precedence: CHERRYPICK_MODULES_HOME (test/override) → CHERRYPICK_HOME/modules (unified with the rest of
# the runtime home for an installed copy) → the per-user default ~/.cherrypick/modules. Kept independent
# of ROOT so a source checkout still parks modules in the user dir rather than nesting them (and their
# runtime data — e.g. Earnings' multi-GB Dolt store) inside the repo.
_CH_HOME_ENV = os.environ.get("CHERRYPICK_HOME")
MODULES_HOME = Path(
    os.environ.get("CHERRYPICK_MODULES_HOME")
    or (Path(_CH_HOME_ENV) / "modules" if _CH_HOME_ENV else _USER_HOME / "modules")
)


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load and lightly validate the cherrypick config."""
    cfg_path = path or CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"cherrypick config not found at {cfg_path}. Copy config.example.json to config.json."
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


def enabled_modules(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return {name: module_cfg} for modules with enabled=true."""
    return {name: mcfg for name, mcfg in cfg.get("modules", {}).items() if mcfg.get("enabled", False)}


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
