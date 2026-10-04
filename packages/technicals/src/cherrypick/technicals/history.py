"""Dolt's whole daily history, for the historical study (docs/signal-log-plan.md, Phase 1).

Landed into `history.db` (`paths.history_db()`) in the same schema as `eod.db`, so the store's own
reading of it (`store.raw_bars`, `store.adjusted_bars`) applies unchanged: the same split dedupe,
the same series break for a ticker that changed hands, and the same reconciled dividends wherever
tastytrade's are on file. Every name Dolt lists, from START, read in month-wide windows with no
symbol filter (Dolt's keys lead with the date, so a symbol filter walks the table). A re-run reads
from RESTATE_DAYS behind the newest bar held, so a restated bar lands.

Rebuildable from Dolt at any time; nothing nightly reads it. `check` compares it with `eod.db` where
the two overlap, and lists what a reader of fifteen years of a free dataset should look at before
trusting it: price jumps no split explains, and names with holes in their history.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import land as _land
from . import paths, store

START = "2011-01-03"
JUMP = 1.4  # a close-to-close move beyond 40% either way, with no split near it, is listed
SPLIT_NEAR_DAYS = 5


# Each name's largest single-day dollar volume. A rolling median can never exceed it, so a name whose
# peak is under the universe's floor can never qualify; this table lets the study skip it unread.
# Kept by the landing (merged window by window), rebuilt in one sequential pass when empty -- a
# GROUP BY over the bars walks the (symbol, date) index and fetched 29M rows one by one (15+ min).
PEAKS_SCHEMA = "CREATE TABLE IF NOT EXISTS peaks (symbol TEXT PRIMARY KEY, max_dollar_volume REAL NOT NULL)"


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = store.connect(path or paths.history_db())
    conn.execute(PEAKS_SCHEMA)
    # A research store, rebuildable from Dolt: durability per transaction is not worth its cost here.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def land(
    cfg: dict | None = None,
    today: date | None = None,
    start: str = START,
    path: Path | None = None,
    progress=None,
) -> dict[str, Any]:
    cfg = {**_land.DEFAULTS, **(cfg or {})}
    today = today or date.today()
    conn = connect(path)
    newest = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    first = (
        start
        if newest is None
        else max(start, (date.fromisoformat(newest) - timedelta(days=_land.RESTATE_DAYS)).isoformat())
    )
    report: dict[str, Any] = {"from": first, "bars": 0, "splits": 0, "dividends": 0, "listings": 0}
    started = time.monotonic()
    try:
        dolt = _land._connect(cfg, cfg["stocks_db"])
    except Exception as exc:  # noqa: BLE001 -- no server is a failed landing, reported, not a crash
        conn.close()
        return {"ok": False, "reason": f"dolt unreachable: {type(exc).__name__}: {exc}"}
    try:
        cur = dolt.cursor()
        cur.execute("SELECT act_symbol, is_etf FROM symbol")
        report["listings"] = store.upsert_listings(conn, ((s, int(e or 0)) for s, e in cur.fetchall()))
        spans = _land.windows(first, today)
        for k, (lo, hi) in enumerate(spans, 1):
            cur.execute(
                "SELECT act_symbol, date, open, high, low, close, volume FROM ohlcv "
                "WHERE date >= %s AND date < %s",
                (lo, hi),
            )
            f = _land._f
            rows = [
                (s, d.isoformat(), f(o), f(h), f(lo_), f(c), f(v)) for s, d, o, h, lo_, c, v in cur.fetchall()
            ]
            report["bars"] += store.upsert_bars(conn, rows)
            merge_peaks(conn, _peaks_of((r[0], r[5], r[6]) for r in rows))
            conn.commit()
            if progress:
                progress(k, len(spans), lo, report["bars"])
        cur.execute("SELECT act_symbol, ex_date, to_factor, for_factor FROM split")
        report["splits"] = store.upsert_splits(
            conn, ((s, d.isoformat(), float(t), float(f)) for s, d, t, f in cur.fetchall())
        )
        cur.execute("SELECT act_symbol, ex_date, amount FROM dividend")
        report["dividends"] = store.upsert_dividends(
            conn, ((s, d.isoformat(), float(a)) for s, d, a in cur.fetchall())
        )
    finally:
        dolt.close()
    through = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    report.update(ok=True, through=through, seconds=round(time.monotonic() - started))
    store.record_landing(conn, through, report["listings"], report["bars"], report)
    conn.commit()
    conn.close()
    return report


def merge_peaks(conn, peaks: dict[str, float]) -> None:
    conn.executemany(
        "INSERT INTO peaks VALUES (?, ?) ON CONFLICT(symbol) DO UPDATE SET "
        "max_dollar_volume = MAX(max_dollar_volume, excluded.max_dollar_volume)",
        peaks.items(),
    )


def _peaks_of(rows) -> dict[str, float]:
    peaks: dict[str, float] = {}
    for sym, close, volume in rows:
        dv = (close or 0.0) * (volume or 0.0)
        if dv > peaks.get(sym, -1.0):
            peaks[sym] = dv
    return peaks


def rebuild_peaks(conn) -> int:
    """Every name's peak dollar volume, in one sequential pass over the table (rowid order)."""
    peaks = _peaks_of(conn.execute("SELECT symbol, close, volume FROM bars NOT INDEXED"))
    conn.execute("DELETE FROM peaks")
    merge_peaks(conn, peaks)
    conn.commit()
    return len(peaks)


def names_peaking_at_least(conn, floor: float) -> list[str]:
    if conn.execute("SELECT COUNT(*) FROM peaks").fetchone()[0] == 0:
        rebuild_peaks(conn)
    return [
        s
        for (s,) in conn.execute(
            "SELECT symbol FROM peaks WHERE max_dollar_volume >= ? ORDER BY symbol", (floor,)
        )
    ]


def symbols_held(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM bars ORDER BY symbol")]


def compare_overlap(hist, eod, names: list[str]) -> dict[str, Any]:
    """Adjusted bars from the history store against the nightly store's, on every date both hold,
    to the cent. The two read the same raw bars, splits and reconciled dividends, so any difference
    is a defect in one of them."""
    out: dict[str, Any] = {"names": 0, "prices": 0, "agree": 0, "disagree": {}}
    for sym in names:
        ours = {b.date: b for b in store.adjusted_bars(eod, sym)}
        if not ours:
            continue
        out["names"] += 1
        bad = 0
        for b in store.adjusted_bars(hist, sym):
            e = ours.get(b.date)
            if e is None:
                continue
            for f in ("open", "high", "low", "close"):
                x, y = getattr(b, f), getattr(e, f)
                if x is None or y is None:
                    continue
                out["prices"] += 1
                if round(x, 2) == round(y, 2):
                    out["agree"] += 1
                else:
                    bad += 1
        if bad:
            out["disagree"][sym] = bad
    return out


# The ratios a split or reverse split lands on, and how close an OPENING gap must sit to one for an
# unexplained jump to be treated as a corporate action Dolt never recorded (a missing split, or a
# spin-off or special distribution priced like one) rather than a market move. The common ratios
# get 3%. The 3:2, 4:3 and 5:4 family needs 1%: earnings gaps land near -25%, -20% and -33% all the
# time, and at 3% they flagged ITGR, INSP, CABO and WAL's genuine moves (2026-10-04).
CLEAN_RATIOS = {
    **{r: 0.03 for f in (2, 3, 4, 5, 8, 10, 20) for r in (f, 1 / f)},
    **{r: 0.01 for f in (1.5, 4 / 3, 1.25) for r in (f, 1 / f)},
}


def clean_ratio(x: float) -> float | None:
    for r, tol in CLEAN_RATIOS.items():
        if abs(x / r - 1) <= tol:
            return r
    return None


def suspected_actions(bars, split_dates: list[date]) -> list[str]:
    """The dates, on raw bars, of unexplained jumps whose opening gap is a clean split ratio."""
    out = []
    for prev, b in zip(bars, bars[1:], strict=False):
        if not prev.close or not b.close or not b.open:
            continue
        ratio = b.close / prev.close
        if JUMP >= ratio >= 1 / JUMP:
            continue
        d = date.fromisoformat(b.date)
        if any(abs((d - s).days) <= SPLIT_NEAR_DAYS for s in split_dates):
            continue
        if clean_ratio(b.open / prev.close) is not None:
            out.append(b.date)
    return out


def unexplained_jumps(conn, sym: str) -> list[tuple[str, float]]:
    """Close-to-close moves beyond JUMP either way, on raw bars, with no split within
    SPLIT_NEAR_DAYS of them -- a missing split or a ticker that changed hands, until shown otherwise."""
    bars = store.raw_bars(conn, sym)
    near = [date.fromisoformat(s.ex_date) for s in store.splits(conn, sym)]
    out = []
    for prev, b in zip(bars, bars[1:], strict=False):
        if not prev.close or not b.close:
            continue
        ratio = b.close / prev.close
        if ratio > JUMP or ratio < 1 / JUMP:
            d = date.fromisoformat(b.date)
            if not any(abs((d - s).days) <= SPLIT_NEAR_DAYS for s in near):
                out.append((b.date, round(ratio, 3)))
    return out
