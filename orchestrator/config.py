"""Config loading and path resolution for Cherrypick.

All paths are derived from this file's location or from config values — never hardcoded
absolute paths (a portability guardrail inherited from both sibling modules).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Cherrypick root = parent of the orchestrator/ package dir.
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
LOGS_DIR = ROOT / "logs"
STATE_DIR = ROOT / "state"


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load and lightly validate the Cherrypick config."""
    cfg_path = path or CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"Cherrypick config not found at {cfg_path}. Copy config.example.json to config.json."
        )
    with cfg_path.open("r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    if "modules" not in cfg:
        raise ValueError("config.json missing 'modules' section")
    return cfg


def module_root(module_cfg: dict[str, Any]) -> Path:
    """Resolve a module's on-disk root from its (relative) 'path', anchored at Cherrypick ROOT."""
    raw = module_cfg.get("path")
    if not raw:
        raise ValueError("module config missing 'path'")
    p = Path(raw)
    if not p.is_absolute():
        p = (ROOT / p)
    return p.resolve()


def enabled_modules(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return {name: module_cfg} for modules with enabled=true."""
    return {
        name: mcfg
        for name, mcfg in cfg.get("modules", {}).items()
        if mcfg.get("enabled", False)
    }


def python_exe() -> str:
    """The interpreter to run module scripts with (same env as Cherrypick)."""
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
