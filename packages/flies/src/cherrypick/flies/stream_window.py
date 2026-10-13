"""Auto-escalate a symbol's requested streamer ATM-window width after repeated
`missing_leg_quotes` refusals, and decay it back down once they stop.

What it does NOT count (2026-09-25): `legs_beyond_strike_window`, a structure placed outside the
snapshot's own `strike_window_pct` of spot. The provider never offers those strikes whatever the
streamer carries, so widening cannot help -- and it did the harm instead: callwall's far walls drove
the SPX request to 105 and 135 strikes a side on 2026-09-25, and each widening recycled the streamer.
Until then both cases read `missing_leg_quotes`.

A static window is a guess: too narrow and a wide wing or a fast move sits outside the streamer's
subscribed strikes (`missing_leg_quotes` -- a data-availability gap, nothing to do with the strike's
actual value); too wide and the daemon subscribes to far more quotes than the symbol usually needs.
Rather than re-guessing a bigger static number, this module reads the real evidence already sitting in
`fly_decisions` (the engine's own refusal journal) and asks the streamer for more, via the same
file-based registry every module already uses (`stream_request.py` -> `window_hints`) -- no streamer
restart needed, since the per-symbol window is re-evaluated every `window_poll_s` cycle.

Escalation and decay are both gradual and hysteresis-guarded: a single miss never escalates (avoids
over-reacting to one blip), and decaying only removes one `increment` per `decay_after_minutes` of
quiet (never below `base_width`) rather than snapping back immediately or all at once.

Two additions on 2026-09-19, for the delta-placed arms (debit-first-up/down, the bwb delta pairs,
and the 5-delta hedge overlay), which read strikes 40-150 points from spot rather than the wings
around it:

  * `request_base_width=True` sends `base_width` itself as the hint at rest, not only an escalated
    width. Until then the module's floor was a private number the streamer never saw -- it
    subscribed its own global default (30 a side, +/-150 SPX points since 2026-08-24) and flies only
    spoke up after misses. A 5-delta strike on a VIX-20 morning sits ~150 points out, at the edge of
    that window, so the overlay would have read "untracked" on exactly the sessions it exists for.
  * `no_delta_quotes_beyond_spot` counts as a miss. The delta rule refuses BEFORE it has chosen
    legs, so it never produces `missing_leg_quotes`, and without this the escalator could not see a
    delta arm starving. A stale-greeks tick trips it too; that costs one escalation step that decays
    back, never a wrong entry.
"""

from __future__ import annotations

from cherrypick.core import streamrequests as _streamrequests

from cherrypick.flies import clock

DEFAULT_INCREMENT = 30
DEFAULT_MAX_WIDTH = 150
DEFAULT_MISS_THRESHOLD = 3
DEFAULT_DECAY_AFTER_MINUTES = 60
# Refusals that mean "the strike I wanted is not in the streamer's window" (see module docstring).
MISS_REASONS = ("missing_leg_quotes", "no_delta_quotes_beyond_spot")


def recent_miss_occurrences(conn, trade_date: str, symbol: str) -> int:
    """The largest `fly_decisions.occurrences` for a window-miss refusal (`MISS_REASONS`) on
    `symbol` today, across every arm/mode. MAX rather than SUM: a shared window gap surfaces in
    multiple arms/modes at once, and summing would inflate urgency for what is really one physical
    cause."""
    return _streamrequests.window_miss_occurrences(conn, "fly_decisions", MISS_REASONS, trade_date, symbol)


def hints_for_symbols(
    conn, symbols, trade_date: str, *, base_width: int, request_base_width: bool = False, **kwargs
) -> dict[str, int]:
    """`{symbol: width}` to declare. By default only a symbol whose current effective width is
    ABOVE `base_width` contributes an entry -- the common case (nothing escalated) sends nothing,
    matching the request payload's own "absent/empty is the default" convention. With
    `request_base_width` every symbol contributes its width, so `base_width` is the floor the
    streamer actually subscribes (it takes max(its default, hint)) rather than a private number.
    `**kwargs` forwards to `evaluate` (increment/max_width/miss_threshold/decay_after_minutes/now)."""
    hints: dict[str, int] = {}
    for symbol in symbols:
        width = evaluate(conn, symbol, trade_date, base_width=base_width, **kwargs)
        if request_base_width or width > base_width:
            hints[symbol] = width
    return hints


def evaluate(
    conn,
    symbol: str,
    trade_date: str,
    *,
    base_width: int,
    increment: int = DEFAULT_INCREMENT,
    max_width: int = DEFAULT_MAX_WIDTH,
    miss_threshold: int = DEFAULT_MISS_THRESHOLD,
    decay_after_minutes: int = DEFAULT_DECAY_AFTER_MINUTES,
    now: str | None = None,
) -> int:
    """The effective ATM-window width to request for `symbol` right now. Widens by `increment`
    (capped at `max_width`) once `miss_threshold` NEW `missing_leg_quotes` occurrences have
    accumulated since the last escalation; decays back down by one `increment` (never below
    `base_width`) once `decay_after_minutes` have passed with no NEW miss. Persists state to
    `fly_stream_window` and returns the width to request.
    """
    return _streamrequests.escalate_window(
        conn,
        symbol,
        state_table="fly_stream_window",
        occurrences=recent_miss_occurrences(conn, trade_date, symbol),
        base_width=base_width,
        increment=increment,
        max_width=max_width,
        miss_threshold=miss_threshold,
        decay_after_minutes=decay_after_minutes,
        now=now or clock.now_iso(),
    )
