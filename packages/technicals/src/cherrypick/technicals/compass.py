"""The bearing: a name's direction, read from its 1M trend, its 6M trend and its 1-10 score.

**What it reproduces.** The vendor labels every symbol with a direction read (its own name for it is
not used here). The dashboard's security list carries that label beside the three inputs it is
read from -- the 1M and 6M trend scores (-4..+4, which `trend.SHORT_TERM` / `trend.LONG_TERM`
reproduce at 99.7%) and the 1-10 score (`levels.rank_score`'s decile, 52 of 62 exact) -- and
`scripts/fetch_vendor_edition.py` keeps one list a session in `market-report/vendor-securities/`.

**What it is.** A lookup on (1M, 6M, score tier), the tiers being 1-4, 5-6 and 7-10: each cell takes
the label most of its names carried. On the 2026-10-07 list (10,789 labelled rows), a table fitted
on half the symbols named the other half's label 90.3% of the time: strong bull 95%, strong bear
99%, the in-between states 45-90%. The tiers came from the data, not the vendor's words: strong
bull never occurs below 7, strong bear never above 4, and plain bull and bear only at 5-6.

**The label names are ours and inferred** from where each code sits: a pullback is a falling 1M
inside a rising 6M; a rally the reverse; improving a rising 1M with a weak score; weakening a strong
score with a falling 1M. Only "strong bull" and "strong bear" are confirmed by the vendor's own pages.

`score-bearing` fits on every list but the latest and scores the latest -- the across-days check the
one-day fit could not make.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from . import paths

LABELS = {
    "sb": "strong bull",
    "bn": "bull",
    "bp": "pullback",
    "bi": "improving",
    "mx": "neutral",
    "br": "rally",
    "bd": "weakening",
    "brn": "bear",
    "sbe": "strong bear",
}
Key = tuple[int, int, int]


def tier(score: int | None) -> int | None:
    """0 for a score of 1-4, 1 for 5-6, 2 for 7-10; None for no score (the list writes 0)."""
    if not score or score < 1:
        return None
    return 0 if score <= 4 else 1 if score <= 6 else 2


def key(s1: int | None, s6: int | None, score: int | None) -> Key | None:
    t = tier(score)
    if s1 is None or s6 is None or t is None:
        return None
    return (s1, s6, t)


def _labelled(rows: list[dict]) -> list[tuple[Key, str]]:
    out = []
    for r in rows:
        code = r.get("directionalEdge")
        k = key(r.get("sentiment1M"), r.get("sentiment6M"), r.get("technicalRank"))
        if code in LABELS and k is not None:
            out.append((k, LABELS[code]))
    return out


def fit(rows: list[dict]) -> dict[Key, str]:
    """The lookup: each (1M, 6M, tier) cell's most common label."""
    cells: dict[Key, Counter] = defaultdict(Counter)
    for k, label in _labelled(rows):
        cells[k][label] += 1
    return {k: c.most_common(1)[0][0] for k, c in cells.items()}


def bearing(lookup: dict[Key, str], s1: int | None, s6: int | None, score: int | None) -> str | None:
    """A name's bearing, or None where an input is missing or the cell was never seen."""
    k = key(s1, s6, score)
    return lookup.get(k) if k is not None else None


def evaluate(lookup: dict[Key, str], rows: list[dict]) -> dict:
    """How often the lookup names the label a list carries: overall, and per label."""
    per: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    unseen = 0
    for k, label in _labelled(rows):
        got = lookup.get(k)
        unseen += got is None
        per[label][0] += got == label
        per[label][1] += 1
    hits, total = sum(v[0] for v in per.values()), sum(v[1] for v in per.values())
    return {
        "rows": total,
        "agree": hits,
        "share": hits / total if total else None,
        "unseen_cells": unseen,
        "by_label": {lab: f"{h}/{n}" for lab, (h, n) in sorted(per.items())},
    }


def snapshots_dir() -> Path:
    return paths.market_report_dir() / "vendor-securities"


def load_snapshots(root: Path | None = None) -> dict[str, list[dict]]:
    """{session: rows} for every kept list (rejected ones are left out)."""
    root = root or snapshots_dir()
    out = {}
    for path in sorted(root.glob("????-??-??.json")):
        out[path.stem] = json.loads(path.read_text(encoding="utf-8")).get("rows") or []
    return out


def score_across_days(snapshots: dict[str, list[dict]]) -> dict:
    """Fit on every list but the latest, score the latest. One list alone cannot be checked this way."""
    days = sorted(snapshots)
    if len(days) < 2:
        return {"ok": False, "reason": f"{len(days)} list(s) saved; need two", "sessions": days}
    fitted = [r for d in days[:-1] for r in snapshots[d]]
    lookup = fit(fitted)
    return {
        "ok": True,
        "fitted_on": days[:-1],
        "scored": days[-1],
        "cells": len(lookup),
        **evaluate(lookup, snapshots[days[-1]]),
    }
