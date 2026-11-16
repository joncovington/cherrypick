"""A symbol's own event dates: its earnings announcements and its ex-dividend dates, with the ranges each
is complete for, kept in one local file so a loop or a backtest reads them without the network.

`<home>/data/market-files/calendar/symbol_events.json`:

    {"generated_at": ISO, "labels": {"Earnings": {SYMBOL: {"dates": [...], "covered": [[first, last]]}},
                                     "ExDividend": {...}}}

- **Earnings** come from the local Dolt clone's `earnings.earnings_calendar`. The calendar is taken as
  complete over its own span (its first to its last date), so a symbol it never lists in that span has
  no announcement there, and every symbol asked for is covered by it.
- **ExDividend** comes from the technicals store's `dividends` table (`technicals/history.db`), landed
  with the same daily bars, so a symbol's dates are complete over the span of its bars there. A symbol
  with no bars there is not covered: its dates are unknown, never "no dividend".

`build` is pure; `write` reads the two stores (Dolt through a `connect(database)` the caller gives, as
`core.sources` does) and replaces the file whole; `read` gives `{label: {symbol: (dates, covered)}}`.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from cherrypick.core import home as _home

LABELS = ("Earnings", "ExDividend")


def path() -> Path:
    return _home.data_dir("market-files") / "calendar" / "symbol_events.json"


def build(
    symbols: Iterable[str],
    *,
    earnings: Iterable[tuple[str, str]],
    earnings_span: tuple[str, str] | None,
    dividends: Iterable[tuple[str, str]],
    bar_spans: dict[str, tuple[str, str]],
    generated_at: str | None = None,
) -> dict:
    """The file's document for `symbols`. `earnings` and `dividends` are (symbol, ISO date) rows,
    `earnings_span` the calendar's own first and last date (None: no calendar), `bar_spans` each
    symbol's first and last bar in the dividend store."""
    wanted = sorted({s.upper() for s in symbols})
    by: dict[str, dict[str, set[str]]] = {label: {s: set() for s in wanted} for label in LABELS}
    for sym, d in earnings:
        if sym.upper() in by["Earnings"]:
            by["Earnings"][sym.upper()].add(str(d)[:10])
    for sym, d in dividends:
        if sym.upper() in by["ExDividend"]:
            by["ExDividend"][sym.upper()].add(str(d)[:10])
    labels: dict[str, dict] = {label: {} for label in LABELS}
    for s in wanted:
        if earnings_span is not None:
            labels["Earnings"][s] = {"dates": sorted(by["Earnings"][s]), "covered": [list(earnings_span)]}
        if s in bar_spans:
            labels["ExDividend"][s] = {"dates": sorted(by["ExDividend"][s]), "covered": [list(bar_spans[s])]}
    return {
        "generated_at": generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "labels": labels,
    }


def _technicals_db() -> Path:
    return _home.data_dir("technicals") / "history.db"


#: `write --all` keeps the symbols still trading: a last bar within this many days of the store's newest.
ACTIVE_DAYS = 14


def stored_symbols(active_days: int = ACTIVE_DAYS) -> list[str]:
    """Every symbol the technicals store holds daily bars for whose last bar is within `active_days` of
    the store's newest (`write --all`): the store keeps delisted names' history, which no automation
    trades."""
    db = _technicals_db()
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        (newest,) = conn.execute("SELECT MAX(date) FROM bars").fetchone()
        if newest is None:
            return []
        since = (date.fromisoformat(str(newest)[:10]) - timedelta(days=active_days)).isoformat()
        return [
            r[0]
            for r in conn.execute(
                "SELECT symbol FROM bars GROUP BY symbol HAVING MAX(date) >= ? ORDER BY symbol", (since,)
            )
        ]
    finally:
        conn.close()


def _dividend_inputs(symbols: list[str]) -> tuple[list[tuple[str, str]], dict[str, tuple[str, str]]]:
    db = _technicals_db()
    if not db.exists():
        return [], {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        marks = ",".join("?" * len(symbols))
        rows = conn.execute(
            f"SELECT symbol, ex_date FROM dividends WHERE symbol IN ({marks})", symbols
        ).fetchall()
        spans = {
            s: (str(a)[:10], str(b)[:10])
            for s, a, b in conn.execute(
                f"SELECT symbol, MIN(date), MAX(date) FROM bars WHERE symbol IN ({marks}) GROUP BY symbol",
                symbols,
            )
            if a and b
        }
    finally:
        conn.close()
    return [(s, str(d)) for s, d in rows], spans


def _earnings_inputs(
    symbols: list[str], connect: Callable[[str], object] | None
) -> tuple[list[tuple[str, str]], tuple[str, str] | None]:
    if connect is None:
        return [], None
    conn = connect("earnings")
    try:
        cur = conn.cursor()  # type: ignore[attr-defined]
        cur.execute("SELECT MIN(date), MAX(date) FROM earnings_calendar")
        lo, hi = cur.fetchone()
        if lo is None:
            return [], None
        marks = ",".join(["%s"] * len(symbols))
        cur.execute(f"SELECT act_symbol, date FROM earnings_calendar WHERE act_symbol IN ({marks})", symbols)
        rows = [(str(s), str(d)) for s, d in cur.fetchall()]
    finally:
        conn.close()  # type: ignore[attr-defined]
    return rows, (str(lo)[:10], str(hi)[:10])


def write(
    symbols: Iterable[str], *, connect: Callable[[str], object] | None, out: Path | None = None
) -> dict:
    """Read both stores for `symbols` and replace the file whole (atomically). Without `connect`,
    earnings are left uncovered rather than guessed."""
    wanted = sorted({s.upper() for s in symbols})
    dividends, spans = _dividend_inputs(wanted) if wanted else ([], {})
    earnings, span = _earnings_inputs(wanted, connect) if wanted else ([], None)
    doc = build(wanted, earnings=earnings, earnings_span=span, dividends=dividends, bar_spans=spans)
    target = out or path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, target)
    return doc


def read(file: Path | None = None) -> dict[str, dict[str, tuple[list[str], list[list[str]]]]]:
    """{label: {symbol: (dates, covered)}}; empty when the file is missing or unreadable."""
    try:
        doc = json.loads((file or path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, tuple[list[str], list[list[str]]]]] = {}
    for label, per in (doc.get("labels") or {}).items():
        if label not in LABELS or not isinstance(per, dict):
            continue
        out[label] = {
            str(s).upper(): (list(v.get("dates") or []), [list(r) for r in v.get("covered") or []])
            for s, v in per.items()
            if isinstance(v, dict)
        }
    return out
