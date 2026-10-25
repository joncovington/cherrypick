"""Entry context for every trade row, derived on read (`docs/trading-book-review-plan.md` B4).

For each closed trade (`core.ledgers` records): SPX against its 50- and 200-day averages, the name's
ATR(14) as a percentage of price, its volume against its 20-day average, its sector, the entry time,
and the holding period at exit. So "where do the losses cluster?" is answered from the ledgers, not
re-derived per study.

**As of the close BEFORE the entry session**, always: a trade entered at 10:05 could not have seen
that day's bar, and reading it would leak the outcome into the tag. Derived here rather than
recorded by each module, so every row already written has it too, and no module's decisions or
schema change. The store is the local end-of-day one (`store.adjusted_bars`); a name it does not
hold, or a window it does not cover, reads None -- unknown, never a guess.

An index option's underlying reads as its index (`XSP`, `SPXW` -> `SPX`). An index or fund has no
sector and reads None.
"""

from __future__ import annotations

from datetime import datetime

from cherrypick.core import environment

from . import indicators

#: The underlying whose bars stand for an option root's.
BAR_SYMBOL = {"XSP": "SPX", "SPXW": "SPX", "$SPX": "SPX", "NDXP": "NDX", "RUTW": "RUT"}
MARKET = "SPX"


def entry_day(record: dict) -> str | None:
    """The session the trade was entered: `entry_session`, else the entry timestamp's date, else
    (a 0DTE schema with neither) the session it closed."""
    for v in (record.get("entry_session"), record.get("entry_time"), record.get("session")):
        if v:
            return str(v)[:10]
    return None


def holding(record: dict) -> dict:
    """Holding period at exit: calendar days between entry and exit sessions, and minutes where both
    timestamps carry a time of day."""
    start, end = entry_day(record), record.get("session")
    days = None
    if start and end:
        days = (datetime.fromisoformat(str(end)[:10]) - datetime.fromisoformat(start)).days
    minutes = None
    et, xt = record.get("entry_time"), record.get("exit_time")
    if et and xt and len(str(et)) > 10 and len(str(xt)) > 10:
        try:
            delta = datetime.fromisoformat(str(xt)) - datetime.fromisoformat(str(et))
            minutes = round(delta.total_seconds() / 60)
        except ValueError:
            minutes = None
    return {"holding_days": days, "holding_minutes": minutes}


def _prior(bars, day: str) -> list:
    """Bars strictly before `day`, oldest first."""
    return [b for b in bars if b.date < day]


def _vs_sma(bars, n: int) -> float | None:
    if len(bars) < n:
        return None
    avg = sum(b.close for b in bars[-n:]) / n
    return round(bars[-1].close / avg - 1, 4) if avg else None


def name_context(bars, day: str) -> dict:
    """ATR % and relative volume of one name as of the close before `day`."""
    prior = _prior(bars, day)
    out = {"atr_pct": None, "rel_volume": None}
    if len(prior) >= 15:
        atr = indicators.atr([b.high for b in prior], [b.low for b in prior], [b.close for b in prior])[-1]
        if atr is not None and prior[-1].close:
            out["atr_pct"] = round(atr / prior[-1].close * 100, 3)
    if len(prior) >= 21:
        avg = sum(b.volume for b in prior[-21:-1]) / 20
        if avg > 0 and prior[-1].volume:
            out["rel_volume"] = round(prior[-1].volume / avg, 3)
    return out


def market_context(spx_bars, day: str) -> dict:
    prior = _prior(spx_bars, day)
    return {
        "spx_vs_50d": _vs_sma(prior, 50),
        "spx_vs_200d": _vs_sma(prior, 200),
        # The suite's one environment label (core.environment, B8), from the same prior closes.
        "environment": environment.classify([b.close for b in prior]),
    }


def context(record: dict, bars_of, sector_of: dict[str, str]) -> dict:
    """The full B4 context for one record. `bars_of(symbol)` returns adjusted bars oldest first (or
    []); `sector_of` maps a symbol to its sector."""
    day = entry_day(record)
    sym = str(record.get("symbol") or "").upper()
    base = BAR_SYMBOL.get(sym, sym)
    out = {
        "entry_day": day,
        "entry_time": record.get("entry_time"),
        "sector": sector_of.get(base),
        **holding(record),
    }
    if day is None:
        return {**out, "spx_vs_50d": None, "spx_vs_200d": None, "environment": None, "atr_pct": None,
                "rel_volume": None}  # fmt: skip
    return {**out, **market_context(bars_of(MARKET), day), **name_context(bars_of(base) if base else [], day)}


def annotate(records: list[dict], bars_of, sector_of: dict[str, str]) -> list[dict]:
    """`records` with their context merged in, bars read once per symbol."""
    cache: dict[str, list] = {}

    def cached(sym: str):
        if sym not in cache:
            cache[sym] = bars_of(sym)
        return cache[sym]

    return [{**r, **context(r, cached, sector_of)} for r in records]
