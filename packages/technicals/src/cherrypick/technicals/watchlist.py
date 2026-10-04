"""The setups watchlist: every position the four chart setups hold, and every one that entered or
exited lately, across every charted name -- one file the console's setups table reads.

Built from the chart files' own content as `chart.write_all` produces them, so the table and the
charts cannot disagree about a trade. A row is a position (`setups.Trade`): its entry, its exit if
it has one, and the context the user reads a signal against, all as of the chart's last session:

- **1M / 6M trend**: our two trend scores, which match the vendor's day by day (`trend.py`), with
  the vendor's three-way page label (`page_label`).
- **RS**: our 1-10 rank, the vendor's "Relative Strength" -- a decile across the whole US market of
  half the 1-month return plus the 6-month return, NOT a comparison with SPY.
- **1M vs SPY**: what the rank is often taken to be: the name's 21-session return less SPY's over
  the same sessions, in percentage points.
- **Nearest support / resistance**: of the levels the vendor's chart draws (`chart.vendor_view`),
  the nearest below and above the last close; none where the vendor's chart was never captured.

"Move" is close to close (exit against entry, or the last close against entry while open). It is
not P&L: no fill, no cost, no slippage, so it is never called one.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VERSION = 1
WINDOW = 20  # sessions of entries and exits kept beside every open position
MONTH = 21  # sessions in the 1M vs SPY return


def page_label(score: float | None) -> str | None:
    """The vendor page's three-way label for a -4..+4 trend score. Seen on 18 names (2026-10-03):
    -4..-2 Bearish, -1 Neutral, +3..+4 Bullish. 0..+2 were not on the panel; taken as the mirror
    (0, +1 Neutral; +2 Bullish), which `docs/setups-watchlist.md` records as unconfirmed."""
    if score is None:
        return None
    return "Bearish" if score <= -2 else "Bullish" if score >= 2 else "Neutral"


def _pct(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None or b == 0 else round(100.0 * (a / b - 1.0), 2)


def vs_spy(dates: list[str], closes: list[float | None], spy: dict[str, float]) -> float | None:
    """The name's MONTH-session return less SPY's over the same two sessions, in points."""
    if len(dates) <= MONTH:
        return None
    a, b = dates[-1 - MONTH], dates[-1]
    mine = _pct(closes[-1], closes[-1 - MONTH])
    theirs = _pct(spy.get(b), spy.get(a))
    return None if mine is None or theirs is None else round(mine - theirs, 2)


def nearest_levels(vendor: dict | None, close: float | None) -> tuple[dict | None, dict | None]:
    """(support below, resistance above) among the levels the vendor's chart draws."""
    if not vendor or close is None:
        return None, None
    view = [lv for lv in vendor.get("levels") or [] if lv.get("vendor_view")]
    below = [lv["value"] for lv in view if lv["kind"] == "support" and lv["value"] < close]
    above = [lv["value"] for lv in view if lv["kind"] == "resistance" and lv["value"] > close]

    def at(v):
        return None if v is None else {"value": v, "pct": _pct(v, close)}

    return at(max(below, default=None)), at(min(above, default=None))


def rows(doc: dict[str, Any], spy: dict[str, float]) -> list[dict]:
    """The watchlist rows for one chart file: open positions, and positions that entered or exited
    within the last WINDOW sessions drawn."""
    dates = doc["bars"]["date"]
    closes = doc["bars"]["close"]
    if not dates:
        return []
    where = {d: i for i, d in enumerate(dates)}
    last = len(dates) - 1

    def ago(d):
        return None if d is None or d not in where else last - where[d]

    close = closes[-1]
    support, resistance = nearest_levels(doc.get("vendor"), close)
    short, long_ = doc["trend_short"][-1], doc["trend_long"][-1]
    context = {
        "symbol": doc["symbol"],
        "session": doc["session"],
        "last_close": close,
        "trend_1m": short,
        "trend_6m": long_,
        "trend_1m_label": page_label(short),
        "trend_6m_label": page_label(long_),
        "rs": doc.get("rank"),
        "vs_spy_1m": vs_spy(dates, closes, spy),
        "support": support,
        "resistance": resistance,
    }
    out = []
    for s in doc.get("setups") or []:
        for t in s["trades"]:
            entry_ago, exit_ago = ago(t["entry_date"]), ago(t["exit_date"])
            is_open = t["exit_date"] is None
            recent = any(a is not None and a < WINDOW for a in (entry_ago, exit_ago))
            if not (is_open or recent):
                continue
            out.append(
                {
                    **context,
                    "setup": s["id"],
                    "setup_name": s["name"],
                    "entry_date": t["entry_date"],
                    "entry_price": t["entry_price"],
                    "entry_ago": entry_ago,
                    "exit_date": t["exit_date"],
                    "exit_price": t["exit_price"],
                    "exit_ago": exit_ago,
                    "reason": t["reason"],
                    "target": t["target"],
                    "status": "open" if is_open else "closed",
                    "move_pct": _pct(close if is_open else t["exit_price"], t["entry_price"]),
                }
            )
    return out


FILE = "setups-index.json"


def write(directory: Path, all_rows: list[dict], session: str | None) -> None:
    """`setups-index.json` beside the chart files, written then renamed like them."""
    doc = {
        "version": VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "session": session,
        "window": WINDOW,
        "rows": all_rows,
    }
    p = directory / FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    tmp.replace(p)
