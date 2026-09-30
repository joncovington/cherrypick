"""The chart layer scored against the vendor's own chart data: every capture on file.

Each capture carries the vendor's daily 1M and 6M trend scores for its name. This compares ours day
by day over the whole overlap: the exact score, the five-step label, and within one step -- and the
capture's overall sentiment label against ours on the same bars. Captures
grow nightly (the collector saves up to 40 names), so the same command re-checks the solved
construction (`trend.py`) on a widening set of names without any change here.
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
    sentiment = {"names": 0, "agree": 0, "misses": []}
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
        # The overall label, on our bars through the capture's own last session.
        through = str((why.get("historicalQuotes") or [{}])[-1].get("date", ""))[:10]
        theirs_label = why.get("sentiment")
        upto = [c for d, c in zip(dates, closes, strict=True) if d <= through]
        ours_label = trend.sentiment(upto) if upto else None
        if theirs_label and ours_label:
            sentiment["names"] += 1
            sentiment["agree"] += ours_label == theirs_label
            if ours_label != theirs_label:
                sentiment["misses"].append(f"{sym}: ours {ours_label}, vendor {theirs_label}")
        by_symbol[sym] = row
    conn.close()

    def rates(t):
        days = t["days"] or 1
        return {"days": t["days"], **{k: round(t[k] / days, 3) for k in ("exact", "label", "within1")}}

    sentiment["rate"] = round(sentiment["agree"] / sentiment["names"], 3) if sentiment["names"] else None
    return {
        "symbols": len(by_symbol),
        **{k: rates(v) for k, v in totals.items()},
        "sentiment": sentiment,
        "by_symbol": by_symbol,
    }


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


def _agreeing(conn, min_bar_agreement: float, skipped: list):
    """(symbol, capture, our bars through the capture) for each name whose adjusted bars agree with
    the vendor's to the cent -- a grid is only as exact as the bars under it. The rest go to
    `skipped`."""
    from . import vendor_check

    for sym, why in sorted(_captures().items()):
        bars = [
            b for b in store.adjusted_bars(conn, sym) if b.date <= why["historicalQuotes"][-1]["date"][:10]
        ]
        ours = {b.date: {f: getattr(b, f) for f in vendor_check.FIELDS} for b in bars}
        agreement = vendor_check.compare(ours, why["historicalQuotes"])
        if not agreement["prices"] or agreement["agree"] / agreement["prices"] < min_bar_agreement:
            skipped.append(sym)
            continue
        yield sym, why, bars


def score_levels(min_bar_agreement: float = 0.99) -> dict:
    """How many of the vendor's levels we can produce, on the names whose adjusted bars agree with the
    vendor's to the cent: support and resistance on our grid, gap levels as edges of our own gaps.
    This checks placement, not selection; `score_level_selection` measures selection."""
    from . import levels

    conn = store.connect()
    names, placed, total, gap_placed, gap_total, skipped = 0, 0, 0, 0, 0, []
    for _sym, why, bars in _agreeing(conn, min_bar_agreement, skipped):
        g = levels.grid([b.high for b in bars], [b.low for b in bars])
        sr = why.get("supportAndResistance") or {}
        values = [x["value"] for k in ("support", "resistance") for x in sr.get(k) or []]
        edges = levels.gap_edges([b.date for b in bars], [b.high for b in bars], [b.low for b in bars])
        for k in ("gapSupport", "gapResistance"):
            for x in sr.get(k) or []:
                gap_total += 1
                gap_placed += levels.places_gap(edges, k, x["value"], str(x.get("date") or "")[:10])
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
        "gap_levels": gap_total,
        "gap_placed": gap_placed,
        "gap_rate": round(gap_placed / gap_total, 4) if gap_total else None,
        "skipped_bars_disagree": skipped,
    }


def score_level_selection(min_bar_agreement: float = 0.99) -> dict:
    """The properties of the grid points the vendor draws, each beside its chance baseline, over the
    same names `score_levels` places levels on (`level_selection` says what each measure means)."""
    from . import level_selection, levels

    conn = store.connect()
    tally, names, skipped = level_selection.Tally(), 0, []
    for _sym, why, bars in _agreeing(conn, min_bar_agreement, skipped):
        window = bars[-levels.WINDOW :]
        g = levels.grid([b.high for b in bars], [b.low for b in bars])
        sr = why.get("supportAndResistance") or {}
        vendor = [
            (x["value"], str(x.get("date") or "")[:10])
            for k in ("support", "resistance")
            for x in sr.get(k) or []
        ]
        if g is None or not vendor:
            continue
        names += 1
        level_selection.measure(
            [b.date for b in window],
            [b.high for b in window],
            [b.low for b in window],
            [b.close for b in window],
            [b.volume for b in window],
            g,
            vendor,
            tally,
        )
    conn.close()
    return {"names": names, **level_selection.summary(tally), "skipped_bars_disagree": skipped}


def score_rank() -> dict:
    """Our 1-10 rank against the vendor's on every capture whose session has market cut-offs: the
    capture's own score (our adjusted bars through its last session) placed against the cut-offs
    the landing stored. Captures on a session without cut-offs are counted, not scored."""
    from collections import Counter

    from . import levels

    root = paths.market_report_dir() / "vendor-charts"
    conn = store.connect()
    names = exact = within = 0
    diff: Counter = Counter()
    no_cutoffs: set[str] = set()
    for path in sorted(root.glob("????-??-??/*.json")):
        if path.name == "trade-ideas.json" or path.name.endswith(".rejected.json"):
            continue
        try:
            why = json.loads(path.read_text(encoding="utf-8"))["why"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        theirs = why.get("technicalRank")
        if not isinstance(theirs, (int, float)):
            continue
        session = why["historicalQuotes"][-1]["date"][:10]
        cutoffs = store.rank_cutoffs(conn, session)
        if not cutoffs:
            no_cutoffs.add(session)
            continue
        sc = levels.rank_score([b.close for b in store.adjusted_bars(conn, path.stem) if b.date <= session])
        if sc is None:
            continue
        ours = levels.rank_from_cutoffs(sc, cutoffs)
        names += 1
        exact += ours == int(theirs)
        within += abs(ours - int(theirs)) <= 1
        diff[int(theirs) - ours] += 1
    conn.close()
    return {
        "captures": names,
        "exact": exact,
        "within_one": within,
        "vendor_minus_ours": dict(sorted(diff.items())),
        "sessions_without_cutoffs": sorted(no_cutoffs),
    }


def score_signals() -> dict:
    """Our scan rules against every scan list the collector has saved: per rule, the vendor's flagged
    names we catch, and the stocks we flag that it did not (among those the store holds)."""
    from . import signals, symbols

    root = paths.market_report_dir() / "vendor-charts"
    conn = store.connect()
    stocks = store.stocks(conn, symbols.candidates())
    per_rule = {r: {"flagged": 0, "caught": 0, "false": 0} for r in signals.RULES}
    sessions = []
    for path in sorted(root.glob("????-??-??/trade-ideas.json")):
        try:
            ideas = json.loads(path.read_text(encoding="utf-8")).get("tradeIdeas") or []
        except (OSError, ValueError):
            continue
        day = path.parent.name
        theirs: dict[str, set] = {}
        for t in ideas:
            sym = str(t.get("symbol", "")).split(".")[0]
            for rule in t.get("rules") or []:
                theirs.setdefault(sym, set()).add(rule.get("ruleMatch"))
        ours: dict[str, set] = {}
        for sym in sorted(set(theirs) | set(stocks)):
            bars = [b for b in store.adjusted_bars(conn, sym) if b.date <= day]
            if not bars or bars[-1].date != day:
                continue
            r = signals.readings([b.high for b in bars], [b.low for b in bars], [b.close for b in bars])
            if r is not None:
                ours[sym] = set(signals.matches(r))
        for rule in signals.RULES:
            flagged = {s for s, rs in theirs.items() if rule in rs and s in ours}
            mine = {s for s, rs in ours.items() if rule in rs}
            per_rule[rule]["flagged"] += len(flagged)
            per_rule[rule]["caught"] += len(flagged & mine)
            per_rule[rule]["false"] += len(mine - {s for s, rs in theirs.items() if rule in rs})
        sessions.append(day)
    conn.close()
    flagged = sum(v["flagged"] for v in per_rule.values())
    caught = sum(v["caught"] for v in per_rule.values())
    return {
        "sessions": sessions,
        "caught": caught,
        "flagged": flagged,
        "rate": round(caught / flagged, 3) if flagged else None,
        "by_rule": per_rule,
    }
