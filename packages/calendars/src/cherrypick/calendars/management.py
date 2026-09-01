"""Position management: what should happen to an open calendar, and whether we may act on it.

Three layers, kept apart on purpose (the earnings pattern):

- `effective_params` is the ONE choke point that restates a position's frozen advised params over
  the config. An advised book's exit rules are stamped on the row at entry and read back here every
  tick, so advice lapsing mid-week never hands an open position to rules nobody chose — and a
  control row comes back untouched.
- `evaluate` is pure over (position, params, a priced mark, the clock) and returns a verdict.
- `execution_gate` separately answers "may we act on this mark at all" — a verdict blocked by a
  gate is still recorded (`executed=0` with the gate), which is the only record that an exit was
  SEEN before it was allowed.

Book semantics:
- `control` — the user-defined baseline: close every leg in the Friday exit window. No stops, no
  targets, no weekend hold. Its verdict repeats until executed; if the window is missed outright,
  settlement takes the shorts and Monday disposition the longs, with the blocked verdicts on file.
- `path` — the permissive superset (MEIC's `open` arm precedent): never closes. Shorts run to cash
  settlement, longs to the Monday disposition. Its whole job is the recorded mark path.
- `advised:<base>` — the frozen params decide: profit target / stop (as fractions of the entry
  debit, on the COMBINED double when both sides are open — see `evaluate`'s note), the
  short-strike-touch side close, the scheduled `time_exit`, and `long_disposition`. With
  `long_disposition: "mon_open"` there is no whole-structure scheduled exit — shorts settle Friday
  and longs dispose Monday, the path shape — while triggers still fire any day both legs live.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from cherrypick.core import calendar as _cal

from cherrypick.calendars import clock, engine

PARAM_DEFAULTS = {
    "exit_window_start": "15:45",
    "exit_window_end": "15:55",
    "noon_exit_start": "12:00",
    "exec_window_start": "09:40",
    "mon_disposition_time": "09:45",
    "max_leg_spread_pct": 0.25,
    # A leg is only too wide when it is wide in percent AND in money. Below this, the width is
    # refused as a reason to refuse -- see `execution_gate`. Mirrors curve's `max_wing_spread_abs`,
    # which was added for the identical zero-bid arithmetic.
    "max_leg_spread_abs": 0.05,
    "profit_target_pct": None,
    "stop_loss_pct_of_debit": None,
    "short_strike_touch_exit": False,
    "time_exit": "fri_close",
    "long_disposition": "fri_close",
}


@dataclass(frozen=True)
class Decision:
    """A verdict about one position. `executed` is decided by the caller after `execution_gate`."""

    action: str  # "hold" | "close_all"
    reason: str
    detail: dict = field(default_factory=dict)

    @property
    def closes(self) -> bool:
        return self.action == "close_all"


def effective_params(position: dict, config: dict) -> dict:
    """The params governing this position: the base book's merged config, with the row's frozen
    `advice_params` overlaid for an advised book. An unreadable stamp is the control's config,
    never a guess."""
    book = position.get("book") or "control"
    base = engine.base_book(book)
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


def _scheduled_exit_day(position: dict, params: dict) -> date | None:
    """The day the whole structure is scheduled to close, per `time_exit` — None when
    `long_disposition` is `mon_open` (no whole-structure schedule exists then)."""
    if params.get("long_disposition") == "mon_open" and params["book"] != "control":
        return None
    front = date.fromisoformat(position["front_expiration"])
    if params["book"] != "control" and params.get("time_exit") == "thu_close":
        day = front - timedelta(days=1)
        while not _cal.is_trading_day(day):
            day -= timedelta(days=1)
        return day
    return front


def _scheduled_exit_minute(params: dict) -> int:
    if params["book"] != "control" and params.get("time_exit") == "fri_noon":
        return clock.hhmm_to_min(params.get("noon_exit_start"), 12 * 60)
    return clock.hhmm_to_min(params.get("exit_window_start"), 15 * 60 + 45)


def evaluate(
    position: dict,
    params: dict,
    *,
    now: datetime,
    combined_value: float | None,
    combined_debit: float | None,
    spot: float | None,
) -> Decision:
    """The verdict for one OPEN position this tick.

    `combined_value`/`combined_debit` are the WHOLE double calendar's mark and entry debit (both
    sides summed) while both positions are open, degrading to this position's own once its twin has
    closed — the profit target and stop are statements about the trade the user defined, which is
    the double, not one side. The caller owns that pairing; this function just compares.
    """
    book = params["book"]
    # Through base_book, so `friday:path` holds exactly as `path` does — a raw name comparison
    # would make the never-closing book close (see engine.base_book).
    if engine.base_book(book) == "path":
        return Decision("hold", "path_holds")

    today = now.date()
    now_min = clock.minute_of_day(now)

    if book.startswith("advised:"):
        pt = params.get("profit_target_pct")
        sl = params.get("stop_loss_pct_of_debit")
        if combined_value is not None and combined_debit:
            move = (combined_value - combined_debit) / combined_debit
            if pt is not None and move >= pt:
                return Decision("close_all", "profit_target", {"move": round(move, 4)})
            if sl is not None and move <= -sl:
                return Decision("close_all", "stop_loss", {"move": round(move, 4)})
        if params.get("short_strike_touch_exit") and spot is not None:
            strike = position["strike"]
            touched = spot <= strike if position["side"] == "put" else spot >= strike
            if touched:
                return Decision("close_all", "short_strike_touch", {"spot": spot, "strike": strike})

    exit_day = _scheduled_exit_day(position, params)
    if exit_day is not None and today >= exit_day and now_min >= _scheduled_exit_minute(params):
        return Decision("close_all", "scheduled_exit", {"exit_day": exit_day.isoformat()})
    return Decision("hold", "working")


def execution_gate(mark_snapshot: dict, params: dict, *, now: datetime) -> str | None:
    """Why this mark may not be acted on, or None if it may. Separate from `evaluate` so a blocked
    verdict is still recorded — an exit seen at 09:33 and taken at 09:41 must be legible as that."""
    if not mark_snapshot.get("ok"):
        return "unusable_mark"
    exec_start = clock.hhmm_to_min(params.get("exec_window_start"), 9 * 60 + 40)
    if clock.minute_of_day(now) < exec_start:
        return "before_exec_window"
    if _spread_blocks(mark_snapshot, params):
        return "spread_too_wide"
    return None


def _spread_blocks(mark_snapshot: dict, params: dict) -> bool:
    """Whether any leg is too wide to act on -- wide in PERCENT and in MONEY, both.

    A percentage alone is the wrong instrument for a cheap leg, and on the way OUT that is the
    common case rather than an edge one. A short that has done its job is worth almost nothing:
    `bid 0.00 / ask 0.01` is a one-cent buyback and, read as a ratio, exactly a 200% spread. On
    2026-08-28 that refused the control put's scheduled Friday exit on all THIRTY ticks of its
    window, at 2.000 every time, while the call side closed normally at 0.222. The position missed
    its exit entirely, its front expired instead, and the longs went on Monday for a different
    result -- which is what the exit-policy replay then reported as a $1.30 disagreement with the
    books it is validated against.

    curve reached the same rule from the entry side (`_wing_spread_blocks`, 56 of 62 refusals in one
    session at exactly 2.000), and it made an exception there: the SHORT leg keeps the plain
    percentage test, because its premium is the whole credit and paying up is what the gate exists
    to prevent. That exception belongs to entry only. On exit there is no premium being protected --
    every leg is being closed, and a penny is a penny whichever one it is -- so the absolute floor
    applies to all of them.

    An older snapshot with no per-leg detail falls back to the percentage test alone, so a stored
    mark cannot silently widen what this admits.
    """
    max_pct = params.get("max_leg_spread_pct", 0.25)
    legs = mark_snapshot.get("leg_spreads")
    if not legs:
        widest = mark_snapshot.get("max_spread_pct")
        return widest is not None and widest > max_pct
    max_abs = params.get("max_leg_spread_abs", 0.05)
    return any(leg["pct"] > max_pct and leg["abs"] > max_abs for leg in legs)
