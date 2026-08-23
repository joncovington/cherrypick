"""Position management: what should happen to an open position this tick, and whether we may act.

Same layers as curve/pmcc, kept apart on purpose:

- `effective_params` is the ONE choke point that restates a position's frozen advised params over
  config. An advised book's rules are stamped on the row at entry and read back here every tick.
- `evaluate` is pure over (position, params, this tick's trigger read) and returns a verdict:
  `hold`, `arm` (latch update only, no order), or `fire_addon` (the add-on prices as a credit —
  execute it). Settlement (expiry) is handled by `book.py`/`paper_loop.py` directly, since it is
  unconditional on DTE rather than a management verdict.
- `execution_gate` separately answers "may we act on this mark at all".

Book semantics: `control` never arms. `delta`/`bounce`/`flip` arm on their own trigger.py
condition; once armed, every tick re-prices the add-on (`engine.plan_addon`) — a non-credit tick
is a recorded refusal (`addon_not_credit`) and the arm stays live; the first credit tick fires.
One add-on maximum per position — after firing, the trigger disarms permanently. Armed until
expiry, no cutoff (a trigger met with hours left may add a tiny credit; that is admissible by the
zero-floor rule).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from cherrypick.bwb import triggers

PARAM_DEFAULTS = {
    "credit_floor": 0.0,
    "addon_credit_floor": 0.0,
    "delta_trigger": 0.50,
    "bounce_pullback": 0.05,
    "flip_buffer": 1.001,
    "max_quote_age_seconds": 300,
    "max_leg_spread_pct": 0.25,
    "entry_time": "10:00",
}


@dataclass(frozen=True)
class Decision:
    action: str  # "hold" | "arm" | "fire_addon"
    reason: str
    detail: dict = field(default_factory=dict)

    @property
    def acts(self) -> bool:
        return self.action != "hold"


def effective_params(position: dict, config: dict) -> dict:
    """The params governing this position: the base book's merged config, with the row's frozen
    `advice_params` overlaid for an advised book. An unreadable stamp is the base's config, never a
    guess."""
    from cherrypick.bwb import engine

    book = position.get("book") or "control"
    base = book.split(":", 1)[1] if book.startswith("advised:") else book
    params = {**PARAM_DEFAULTS, **engine.merged_params(config, base)}
    params["book"] = book
    raw = position.get("advice_params")
    if book.startswith("advised:") and raw:
        try:
            overlay = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError):
            overlay = {}
        params.update(overlay)
    return params


def _base_book(params: dict) -> str:
    book = params.get("book") or "control"
    return book.split(":", 1)[1] if book.startswith("advised:") else book


def evaluate(
    position: dict,
    params: dict,
    *,
    trigger_state: dict,
    tick: dict,
    addon_credit: float | None,
) -> tuple[Decision, dict]:
    """The verdict for one OPEN position this tick, plus the UPDATED latch state to persist
    (`peak_abs_delta`, `below_flip_seen`) — persisted regardless of book, the counterfactual-on-
    control property.

    `addon_credit` is what the add-on vertical would price at right now (None if unpriced or not
    yet evaluated this tick — the caller only supplies it once armed). `fire_addon` requires the
    position to already be armed (`position["armed_at"]` set) and not yet fired
    (`position["addon_fired_at"]` unset)."""
    base = _base_book(params)
    read = triggers.evaluate(base, trigger_state, tick, params)
    latches = {"peak_abs_delta": read["peak_abs_delta"], "below_flip_seen": read["below_flip_seen"]}

    already_fired = bool(position.get("addon_fired_at"))
    already_armed = bool(position.get("armed_at"))

    if already_fired:
        return Decision("hold", "addon_already_fired"), latches

    if not already_armed:
        if read["fired"]:
            return Decision("arm", f"{base}_trigger_met"), latches
        return Decision("hold", "not_triggered"), latches

    # Armed: every tick re-prices the add-on.
    if addon_credit is None:
        return Decision("hold", "addon_unpriced"), latches
    floor = params.get("addon_credit_floor", 0.0)
    if addon_credit <= floor:
        return Decision("hold", "addon_not_credit", {"credit": addon_credit}), latches
    return Decision("fire_addon", "addon_credit_met", {"credit": addon_credit}), latches


def execution_gate(mark_snapshot: dict, params: dict, *, now) -> str | None:
    """Why this mark may not be acted on, or None if it may."""
    if not mark_snapshot.get("ok"):
        return "unusable_mark"
    widest = mark_snapshot.get("max_spread_pct")
    if widest is not None and widest > params.get("max_leg_spread_pct", 0.25):
        return "spread_too_wide"
    return None
