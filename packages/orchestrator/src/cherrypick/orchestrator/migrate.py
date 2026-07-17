"""`cherrypick migrate-home` — one-shot sweep of pre-home-cutover files into ``~/.cherrypick``.

Everything runtime now resolves under the per-user home (see ``cherrypick.core.home``); the loaders fall
back to in-repo files until this runs, so migration is explicit and never automatic. It does two things:

  1. **Move the config files into the home** — the suite config to ``~/.cherrypick/config.json`` and each
     module's config to ``~/.cherrypick/config/<pkg>.json`` — so the home copy is authoritative (no more
     silent fallback to a stale in-repo config). An existing home config is never overwritten.
  2. **Sweep regenerable leftovers** out of the checkouts — old ``*.log``, a generated ``dashboard.html``,
     ``state/*.json``, and ``reports/*.html``. These all regenerate under the home, so removing the repo
     copies is safe. **``*.db`` files are never deleted** (they may hold data) — they are only reported.

``dry_run=True`` (the CLI default) prints the plan and touches nothing.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

from . import config as cfgmod
from . import doctor

# Known legacy config layouts, keyed by module/section name. meic keeps config.json at its root;
# earnings nests it under config/. The suite (orchestrator) config sits at ROOT/config.json.
_MODULE_CONFIG_REL = {"earnings": Path("config") / "config.json"}


def _config_moves(cfg: dict[str, Any]) -> list[tuple[str, Path, Path]]:
    """(name, legacy_src, home_dest) for every config file, whether or not the src exists yet."""
    moves: list[tuple[str, Path, Path]] = [("orchestrator", cfgmod.ROOT / "config.json", _home.config_path())]
    for name, mcfg in cfg.get("modules", {}).items():
        root = cfgmod.module_root(mcfg, name)
        legacy = root / _MODULE_CONFIG_REL.get(name, Path("config.json"))
        moves.append((name, legacy, _home.config_path(name)))
    for sec in cfg.get("dashboard", {}).get("sections", []) or []:
        if sec.get("id") and (sec.get("path") or sec.get("repo")):
            root = cfgmod.module_root(sec, sec["id"])
            moves.append((sec["id"], root / "config.json", _home.config_path(sec["id"])))
    return moves


def _sweep_roots(cfg: dict[str, Any]) -> list[Path]:
    roots = [cfgmod.ROOT]
    for name, mcfg in cfg.get("modules", {}).items():
        roots.append(cfgmod.module_root(mcfg, name))
    for sec in cfg.get("dashboard", {}).get("sections", []) or []:
        if sec.get("id") and (sec.get("path") or sec.get("repo")):
            roots.append(cfgmod.module_root(sec, sec["id"]))
    # de-dup while preserving order
    seen: set[Path] = set()
    return [r for r in roots if not (r in seen or seen.add(r))]


def plan(cfg: dict[str, Any]) -> dict[str, Any]:
    """Compute the migration plan without touching the filesystem."""
    moves = []
    for name, src, dest in _config_moves(cfg):
        if dest.exists():
            moves.append({"name": name, "src": str(src), "dest": str(dest), "action": "skip-dest-exists"})
        elif src.exists():
            moves.append({"name": name, "src": str(src), "dest": str(dest), "action": "move"})
        # a config that exists in neither place is simply not present on this machine — nothing to do
    stray = doctor.find_stray_artifacts(_sweep_roots(cfg))
    deletes = [str(p) for p in stray if p.suffix != ".db"]
    db_review = [str(p) for p in stray if p.suffix == ".db"]
    return {"moves": moves, "deletes": deletes, "db_review": db_review}


def apply(plan_dict: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    """Execute (or, with dry_run, just report) a plan from :func:`plan`."""
    moved, deleted = [], []
    for mv in plan_dict["moves"]:
        if mv["action"] != "move":
            continue
        if not dry_run:
            dest = Path(mv["dest"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(mv["src"], str(dest))
        moved.append(mv)
    for path in plan_dict["deletes"]:
        if not dry_run:
            try:
                Path(path).unlink()
            except OSError:
                continue
        deleted.append(path)
    return {"dry_run": dry_run, "moved": moved, "deleted": deleted, "db_review": plan_dict["db_review"]}


def run(cfg: dict[str, Any] | None = None, *, dry_run: bool = True) -> dict[str, Any]:
    cfg = cfg or cfgmod.load_config()
    return apply(plan(cfg), dry_run=dry_run)
