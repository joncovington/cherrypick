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
