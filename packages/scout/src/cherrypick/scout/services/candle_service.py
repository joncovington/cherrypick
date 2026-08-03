"""Symbol candle history: a one-time Dolt seed plus a short-lived DXLink tail top-up.

**Dolt seed** -- `stocks.ohlcv` (`act_symbol`, `date`, `open`, `high`, `low`, `close`, `volume`) gives
years of daily history essentially for free, no websocket, no rate limit. Fetched once per symbol
(when `candle_meta` has no row yet); after that, this module never re-reads Dolt for that symbol.

**DXLink tail** -- Dolt is a periodic snapshot, so it lags real time by however long since the last
sync. The gap from Dolt's last row to now is filled by a **short-lived** `DXLinkStreamer` (bounded:
an idle timeout with no new event, and a hard wall-clock cap regardless), opened on demand and never
held resident -- consistent with the suite's "only the streamer talks to the broker" rule being a
*streaming-path* rule; this is the one narrow, bounded exception, documented in the package CLAUDE.md.

Retries are floored independently of the candle TTL itself: a failed tail fetch (no credentials,
DXLink misbehaving, a network hiccup) does not advance `candle_meta.last_backfill` -- that column
stays truthful to the newest *real* bar in the cache -- but a short separate "last attempt" marker
stops every page load from retrying the broker while a real gap is still open. On total DXLink
failure the fallback is a single synthesized daily-close bar from a snapshot equity quote, so the
chart still shows *today* rather than nothing.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from . import cache as _cache
from .session import BrokerSession

PERIOD = "1d"
_DOLT_DATABASE = "stocks"
_DOLT_CONNECT_TIMEOUT_SECONDS = 5
_DOLT_QUERY_TIMEOUT_SECONDS = 30
_DXLINK_IDLE_TIMEOUT_SECONDS = 3.0
_DXLINK_HARD_TIMEOUT_SECONDS = 20.0
_INITIAL_TAIL_LOOKBACK_DAYS = 21
_ATTEMPT_BUCKET = "candle_attempt"

# One lock per (symbol, period) so two concurrent requests for the same symbol never double-backfill.
# Unbounded for the process lifetime, which is fine -- scout's universe is a curated watchlist, not
# the whole market.
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(symbol: str, period: str) -> asyncio.Lock:
    return _locks.setdefault(f"{symbol}:{period}", asyncio.Lock())


def _day_epoch(d: date) -> int:
    return int(datetime.combine(d, datetime.min.time(), tzinfo=UTC).timestamp())


def _fetch_dolt_ohlcv_sync(cfg: dict, symbol: str) -> list[dict]:
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
            "SELECT `date`, open, high, low, close, volume FROM ohlcv WHERE act_symbol = %s ORDER BY `date`",
            (symbol,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {
            "t": _day_epoch(row["date"]),
            "o": float(row["open"]),
            "h": float(row["high"]),
            "l": float(row["low"]),
            "c": float(row["close"]),
            "v": float(row["volume"]) if row["volume"] is not None else None,
        }
        for row in rows
    ]


async def _fetch_dolt_ohlcv(cfg: dict, symbol: str) -> list[dict] | None:
    """`None` means Dolt is unreachable -- the caller degrades to DXLink/synthesis alone."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_fetch_dolt_ohlcv_sync, cfg, symbol),
            timeout=_DOLT_CONNECT_TIMEOUT_SECONDS + _DOLT_QUERY_TIMEOUT_SECONDS,
        )
    except Exception:
        return None


async def _dxlink_tail(session: BrokerSession, symbol: str, start: date) -> list[dict] | None:
    """Bars from `start` forward, or `None` if DXLink can't be reached/authenticated at all. Whatever
    bars arrived before a mid-stream failure are still returned -- partial progress beats none."""
    try:
        from tastytrade import DXLinkStreamer
        from tastytrade.dxfeed import Candle
    except ImportError:
        return None
    try:
        tt_session = session.get_raw_session()
    except Exception:
        return None

    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    bars: dict[int, dict] = {}
    try:
        async with DXLinkStreamer(tt_session) as streamer:
            await streamer.subscribe_candle([symbol], interval=PERIOD, start_time=start_dt)
            deadline = time.monotonic() + _DXLINK_HARD_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                wait_for = max(0.01, min(_DXLINK_IDLE_TIMEOUT_SECONDS, remaining))
                try:
                    event = await asyncio.wait_for(streamer.get_event(Candle), timeout=wait_for)
                except TimeoutError:
                    break
                if event.open is None or event.close is None or float(event.close) <= 0:
                    # DXLink pushes a zero-filled placeholder for the still-forming current-day
                    # candle before any real trades have printed -- not a genuine bar, and a real
                    # equity price is never zero or negative.
                    continue
                bars[event.time] = {
                    "t": int(event.time / 1000),
                    "o": float(event.open),
                    "h": float(event.high),
                    "l": float(event.low),
                    "c": float(event.close),
                    "v": float(event.volume) if event.volume is not None else None,
                }
            await streamer.unsubscribe_candle(symbol, interval=PERIOD)
    except Exception:
        pass  # whatever was collected before the failure is still usable
    return sorted(bars.values(), key=lambda b: b["t"]) if bars else None


async def _synth_from_snapshot(session: BrokerSession, symbol: str) -> list[dict] | None:
    """Last resort when DXLink itself is unavailable: one daily bar synthesized from the current
    equity snapshot quote (o=h=l=c=mark). Coarse on purpose -- a degrade path, not a real candle."""
    try:
        from tastytrade import market_data as _market_data
    except ImportError:
        return None
    try:
        quotes = await session.call(_market_data.get_market_data_by_type, equities=[symbol])
    except Exception:
        return None
    if not quotes or quotes[0].mark is None:
        return None
    mark = float(quotes[0].mark)
    if mark <= 0:
        return None
    today = datetime.now(tz=UTC).date()
    return [{"t": _day_epoch(today), "o": mark, "h": mark, "l": mark, "c": mark, "v": None}]


async def get_candles(
    conn: sqlite3.Connection,
    session: BrokerSession,
    cfg: dict,
    symbol: str,
    *,
    period: str = PERIOD,
    now: float | None = None,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    symbol = symbol.strip().upper()
    ttl = cfg.get("refresh", {}).get("candles_ttl_seconds", 3600)
    retry_floor = min(ttl, 60.0)

    async with _lock_for(symbol, period):
        last_backfill = _cache.get_candle_meta(conn, symbol, period)

        if last_backfill is None:
            dolt_bars = await _fetch_dolt_ohlcv(cfg, symbol)
            if dolt_bars:
                _cache.write_candles(conn, symbol, period, dolt_bars)
                last_backfill = dolt_bars[-1]["t"]
                _cache.set_candle_meta(conn, symbol, period, last_backfill)

        stale = last_backfill is None or (now - last_backfill) > ttl
        if stale:
            attempt_key = f"{symbol}:{period}"
            last_attempt = _cache.peek(conn, _ATTEMPT_BUCKET, attempt_key)
            attempt_due = last_attempt is None or (now - last_attempt[1]) >= retry_floor
            if attempt_due:
                start = (
                    datetime.fromtimestamp(last_backfill, tz=UTC).date() + timedelta(days=1)
                    if last_backfill
                    else datetime.now(tz=UTC).date() - timedelta(days=_INITIAL_TAIL_LOOKBACK_DAYS)
                )
                tail_bars = await _dxlink_tail(session, symbol, start)
                if not tail_bars:
                    tail_bars = await _synth_from_snapshot(session, symbol)
                _cache.put(conn, _ATTEMPT_BUCKET, attempt_key, now, now)
                if tail_bars:
                    _cache.write_candles(conn, symbol, period, tail_bars)
                    last_backfill = max(b["t"] for b in tail_bars)
                    _cache.set_candle_meta(conn, symbol, period, last_backfill)

    bars = _cache.read_candles(conn, symbol, period)
    return {"ok": True, "symbol": symbol, "period": period, "as_of": now, "stale": stale, "bars": bars}
