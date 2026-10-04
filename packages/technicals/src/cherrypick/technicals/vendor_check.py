"""Our adjusted bars against the vendor's own, from every chart capture on file.

The adjustment method was settled by matching the vendor's MSFT bars on all 753 sessions; this keeps
it settled. Every evening the collector saves more captures, and each one is three years of the
vendor's adjusted bars for a name -- a check that costs nothing to repeat and catches a restated
dividend, a missed split or a Dolt correction the day it lands.

A price agrees when it is within `TOLERANCE` of the vendor's: one cent, because before a split the
vendor's raw prices carry more precision than Dolt's and a division can land on a half cent (ANET:
21 of 3,012 prices).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import paths, store

TOLERANCE = 0.0100001
FIELDS = ("open", "high", "low", "close")


def compare(ours: dict[str, dict], vendor: list[dict]) -> dict:
    """{sessions, prices, agree, worst: [(date, field, ours, vendor)]} over the sessions both hold."""
    prices = agree = sessions = 0
    misses = []
    for q in vendor:
        day = str(q.get("date", ""))[:10]
        bar = ours.get(day)
        if bar is None:
            continue
        sessions += 1
        for f in FIELDS:
            v = q.get(f)
            if not isinstance(v, (int, float)):
                continue
            prices += 1
            if abs(bar[f] - v) <= TOLERANCE:
                agree += 1
            else:
                misses.append((day, f, round(bar[f], 4), v))
    misses.sort(key=lambda m: -abs(m[2] - m[3]))
    return {"sessions": sessions, "prices": prices, "agree": agree, "worst": misses[:5]}


def check(captures: list[Path] | None = None) -> dict:
    """Compare every symbol's newest capture with the store. Returns per-symbol results and a
    summary; a symbol the store does not hold is listed, not failed."""
    root = paths.market_report_dir() / "vendor-charts"
    newest: dict[str, Path] = {}
    for path in sorted(captures or root.glob("????-??-??/*.json")):
        if path.name == "trade-ideas.json":
            continue
        newest[path.stem] = path  # sorted by session folder, so the last one wins
    conn = store.connect()
    results, not_held = {}, []
    for sym, path in sorted(newest.items()):
        try:
            vendor = json.loads(path.read_text(encoding="utf-8"))["why"]["historicalQuotes"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        # As adjusted on the capture's last session, not today: a later dividend is not a disagreement.
        as_of = str(vendor[-1].get("date", ""))[:10] or None if vendor else None
        ours = {b.date: {f: getattr(b, f) for f in FIELDS} for b in store.adjusted_bars(conn, sym, as_of)}
        if not ours:
            not_held.append(sym)
            continue
        results[sym] = {"capture": path.parent.name, **compare(ours, vendor)}
    conn.close()
    prices = sum(r["prices"] for r in results.values())
    agree = sum(r["agree"] for r in results.values())
    return {
        "symbols": len(results),
        "prices": prices,
        "agree": agree,
        "agree_rate": round(agree / prices, 5) if prices else None,
        "not_held": not_held,
        "by_symbol": results,
    }
