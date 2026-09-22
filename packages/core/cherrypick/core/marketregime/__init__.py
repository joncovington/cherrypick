"""The daily-bar market regime: what KIND of market a session sat in, derived and never recorded.

Two axes, the ones a regime read actually turns on. **Trend** is price against a moving average
that is itself rising or falling, confirmed by the session structure (higher highs and higher lows,
or their mirror). **Volatility** is where this stretch's average true range sits in its own trailing
year — expanding or compressed, which decides how much room the same structure needs.

Distinct from `cherrypick.core.regime`, which is the INTRADAY join: a timestamp against a ~1-minute
vol-complex series, with a staleness bound. This module shares none of that — different table,
different key, different refusal — and answers a different question: not "what was the tape doing
at 10:42" but "what kind of market has this been for the last N sessions".

**Derived at read time, recorded nowhere.** The same rule `core.regime` states: a stored label
freezes a derivation that can otherwise be re-cut forever, and every threshold here is a current
best estimate rather than a calibrated constant. The inputs are daily OHLC bars the streamer
already backfills from DXLink daily candles; nothing new is collected to produce this.

**No look-ahead, by construction.** A session's label is computed from bars STRICTLY BEFORE it, so
a label is safe to attach to a decision taken during that session. `as_of=True` opts into the
post-hoc reading (bars through the session itself) and is never the default.

Why it exists: on 2026-09-22 this classification over 1,334 sessions of SPX history showed the
market down-trending about 16% of the time and the suite's entire 45-session flies era containing
ZERO down-trending sessions, while carrying four times the historical share of compressed,
range-bound ones. Every completion rate and every regime cut the suite has published rests on that
one stretch. This module exists so that caveat is a measured number a reader can re-derive, rather
than an impression.

It is a description of the market, **not a P&L claim and not a signal**: no trade was priced on any
of these sessions, and nothing here says a regime is tradeable.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from cherrypick.core import db as _db
from cherrypick.core import home as _home
from cherrypick.core import regimecuts as _rc
from cherrypick.core import streamcache as _sc

HISTORY_READ_CAP = 5000
PERCENTILE_LOOKBACK = 252
HIGH_VOL_PERCENTILE = 0.5

# Three horizons off one classifier rather than resampling to calendar weeks: a week is not a fixed
# number of sessions (holidays, early closes), so resampling would make a four-session holiday week
# and a five-session ordinary one the same object. Windows in SESSIONS say what they mean.
HORIZONS: dict[str, dict[str, int]] = {
    "session": {"sma": 20, "atr": 10, "structure": 10, "slope": 5},
    "swing": {"sma": 50, "atr": 20, "structure": 20, "slope": 10},
    "primary": {"sma": 200, "atr": 60, "structure": 60, "slope": 20},
}

TRENDS = ("uptrend", "downtrend", "range")
VOLS = ("high", "low")


def true_ranges(bars: list[dict]) -> list[float | None]:
    """Per-bar true range, aligned with `bars`. The first bar has no prior close, so it is None
    rather than `high - low`: a range measured against a close we do not have is a different
    measure wearing the same name.

    The same arithmetic as `meic.tt._true_ranges`, which lives behind a subprocess and cannot be
    imported; this is the shared home and that one can adopt it.
    """
    out: list[float | None] = []
    for index, bar in enumerate(bars):
        if index == 0:
            out.append(None)
            continue
        prior = bars[index - 1]["close"]
        out.append(max(bar["high"] - bar["low"], abs(bar["high"] - prior), abs(bar["low"] - prior)))
    return out


def _mean(values: list[float | None], end: int, window: int) -> float | None:
    """Mean of `window` values ending at `end` inclusive, or None if any is missing."""
    if end + 1 < window:
        return None
    chunk = values[end - window + 1 : end + 1]
    if any(v is None for v in chunk):
        return None
    return statistics.fmean([v for v in chunk if v is not None])


def percentile_rank(values: list[float], value: float) -> float:
    """Share of `values` strictly below `value`. The convention `overview.score.percentile_rank`
    uses; twins to fold when either is next touched."""
    if not values:
        return 0.0
    return sum(1 for v in values if v < value) / len(values)


def _structure(bars: list[dict], end: int, window: int) -> tuple[bool, bool]:
    """(higher_highs_or_lows, lower_lows_or_highs) comparing the recent half of a window against
    its older half. Two halves rather than first-vs-last bar because one spike should not decide
    what a month looked like."""
    if end + 1 < window:
        return (False, False)
    seg = bars[end - window + 1 : end + 1]
    half = len(seg) // 2
    older, recent = seg[:half], seg[half:]
    hh = max(b["high"] for b in recent) > max(b["high"] for b in older)
    hl = min(b["low"] for b in recent) > min(b["low"] for b in older)
    ll = min(b["low"] for b in recent) < min(b["low"] for b in older)
    lh = max(b["high"] for b in recent) < max(b["high"] for b in older)
    return (hh or hl, ll or lh)


def classify(bars: list[dict], horizon: str = "swing") -> dict[str, Any]:
    """Label the state the bars END in, using every bar given. Returns the label plus the measures
    behind it, so a reader can see WHY rather than take the word for it.

    Refuses rather than guesses: too few bars for the horizon's own windows, or for a percentile to
    mean anything, is `unmeasured` with the reason — never a label computed on a short window that
    reads identically to one computed on a long one.
    """
    spec = HORIZONS[horizon]
    need = max(spec["sma"], spec["atr"] + 1, spec["structure"], spec["slope"] + spec["sma"])
    if len(bars) < need:
        return {"status": "unmeasured", "reason": f"needs {need} bars, has {len(bars)}"}
    end = len(bars) - 1
    closes = [b["close"] for b in bars]
    trs = true_ranges(bars)

    atr = _mean(trs, end, spec["atr"])
    if atr is None:
        return {"status": "unmeasured", "reason": "true range is incomplete over the window"}
    window = range(max(0, end - PERCENTILE_LOOKBACK + 1), end + 1)
    history = [a for i in window if (a := _mean(trs, i, spec["atr"])) is not None]
    wanted = spec["atr"] * 2
    if len(history) < wanted:
        return {
            "status": "unmeasured",
            "reason": f"needs {wanted} ATR samples for a percentile, has {len(history)}",
        }
    atr_pct = percentile_rank(history, atr)

    sma = statistics.fmean(closes[end - spec["sma"] + 1 : end + 1])
    prior_sma = statistics.fmean(closes[end - spec["slope"] - spec["sma"] + 1 : end - spec["slope"] + 1])
    slope = sma - prior_sma
    rising, falling = _structure(bars, end, spec["structure"])
    above = closes[end] > sma

    if above and slope > 0 and rising:
        trend = "uptrend"
    elif not above and slope < 0 and falling:
        trend = "downtrend"
    else:
        trend = "range"
    vol = "high" if atr_pct >= HIGH_VOL_PERCENTILE else "low"
    return {
        "status": "measured",
        "session": bars[end]["session"],
        "horizon": horizon,
        "trend": trend,
        "vol": vol,
        "quadrant": f"{trend}/{vol}",
        "close": round(closes[end], 4),
        "sma": round(sma, 4),
        "sma_slope": round(slope, 4),
        "atr": round(atr, 4),
        "atr_percentile": round(atr_pct, 4),
        "bars": len(bars),
    }


def series(bars: list[dict], horizon: str = "swing", *, as_of: bool = False) -> list[dict]:
    """One label per session, oldest first, each computed from the bars BEFORE it.

    `as_of=True` includes the session's own bar — the post-hoc reading. The default excludes it, so
    a label can be attached to a decision made during that session without grading it against a
    close nobody had yet.
    """
    out = []
    for index, bar in enumerate(bars):
        window = bars[: index + 1] if as_of else bars[:index]
        got = classify(window, horizon)
        if got.get("status") != "measured":
            continue
        out.append({**got, "session": bar["session"]})
    return out


def distribution(labels: list[dict]) -> dict[str, Any]:
    """How the labelled sessions split across the quadrants, with shares. The block that makes a
    sample's regime breadth a number instead of an impression."""
    counts = Counter(row["quadrant"] for row in labels)
    total = sum(counts.values())
    return {
        "sessions": total,
        "quadrants": {
            key: {"sessions": value, "share": round(value / total, 4) if total else None}
            for key, value in sorted(counts.items(), key=lambda kv: -kv[1])
        },
    }


def _data_dir() -> Path:
    return _home.data_dir("marketregime")


def build(
    conn,
    symbol: str = "SPX",
    *,
    through: str | None = None,
    horizon: str = "swing",
    as_of: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """The artifact: every session this symbol's bars can label, and how they split."""
    session = through or _dt.date.today().isoformat()
    bars = _sc.daily_bars(conn, symbol, through=session, limit=HISTORY_READ_CAP) if conn else []
    labels = series(bars, horizon, as_of=as_of)
    latest = classify(bars, horizon) if bars else {"status": "unmeasured", "reason": "no bars"}
    return {
        "module": "marketregime",
        "symbol": symbol,
        "session": session,
        "horizon": horizon,
        "as_of": bool(as_of),
        "generated_at": generated_at or _dt.datetime.now(tz=_dt.UTC).isoformat(),
        "latest": latest,
        "coverage": {
            "bars": len(bars),
            "first_bar": bars[0]["session"] if bars else None,
            "last_bar": bars[-1]["session"] if bars else None,
            "labelled": len(labels),
        },
        "distribution": distribution(labels),
        "horizons": {name: classify(bars, name) for name in HORIZONS},
        "series": labels,
        "not_pnl": "A description of the market, not a P&L claim: no trade was priced on these sessions.",
        "no_look_ahead": (
            "Each session's label is computed from bars strictly before it unless as_of is true."
        ),
    }


LATEST_NAME = "market_regime.json"


def write(doc: dict) -> dict:
    """Dated file always; `market_regime.json` only when this session is at or after the one
    already there, so a re-cut of a past day lands beside the current one and never over it.

    `regimecuts.write_artifact` states the same rule but hardcodes its own filenames, so the rule
    is restated here over its shared `write_json_atomic` rather than bent to serve two artifacts.
    """
    directory = _data_dir()
    session = str(doc["session"])
    dated = _rc.write_json_atomic(directory / f"market_regime-{session}.json", doc)
    current = None
    try:
        with open(directory / LATEST_NAME, encoding="utf-8") as handle:
            current = json.load(handle).get("session")
    except (OSError, ValueError, AttributeError):
        current = None
    latest = None
    if current is None or session >= str(current):
        latest = _rc.write_json_atomic(directory / LATEST_NAME, doc)
    return {"dated": str(dated), "latest": str(latest) if latest else None}


def _cache_path() -> Path:
    return _home.data_dir("marketdata") / "stream_cache.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cherrypick.core.marketregime", description=__doc__)
    parser.add_argument("--symbol", default="SPX")
    parser.add_argument("--through", default=None, help="classify sessions up to this date (exclusive)")
    parser.add_argument("--horizon", default="swing", choices=sorted(HORIZONS))
    parser.add_argument("--as-of", action="store_true", help="include each session's own bar (post-hoc)")
    parser.add_argument("--cache", default=None, help="stream cache path")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--series", action="store_true", help="include the per-session series in stdout")
    args = parser.parse_args(argv)

    path = Path(args.cache) if args.cache else _cache_path()
    try:
        conn = _db.connect_ro(path)
    except Exception as exc:  # noqa: BLE001 -- an unreadable cache is a reported state, not a crash
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "cache": str(path)}))
        return 2
    doc = build(conn, args.symbol, through=args.through, horizon=args.horizon, as_of=args.as_of)
    written = write(doc) if args.write else None
    out = {k: v for k, v in doc.items() if k != "series" or args.series}
    print(json.dumps({"ok": True, "written": written, **out}, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
