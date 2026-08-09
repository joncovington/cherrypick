"""The earnings calendar: the union of two sources with very different honesty profiles.

**Metrics (fresh, watchlist-scoped)** -- `metrics_service`'s `get_market_metrics` call, which carries
a real `expected_report_date`/`time_of_day`/`consensus_estimate` per symbol, refreshed on the
`refresh.metrics_ttl_seconds` TTL. Only ever fetched for the user's own watchlist, so it stays a
handful of symbols per refresh.

**Dolt (broad, stale-labeled)** -- the `earnings` Dolt database's `earnings_calendar` table
(`act_symbol`, `date`, `` `when` ``), a periodic DoltHub snapshot rather than a live feed. It covers
far more symbols than the watchlist, which is the point (a name the user hasn't added yet, reporting
next week, is exactly what a calendar is for) -- but every row from it is labeled `stale: True`, and a
Dolt-absent host degrades to metrics-only rather than failing the whole calendar.

When both sources name the same (symbol, date), the metrics row wins (it is strictly richer and
fresher); the Dolt row is only added when metrics has nothing for that day.

This module is deliberately broker-quote-free -- every field here (dates, timing, IV rank/
percentile, market cap) comes from tastytrade's `metrics` reference dataset (cheap, batches
across symbols for free) or the Dolt snapshot, never a per-symbol chain/quote round trip. Expected
move / term structure / IV-RV ratio / winrate -- the genuinely broker-quote- and Dolt-history-
heavy signals -- are computed by **packages/earnings**' own scheduled scan (real per-symbol chain
fetches + Greeks-based term structure, the same machinery `scanner.py` already uses for entry
screening, run on a recurring interval independent of this page) and merged onto matching rows by
`earnings_metrics_service.get_upcoming`, which reads that scan's output table read-only -- same
posture as the Screens section already reading `entry_reviews`. Measured cost of computing that
per symbol live (~1s, dominated by the full option-chain fetch) is exactly why it doesn't belong
on this module's request path across a ~85-symbol earnings watchlist.

`config.calendar.liquid_only` (default True) pre-filters the combined rows to
`liquidity_service.get_liquid_symbols`'s tastytrade-sourced "Liquid Symbols" watchlist membership
-- a zero-per-symbol-broker-call liquidity signal that covers the broad Dolt rows too, which have
no `liquidity_rating` of their own (that field only exists on metrics/watchlist rows).
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime, timedelta
from datetime import time as _time
from typing import Any

from cherrypick.core import calendar as _core_calendar

from . import earnings_watchlist_service, liquidity_service, metrics_service
from .session import BrokerSession

_DOLT_DATABASE = "earnings"
_DOLT_CONNECT_TIMEOUT_SECONDS = 5
_DOLT_QUERY_TIMEOUT_SECONDS = 10

_MARKET_OPEN_ET = _time(9, 30)
_MARKET_CLOSE_ET = _time(16, 0)


def is_market_hours(now: float) -> bool:
    """Regular trading hours (9:30-16:00 ET) on an NYSE trading day. Self-contained rather than
    importing the orchestrator's own `timeutil.is_market_hours` -- this package's own CLAUDE.md
    invariant ("additive-only outside packages/scout, don't reach into a sibling module") rules
    that import out, so this duplicates the small time-of-day check while reusing
    `cherrypick.core.calendar.is_trading_day` for the weekend/holiday side rather than
    re-deriving the NYSE holiday calendar a second time. Best-effort: a timezone-database failure
    (shouldn't happen -- `tzdata` is present in this repo's venv) degrades to "assume open" rather
    than silently block a fetch that might well still succeed.
    """
    try:
        from zoneinfo import ZoneInfo

        dt_et = datetime.fromtimestamp(now, tz=ZoneInfo("America/New_York"))
    except Exception:
        return True
    if not _core_calendar.is_trading_day(dt_et.date()):
        return False
    return _MARKET_OPEN_ET <= dt_et.time() <= _MARKET_CLOSE_ET


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _metrics_rows(metrics: dict[str, dict], start: date, end: date) -> dict[tuple[str, date], dict]:
    rows: dict[tuple[str, date], dict] = {}
    for symbol, info in metrics.items():
        earnings = info.get("earnings") or {}
        report_date = _parse_date(earnings.get("expected_report_date"))
        if report_date is None or not (start <= report_date <= end):
            continue
        rows[(symbol, report_date)] = {
            "symbol": symbol,
            "date": report_date.isoformat(),
            "when": earnings.get("time_of_day"),
            "consensus_eps": earnings.get("consensus_estimate"),
            "estimated": earnings.get("estimated"),
            "iv_rank": info.get("iv_rank"),
            "iv_percentile": info.get("iv_percentile"),
            "liquidity_rating": info.get("liquidity_rating"),
            "market_cap": info.get("market_cap"),
            # Filled in downstream, not here -- expected_move/term_structure/iv_rv_ratio/winrate
            # are earnings-package-computed (packages/earnings' own scheduled scan, richer than
            # anything scout would compute itself), joined onto matching rows by
            # earnings_metrics_service.get_upcoming. calendar_service stays broker-quote-free.
            "expected_move_pct": None,
            "expected_move_is_live": None,
            "source": "metrics",
            "stale": False,
        }
    return rows


def _fetch_dolt_calendar_sync(cfg: dict, start: date, end: date) -> list[dict]:
    import mysql.connector

    dolt_cfg = cfg.get("dolt", {}) or {}
    conn = mysql.connector.connect(
        host=dolt_cfg.get("host", "127.0.0.1"),
        port=int(dolt_cfg.get("port", 3306)),
        user=dolt_cfg.get("user", "root"),
        database=_DOLT_DATABASE,
        connection_timeout=dolt_cfg.get("connect_timeout_seconds", _DOLT_CONNECT_TIMEOUT_SECONDS),
    )
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT act_symbol AS symbol, `date` AS report_date, `when` AS timing "
            "FROM earnings_calendar WHERE `date` BETWEEN %s AND %s",
            (start.isoformat(), end.isoformat()),
        )
        return cur.fetchall()
    finally:
        conn.close()


async def _fetch_dolt_calendar(cfg: dict, start: date, end: date) -> list[dict] | None:
    """`None` means Dolt is unreachable (down, not installed, wrong port) -- a caller degrades
    gracefully rather than erroring. mysql-connector is sync; run it off the event loop with a wall-
    clock bound, since Dolt does not honor MySQL's server-side query timeout."""
    import asyncio

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_fetch_dolt_calendar_sync, cfg, start, end),
            timeout=_DOLT_CONNECT_TIMEOUT_SECONDS + _DOLT_QUERY_TIMEOUT_SECONDS,
        )
    except Exception:
        return None


def _dolt_rows(raw_rows: list[dict], covered: set[tuple[str, date]]) -> dict[tuple[str, date], dict]:
    rows: dict[tuple[str, date], dict] = {}
    for row in raw_rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        report_date = _parse_date(row.get("report_date"))
        if not symbol or report_date is None:
            continue
        key = (symbol, report_date)
        if key in covered:
            continue  # metrics already has a richer row for this symbol/date
        rows[key] = {
            "symbol": symbol,
            "date": report_date.isoformat(),
            "when": row.get("timing"),
            "consensus_eps": None,
            "estimated": None,
            "iv_rank": None,
            "iv_percentile": None,
            "liquidity_rating": None,
            "market_cap": None,
            "expected_move_pct": None,
            "expected_move_is_live": None,
            "source": "dolt",
            "stale": True,
        }
    return rows


async def get_calendar(
    conn: sqlite3.Connection,
    session: BrokerSession,
    cfg: dict,
    watchlist_symbols: list[str],
    *,
    days: int = 14,
    now: float | None = None,
) -> dict:
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now).date()
    end = today + timedelta(days=max(1, days))

    metrics_ttl = cfg.get("refresh", {}).get("metrics_ttl_seconds", 900)

    # Broad coverage beyond the user's own watchlist prefers tastytrade's own "All Earnings" public
    # watchlist over Dolt where the two overlap -- fetched once (a cached watchlist membership
    # call, not a per-symbol one), then unioned into the SAME batched metrics call the user's own
    # watchlist already makes, so these symbols get real dates from live metrics instead of Dolt's
    # third-party periodic snapshot.
    use_earnings_watchlist = cfg.get("calendar", {}).get("use_tastytrade_earnings_watchlist", True)
    earnings_watchlist_symbols: set[str] = set()
    if use_earnings_watchlist:
        earnings_watchlist_symbols = await earnings_watchlist_service.get_earnings_watchlist_symbols(
            conn, session, now=now
        )
    metrics_symbols = sorted({s.strip().upper() for s in watchlist_symbols if s} | earnings_watchlist_symbols)

    metrics = await metrics_service.get_metrics(conn, session, metrics_symbols, metrics_ttl, now=now)
    metrics_rows = _metrics_rows(metrics, today, end)

    raw_dolt = await _fetch_dolt_calendar(cfg, today, end)
    dolt_available = raw_dolt is not None
    dolt_rows = _dolt_rows(raw_dolt or [], covered=set(metrics_rows.keys()))

    all_rows = list(metrics_rows.values()) + list(dolt_rows.values())

    # Pre-filter to tastytrade's own "Liquid Symbols" watchlist -- covers Dolt-sourced rows too,
    # which carry no `liquidity_rating` of their own (that only exists on metrics rows), unlike
    # the screener's chip which can lean on a per-symbol metrics field. An empty result (a fetch
    # failure with nothing cached yet) means "couldn't determine liquidity", not "nothing is
    # liquid" -- the filter is skipped rather than emptying the whole calendar on one hiccup.
    liquid_only = cfg.get("calendar", {}).get("liquid_only", True)
    liquidity_filter_available = False
    if liquid_only:
        liquid_symbols = await liquidity_service.get_liquid_symbols(conn, session, now=now)
        if liquid_symbols:
            liquidity_filter_available = True
            all_rows = [r for r in all_rows if r["symbol"] in liquid_symbols]

    all_rows.sort(key=lambda r: (r["date"], r["symbol"]))

    return {
        "ok": True,
        "as_of": now,
        "stale": not dolt_available,
        "dolt_available": dolt_available,
        "market_hours": is_market_hours(now),
        "days": days,
        "liquid_only": liquid_only,
        "liquidity_filter_available": liquidity_filter_available,
        "entries": all_rows,
    }
