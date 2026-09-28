"""The chart layer scored against the vendor's own chart data: every capture on file.

Each capture carries the vendor's daily 1M and 6M trend scores for its name. This compares ours day
by day over the whole overlap: the exact score, the five-step label, and within one step. Captures
grow nightly (the collector saves up to 40 names), so the same command measures the baseline on a
widening set of names without any change here.
"""

from __future__ import annotations

import json

from . import paths, store, trend

SERIES = (
    ("short", "syrahSentimentShortTerm", trend.SHORT_TERM),
    ("long", "syrahSentimentLongTerm", trend.LONG_TERM),
)


def score_trends() -> dict:
    root = paths.market_report_dir() / "vendor-charts"
    newest = {}
    for path in sorted(root.glob("????-??-??/*.json")):
        if path.name != "trade-ideas.json":
            newest[path.stem] = path
    conn = store.connect()
    totals = {k: {"days": 0, "exact": 0, "label": 0, "within1": 0} for k, _, _ in SERIES}
    by_symbol = {}
    for sym, path in sorted(newest.items()):
        try:
            why = json.loads(path.read_text(encoding="utf-8"))["why"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        bars = store.adjusted_bars(conn, sym)
        if not bars:
            continue
        dates = [b.date for b in bars]
        closes = [b.close for b in bars]
        row = {}
        for key, field, spec in SERIES:
            ours = dict(zip(dates, trend.scores(closes, spec), strict=True))
            theirs = {
                p["date"][:10]: p["value"]
                for p in why.get(field) or []
                if isinstance(p.get("value"), (int, float))
            }
            pairs = [(ours[d], v) for d, v in theirs.items() if ours.get(d) is not None]
            t = {
                "days": len(pairs),
                "exact": sum(a == b for a, b in pairs),
                "label": sum(trend.label(a) == trend.label(b) for a, b in pairs),
                "within1": sum(abs(a - b) <= 1 for a, b in pairs),
            }
            row[key] = t
            for k in t:
                totals[key][k] += t[k]
        by_symbol[sym] = row
    conn.close()

    def rates(t):
        days = t["days"] or 1
        return {"days": t["days"], **{k: round(t[k] / days, 3) for k in ("exact", "label", "within1")}}

    return {"symbols": len(by_symbol), **{k: rates(v) for k, v in totals.items()}, "by_symbol": by_symbol}


def _captures() -> dict:
    root = paths.market_report_dir() / "vendor-charts"
    newest = {}
    for path in sorted(root.glob("????-??-??/*.json")):
        if path.name != "trade-ideas.json" and not path.name.endswith(".rejected.json"):
            newest[path.stem] = path
    out = {}
    for sym, path in newest.items():
        try:
            out[sym] = json.loads(path.read_text(encoding="utf-8"))["why"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def score_levels(min_bar_agreement: float = 0.99) -> dict:
    """How many of the vendor's levels our grid can produce, on the names whose adjusted bars agree
    with the vendor's to the cent (a grid is only as exact as the bars under it). Levels not yet
    reproduced as a SET -- this checks placement, not selection."""
    from . import levels, vendor_check

    conn = store.connect()
    names, placed, total, skipped = 0, 0, 0, []
    for sym, why in sorted(_captures().items()):
        bars = [
            b for b in store.adjusted_bars(conn, sym) if b.date <= why["historicalQuotes"][-1]["date"][:10]
        ]
        ours = {b.date: {f: getattr(b, f) for f in vendor_check.FIELDS} for b in bars}
        agreement = vendor_check.compare(ours, why["historicalQuotes"])
        if not agreement["prices"] or agreement["agree"] / agreement["prices"] < min_bar_agreement:
            skipped.append(sym)
            continue
        g = levels.grid([b.high for b in bars], [b.low for b in bars])
        sr = why.get("supportAndResistance") or {}
        values = [x["value"] for k in ("support", "resistance") for x in sr.get(k) or []]
        if g is None or not values:
            continue
        names += 1
        total += len(values)
        placed += sum(g.explains(v) for v in values)
    conn.close()
    return {
        "names": names,
        "levels": total,
        "placed": placed,
        "rate": round(placed / total, 4) if total else None,
        "skipped_bars_disagree": skipped,
    }


def score_rank() -> dict:
    """Our 1-10 rank against the vendor's, ranked across the stocks the store holds."""
    from . import levels, symbols

    caps = _captures()
    conn = store.connect()
    names = set(store.stocks(conn, symbols.candidates())) | set(caps)
    session = max(why["historicalQuotes"][-1]["date"][:10] for why in caps.values()) if caps else None
    returns = {}
    for sym in names:
        closes = [b.close for b in store.adjusted_bars(conn, sym) if session and b.date <= session]
        if len(closes) > levels.RANK_SESSIONS:
            returns[sym] = closes[-1] / closes[-1 - levels.RANK_SESSIONS] - 1
    conn.close()
    ours = levels.rank(returns)
    pairs = [
        (ours[s], int(w["technicalRank"]))
        for s, w in caps.items()
        if s in ours and isinstance(w.get("technicalRank"), (int, float))
    ]
    return {
        "session": session,
        "ranked_universe": len(returns),
        "names": len(pairs),
        "exact": sum(a == b for a, b in pairs),
        "within_one": sum(abs(a - b) <= 1 for a, b in pairs),
    }
