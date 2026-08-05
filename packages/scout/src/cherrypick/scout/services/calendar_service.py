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

Expected move (`0.85 * front straddle`, `earnings.scanner.compute_expected_move_and_term_structure`'s
heuristic) is fetched -- one narrow ATM chain snapshot, not `chain_service`'s full multi-expiration
cache -- **only** for watchlist/metrics-sourced rows inside the requested window, since a broad Dolt
row can number in the hundreds and would turn a calendar page load into a call storm.

`config.calendar.liquid_only` (default True) pre-filters the combined rows to
`liquidity_service.get_liquid_symbols`'s tastytrade-sourced "Liquid Symbols" watchlist membership
-- a zero-per-symbol-broker-call liquidity signal that covers the broad Dolt rows too, which have
no `liquidity_rating` of their own (that field only exists on metrics/watchlist rows).
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Any

from . import earnings_watchlist_service, liquidity_service, metrics_service
from .cache import peek, put
from .session import BrokerSession

_STRADDLE_BUCKET = "straddle"
_DOLT_DATABASE = "earnings"
_DOLT_CONNECT_TIMEOUT_SECONDS = 5
_DOLT_QUERY_TIMEOUT_SECONDS = 10


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
            "liquidity_rating": info.get("liquidity_rating"),
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
            "liquidity_rating": None,
            "expected_move_pct": None,
            "source": "dolt",
            "stale": True,
        }
    return rows


async def _atm_straddle_mid(session: BrokerSession, symbol: str) -> tuple[float, float] | None:
    """`(spot, expected_move_dollars)` from the nearest-expiration ATM call+put mid, or `None` if the
    chain/quotes aren't available. Deliberately a single narrow fetch, not `chain_service`'s cache."""
    from tastytrade import instruments as _instruments
    from tastytrade import market_data as _market_data

    chain = await session.call(_instruments.get_option_chain, symbol)
    if not chain:
        return None
    front_expiration = min(chain)
    options = chain[front_expiration]

    equity_data = await session.call(_market_data.get_market_data_by_type, equities=[symbol])
    if not equity_data or equity_data[0].mark is None:
        return None
    spot = float(equity_data[0].mark)

    calls = [o for o in options if o.option_type.value.lower().startswith("c")]
    puts = [o for o in options if o.option_type.value.lower().startswith("p")]
    if not calls or not puts:
        return None
    atm_call = min(calls, key=lambda o: abs(float(o.strike_price) - spot))
    atm_put = min(puts, key=lambda o: abs(float(o.strike_price) - spot))

    quotes = await session.call(
        _market_data.get_market_data_by_type, options=[atm_call.symbol, atm_put.symbol]
    )
    by_symbol = {q.symbol: q for q in quotes}
    call_quote, put_quote = by_symbol.get(atm_call.symbol), by_symbol.get(atm_put.symbol)
    if call_quote is None or put_quote is None or call_quote.mid is None or put_quote.mid is None:
        return None
    expected_move = 0.85 * float(call_quote.mid + put_quote.mid)
    return spot, expected_move


async def _expected_move_pct(
    conn: sqlite3.Connection, session: BrokerSession, symbol: str, ttl: float, now: float
) -> float | None:
    """Cached `(spot, expected_move_dollars)` -> `expected_move_pct`, TTL-checked by hand rather than
    through `cache.get_or_fetch` -- that primitive's `fetch_fn` is synchronous, and the straddle fetch
    is an awaited broker call. `None` is itself a valid cached payload (symbol has no usable chain),
    so a repeated miss doesn't retry the broker every request within the TTL."""
    cached = peek(conn, _STRADDLE_BUCKET, symbol)
    if cached is not None and (now - cached[1]) < ttl:
        payload = cached[0]
    else:
        try:
            result = await _atm_straddle_mid(session, symbol)
        except Exception:
            result = None
        payload = list(result) if result is not None else None
        put(conn, _STRADDLE_BUCKET, symbol, payload, now)
    if not payload:
        return None
    spot, expected_move = payload
    return (expected_move / spot) if spot else None


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
    calendar_ttl = cfg.get("refresh", {}).get("calendar_ttl_seconds", 3600)

    # Broad coverage beyond the user's own watchlist prefers tastytrade's own "All Earnings" public
    # watchlist over Dolt where the two overlap -- fetched once (a cached watchlist membership
    # call, not a per-symbol one), then unioned into the SAME batched metrics call the user's own
    # watchlist already makes, so these symbols get real dates from live metrics instead of Dolt's
    # third-party periodic snapshot. Deliberately NOT unioned into the expected-move loop below --
    # that's a per-symbol chain+quote broker call each, and the whole reason this module bounds it
    # to metrics-sourced rows is to avoid a "call storm" from a wide symbol set (see module
    # docstring); up to 85 more names in the window would reintroduce exactly that risk.
    use_earnings_watchlist = cfg.get("calendar", {}).get("use_tastytrade_earnings_watchlist", True)
    earnings_watchlist_symbols: set[str] = set()
    if use_earnings_watchlist:
        earnings_watchlist_symbols = await earnings_watchlist_service.get_earnings_watchlist_symbols(
            conn, session, now=now
        )
    metrics_symbols = sorted({s.strip().upper() for s in watchlist_symbols if s} | earnings_watchlist_symbols)

    metrics = await metrics_service.get_metrics(conn, session, metrics_symbols, metrics_ttl, now=now)
    metrics_rows = _metrics_rows(metrics, today, end)

    own_watchlist = {s.strip().upper() for s in watchlist_symbols if s}
    for (symbol, _report_date), row in metrics_rows.items():
        row["expected_move_pct"] = (
            await _expected_move_pct(conn, session, symbol, calendar_ttl, now)
            if symbol in own_watchlist
            else None
        )

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
        "days": days,
        "liquid_only": liquid_only,
        "liquidity_filter_available": liquidity_filter_available,
        "entries": all_rows,
    }
