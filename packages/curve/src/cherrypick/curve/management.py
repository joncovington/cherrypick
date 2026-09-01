"""Position management: what should happen to an open spread, and whether we may act on it.

Same three layers as pmcc/calendars, kept apart on purpose:

- `effective_params` is the ONE choke point that restates a position's frozen advised params over
  config. An advised book's rules are stamped on the row at entry and read back here every tick.
- `evaluate` is pure over (position, params, a priced mark, the day's regime read) and returns a
  verdict.
- `execution_gate` separately answers "may we act on this mark at all".

Book semantics:
- `control` — close at `profit_take_pct` of the entry credit, OR the regime-flip hard exit
  (measured ratio crosses >= 1.0 mid-trade -> close next tick regardless of P&L), OR `close_dte`.
- `noflip` — control's exit MINUS the flip rule: holds through backwardation to target or
  `close_dte`. Its entry is identical to control's, same tick, same fills (the pairing).
- `hook` — control's exit rules, entered only on the two-day-confirmed hook signal.

Rule 6 (the module's honesty rules): missing regime data can never force an exit. The flip fires
only on a MEASURED crossing; an unmeasured regime tick holds the position's last verdict and is
flagged, never treated as a flip.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

PARAM_DEFAULTS = {
    "profit_take_pct": 0.50,
    "close_dte": 7,
    "assignment_exposure_tv": 0.05,
    "entry_window_start": "10:00",
    "entry_window_end": "10:30",
    "exec_window_start": "09:40",
    "max_leg_spread_pct": 0.25,
    # The floor under the percentage: refuse a leg only when wide in percent AND in money.
    "max_leg_spread_abs": 0.05,
    "allow_delta_computed_fallback": True,
}

FLIP_BOOKS = ("control", "hook")  # noflip is the one book without the regime-flip exit


@dataclass(frozen=True)
class Decision:
    action: str  # "hold" | "close_all"
    reason: str
    detail: dict = field(default_factory=dict)

    @property
    def acts(self) -> bool:
        return self.action != "hold"


def effective_params(position: dict, config: dict) -> dict:
    """The params governing this position: the base book's merged config, with the row's frozen
    `advice_params` overlaid for an advised book. An unreadable stamp is the base's config, never a
    guess."""
    from cherrypick.curve import engine

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


def assignment_exposed(short_tv: float | None, params: dict) -> bool:
    """Whether the short leg's mark sits in the early-assignment-exposed region. Telemetry only —
    gates nothing."""
    if short_tv is None:
        return False
    return short_tv < params.get("assignment_exposure_tv", 0.05)


def evaluate(
    position: dict,
    params: dict,
    *,
    now: datetime,
    close_cost: float | None,
    regime: dict | None,
) -> Decision:
    """The verdict for one OPEN position this tick.

    `close_cost` is what closing the spread would cost at mid right now (None on an unpriced
    mark — nothing acts on a hole). `regime` is today's regime reading (`{"ok", "ratio", ...}` or
    None) — a missing/unmeasured read never forces an exit; it only ever holds.
    """
    dte = (
        (datetime.fromisoformat(position["expiration"]).date() - now.date()).days
        if position.get("expiration")
        else None
    )
    if dte is not None and dte <= int(params.get("close_dte", 7)):
        return Decision("close_all", "close_dte", {"dte": dte})

    if (
        _base_book(params) in FLIP_BOOKS
        and regime
        and regime.get("ok")
        and regime.get("regime") == "backwardation"
    ):
        return Decision("close_all", "regime_flip", {"ratio": regime.get("ratio")})

    if close_cost is None:
        return Decision("hold", "unpriced_mark")

    entry_credit = position.get("entry_credit")
    if entry_credit is None:
        return Decision("hold", "no_entry_credit")
    target_cost = entry_credit * (1.0 - params.get("profit_take_pct", 0.50))
    if close_cost <= target_cost:
        return Decision(
            "close_all", "profit_take", {"close_cost": close_cost, "target_cost": round(target_cost, 4)}
        )
    return Decision("hold", "working")


def execution_gate(mark_snapshot: dict, params: dict, *, now) -> str | None:
    """Why this mark may not be acted on, or None if it may."""
    from cherrypick.curve import clock

    if not mark_snapshot.get("ok"):
        return "unusable_mark"
    exec_start = clock.hhmm_to_min(params.get("exec_window_start"), 9 * 60 + 40)
    if clock.minute_of_day(now) < exec_start:
        return "before_exec_window"
    if _spread_blocks(mark_snapshot, params):
        return "spread_too_wide"
    return None


def _spread_blocks(mark_snapshot: dict, params: dict) -> bool:
    """Whether any leg is too wide to act on -- wide in PERCENT and in MONEY, both, per leg.

    The zero-bid arithmetic, pre-empted rather than measured here: a short held to the end of its
    life quotes 0.00/0.01 -- a one-cent buyback and, as a ratio, exactly a 200% spread -- and a
    percentage-only gate refuses precisely the scheduled exit the book is built around. earnings
    measured 32 profit-target exits refused that way before its 2026-08-31 fix, and calendars lost
    a Friday close to it; this module's gate had not fired yet only because no position had aged
    into the state. A leg is refused only when both readings say wide; an older snapshot with no
    per-leg detail falls back to the percentage alone, so nothing widens silently.
    """
    max_pct = params.get("max_leg_spread_pct", 0.25)
    legs = mark_snapshot.get("leg_spreads")
    if not legs:
        widest = mark_snapshot.get("max_spread_pct")
        return widest is not None and widest > max_pct
    max_abs = params.get("max_leg_spread_abs", 0.05)
    return any(leg["pct"] > max_pct and leg["abs"] > max_abs for leg in legs)
