"""The read side of the chain's recorded quotes (gex's history DB, from 2026-10-13).

Stored as `gex_quote_samples` (one row per symbol and tick: date, time, expiration) and `gex_quotes`
(one per side and strike of a sample, the side 0 for a put and 1 for a call); the layout is the
recorder's (`service._ensure_history_table`), and this module hides it.

The gex recorder keeps, every tick (~15 s) in RTH, the usable quotes of the strikes nearest spot on its
chain -- the only quote history in the suite (the stream cache keeps the latest quote per leg and
nothing else). This module hands a session back sample by sample in the shape a module's snapshot
already has (`underlying_price`, `now_min`, `puts`/`calls` as {strike: {bid, ask, mid, age_seconds,
delta}}), so a replay of a rule a module never ran prices against the market as it was through the
module's own pricing functions.

Spot is not stored with the quotes: `gex_spot_history` already records it every recorder tick, and
it is joined here AT OR BEFORE the sample, within `max_spot_staleness_seconds` -- `core.regime`'s
rule, for its reason (a later spot is look-ahead; a stale one is a gap, not a value). A minute whose
spot cannot be joined keeps `underlying_price = None` and says so in `spot_reason`, never borrows a
neighbour's.

Reads a database another package's recorder writes, the suite's ordinary read-model relationship;
core imports nothing from gex.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from cherrypick.core import clock as _clock
from cherrypick.core import db as _db
from cherrypick.core import home as _home

DEFAULT_MAX_SPOT_STALENESS_SECONDS = 60.0

# `gex_quotes.side`: the recorder's `service.QUOTE_SIDES` (put 0, call 1). Kept as a literal, since
# core imports nothing from gex; change both or neither.
PUT, CALL = 0, 1


def default_history_db() -> Path:
    return _home.data_dir("gex") / "gex_history.db"


def session(
    symbol: str,
    trade_date: str,
    *,
    history_db: Path | str | None = None,
    max_spot_staleness_seconds: float = DEFAULT_MAX_SPOT_STALENESS_SECONDS,
) -> list[dict]:
    """Every recorded sample of `symbol` on `trade_date`, oldest first. Empty when nothing was kept."""
    path = Path(history_db) if history_db is not None else default_history_db()
    if not path.exists():
        return []
    symbol = symbol.strip().upper()
    conn = _db.connect_ro(path)
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"gex_quote_samples", "gex_quotes"} <= have:
            return []
        minutes: dict[float, dict] = {}
        for r in conn.execute(
            "SELECT s.ts, s.expiration, q.side, q.strike, q.bid, q.ask, q.mid, q.age_s, q.delta "
            "FROM gex_quote_samples s JOIN gex_quotes q ON q.sample_id = s.id "
            "WHERE s.symbol = ? AND s.trade_date = ? ORDER BY s.ts, q.side, q.strike",
            (symbol, trade_date),
        ):
            snap = minutes.get(r["ts"])
            if snap is None:
                when = datetime.fromtimestamp(r["ts"], _clock.ET)
                try:
                    dte = (date.fromisoformat(r["expiration"]) - when.date()).days
                except (TypeError, ValueError):
                    dte = None
                snap = minutes[r["ts"]] = {
                    "ok": True,
                    "symbol": symbol,
                    "date": trade_date,
                    "ts": r["ts"],
                    "now_min": _clock.minute_of_day(when),
                    "expiration": r["expiration"],
                    "dte": dte,
                    "underlying_price": None,
                    "spot_reason": None,
                    "puts": {},
                    "calls": {},
                }
            q = {"bid": r["bid"], "ask": r["ask"], "mid": r["mid"], "age_seconds": r["age_s"]}
            if r["delta"] is not None:
                q["delta"] = r["delta"]
            snap["puts" if r["side"] == PUT else "calls"][r["strike"]] = q
        if not minutes:
            return []
        spots = (
            [
                (row["ts"], row["spot"])
                for row in conn.execute(
                    "SELECT ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date = ? ORDER BY ts",
                    (symbol, trade_date),
                )
            ]
            if "gex_spot_history" in have
            else []
        )
    finally:
        conn.close()

    i = -1
    out = []
    for ts in sorted(minutes):
        while i + 1 < len(spots) and spots[i + 1][0] <= ts:
            i += 1
        snap = minutes[ts]
        if i < 0:
            snap["spot_reason"] = "no_spot_before"
        elif ts - spots[i][0] > max_spot_staleness_seconds:
            snap["spot_reason"] = "spot_stale"
        else:
            snap["underlying_price"] = spots[i][1]
        out.append(snap)
    return out
