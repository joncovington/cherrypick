"""Auto-escalate a symbol's requested streamer ATM-window width after repeated
`missing_leg_quotes` refusals, and decay it back down once they stop.

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
"""

from __future__ import annotations

from datetime import datetime

import clock

DEFAULT_INCREMENT = 30
DEFAULT_MAX_WIDTH = 150
DEFAULT_MISS_THRESHOLD = 3
DEFAULT_DECAY_AFTER_MINUTES = 60


def recent_miss_occurrences(conn, trade_date: str, symbol: str) -> int:
    """The largest `fly_decisions.occurrences` for a `missing_leg_quotes` refusal on `symbol` today,
    across every arm/mode. MAX rather than SUM: a shared window gap surfaces in multiple arms/modes
    at once, and summing would inflate urgency for what is really one physical cause."""
    row = conn.execute(
        "SELECT MAX(occurrences) AS n FROM fly_decisions "
        "WHERE trade_date = ? AND symbol = ? AND reason = 'missing_leg_quotes'",
        (trade_date, symbol),
    ).fetchone()
    return int(row["n"]) if row and row["n"] is not None else 0


def _state(conn, symbol: str) -> dict:
    row = conn.execute("SELECT * FROM fly_stream_window WHERE symbol = ?", (symbol,)).fetchone()
    if row:
        return dict(row)
    return {
        "symbol": symbol,
        "width": None,
        "last_escalated_occurrences": 0,
        "last_checked_occurrences": 0,
        "last_escalated_at": None,
        "last_miss_at": None,
    }


def hints_for_symbols(conn, symbols, trade_date: str, *, base_width: int, **kwargs) -> dict[str, int]:
    """`{symbol: width}` for every symbol whose current effective width is ABOVE `base_width` —
    the common case (nothing currently escalated) contributes no entries, matching the request
    payload's own "absent/empty is the default" convention. `**kwargs` forwards to `evaluate`
    (increment/max_width/miss_threshold/decay_after_minutes/now)."""
    hints: dict[str, int] = {}
    for symbol in symbols:
        width = evaluate(conn, symbol, trade_date, base_width=base_width, **kwargs)
        if width > base_width:
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
    now = now or clock.now_iso()
    state = _state(conn, symbol)
    width = max(state["width"] or base_width, base_width)  # a raised config default still floors it

    occurrences = recent_miss_occurrences(conn, trade_date, symbol)
    last_checked = state["last_checked_occurrences"] or 0
    last_escalated = state["last_escalated_occurrences"] or 0
    last_miss_at = state["last_miss_at"]
    last_escalated_at = state["last_escalated_at"]

    is_new_miss = occurrences > last_checked
    if is_new_miss:
        last_miss_at = now

    delta = occurrences - last_escalated
    if delta >= miss_threshold:
        # Multi-step in one call for a burst that crosses several threshold multiples at once (e.g.
        # a quiet symbol suddenly missing dozens of times before the next tick), not just one
        # increment regardless of how far past the threshold -- leftover occurrences short of the
        # next full threshold multiple stay un-escalated for a future tick.
        steps = delta // miss_threshold
        width = min(width + steps * increment, max_width)
        last_escalated = last_escalated + steps * miss_threshold
        last_escalated_at = now

    if width > base_width and last_miss_at:
        try:
            quiet_minutes = (
                datetime.fromisoformat(now) - datetime.fromisoformat(last_miss_at)
            ).total_seconds() / 60
        except ValueError:
            quiet_minutes = 0.0
        if quiet_minutes >= decay_after_minutes:
            width = max(base_width, width - increment)
            last_miss_at = now  # reset the clock so decay steps down gradually, not all at once

    conn.execute(
        "INSERT INTO fly_stream_window "
        "(symbol, width, last_escalated_occurrences, last_checked_occurrences, "
        "last_escalated_at, last_miss_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(symbol) DO UPDATE SET "
        "width=excluded.width, last_escalated_occurrences=excluded.last_escalated_occurrences, "
        "last_checked_occurrences=excluded.last_checked_occurrences, "
        "last_escalated_at=excluded.last_escalated_at, last_miss_at=excluded.last_miss_at, "
        "updated_at=excluded.updated_at",
        (symbol, width, last_escalated, occurrences, last_escalated_at, last_miss_at, now),
    )
    conn.commit()
    return width
