"""Position management: what should happen to an open position, and whether we may act on it.

Three layers, kept apart on purpose (the earnings pattern, via calendars):

- `effective_params` is the ONE choke point that restates a position's frozen advised params over
  the config. An advised book's rules are stamped on the row at entry and read back here every
  tick, so advice lapsing mid-position never hands it to rules nobody chose — and a control row
  comes back untouched.
- `evaluate` is pure over (position, params, a priced mark, the clock) and returns a verdict.
- `execution_gate` separately answers "may we act on this mark at all" — a verdict blocked by a
  gate is still recorded (`executed=0` with the gate), which is the only record that an exit was
  SEEN before it was allowed.

Book semantics (the strategy as taught, then one contrast each):
- `control` — hold the short while it is ITM; when its time value is exhausted
  (`short_tv ≤ tv_close_threshold`), close BOTH legs together, never roll. If spot falls below the
  short strike, hold like a covered call to the short's expiry (`covered_call_hold`, recorded every
  tick so the hold is legible).
- `keltner` — control's management exactly; only the ENTRY differs (the pullback-and-reversal gate
  in keltner.py). Any difference between the two books is entry timing and nothing else.
- `roll` — control's entry exactly; on a breach it ROLLS the short down/out instead of holding
  (once per position per session, and never once the long is under `min_long_dte_for_roll` days —
  then it closes, `roll_exhausted`). Measures roll-vs-hold.
- `advised:<base>` — the base book's rules with the admitted param overrides frozen at entry.

Assignment-exposure telemetry lives BESIDE the verdict, not in it: `assignment_exposed` flags a
mark whose short extrinsic sits under `assignment_exposure_tv` — the region where a real short is
liable to be assigned early, which this module measures and deliberately does not model. It gates
nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime

from cherrypick.pmcc import clock, engine

PARAM_DEFAULTS = {
    "tv_close_threshold": 0.10,
    "assignment_exposure_tv": 0.05,
    "entry_window_start": "10:00",
    "entry_window_end": "15:30",
    "exec_window_start": "09:40",
    "max_leg_spread_pct": 0.25,
    "min_long_dte_for_roll": 6,
}


@dataclass(frozen=True)
class Decision:
    """A verdict about one position. `executed` is decided by the caller after `execution_gate`."""

    action: str  # "hold" | "close_all" | "roll_short"
    reason: str
    detail: dict = field(default_factory=dict)

    @property
    def acts(self) -> bool:
        return self.action != "hold"


def effective_params(position: dict, config: dict) -> dict:
    """The params governing this position: the base book's merged config, with the row's frozen
    `advice_params` overlaid for an advised book. An unreadable stamp is the base's config, never a
    guess."""
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


def assignment_exposed(short_tv: float | None, params: dict) -> bool:
    """Whether this mark sits in the early-assignment-exposed region. Telemetry only."""
    if short_tv is None:
        return False
    return short_tv < params.get("assignment_exposure_tv", 0.05)


def _base_book(params: dict) -> str:
    book = params.get("book") or "control"
    return book.split(":", 1)[1] if book.startswith("advised:") else book


def evaluate(
    position: dict,
    params: dict,
    *,
    now: datetime,
    short_tv: float | None,
    spot: float | None,
    rolled_today: bool = False,
) -> Decision:
    """The verdict for one OPEN position this tick.

    `short_tv` is the short leg's per-share extrinsic at the current mark (None when the mark was
    refused — nothing acts on a hole). `rolled_today` is the caller's read of the events table; the
    once-per-session roll cadence lives there rather than in a param because it is a rule about the
    ledger, not a threshold.
    """
    if spot is None or short_tv is None:
        return Decision("hold", "unpriced_mark")
    strike = position["short_strike"]

    if spot > strike:
        if short_tv <= params.get("tv_close_threshold", 0.10):
            return Decision("close_all", "tv_exhausted", {"short_tv": short_tv, "spot": spot})
        return Decision("hold", "working")

    # Breach: spot at or below the short strike.
    if _base_book(params) != "roll":
        return Decision("hold", "covered_call_hold", {"spot": spot, "strike": strike})
    long_dte = (date.fromisoformat(position["long_expiration"]) - now.date()).days
    if long_dte < int(params.get("min_long_dte_for_roll", 6)):
        return Decision("close_all", "roll_exhausted", {"long_dte": long_dte})
    if rolled_today:
        return Decision("hold", "roll_cadence", {"spot": spot, "strike": strike})
    return Decision("roll_short", "short_strike_breach", {"spot": spot, "strike": strike})


def execution_gate(mark_snapshot: dict, params: dict, *, now: datetime) -> str | None:
    """Why this mark may not be acted on, or None if it may. Separate from `evaluate` so a blocked
    verdict is still recorded — an exit seen at 09:33 and taken at 09:41 must be legible as that."""
    if not mark_snapshot.get("ok"):
        return "unusable_mark"
    exec_start = clock.hhmm_to_min(params.get("exec_window_start"), 9 * 60 + 40)
    if clock.minute_of_day(now) < exec_start:
        return "before_exec_window"
    widest = mark_snapshot.get("max_spread_pct")
    if widest is not None and widest > params.get("max_leg_spread_pct", 0.25):
        return "spread_too_wide"
    return None
