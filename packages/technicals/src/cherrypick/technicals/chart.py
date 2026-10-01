"""One chart file per name, for the console's chart page: our bars and readings beside the vendor's.

Phase 7's per-name view. For every stock the store holds, `charts/<SYMBOL>.json` carries the last
`DISPLAY` sessions of adjusted bars, the level grid those same sessions define, CCI (14 and the
scan rules' 5), RSI 14, both trend scores, and the scan-rule matches for every displayed session --
all from this package's engines, so the console draws them and computes nothing.

Where the vendor's chart for the name has been captured, the file also carries what the vendor
drew: its support, resistance and gap levels, each marked with whether OUR grid can produce it,
its trend grades by day and its rank, and how many of its bars agree with ours to the cent. Each
vendor level is marked with whether we can produce it: support and resistance against our grid, gap
levels against the edges of our own gaps. That comparison is the point of the view: level SELECTION
is unsolved (`levels.py`), so this draws our grid, not a set of levels we claim are the vendor's, and
a disagreement is visible rather than a number in a scorer's table.

The file is overwritten each session (it names its own session); the capture keeps its own date,
because a capture from last week is still last week's levels.

Record-only, like everything in the market report.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from . import indicators, levels, paths, signals, store, symbols, trend, vendor_check

CHART_VERSION = 2  # 2: gap levels are asked against our gap edges, no longer null
DISPLAY = levels.WINDOW  # the sessions the grid is built on, and so the ones worth drawing
# The index funds charted beside the stocks. Named, not taken from `store.stocks`: that filter
# drops funds on purpose, because breadth counts stocks only, and the chart page is not breadth.
INDEX_FUNDS = ("SPY", "QQQ", "IWM")


def _r(v: float | None, digits: int = 4) -> float | None:
    return None if v is None else round(v, digits)


def signal_days(highs, lows, closes, start: int = 0) -> list[dict]:
    """The scan rules each session from `start` matched, computed from whole-history series once
    rather than re-deriving `signals.readings` on every prefix (the same inputs, O(n) not O(n^2))."""
    short = trend.scores(closes, trend.SHORT_TERM)
    long_ = trend.scores(closes, trend.LONG_TERM)
    cci5 = indicators.cci(highs, lows, closes, 5)
    cci14 = indicators.cci(highs, lows, closes, 14)
    rsi14 = indicators.rsi(closes, 14)
    out = []
    for i in range(max(start, 1), len(closes)):
        r = signals.Readings(short[i], long_[i], cci5[i], cci5[i - 1], cci14[i], rsi14[i])
        if any(v is None for v in vars(r).values()):
            continue
        rules = signals.matches(r)
        if rules:
            out.append({"index": i, "rules": rules})
    return out


def _vendor(symbol: str, bars) -> dict | None:
    """The newest capture of the vendor's chart for `symbol`, or None."""
    root = paths.market_report_dir() / "vendor-charts"
    found = sorted(root.glob(f"????-??-??/{symbol}.json"))
    if not found:
        return None
    try:
        doc = json.loads(found[-1].read_text(encoding="utf-8"))
        why = doc["why"]
        quotes = why["historicalQuotes"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    through = quotes[-1]["date"][:10] if quotes else None
    upto = [b for b in bars if through is None or b.date <= through]
    ours = {b.date: {f: getattr(b, f) for f in vendor_check.FIELDS} for b in upto}
    agreement = vendor_check.compare(ours, quotes)
    # The grid as of the capture's own last bar -- the one the vendor's levels were drawn on.
    g = levels.grid([b.high for b in upto], [b.low for b in upto])
    gaps = levels.gap_edges([b.date for b in upto], [b.high for b in upto], [b.low for b in upto])
    sr = why.get("supportAndResistance") or {}
    lv = []
    for kind in ("support", "resistance", "gapSupport", "gapResistance"):
        for x in sr.get(kind) or []:
            if x.get("value") is None:
                continue
            date = str(x.get("date") or "")[:10] or None
            if kind in ("support", "resistance"):
                placed = g.explains(x["value"]) if g else None
            else:  # a gap level is asked whether it is an edge of one of our gaps, on the same bar
                placed = levels.places_gap(gaps, kind, x["value"], date) if upto else None
            # "on_our_grid" keeps its name for the file's readers; for a gap it means our gap edges.
            lv.append({"kind": kind, "value": x["value"], "date": date, "on_our_grid": placed})

    def series(key):
        rows = why.get(key) or []
        return sorted(
            ({"date": str(r["date"])[:10], "value": r["value"]} for r in rows if r.get("value") is not None),
            key=lambda r: r["date"],
        )

    return {
        "capture": found[-1].parent.name,
        "fetched_at": doc.get("fetched_at"),
        "through": through,
        "levels": lv,
        "rank": why.get("technicalRank"),
        "sentiment": why.get("sentiment"),
        "iv_rank": why.get("impliedVolatilityRank"),
        "trend_short": series("syrahSentimentShortTerm"),
        "trend_long": series("syrahSentimentLongTerm"),
        "bars_compared": agreement.get("prices"),
        "bars_agree": agreement.get("agree"),
        "grid": None if g is None else {"low": g.low, "high": g.high, "step": g.step},
    }


def _rank(conn, closes: list[float], session: str) -> int | None:
    """Our 1-10 rank on the session, against the market cut-offs the landing stored; None without them."""
    cutoffs = store.rank_cutoffs(conn, session)
    score = levels.rank_score(closes)
    return None if not cutoffs or score is None else levels.rank_from_cutoffs(score, cutoffs)


def build(conn, symbol: str, session: str | None = None) -> dict[str, Any] | None:
    bars = [b for b in store.adjusted_bars(conn, symbol) if session is None or b.date <= session]
    if len(bars) < 2:
        return None
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]
    start = max(0, len(bars) - DISPLAY)
    shown = bars[start:]
    g = levels.grid(highs, lows)
    grid = None
    if g is not None:
        window = bars[-levels.WINDOW :]
        grid = {
            "low": g.low,
            "high": g.high,
            "step": g.step,
            "low_date": min(window, key=lambda b: b.low).date,
            "high_date": max(window, key=lambda b: b.high).date,
            "window": levels.WINDOW,
        }

    def tail(xs, digits=2):
        return [_r(v, digits) for v in xs[start:]]

    return {
        "ok": True,
        "chart_version": CHART_VERSION,
        "symbol": symbol,
        "session": bars[-1].date,
        "generated_at": datetime.now(UTC).isoformat(),
        "bars": {
            "date": [b.date for b in shown],
            "open": [_r(b.open) for b in shown],
            "high": [_r(b.high) for b in shown],
            "low": [_r(b.low) for b in shown],
            "close": [_r(b.close) for b in shown],
            # An index has no volume: null, never the 0 the store's reader puts in its place.
            "volume": [None if symbol in symbols.INDEXES else round(b.volume) for b in shown],
        },
        "grid": grid,
        "cci14": tail(indicators.cci(highs, lows, closes, 14)),
        "cci5": tail(indicators.cci(highs, lows, closes, 5)),
        "rsi14": tail(indicators.rsi(closes, 14)),
        "trend_short": tail(trend.scores(closes, trend.SHORT_TERM), 0),
        "trend_long": tail(trend.scores(closes, trend.LONG_TERM), 0),
        "sentiment": trend.sentiment(closes),
        # The 1-10 rank is a decile across US-listed stocks; an index is not one of them.
        "rank": None if symbol in symbols.INDEXES else _rank(conn, closes, bars[-1].date),
        # Ours from Dolt's IV where it covers the name, else tastytrade's; `source` says which.
        "iv_rank": store.iv_rank(conn, symbol, bars[-1].date),
        "signals": [
            {"date": bars[s["index"]].date, "rules": s["rules"]}
            for s in signal_days(highs, lows, closes, start)
        ],
        "vendor": _vendor(symbol, bars),
        "record_only": True,
    }


def charts_dir():
    return paths.data_dir() / "charts"


def _write(path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)  # write-then-rename: a reader never sees half a chart


def write_all(session: str | None = None, conn=None) -> dict:
    """Every stock the store holds, the `INDEX_FUNDS` and the cash indexes, plus an index file the
    console's picker reads."""
    own = conn is None
    conn = conn or store.connect()
    written, sessions = [], set()
    names = store.stocks(conn, symbols.all_symbols())
    for sym in [*symbols.INDEXES, *INDEX_FUNDS, *(s for s in names if s not in INDEX_FUNDS)]:
        doc = build(conn, sym, session)
        if doc is None:
            continue
        _write(charts_dir() / f"{sym}.json", doc)
        written.append({"symbol": sym, "session": doc["session"], "vendor": doc["vendor"] is not None})
        sessions.add(doc["session"])
    if own:
        conn.close()
    index = {
        "chart_version": CHART_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "session": max(sessions) if sessions else None,
        "symbols": written,
    }
    _write(charts_dir() / "index.json", index)
    return {
        "charts": len(written),
        "with_vendor": sum(w["vendor"] for w in written),
        "session": index["session"],
    }
