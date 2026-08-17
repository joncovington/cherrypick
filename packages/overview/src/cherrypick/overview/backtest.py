"""Recompute the deployment score across history and report what its zones would have separated.

This is the read side of the score, and it exists to answer one question before anyone is allowed
to act on the number: **did the zones ever mark anything apart?** A score that puts 95% of sessions
in FULL DEPLOY is not a regime signal, it is a constant with extra steps, and the distribution
block here is the first thing to read.

Three rules make the answer honest:

- **No look-ahead, enforced by construction.** A session's zone comes from the score computed on
  the session BEFORE it, because that is the only score a pre-open reader could have had. The
  scored day and the day whose return it is credited with are never the same day, and
  ``test_backtest.py`` pins that off-by-one rather than trusting the comment.
- **This is SPX, not the suite's P&L.** The forward return is the index's own next-session move, a
  benchmark for whether the zones separate market regimes at all. It is emphatically NOT what any
  module would have made -- the suite sells options, whose returns are not the index's, and no
  trade was taken on any of these days. Every field here is named ``spx_*`` for that reason.
- **A day that could not be scored is reported, not skipped quietly.** Each scored day needs a full
  year of history behind it, so the first ~252 sessions of any series are warmup and produce
  nothing. The result carries the requested span, the scored span, and the reason for the gap.

The score itself is recomputed through ``score.evaluate`` -- the same pure function the live pack
calls, given a historical day's readings and a history sliced to end on that day. A second
implementation of the blend would be a second opinion free to drift from the one being tested.
"""

from __future__ import annotations

import json
from statistics import fmean, median
from typing import Any

from . import paths as _paths
from . import score as _score
from . import symbols as _symbols

# The score needs a trailing year behind each day it scores; anything shorter is warmup.
WARMUP_SESSIONS = _score.PERCENTILE_LOOKBACK

# How far back the backtest will READ. Deliberately decoupled from what the module asks the producer
# to backfill: the request is a load decision made against a live cache every module depends on,
# while this is a read over rows that already exist. A generous cap costs one query, so the backtest
# scores whatever landed rather than being clipped to the size of the request that fetched it.
BACKTEST_MAX_DAYS = 5000

ZONES = ("full", "reduced", "defensive")


def _sessions(history: dict[str, list[dict]]) -> list[str]:
    """Every session any symbol has a close for, in order. The union rather than an intersection:
    a signal missing one symbol goes UNKNOWN and the blend renormalizes, which is the behaviour
    being tested -- an intersection would silently test only the days where everything was clean."""
    days: set[str] = set()
    for series in history.values():
        days.update(row["session"] for row in series)
    return sorted(days)


def _as_of(history: dict[str, list[dict]], session: str) -> dict[str, list[dict]]:
    """History truncated to end on `session` inclusive -- what a reader on that day could see."""
    return {
        symbol: [row for row in series if row["session"] <= session]
        for symbol, series in history.items()
    }


def _readings_on(history: dict[str, list[dict]], session: str) -> dict[str, Any]:
    """The current-quote readings the score expects, taken from that day's closes.

    Pre-open the live pack reads VIX and VIX3M as quotes; a historical day has only its close, and
    the close is the honest stand-in -- it is what the score would have blended had it run at that
    session's end.
    """
    readings: dict[str, Any] = {}
    for key, symbol in (("vix", "VIX"), ("vix3m", "VIX3M")):
        match = [row["close"] for row in history.get(symbol) or [] if row["session"] == session]
        readings[key] = {"value": match[-1] if match else None}
    return readings


def score_series(history: dict[str, list[dict]], sector_symbols) -> list[dict]:
    """One entry per session that could be scored: {session, score, zone, signals_measured}."""
    out: list[dict] = []
    for session in _sessions(history):
        sliced = _as_of(history, session)
        if len(sliced.get("VIX") or []) < WARMUP_SESSIONS:
            continue  # inside the warmup -- no year of history behind this day yet
        block = _score.evaluate(_readings_on(history, session), sliced, sector_symbols)
        if block.get("score") is None:
            continue
        out.append({
            "session": session,
            "score": block["score"],
            "zone": block["zone"],
            "signals_measured": block["signals_measured"],
        })
    return out


def _spx_returns(history: dict[str, list[dict]]) -> dict[str, float]:
    """Next-session SPX return keyed by the session it FOLLOWS: returns[D] is the move from D's
    close to the next session's close. Keying it this way is what makes the join below unable to
    look ahead -- a zone decided on D is credited with the move that happens after D."""
    series = history.get("SPX") or []
    out: dict[str, float] = {}
    for earlier, later in zip(series, series[1:], strict=False):
        if earlier["close"]:
            out[earlier["session"]] = (later["close"] / earlier["close"] - 1.0) * 100.0
    return out


def _distribution(scores: list[float]) -> dict[str, Any]:
    if not scores:
        return {"min": None, "median": None, "max": None, "mean": None}
    return {
        "min": round(min(scores), 1),
        "median": round(median(scores), 1),
        "max": round(max(scores), 1),
        "mean": round(fmean(scores), 1),
    }


def run(history: dict[str, list[dict]], sector_symbols) -> dict[str, Any]:
    """The zone-overlay result: per-zone SPX forward returns, plus the diagnostics that say whether
    the zones separated anything at all."""
    scored = score_series(history, sector_symbols)
    returns = _spx_returns(history)
    all_sessions = _sessions(history)

    # The join. Each scored day D contributes the move AFTER D -- so the zone is always decided
    # before the return it is credited with exists.
    rows: list[dict] = []
    for entry in scored:
        forward = returns.get(entry["session"])
        if forward is None:
            continue  # the last scored day has no session after it yet
        rows.append({**entry, "spx_forward_return_pct": round(forward, 4)})

    zones: dict[str, Any] = {}
    for zone in ZONES:
        moves = [row["spx_forward_return_pct"] for row in rows if row["zone"] == zone]
        zones[zone] = {
            "sessions": len(moves),
            "share_pct": round(len(moves) / len(rows) * 100.0, 1) if rows else None,
            "spx_mean_forward_return_pct": round(fmean(moves), 4) if moves else None,
            "spx_median_forward_return_pct": round(median(moves), 4) if moves else None,
            "spx_positive_share_pct": (round(sum(1 for m in moves if m > 0) / len(moves) * 100.0, 1)
                                       if moves else None),
        }

    return {
        "sessions_available": len(all_sessions),
        "sessions_scored": len(scored),
        "sessions_joined": len(rows),
        "warmup_sessions": WARMUP_SESSIONS,
        "unscored_reason": (
            f"each scored day needs {WARMUP_SESSIONS} sessions of history behind it; the earliest "
            "sessions in the series are warmup and a symbol whose candles do not reach back far "
            "enough shortens the scored span further"
        ),
        "score_distribution": _distribution([row["score"] for row in rows]),
        "zones": zones,
        "series": rows,
        "benchmark": "SPX next-session close-to-close change, in percent",
        "not_pnl": (
            "an index benchmark, not suite P&L -- no trade was taken on any of these sessions, and "
            "the suite sells options, whose returns are not the index's"
        ),
        "no_look_ahead": (
            "a session's zone comes from the score computed on the previous session; the scored "
            "day and the day whose return it is credited with are never the same day"
        ),
    }


def write(result: dict[str, Any]) -> str:
    """Atomic tmp-then-replace into the overview home, same as the fact pack's writer.

    One file, overwritten: this is a recomputation over stored history, not a per-session record,
    so a dated series of them would be a series of near-identical files.
    """
    path = _paths.data_dir() / "score-history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return str(path)


def build(session: str | None = None) -> dict[str, Any]:
    """Read the stored history and run the overlay. Imported here rather than at module scope so
    the pure functions above stay testable without a cache on disk."""
    from datetime import UTC, datetime

    from cherrypick.core import db as _db

    from . import facts as _facts

    session = session or _facts.default_session()
    try:
        cache = _db.connect_ro(_paths.stream_cache_db())
    except Exception:  # noqa: BLE001 -- no cache is an empty result, not a crash
        cache = None
    try:
        # Read every row the cache holds, not the request size. The request bounds what we ASK the
        # producer to backfill; the backtest should score whatever actually landed, which may be
        # more (a candle feed over-delivers) or less (it could not reach that far back).
        history = _facts._close_history(cache, _symbols.HISTORY_DAYS, session, BACKTEST_MAX_DAYS)
    finally:
        if cache is not None:
            cache.close()

    result = run(history, _symbols.SECTOR_ETFS)
    result["session"] = session
    result["generated_at"] = datetime.now(tz=UTC).isoformat()
    result["history_requested"] = _symbols.HISTORY_LOOKBACK
    result["history_read_cap"] = BACKTEST_MAX_DAYS
    return result
