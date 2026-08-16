"""The advisor's knobs, read from the suite config's ``advisor`` block.

The orchestrator owns the schedule (which slots fire, at what times, on which model) and resolves
that half itself. This module reads the half the deterministic side needs — how many experiments a
module may run at once, and how long one runs for — so a human can retune the governance without
touching code, and so both sides read the same block rather than each keeping their own defaults.

Everything is off or conservative by default. An absent config produces exactly the shape below.

`calibration_rule` reads a *different* block of the same file, for the same reason: the
qualification thresholds a module is actually judged by live in `modules.<name>.calibration.rule`,
and the advisor has to apply the module's own rule rather than the library default or it shows the
model a weaker gate than the suite uses.
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
        "pmcc": {"enabled": False},
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


def calibration_rule(module: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """The qualification thresholds this module is actually judged by, from the suite config's
    `modules.<name>.calibration.rule` — the same block `orchestrator.calibrate` reads.

    Why this exists (found 2026-08-14 acting on advisor proposal #4): the fact pack computed
    `qualify_readings(readings)` with NO rule, so the model was shown the library default
    (sample/win_rate/days) while `calibrate` applied each module's configured rule on the same
    readings. meic's config has demanded `min_return_on_capital` and `require_slippage_survival`
    since the fork cutover, so the two surfaces disagreed about which arms were qualified — and the
    advisor reasoned, correctly but from the weaker gate, that arms passing qualification were
    losing money. The fix is to read the rule rather than to restate its defaults here.

    `margin` is stripped: it belongs to `recommend_champion`'s comparison, not to the per-tag
    threshold check, and `calibrate` pops it off the same way before passing the rule down.
    """
    if cfg is None:
        cfg = _store.read_json(_home.config_path(), default={}) or {}
    modules = cfg.get("modules")
    mcfg = (modules or {}).get(module) if isinstance(modules, dict) else None
    rule = ((mcfg or {}).get("calibration") or {}).get("rule")
    if not isinstance(rule, dict):
        return {}
    return {k: v for k, v in rule.items() if k != "margin"}


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
