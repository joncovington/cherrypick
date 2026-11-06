"""The next cash dividend of a name, from the local Dolt `stocks.dividend` table.

The assignment-risk exit (`management._assignment_risk`) asks one question of this module: is there a
dividend going ex after today and on or before a leg's expiry, and how large. The answer is read from
the table the scanner already queries, never from the network, and is only asked for when a short call
is in the money -- so a tick that holds nothing in the money makes no query at all.

The table is a HISTORY: its newest ex-dates trail the calendar by days, and a dividend that has been
declared but has not yet gone ex is not in it. So the next ex-date is read in two ways, and says which:

  * `declared` -- a row dated after today exists. Rare in practice; used as written.
  * `projected` -- the last ex-date plus the median gap between the recent ones, with the last amount.
    Dividends are paid on a cadence, so this is right to within a few days for a regular payer. A name
    with fewer than two dividends in the window, a gap outside 25..400 days, or a last ex-date more than
    two gaps old (a payer that has stopped) has NO projection and is reported unknown.

Unknown is a real answer and is recorded as one: the caller then applies the time-value floor alone.
A lookup that fails (Dolt down) is unknown too, with the reason, and is not retried for five minutes.
"""

from __future__ import annotations

import statistics
import time
from datetime import date, timedelta

LOOKBACK_DAYS = 800
MIN_GAP_DAYS = 25
MAX_GAP_DAYS = 400
_FAILURE_RETRY_SECONDS = 300

_cache: dict[tuple[str, str], dict] = {}
_failed_until: float = 0.0


def next_dividend(rows: list[tuple[date, float | None]], today: date) -> dict:
    """The next ex-date and amount after `today` from `(ex_date, amount)` rows, or an unknown.

    Pure. Returns `{"known": True, "ex_date": iso, "amount": float, "source": "declared"|"projected"}`
    or `{"known": False, "note": why}`.
    """
    rows = sorted((d, a) for d, a in rows if d is not None)
    future = [(d, a) for d, a in rows if d > today and a]
    if future:
        d, a = future[0]
        return {"known": True, "ex_date": d.isoformat(), "amount": float(a), "source": "declared"}
    past = [(d, a) for d, a in rows if d <= today]
    if len(past) < 2:
        return {"known": False, "note": "fewer_than_two_dividends_on_record"}
    gaps = [(b[0] - a[0]).days for a, b in zip(past, past[1:], strict=False)]
    gap = int(statistics.median(gaps))
    if not MIN_GAP_DAYS <= gap <= MAX_GAP_DAYS:
        return {"known": False, "note": f"irregular_cadence_{gap}d"}
    last_date, last_amount = past[-1]
    if (today - last_date).days > 2 * gap or not last_amount:
        return {"known": False, "note": "dividend_appears_stopped"}
    nxt = last_date + timedelta(days=gap)
    while nxt <= today:
        nxt += timedelta(days=gap)
    return {"known": True, "ex_date": nxt.isoformat(), "amount": float(last_amount), "source": "projected"}


def _fetch(symbol: str, today: date, config: dict) -> list[tuple[date, float | None]]:
    from cherrypick.earnings import scanner

    conn = scanner._dolt_connect(config, config.get("dolthub_stocks_database", "stocks"))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT ex_date, amount FROM dividend WHERE act_symbol = %s AND ex_date >= %s ORDER BY ex_date",
            (symbol, today - timedelta(days=LOOKBACK_DAYS)),
        )
        return [(d, None if a is None else float(a)) for d, a in cur.fetchall()]
    finally:
        conn.close()


def lookup_for(config: dict):
    """A `(symbol, today) -> dict` lookup bound to `config`, cached per symbol and day."""

    def lookup(symbol: str, today: date) -> dict:
        global _failed_until
        key = (symbol.upper(), today.isoformat())
        if key in _cache:
            return _cache[key]
        if time.monotonic() < _failed_until:
            return {"known": False, "note": "dividend_lookup_failed"}
        try:
            out = next_dividend(_fetch(symbol.upper(), today, config), today)
        except Exception as exc:  # Dolt down, table missing: unknown, never a crash on the tick
            _failed_until = time.monotonic() + _FAILURE_RETRY_SECONDS
            return {"known": False, "note": f"dividend_lookup_failed: {type(exc).__name__}"}
        _cache[key] = out
        return out

    return lookup
