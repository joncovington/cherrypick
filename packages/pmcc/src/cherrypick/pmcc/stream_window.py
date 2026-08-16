"""Per-symbol streamer ATM-window sizing — computed from the chain, escalated on real misses.

Unlike its flies ancestor, the width here is not primarily a reaction to trouble: a 99-delta long
call lives 30–45% below spot, which is OUTSIDE any default ATM window by construction, so the
module computes the width it structurally needs from the cached chain (strikes between
`spot × (1 − deep_window_pct)` and spot, plus margin) and asks for it up front via `window_hints`.
The flies escalation/decay mechanism rides on top, fed by this module's own refusal journal
(`no_deep_itm_long` / `missing_leg_quotes` in `pmcc_decisions`), for whatever the computation
under-estimates. The final hint is the max of the two; `union_window_hints` in core maxes per
symbol across modules, so this can never narrow anyone else.

State persists in `pmcc_stream_window`; the whole file is best-effort telemetry-class — a failure
here degrades to the streamer's default window, never breaks a tick.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from cherrypick.pmcc import clock

DEFAULT_BASE_WIDTH = 60
DEFAULT_MARGIN = 10
DEFAULT_INCREMENT = 30
DEFAULT_MAX_WIDTH = 200
DEFAULT_MISS_THRESHOLD = 3
DEFAULT_DECAY_AFTER_MINUTES = 60

_MISS_REASONS = ("no_deep_itm_long", "missing_leg_quotes")


def window_params(config: dict) -> dict:
    block = config.get("stream_window") or {}
    return {
        "base_width": int(block.get("base_width", DEFAULT_BASE_WIDTH)),
        "margin": int(block.get("margin", DEFAULT_MARGIN)),
        "increment": int(block.get("increment", DEFAULT_INCREMENT)),
        "max_width": int(block.get("max_width", DEFAULT_MAX_WIDTH)),
        "miss_threshold": int(block.get("miss_threshold", DEFAULT_MISS_THRESHOLD)),
        "decay_after_minutes": int(block.get("decay_after_minutes", DEFAULT_DECAY_AFTER_MINUTES)),
    }


def needed_width(cache_path, symbol: str, *, deep_window_pct: float, margin: int) -> int | None:
    """The structural need: how many listed strikes sit between the deep window's floor and spot on
    the NEAREST cached expiration, plus margin. None when the cache cannot answer (no spot, no
    chain) — the caller falls back to escalation state alone."""
    try:
        conn = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return None
    try:
        tr = conn.execute("SELECT last FROM stream_trades WHERE symbol = ?", (symbol,)).fetchone()
        spot = float(tr["last"]) if tr and tr["last"] is not None else None
        if not spot:
            return None
        import json as _json

        strikes: set[float] = set()
        for row in conn.execute("SELECT data_json FROM stream_chain WHERE underlying_symbol = ?", (symbol,)):
            try:
                opt = _json.loads(row["data_json"])
                strike = float(opt.get("strike_price"))
            except (TypeError, ValueError):
                continue
            if spot * (1.0 - deep_window_pct) <= strike <= spot:
                strikes.add(strike)
        if not strikes:
            return None
        return len(strikes) + margin
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def recent_miss_occurrences(conn, trade_date: str, symbol: str) -> int:
    """The largest `pmcc_decisions.occurrences` for a window-miss refusal on `symbol` today, across
    books and reasons. MAX rather than SUM: a shared window gap surfaces in multiple books at once,
    and summing would inflate urgency for what is really one physical cause."""
    placeholders = ", ".join("?" * len(_MISS_REASONS))
    row = conn.execute(
        f"SELECT MAX(occurrences) AS n FROM pmcc_decisions "
        f"WHERE trade_date = ? AND symbol = ? AND reason IN ({placeholders})",
        (trade_date, symbol, *_MISS_REASONS),
    ).fetchone()
    return int(row["n"]) if row and row["n"] is not None else 0


def _state(conn, symbol: str) -> dict:
    row = conn.execute("SELECT * FROM pmcc_stream_window WHERE symbol = ?", (symbol,)).fetchone()
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
    """The escalation half (flies' shape verbatim): widen by `increment` once `miss_threshold` NEW
    miss occurrences accumulate since the last escalation; decay one `increment` per
    `decay_after_minutes` of quiet, never below `base_width`. Persists to `pmcc_stream_window`."""
    now = now or clock.now_iso()
    state = _state(conn, symbol)
    width = max(state["width"] or base_width, base_width)

    occurrences = recent_miss_occurrences(conn, trade_date, symbol)
    last_checked = state["last_checked_occurrences"] or 0
    last_escalated = state["last_escalated_occurrences"] or 0
    last_miss_at = state["last_miss_at"]
    last_escalated_at = state["last_escalated_at"]

    if occurrences > last_checked:
        last_miss_at = now

    delta = occurrences - last_escalated
    if delta >= miss_threshold:
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
        "INSERT INTO pmcc_stream_window "
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


def hints_for_symbols(
    conn, cache_path, symbols: list[str], trade_date: str, config: dict, *, deep_window_pct: float
) -> dict[str, int]:
    """`{symbol: width}` — max(structural need, escalated width) per symbol, entries only where the
    result exceeds `base_width` (absent/empty is the request payload's own default convention)."""
    p = window_params(config)
    hints: dict[str, int] = {}
    for symbol in symbols:
        symbol = symbol.strip().upper()
        computed = needed_width(cache_path, symbol, deep_window_pct=deep_window_pct, margin=p["margin"])
        escalated = evaluate(
            conn,
            symbol,
            trade_date,
            base_width=p["base_width"],
            increment=p["increment"],
            max_width=p["max_width"],
            miss_threshold=p["miss_threshold"],
            decay_after_minutes=p["decay_after_minutes"],
        )
        width = max(computed or 0, escalated)
        width = min(width, p["max_width"])
        if width > p["base_width"]:
            hints[symbol] = width
    return hints
