"""Live-ops readiness view (read-only: files + keyring, no broker, no trading).

The phase-5 gate made visible: before any live enablement the hub must show, at a glance,
(1) each module's `enable_live_trading` kill switch, (2) which real account each module has
designated for live trading, and (3) the suite halt flag — the file kill switch a live loop
polls every tick (present ⇒ halt new live entries). This module only *reads*; flipping
`enable_live_trading` or writing the halt flag is a human action, per the invariant that the
orchestrator never flips live trading. The broker-truth side of live ops (the live book) is
`reconcile`, which the Live Ops card composes alongside this.

The halt flag's path is defined HERE (`state/halt-live.flag` in the cherrypick home) so the
convention exists before any live loop does — phase 5's loops poll the same path this view
reports, and the view showing "absent" is the day-one proof the wiring points somewhere real.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

from . import accounts
from . import config as cfgmod
from .util import mask_account

HALT_FLAG_NAME = "halt-live.flag"


def halt_flag_path() -> Path:
    """`~/.cherrypick/state/halt-live.flag` — the suite-wide live kill switch. Its *presence* is
    the signal (contents ignored), so `touch` halts and `del` re-arms with no parser in between."""
    return cfgmod.state_file(HALT_FLAG_NAME)


def _live_enabled(name: str, root: Path) -> tuple[bool | None, str | None]:
    """A module's `enable_live_trading`, home config first (`~/.cherrypick/config/<name>.json`,
    where migrated modules keep it) then the in-repo fallbacks accounts.py reads. Returns
    (flag, source) — (None, None) when no config is readable, which the view shows as unknown
    rather than defaulting to a reassuring 'off'."""
    candidates = [_home.config_path(name), root / "config" / "config.json", root / "config.json"]
    for p in candidates:
        if p.exists():
            try:
                flag = bool(json.loads(p.read_text(encoding="utf-8")).get("enable_live_trading", False))
            except (OSError, ValueError):
                return None, cfgmod.portable_path(p)
            return flag, cfgmod.portable_path(p)
    return None, None


def run(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """The live-ops readiness snapshot. Fast and file/keyring-only — safe on a served route."""
    cfg = cfg or cfgmod.load_config()
    modules = []
    for name in cfgmod.enabled_modules(cfg):
        mcfg = cfg.get("modules", {}).get(name) or {}
        root = cfgmod.module_root(mcfg, name)
        flag, source = _live_enabled(name, root) if root.exists() else (None, None)
        store = accounts.keyring_store(cfg, name)
        designated = accounts._designated_number(store)
        modules.append(
            {
                "module": name,
                "live_enabled": flag,  # None = no readable config (unknown, not off)
                "config_source": source,
                "designated": mask_account(designated) if designated else None,
            }
        )
    # Credential source per module (own/shared/missing) — the onboarding panel's column,
    # merged in so the Live Ops card shows setup state next to the kill switches.
    try:
        ob = accounts.onboarding_status(cfg)
        if ob.get("ok"):
            by_name = {m["module"]: m for m in ob["modules"]}
            for m in modules:
                row = by_name.get(m["module"]) or {}
                m["credentials"] = row.get("credentials")
                m["account_source"] = row.get("account_source")
    except Exception:
        pass
    halt = halt_flag_path()
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "halt_flag": {"path": cfgmod.portable_path(halt), "present": halt.exists()},
        "modules": modules,
    }
