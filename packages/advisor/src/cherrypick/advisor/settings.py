"""The advisor's knobs, read from the suite config's ``advisor`` block.

The orchestrator owns the schedule (which slots fire, at what times, on which model) and resolves
that half itself. This module reads the half the deterministic side needs — how many experiments a
module may run at once, and how long one runs for — so a human can retune the governance without
touching code, and so both sides read the same block rather than each keeping their own defaults.

Everything is off or conservative by default. An absent config produces exactly the shape below.
"""

from __future__ import annotations

from typing import Any

from cherrypick.core import home as _home

from cherrypick.advisor import store as _store

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    # One per module, and that is not arbitrary: each module's consumer builds exactly one
    # `advised:<base>` book from the day's artifact, so a second concurrent experiment has nowhere
    # to be measured. Over-cap specs queue and activate FIFO.
    "max_experiments_per_module": 1,
    # 15 sessions so an experiment that runs its course can actually satisfy the promotion gate
    # (min_days 14, min_sample 20). A model asking for fewer gets its request honored, clamped, and
    # a verdict labeled `underpowered` if the numbers land below the bar.
    "experiment_sessions": 15,
    "experiment_sessions_min": 5,
    "experiment_sessions_max": 30,
    "modules": {
        "meic": {"enabled": True},
        "flies": {"enabled": False},
        "earnings": {"enabled": True},
        # Off until the module's own advice block is turned on too — the two switches must agree.
        "calendars": {"enabled": False},
    },
}


def load(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolved advisor settings. Pass `cfg` (the whole suite config) to avoid a second file read."""
    if cfg is None:
        cfg = _store.read_json(_home.config_path(), default={}) or {}
    block = cfg.get("advisor")
    if not isinstance(block, dict):
        return dict(DEFAULTS)
    resolved = {**DEFAULTS, **block}
    resolved["modules"] = {**DEFAULTS["modules"], **(block.get("modules") or {})}
    return resolved


def module_enabled(module: str, settings: dict[str, Any] | None = None) -> bool:
    """Whether the advisor may operate on this module at all.

    Two switches have to agree before anything happens: this one (the suite decided the advisor
    covers this module) and the module's own `advice` block (the module decided it accepts advice,
    and within which bounds). Either one off means nothing is admitted.
    """
    settings = settings if settings is not None else load()
    return bool((settings["modules"].get(module) or {}).get("enabled"))


def clamp_sessions(requested: Any, settings: dict[str, Any] | None = None) -> int:
    """Honor a proposal's requested length, inside the configured floor and ceiling."""
    settings = settings if settings is not None else load()
    default = int(settings["experiment_sessions"])
    lo = int(settings["experiment_sessions_min"])
    hi = int(settings["experiment_sessions_max"])
    try:
        value = int(requested)
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))
