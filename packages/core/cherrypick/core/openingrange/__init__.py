"""The opening range: what the market did between 09:30 and 10:00 ET, derived and never recorded.

Both flies and MEIC are forbidden to enter before 10:00 (`no_entry_before` / `entry_window_start`),
which makes 09:30-10:00 a half-hour of information every entry decision could use and none
currently does. This module turns the gex recorder's spot trail into six 5-minute bars and a small
set of features over them, so the question "does the open say anything about the session" can be
asked against recorded rows instead of intuition.

**A declared holdout, not a pre-registration, and the difference is the point.** The feature set
below was chosen after examining 38 sessions of this same data, so those sessions are IN-SAMPLE.
What is frozen on the declaration date is the DEFINITIONS -- not the names -- and every session
after it is out-of-sample. `packages/flies/docs/openingrange.md` holds the contract: the outcomes
in causal order, the sign expected per module, the decision rule, and what would retire it.
Calling this pre-registered would be the first dishonest sentence in the study.

**Reports, gates nothing.** No entry path reads this module. The route to a gate is the suite's
standing one -- report, then a declared paper arm beside control, then a bound -- and each step
gates the next.

Derived at read time and stored nowhere, per `core.regime`'s rule: a recorded label freezes a
derivation that could otherwise be re-cut forever. The spot trail is the record. Reading the gex
recorder's database is the suite's ordinary read-model relationship, the same thing `core.regime`
does, not an import reach-back.

**On the intraday U-shape.** An opening range is wide partly by deterministic seasonal (Wood,
McInish & Ord 1985; Andersen & Bollerslev 1997), which is a real objection to comparing DIFFERENT
times of day. It is not an objection here: every session is measured over the same 09:30-10:00
window, so the seasonal is held constant. Stated so nobody "fixes" it by deseasonalising a
comparison that does not need it.
"""

from __future__ import annotations

import datetime as _dt
import statistics
from zoneinfo import ZoneInfo

from cherrypick.core import marketregime as _mr
from cherrypick.core import metrics as _metrics
from cherrypick.core import streamcache as _sc

ET = ZoneInfo("America/New_York")

OPEN_MIN = 9 * 60 + 30
ENTRY_MIN = 10 * 60
CLOSE_MIN = 16 * 60
BUCKET_MINUTES = 5
ATR_WINDOW = 20

# The floor a cell must clear before a difference may be read off it, in SESSIONS: the suite's one
# `core.metrics.MIN_EFFECTIVE_N`, the bar meic and flies already read.
MIN_SESSIONS = _metrics.MIN_EFFECTIVE_N
# Out-of-sample sessions required before the headline split is evaluated at all. Higher than
# MIN_SESSIONS because the headline is a two-way split: each side needs its own floor.
MIN_OUT_OF_SAMPLE = 30


def _unmeasured(reason: str) -> dict:
    return {"status": "unmeasured", "reason": reason}


def _et_minute(ts: float) -> int:
    moment = _dt.datetime.fromtimestamp(float(ts), ET)
    return moment.hour * 60 + moment.minute


def bars_from_trail(
    rows,
    *,
    bucket_minutes: int = BUCKET_MINUTES,
    start_min: int = OPEN_MIN,
    end_min: int = ENTRY_MIN,
) -> list[dict]:
    """Spot ticks -> OHLC buckets on ET wall-clock boundaries, oldest first.

    `rows` are `(ts_epoch, spot)` pairs or mappings with those keys. Ticks outside
    `[start_min, end_min)` are ignored. A bucket with no tick is simply absent from the result --
    it is NOT interpolated, and `features` refuses a window that is missing one. Wall-clock ET via
    `zoneinfo` rather than a fixed offset, so a DST boundary cannot shift the window.
    """
    buckets: dict[int, list[tuple[float, float]]] = {}
    for row in rows:
        ts, spot = (row["ts"], row["spot"]) if isinstance(row, dict) else (row[0], row[1])
        try:
            ts, spot = float(ts), float(spot)
        except (TypeError, ValueError):
            continue
        if spot <= 0:
            continue
        minute = _et_minute(ts)
        if not start_min <= minute < end_min:
            continue
        buckets.setdefault((minute - start_min) // bucket_minutes, []).append((ts, spot))
    out = []
    for index in sorted(buckets):
        ticks = sorted(buckets[index])
        prices = [p for _, p in ticks]
        out.append(
            {
                "bucket": index,
                "minute": start_min + index * bucket_minutes,
                "open": prices[0],
                "high": max(prices),
                "low": min(prices),
                "close": prices[-1],
                "ticks": len(prices),
            }
        )
    return out


def features(
    bars: list[dict],
    *,
    atr: float | None,
    prior_close: float | None = None,
    prior_range: float | None = None,
    expected_buckets: int | None = None,
) -> dict:
    """The declared features over one session's opening bars. Frozen definitions -- see
    `packages/flies/docs/openingrange.md`; changing one of these is a new declaration, not a fix.

    Refuses rather than guesses: a window missing any bucket is `unmeasured`, because a range
    measured over four of six buckets is a different measure wearing the same name. Features that
    need an input we do not have (ATR, the prior session) are None individually while the rest
    still report -- a missing ATR is not a reason to lose the point count.
    """
    wanted = expected_buckets if expected_buckets is not None else (ENTRY_MIN - OPEN_MIN) // BUCKET_MINUTES
    if len(bars) < wanted:
        return _unmeasured(f"window has {len(bars)} of {wanted} buckets")

    high = max(b["high"] for b in bars)
    low = min(b["low"] for b in bars)
    or_points = high - low
    first = bars[0]["open"]
    last = bars[-1]["close"]
    closes = [b["close"] for b in bars]
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))

    usable_atr = atr if atr and atr > 0 else None
    return {
        "status": "measured",
        "high": round(high, 4),
        "low": round(low, 4),
        "first": round(first, 4),
        "last": round(last, 4),
        "or_points": round(or_points, 4),
        "or_atr": round(or_points / usable_atr, 4) if usable_atr else None,
        # Where the 09:55 close sits in the window: 1.0 means we finished the half-hour on its high.
        "position": round((last - low) / or_points, 4) if or_points > 0 else None,
        # Net displacement over distance travelled: 1.0 is a straight line, 0.0 a round trip.
        "efficiency": round(abs(last - first) / path, 4) if path > 0 else None,
        "gap_atr": (
            round((first - prior_close) / usable_atr, 4)
            if usable_atr and prior_close and prior_close > 0
            else None
        ),
        "or_vs_prior": (round(or_points / prior_range, 4) if prior_range and prior_range > 0 else None),
        "buckets": len(bars),
        "closes": [round(c, 4) for c in closes],
    }


def outcome(rows, *, atr: float | None, start_min: int = ENTRY_MIN, end_min: int = CLOSE_MIN) -> dict:
    """The primary outcome: how far spot travelled AFTER the entry window opened.

    Primary rather than the strategy result because it is the mechanism's own variable -- opening
    range forecasts session travel, and completion is downstream of travel -- and because it is
    measurable on every session with a trail, traded or not, which is where the extra n comes from.
    """
    prices = []
    for row in rows:
        ts, spot = (row["ts"], row["spot"]) if isinstance(row, dict) else (row[0], row[1])
        try:
            ts, spot = float(ts), float(spot)
        except (TypeError, ValueError):
            continue
        if spot > 0 and start_min <= _et_minute(ts) <= end_min:
            prices.append(spot)
    if not prices:
        return _unmeasured("no ticks after the entry window opened")
    travel = max(prices) - min(prices)
    return {
        "status": "measured",
        "rest_of_day_points": round(travel, 4),
        "rest_of_day_range_atr": round(travel / atr, 4) if atr and atr > 0 else None,
        "ticks": len(prices),
    }


def _daily_context(cache_conn, symbol: str, session: str) -> dict:
    """ATR20, the prior close and the prior session's range -- all from bars STRICTLY BEFORE
    `session`, so nothing the session itself did can leak into its own features."""
    if cache_conn is None:
        return {"atr": None, "prior_close": None, "prior_range": None, "regime": None}
    bars = _sc.daily_bars(cache_conn, symbol, through=session, limit=5000)
    if not bars:
        return {"atr": None, "prior_close": None, "prior_range": None, "regime": None}
    trs = [t for t in _mr.true_ranges(bars)[-ATR_WINDOW:] if t is not None]
    atr = statistics.fmean(trs) if len(trs) == ATR_WINDOW else None
    prior = bars[-1]
    label = _mr.classify(bars, "swing")
    return {
        "atr": atr,
        "prior_close": prior["close"],
        "prior_range": prior["high"] - prior["low"],
        "regime": label.get("quadrant") if label.get("status") == "measured" else None,
    }


def _trail(spot_conn, symbol: str, session: str) -> list[tuple[float, float]]:
    if spot_conn is None:
        return []
    try:
        rows = spot_conn.execute(
            "SELECT ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date = ? ORDER BY ts",
            (symbol, session),
        ).fetchall()
    except Exception:  # noqa: BLE001 -- an unreadable trail is an unmeasured session, not a crash
        return []
    return [(r[0], r[1]) for r in rows]


def build(spot_conn, cache_conn, *, session: str, symbol: str = "SPX") -> dict:
    """One session's opening-range facts, its outcome, and the regime it sat in."""
    trail = _trail(spot_conn, symbol, session)
    if not trail:
        return {"session": session, "symbol": symbol, **_unmeasured("no spot trail for this session")}
    context = _daily_context(cache_conn, symbol, session)
    bars = bars_from_trail(trail)
    opening = features(
        bars,
        atr=context["atr"],
        prior_close=context["prior_close"],
        prior_range=context["prior_range"],
    )
    return {
        "session": session,
        "symbol": symbol,
        "status": opening.get("status", "unmeasured"),
        "opening": opening,
        "outcome": outcome(trail, atr=context["atr"]),
        "regime": context["regime"],
        "atr": round(context["atr"], 4) if context["atr"] else None,
    }


def series(spot_conn, cache_conn, *, sessions, symbol: str = "SPX") -> list[dict]:
    """`build` over many sessions, in the order given. Unmeasured sessions are KEPT, with their
    reason: a session the recorder missed is a fact about coverage, and dropping it silently would
    make the sample look more complete than it is."""
    return [build(spot_conn, cache_conn, session=day, symbol=symbol) for day in sessions]


def study(
    rows: list[dict],
    *,
    feature: str = "or_atr",
    outcome_key: str = "rest_of_day_range_atr",
    in_sample_through: str | None = None,
    min_sessions: int = MIN_SESSIONS,
    conditioner: str | None = "regime",
) -> dict:
    """Split sessions at the feature's median and report each side -- the analysis DESIGN, frozen
    here so it cannot be shaped later by the data it will be run on.

    Deliberately stops short of a significance test. What needs pre-committing is which split,
    which floors, and what counts as readable; the estimator itself is a standard one named in the
    declaration (`meic.analytics.session_bootstrap`, the suite's session-axis bootstrap) and the
    caller runs it at checkpoint time over `paired`, which this returns. That also keeps a base-
    layer module from reaching into a trading package for an estimator.

    **Sessions are the unit, never entries.** A table of hundreds of entries over dozens of
    sessions reads as powerful and is not: entries inside one session share a tape. Every count
    here is sessions, and a cell under `min_sessions` reports `not_yet_readable` rather than a
    difference -- the same posture `regime_coverage`'s `effective_n` takes.

    `in_sample_through` marks the declared holdout boundary: sessions on or before it were used to
    choose these features and are reported separately, never pooled with the out-of-sample ones.
    """
    usable = []
    for row in rows:
        value = (row.get("opening") or {}).get(feature)
        result = (row.get("outcome") or {}).get(outcome_key)
        if row.get("status") != "measured" or value is None or result is None:
            continue
        usable.append(
            {
                "session": row["session"],
                "value": value,
                "outcome": result,
                "regime": row.get("regime"),
                "in_sample": bool(in_sample_through and row["session"] <= in_sample_through),
            }
        )

    out_of_sample = [r for r in usable if not r["in_sample"]]
    in_sample = [r for r in usable if r["in_sample"]]

    def _split(subset: list[dict], label: str) -> dict:
        if len(subset) < 2:
            return {"scope": label, "sessions": len(subset), "readable": False, "reason": "too few sessions"}
        cut = statistics.median([r["value"] for r in subset])
        low = [r for r in subset if r["value"] <= cut]
        high = [r for r in subset if r["value"] > cut]
        readable = len(low) >= min_sessions and len(high) >= min_sessions
        block = {
            "scope": label,
            "sessions": len(subset),
            "median": round(cut, 4),
            "low": {
                "sessions": len(low),
                "mean_outcome": round(statistics.fmean([r["outcome"] for r in low]), 4) if low else None,
            },
            "high": {
                "sessions": len(high),
                "mean_outcome": round(statistics.fmean([r["outcome"] for r in high]), 4) if high else None,
            },
            "readable": readable,
        }
        if not readable:
            block["reason"] = f"a side holds fewer than {min_sessions} sessions"
            block["not_yet_readable"] = True
        elif low and high:
            block["observed_diff"] = round(
                statistics.fmean([r["outcome"] for r in high])
                - statistics.fmean([r["outcome"] for r in low]),
                4,
            )
        # The paired per-session values a session-axis bootstrap needs, keyed by session.
        block["paired"] = {
            "high": {r["session"]: r["outcome"] for r in high},
            "low": {r["session"]: r["outcome"] for r in low},
        }
        return block

    result = {
        "feature": feature,
        "outcome": outcome_key,
        "min_sessions": min_sessions,
        "min_out_of_sample": MIN_OUT_OF_SAMPLE,
        "in_sample_through": in_sample_through,
        "coverage": {
            "usable_sessions": len(usable),
            "in_sample": len(in_sample),
            "out_of_sample": len(out_of_sample),
            "unmeasured": sum(1 for r in rows if r.get("status") != "measured"),
        },
        "headline": _split(out_of_sample, "out_of_sample"),
        "in_sample_reference": _split(in_sample, "in_sample"),
        "checkpoint_ready": len(out_of_sample) >= MIN_OUT_OF_SAMPLE,
        "not_pnl": "A description of the market and of completion rates; no trade was priced here.",
        "declared": "packages/flies/docs/openingrange.md",
    }
    if conditioner:
        cells = {}
        for row in out_of_sample:
            cells.setdefault(row.get(conditioner) or "unknown", []).append(row)
        result["by_" + conditioner] = {key: _split(value, key) for key, value in sorted(cells.items())}
    return result
