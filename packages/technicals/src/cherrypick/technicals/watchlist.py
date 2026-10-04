"""The setups watchlist: every position the chart setups hold, long and short, and every one that
entered or exited lately, across every charted name -- one file the console's setups table reads.

Built from the chart files' own content as `chart.write_all` produces them, so the table and the
charts cannot disagree about a trade. A row is a position (`setups.Trade`): its entry, its exit if
it has one, and the context the user reads a signal against, all as of the chart's last session:

- **1M / 6M trend**: our two trend scores (`trend.py`) and our five-step label (`trend.label`). The
  vendor only checks them (99.7% day by day); its page's own three-way wording is not used.
- **Trend agrees**: both scores on the trade's side of zero -- above it for a long, below for a short.
- **RS**: our 1-10 rank, the vendor's "Relative Strength" -- a decile across the whole US market of
  half the 1-month return plus the 6-month return, NOT a comparison with SPY.
- **1M vs SPY**: what the rank is often taken to be: the name's 21-session return less SPY's over
  the same sessions, in percentage points.
- **Nearest support / resistance**: our own swing levels (`swings.py`), the nearest below and above
  the last close. No vendor data: the vendor's levels are a comparison on the chart, nothing more.
- **Dollar volume**: the 50-session median of close x volume to the session before the entry -- the
  number the historical study's universe and its "$300M a day" rule read (`universe.membership`).
- **Options-tradable**: the name lists weekly options and its stock trades at least $100M a day
  (`tradable.py`); null when there is no label.

A row from a setup carries `tested: null`. The historical study's confirmed rules (`chart.TESTED`)
add their own rows, each with `tested` naming the rule: the same setup re-walked with the study's
one change, so most of its trades are also a setup row, and a reader shows one kind or the other.

"Move" is close to close in the trade's direction (exit against entry, or the last close against
entry while open; a fall is a positive move for a short). It is not P&L: no fill, no cost, no
slippage, so it is never called one.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import tradable, trend

# 2: short setups (family, side), our own levels and labels, trend_agrees; 3: tested rows, dollar
# volume at entry, options_tradable
VERSION = 3
WINDOW = 20  # sessions of entries and exits kept beside every open position
MONTH = 21  # sessions in the 1M vs SPY return


def trend_agrees(side: str, short: float | None, long_: float | None) -> bool | None:
    """Both trend scores on the trade's side of zero; None until both are defined."""
    if short is None or long_ is None:
        return None
    return short > 0 and long_ > 0 if side == "long" else short < 0 and long_ < 0


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


def nearest_levels(ours: list[dict] | None, close: float | None) -> tuple[dict | None, dict | None]:
    """(support below, resistance above): the nearest of our own levels (`swings.levels`)."""
    if not ours or close is None:
        return None, None
    below = [lv["value"] for lv in ours if lv["kind"] == "support" and lv["value"] < close]
    above = [lv["value"] for lv in ours if lv["kind"] == "resistance" and lv["value"] > close]

    def at(v):
        return None if v is None else {"value": v, "pct": _pct(v, close)}

    return at(max(below, default=None)), at(min(above, default=None))


def rows(doc: dict[str, Any], spy: dict[str, float], weekly: set[str] | None = None) -> list[dict]:
    """The watchlist rows for one chart file: open positions, and positions that entered or exited
    within the last WINDOW sessions drawn, from its setups and its tested rules. `weekly` is the
    names listing weekly options (`tradable.weeklies`); None when there is no label, which leaves
    every row's options-tradable flag null."""
    dates = doc["bars"]["date"]
    closes = doc["bars"]["close"]
    if not dates:
        return []
    where = {d: i for i, d in enumerate(dates)}
    last = len(dates) - 1

    def ago(d):
        return None if d is None or d not in where else last - where[d]

    close = closes[-1]
    support, resistance = nearest_levels(doc.get("our_levels"), close)
    short, long_ = doc["trend_short"][-1], doc["trend_long"][-1]
    context = {
        "symbol": doc["symbol"],
        "session": doc["session"],
        "last_close": close,
        "trend_1m": short,
        "trend_6m": long_,
        "trend_1m_label": trend.label(short),
        "trend_6m_label": trend.label(long_),
        "rs": doc.get("rank"),
        "vs_spy_1m": vs_spy(dates, closes, spy),
        "support": support,
        "resistance": resistance,
        "options_tradable": tradable.tradable(doc["symbol"], weekly, doc.get("dollar_volume_50d")),
    }
    blocks = [(s, None) for s in doc.get("setups") or []]
    # A tested rule names the setup it changes; its rows read as that setup's, marked with the rule.
    blocks += [({**t, "id": t["setup"]}, t["id"]) for t in doc.get("tested") or []]
    out = []
    for s, tested in blocks:
        for t in s["trades"]:
            entry_ago, exit_ago = ago(t["entry_date"]), ago(t["exit_date"])
            is_open = t["exit_date"] is None
            recent = any(a is not None and a < WINDOW for a in (entry_ago, exit_ago))
            if not (is_open or recent):
                continue
            side = s.get("side", "long")
            move = _pct(close if is_open else t["exit_price"], t["entry_price"])
            out.append(
                {
                    **context,
                    "setup": s["id"],
                    "setup_name": s["name"],
                    "family": s.get("family", s["id"]),
                    "side": side,
                    "tested": tested,
                    "trend_agrees": trend_agrees(side, short, long_),
                    "entry_date": t["entry_date"],
                    "entry_price": t["entry_price"],
                    "entry_ago": entry_ago,
                    "exit_date": t["exit_date"],
                    "exit_price": t["exit_price"],
                    "exit_ago": exit_ago,
                    "reason": t["reason"],
                    "target": t["target"],
                    "dollar_volume": t.get("dollar_volume"),
                    "status": "open" if is_open else "closed",
                    # In the trade's direction: a fall is a positive move for a short.
                    "move_pct": None if move is None else (move if side == "long" else -move),
                }
            )
    return out


FILE = "setups-index.json"


def write(directory: Path, all_rows: list[dict], session: str | None, label_day: str | None = None) -> None:
    """`setups-index.json` beside the chart files, written then renamed like them. `label_day` is the
    day the weeklies were read from; None when there is no label."""
    doc = {
        "version": VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "session": session,
        "window": WINDOW,
        "options_label_day": label_day,
        "rows": all_rows,
    }
    p = directory / FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    tmp.replace(p)
