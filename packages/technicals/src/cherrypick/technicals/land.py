"""Land the end-of-day data from the local Dolt clones into the store.

Reads the `stocks` clone (ohlcv, split, dividend) and the `options` clone (volatility_history)
through the local `dolt sql-server` -- the one the earnings module already reads, pulled each
morning at 05:30 by scripts/refresh_dolt_data.py. Read-only against Dolt; writes only the store.

Each run re-reads a short window behind what the store already holds, so a bar Dolt restates is
picked up, and backfills a new symbol in full (`BACKFILL_DAYS`). Idempotent: a second run the same
morning lands the same rows again.

**Query shape is the whole cost.** `ohlcv` and `volatility_history` lead their primary keys with
`date`, so a query filtered by a list of symbols walks the table: one month for 200 symbols took
27.5 s, the same month for ALL ~13,000 symbols 4.4 s (2026-09-27), and the first landing written
the obvious way ran past ten minutes. So the data is read in month-wide date windows with no symbol
filter, and the symbols are kept here. The event tables are small and read whole.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import levels, paths, store, symbols

# Three years of bars for a new symbol: the depth of the vendor's own chart data (753 sessions),
# which is what the level engine is scored against.
BACKFILL_DAYS = 3 * 366
# A window re-read behind the newest stored bar, so an upstream restatement lands.
RESTATE_DAYS = 10
# Implied-volatility history is read over one year: the rank needs only the latest row; the history
# is kept for later percentile work.
IV_DAYS = 400
WINDOW_DAYS = 31

DEFAULTS = {"host": "127.0.0.1", "port": 3306, "user": "root", "stocks_db": "stocks", "options_db": "options"}


def _connect(cfg: dict, database: str):
    import mysql.connector

    return mysql.connector.connect(
        host=cfg["host"], port=int(cfg["port"]), user=cfg["user"], database=database, connection_timeout=15
    )


def _f(value) -> float | None:
    return float(value) if value is not None else None


RANK_BACKFILL = 15  # sessions back the landing fills market rank cut-offs for, when missing


def land_rank_cutoffs(cur, conn, split_rows) -> list[str]:
    """Store the whole market's 1-10 rank cut-offs for each recent session that has none.

    The rank is a decile across every US-listed name Dolt carries, not the ~550 the store lands, so
    this reads three dates' closes for the whole market per session -- `date` leads the key, so a
    date filter is the cheap query -- and keeps only the nine cut-offs. A name with a split inside
    the window is left out rather than adjusted (raw closes would fake its return), and so is one
    trading under `RANK_MIN_DOLLAR_VOLUME`. Dividends are not adjusted for: a quarter's dividend
    moves a six-month return by a point or so. Sessions come from the store's own SPY bars."""
    dates = [b.date for b in store.raw_bars(conn, "SPY")]
    have = store.rank_sessions(conn)
    done = []
    for i in range(max(levels.RANK_LONG, len(dates) - RANK_BACKFILL), len(dates)):
        day = dates[i]
        if day in have:
            continue
        d_short, d_long = dates[i - levels.RANK_SHORT], dates[i - levels.RANK_LONG]
        closes: dict[str, dict[str, float]] = {}
        volume: dict[str, float] = {}
        for d in (day, d_short, d_long):
            cur.execute("SELECT act_symbol, close, volume FROM ohlcv WHERE date = %s", (d,))
            for s, c, v in cur.fetchall():
                if c is not None:
                    closes.setdefault(s, {})[d] = float(c)
                    if d == day:
                        volume[s] = float(v or 0)
        split = {s for s, d, *_ in split_rows if d_long < d.isoformat() <= day}
        scores = [
            levels.RANK_SHORT_WEIGHT * (c[day] / c[d_short] - 1) + (c[day] / c[d_long] - 1)
            for s, c in closes.items()
            if s not in split
            and all(c.get(d, 0) > 0 for d in (day, d_short, d_long))
            and c[day] * volume.get(s, 0) >= levels.RANK_MIN_DOLLAR_VOLUME
        ]
        if len(scores) >= 100:  # a thin day (a half-landed Dolt pull) is not a market
            store.put_rank_cutoffs(conn, day, len(scores), levels.rank_cutoffs(scores))
            done.append(day)
    return done


def plan_starts(wanted: list[str], latest: dict[str, str], today: date) -> dict[str, str]:
    """The first date to read for each symbol: a restatement window behind its newest stored bar,
    or a full backfill for a symbol the store does not hold."""
    backfill = (today - timedelta(days=BACKFILL_DAYS)).isoformat()
    out = {}
    for sym in wanted:
        last = latest.get(sym)
        out[sym] = (date.fromisoformat(last) - timedelta(days=RESTATE_DAYS)).isoformat() if last else backfill
    return out


def windows(start: str, today: date, days: int = WINDOW_DAYS) -> list[tuple[str, str]]:
    """[start, end) date windows covering `start` through `today` inclusive."""
    out = []
    lo = date.fromisoformat(start)
    end = today + timedelta(days=1)
    while lo < end:
        hi = min(lo + timedelta(days=days), end)
        out.append((lo.isoformat(), hi.isoformat()))
        lo = hi
    return out


def keep(rows, starts: dict[str, str]):
    """The rows for wanted symbols, on or after that symbol's own start date."""
    for row in rows:
        start = starts.get(row[0])
        if start is not None and row[1].isoformat() >= start:
            yield row


def land_index_bars(conn, wanted) -> list[str]:
    """The cash indexes (`symbols.INDEXES`) from the broker's daily candles, the file
    scripts/fetch_index_bars.py writes -- Dolt carries none. Every row, upserted: a few hundred per
    index, so a restated candle lands without a window to manage. No volume: an index has none.
    Returns the symbols landed; a missing or unreadable file lands nothing."""
    want = [s for s in symbols.INDEXES if s in set(wanted)]
    path = paths.index_bars()
    if not want or not path.exists():
        return []
    import sqlite3

    try:
        src = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            rows = src.execute(
                f"SELECT symbol, date, open, high, low, close FROM index_bars WHERE symbol IN ({','.join('?' * len(want))})",
                want,
            ).fetchall()
        finally:
            src.close()
    except sqlite3.Error:
        return []
    store.upsert_bars(conn, ((s, d, _f(o), _f(h), _f(lo), _f(c), None) for s, d, o, h, lo, c in rows))
    return sorted({r[0] for r in rows})


def land(
    cfg: dict | None = None, wanted: list[str] | None = None, today: date | None = None
) -> dict[str, Any]:
    cfg = {**DEFAULTS, **(cfg or {})}
    today = today or date.today()
    wanted = sorted(set(wanted or symbols.all_symbols()))
    want = set(wanted)
    conn = store.connect()
    starts = plan_starts(wanted, store.latest_dates(conn), today)
    report: dict[str, Any] = {"symbols": len(wanted), "bars": 0, "splits": 0, "dividends": 0, "iv": 0}
    # First, and independent of Dolt: the indexes come from the broker's file, so a Dolt outage
    # does not hold them back.
    report["indexes"] = land_index_bars(conn, wanted)
    conn.commit()
    try:
        stocks = _connect(cfg, cfg["stocks_db"])
    except Exception as exc:  # noqa: BLE001 -- no server is a failed landing, reported, not a crash
        conn.close()
        return {"ok": False, "reason": f"dolt unreachable: {type(exc).__name__}: {exc}"}
    try:
        cur = stocks.cursor()
        # Symbols Dolt does not list (SPX, NDX and VIX are indexes; a retired ticker) can never land,
        # and left in the plan they looked new on every run -- a three-year backfill window each
        # morning for nothing. They are reported instead.
        cur.execute("SELECT act_symbol, is_etf FROM symbol")
        listing = {row[0]: int(row[1] or 0) for row in cur.fetchall()}
        listed = set(listing)
        store.upsert_listings(conn, ((s, listing[s]) for s in wanted if s in listing))
        report["not_in_dolt"] = sorted(s for s in wanted if s not in listed and s not in report["indexes"])
        starts = {s: d for s, d in starts.items() if s in listed}
        if not starts:
            stocks.close()
            conn.close()
            return {"ok": False, "reason": "none of the wanted symbols is in Dolt's symbol table", **report}
        for lo, hi in windows(min(starts.values()), today):
            cur.execute(
                "SELECT act_symbol, date, open, high, low, close, volume FROM ohlcv "
                "WHERE date >= %s AND date < %s",
                (lo, hi),
            )
            report["bars"] += store.upsert_bars(
                conn,
                (
                    (s, d.isoformat(), _f(o), _f(h), _f(lo_), _f(c), _f(v))
                    for s, d, o, h, lo_, c, v in keep(cur.fetchall(), starts)
                ),
            )
        cur.execute("SELECT act_symbol, ex_date, to_factor, for_factor FROM split")
        split_rows = cur.fetchall()
        report["splits"] += store.upsert_splits(
            conn, ((s, d.isoformat(), float(t), float(f)) for s, d, t, f in split_rows if s in want)
        )
        report["rank_sessions"] = land_rank_cutoffs(cur, conn, split_rows)
        cur.execute("SELECT act_symbol, ex_date, amount FROM dividend")
        report["dividends"] += store.upsert_dividends(
            conn, ((s, d.isoformat(), float(a)) for s, d, a in cur.fetchall() if s in want)
        )
    finally:
        stocks.close()
    try:
        options = _connect(cfg, cfg["options_db"])
        try:
            cur = options.cursor()
            # Incremental like the bars: a year for a symbol the store has no IV for, a short
            # restatement window otherwise. Reading the year on every run cost three minutes a
            # morning to land a few thousand rows.
            iv_latest = store.latest_iv_dates(conn)
            iv_first = (today - timedelta(days=IV_DAYS)).isoformat()
            iv_starts = {
                s: (date.fromisoformat(iv_latest[s]) - timedelta(days=RESTATE_DAYS)).isoformat()
                if s in iv_latest
                else iv_first
                for s in wanted
            }
            for lo, hi in windows(min(iv_starts.values()), today):
                cur.execute(
                    "SELECT act_symbol, date, iv_current, iv_year_high, iv_year_low, hv_current "
                    "FROM volatility_history WHERE date >= %s AND date < %s",
                    (lo, hi),
                )
                report["iv"] += store.upsert_iv(
                    conn,
                    (
                        (s, d.isoformat(), _f(iv), _f(h), _f(lo_), _f(hv))
                        for s, d, iv, h, lo_, hv in keep(cur.fetchall(), iv_starts)
                    ),
                )
        finally:
            options.close()
    except Exception as exc:  # noqa: BLE001 -- IV is additive; its absence costs IV rank, not bars
        report["iv_problem"] = f"{type(exc).__name__}: {exc}"
    latest = store.latest_dates(conn)
    report["through"] = max(latest.values()) if latest else None
    report["missing"] = sorted(s for s in wanted if s not in latest and s not in report["not_in_dolt"])
    store.record_landing(conn, report["through"], len(wanted), report["bars"], report)
    conn.commit()
    conn.close()
    report["ok"] = True
    return report
