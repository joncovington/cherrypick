"""cherrypick.core.streamer — a generic persistent DXLink option-chain streaming engine.

Maintains one WebSocket to tastytrade's DXLink feed and writes the latest Quote / Greeks / Trade /
Summary events to a `cherrypick.core.streamcache` cache, giving each traded underlying its own
near-the-money strike window (which doubles as that symbol's GEX profile). Extracted from MEIC's
streamer daemon (plan Phase A) so both MEIC and the standalone GEX module run one engine instead of two.

Everything MEIC-specific is injected, so the engine itself has no MEIC dependency:
  * `session_factory()` -> a tastytrade Session (thread-appropriate; the engine calls it on its loop).
  * `extra_subscriptions(symbols)` -> {event_type: [streamer_symbol]} — extra symbols to keep subscribed
    beyond each symbol's live window (MEIC adds its open-position legs; the default is underlyings only).
  * `protected_symbols()` -> a set never unsubscribed when a window re-centres (MEIC's open legs).
  * `trade_hook(engine, symbol, price, ts)` -> called on every underlying Trade tick (MEIC's ORB capture).
  * `expirations_for(symbol)` -> extra ISO expiration dates to serve chain metadata plus an ATM quote
    window for, beyond the nearest expiration served by default (a weekly calendar module's 4DTE/7DTE
    legs). Re-read every window pass — like the legs above, growth is served with no restart. None
    (the default) is exactly the historical nearest-expiration-only behavior.

Pure engine: no argparse, no HTTP server, no PID file, no config file — a thin per-consumer wrapper adds
those. `run()` blocks with reconnect/backoff until `stop()` (or SIGTERM/SIGINT if `install_signals`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
import traceback
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cherrypick.core import streamcache

_RECONNECT_BASE = 2.0
_RECONNECT_MAX = 60.0
_COMMIT_BATCH_INTERVAL_S = 0.5
_COMMIT_BATCH_MAX_PENDING = 25
# 2+4+8+16+32+60s ~= 2 minutes of retries for a single symbol's chain fetch before giving up and
# disabling its window for the rest of this connection's lifetime (same as before this existed).
_CHAIN_FETCH_MAX_ATTEMPTS = 6
# A requested extra expiration that is not listed yet (a next-Monday weekly asked for up to ~13 days
# out) re-checks the full chain on this cooldown rather than every window pass — the retry IS the
# next cooldown lapse, so there is no backoff loop to stall the window task.
_EXTRA_CHAIN_REFETCH_COOLDOWN_S = 900.0

_ET = ZoneInfo("America/New_York")


def _et_date(ts: float) -> str:
    """The ET trading date a wall-clock timestamp belongs to (stream_summary's day key)."""
    return datetime.fromtimestamp(ts, tz=_ET).date().isoformat()


class _State:
    """Engine state for one connection lifetime (the connection is recreated across reconnects, but
    the cache connection and per-symbol window tracking persist for the daemon's whole run)."""

    def __init__(self, conn, symbols: list[str]) -> None:
        self.stop_event = asyncio.Event()
        self.subscribed: dict[str, list[str]] = {"Trade": [], "Quote": [], "Greeks": [], "Summary": []}
        self.reconnect_count = 0
        self.last_event_at: str | None = None
        self.conn = conn
        self.symbols = list(symbols)
        self.chains: dict[str, dict] = {}  # symbol -> {streamer_symbol: option}
        # Window tracking is keyed by the underlying symbol for the default nearest-expiration
        # window, and by "SYMBOL@YYYY-MM-DD" for each extra requested expiration — one key space, so
        # _total_subscribed and _apply_subscriptions' window-union protection cover both unchanged.
        self.window_syms: dict[str, list[str]] = {}  # window key -> subscribed window symbols
        self.centers: dict[str, float] = {}  # window key -> price the window is centred on
        self.window_strike_counts: dict[str, int] = {}  # window key -> strike count last used
        self.full_chains: dict[str, dict[str, dict]] = {}  # symbol -> {iso date -> {streamer_symbol: option}}
        self.last_full_fetch: dict[str, float] = {}  # symbol -> monotonic-ish ts of last full-chain fetch
        self.pending_writes = 0
        self.last_commit_at = 0.0


class ChainStreamer:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Any],
        db_path: Path | str,
        symbols: list[str],
        extra_subscriptions: Callable[[list[str]], dict[str, list[str]]] | None = None,
        protected_symbols: Callable[[], set[str]] | None = None,
        trade_hook: Callable[[ChainStreamer, str, float | None, float], None] | None = None,
        expirations_for: Callable[[str], list[str]] | None = None,
        window_strike_count: int = 60,
        window_strike_count_for: Callable[[str], int] | None = None,
        window_refresh_pts: float = 1.0,
        window_poll_s: float = 5.0,
        subscription_poll_s: float = 30.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.db_path = Path(db_path)
        self.symbols = [s.strip().upper() for s in symbols]
        self._extra_subscriptions = extra_subscriptions
        self._protected_symbols = protected_symbols or (lambda: set())
        self._trade_hook = trade_hook
        self._expirations_for = expirations_for
        self.window_strike_count = window_strike_count
        self._window_strike_count_for = window_strike_count_for
        self.window_refresh_pts = window_refresh_pts
        self.window_poll_s = window_poll_s
        self.subscription_poll_s = subscription_poll_s
        self.log = logger or logging.getLogger("cherrypick.core.streamer")
        self.state: _State | None = None

    # -- injected policy defaults ----------------------------------------------------------------
    def _subscriptions(self) -> dict[str, list[str]]:
        if self._extra_subscriptions is not None:
            return self._extra_subscriptions(self.symbols)
        # Default: subscribe Trade + Summary for the underlyings (spot + session data); per-symbol
        # windows add the option Quote/Greeks/Summary/Trade themselves.
        return {"Trade": list(self.symbols), "Quote": [], "Greeks": [], "Summary": list(self.symbols)}

    # -- commit batching -------------------------------------------------------------------------
    def _maybe_commit(self, state: _State) -> None:
        state.pending_writes += 1
        now = time.time()
        if (
            state.pending_writes >= _COMMIT_BATCH_MAX_PENDING
            or (now - state.last_commit_at) >= _COMMIT_BATCH_INTERVAL_S
        ):
            state.conn.commit()
            state.pending_writes = 0
            state.last_commit_at = now

    def _total_subscribed(self, state: _State) -> int:
        window_union: set[str] = set()
        for syms in state.window_syms.values():
            window_union.update(syms)
        total = 0
        for key in ("Trade", "Quote", "Greeks", "Summary"):
            total += len(set(state.subscribed.get(key, [])) | window_union)
        return total

    # -- connection lifetime ---------------------------------------------------------------------
    async def _run_stream(self, state: _State) -> None:
        from tastytrade import DXLinkStreamer
        from tastytrade.dxfeed import Greeks, Quote, Summary, Trade

        session = self.session_factory()
        self.log.info("Connecting DXLinkStreamer…")
        async with DXLinkStreamer(session) as streamer:
            streamcache.upsert_status(
                state.conn,
                pid=os.getpid(),
                connected_since=datetime.now(UTC).isoformat(),
                reconnect_count=state.reconnect_count,
            )
            self.log.info("DXLinkStreamer connected (reconnects: %d)", state.reconnect_count)
            await self._apply_subscriptions(
                streamer, state, self._subscriptions(), Trade, Quote, Greeks, Summary
            )
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._listen_trade(streamer, state, Trade))
                tg.create_task(self._listen_quote(streamer, state, Quote))
                tg.create_task(self._listen_greeks(streamer, state, Greeks))
                tg.create_task(self._listen_summary(streamer, state, Summary))
                tg.create_task(self._poll_subscriptions(streamer, state, Trade, Quote, Greeks, Summary))
                tg.create_task(self._flush_status(state))
                tg.create_task(self._watch_stop(state))
                for sym in self.symbols:
                    tg.create_task(
                        self._symbol_refresher(streamer, state, sym, Quote, Greeks, Summary, Trade)
                    )

    async def _apply_subscriptions(
        self, streamer, state: _State, subs: dict, Trade, Quote, Greeks, Summary
    ) -> None:
        cls_map = {"Trade": Trade, "Quote": Quote, "Greeks": Greeks, "Summary": Summary}
        window_union: set[str] = set()
        for syms in state.window_syms.values():
            window_union.update(syms)
        for key, symbols in subs.items():
            current = set(state.subscribed.get(key, []))
            wanted = set(symbols)
            add = wanted - current
            remove = current - wanted
            if key in ("Quote", "Greeks", "Summary"):
                remove -= window_union  # a window still wants these even if the extra-policy dropped them
            cls = cls_map[key]
            if add:
                await streamer.subscribe(cls, list(add))
                self.log.info("Subscribed %s %s", key, list(add))
            if remove:
                await streamer.unsubscribe(cls, list(remove))
                self.log.info("Unsubscribed %s %s", key, list(remove))
            state.subscribed[key] = list(wanted)
        streamcache.upsert_status(state.conn, subscribed_symbols=self._total_subscribed(state))

    async def _poll_subscriptions(self, streamer, state: _State, Trade, Quote, Greeks, Summary) -> None:
        while not state.stop_event.is_set():
            await asyncio.sleep(self.subscription_poll_s)
            if state.stop_event.is_set():
                break
            try:
                await self._apply_subscriptions(
                    streamer, state, self._subscriptions(), Trade, Quote, Greeks, Summary
                )
                if state.last_event_at:
                    streamcache.upsert_status(state.conn, last_event_at=state.last_event_at)
            except Exception as exc:
                self.log.warning("Subscription poll error: %s", exc)

    # -- listeners -------------------------------------------------------------------------------
    def _touch(self, state: _State, ts: float) -> None:
        state.last_event_at = datetime.fromtimestamp(ts, tz=UTC).isoformat()

    async def _listen_trade(self, streamer, state: _State, Trade) -> None:
        conn = state.conn
        async for event in streamer.listen(Trade):
            if state.stop_event.is_set():
                break
            ts = time.time()
            try:
                conn.execute(
                    "INSERT INTO stream_trades (symbol, last, change, volume, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
                    "last=excluded.last, change=excluded.change, volume=excluded.volume, "
                    "updated_at=excluded.updated_at",
                    (
                        event.event_symbol,
                        streamcache.to_float(event.price),
                        streamcache.to_float(event.change),
                        streamcache.to_float(event.day_volume),
                        ts,
                    ),
                )
                self._maybe_commit(state)
                self._touch(state, ts)
                if self._trade_hook is not None:
                    self._trade_hook(self, event.event_symbol, streamcache.to_float(event.price), ts)
            except Exception as exc:
                self.log.warning("Trade write error: %s", exc)

    async def _listen_quote(self, streamer, state: _State, Quote) -> None:
        conn = state.conn
        async for event in streamer.listen(Quote):
            if state.stop_event.is_set():
                break
            ts = time.time()
            bid = streamcache.to_float(event.bid_price)
            ask = streamcache.to_float(event.ask_price)
            mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else None
            try:
                conn.execute(
                    "INSERT INTO stream_quotes (symbol, bid, ask, mid, bid_size, ask_size, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
                    "bid=excluded.bid, ask=excluded.ask, mid=excluded.mid, "
                    "bid_size=excluded.bid_size, ask_size=excluded.ask_size, updated_at=excluded.updated_at",
                    (
                        event.event_symbol,
                        bid,
                        ask,
                        mid,
                        streamcache.to_float(event.bid_size),
                        streamcache.to_float(event.ask_size),
                        ts,
                    ),
                )
                self._maybe_commit(state)
                self._touch(state, ts)
            except Exception as exc:
                self.log.warning("Quote write error: %s", exc)

    async def _listen_greeks(self, streamer, state: _State, Greeks) -> None:
        conn = state.conn
        async for event in streamer.listen(Greeks):
            if state.stop_event.is_set():
                break
            ts = time.time()
            try:
                conn.execute(
                    "INSERT INTO stream_greeks "
                    "(symbol, delta, gamma, theta, vega, rho, iv, price, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
                    "delta=excluded.delta, gamma=excluded.gamma, theta=excluded.theta, "
                    "vega=excluded.vega, rho=excluded.rho, iv=excluded.iv, "
                    "price=excluded.price, updated_at=excluded.updated_at",
                    (
                        event.event_symbol,
                        streamcache.to_float(event.delta),
                        streamcache.to_float(event.gamma),
                        streamcache.to_float(event.theta),
                        streamcache.to_float(event.vega),
                        streamcache.to_float(event.rho),
                        streamcache.to_float(event.volatility),
                        streamcache.to_float(event.price),
                        ts,
                    ),
                )
                self._maybe_commit(state)
                self._touch(state, ts)
            except Exception as exc:
                self.log.warning("Greeks write error: %s", exc)

    async def _listen_summary(self, streamer, state: _State, Summary) -> None:
        conn = state.conn
        async for event in streamer.listen(Summary):
            if state.stop_event.is_set():
                break
            ts = time.time()
            wrote = False
            try:
                oi = event.open_interest
                if oi is not None:
                    conn.execute(
                        "INSERT INTO stream_oi (symbol, open_interest, updated_at) "
                        "VALUES (?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
                        "open_interest=excluded.open_interest, updated_at=excluded.updated_at",
                        (event.event_symbol, int(oi), ts),
                    )
                    wrote = True
                # The UNDERLYING's Summary carries the session's exchange-official OHLC and
                # prior close. Cash indices have no OI, so the old oi-only branch dropped
                # these events on the floor even though they were already on the wire.
                # Persisted per (symbol, trade_date): today's row feeds the intraday-range
                # gates; the accumulated rows feed a true-range ATR once a lookback's worth
                # of sessions exists.
                if event.event_symbol in self.symbols:
                    high = streamcache.to_float(getattr(event, "day_high_price", None))
                    low = streamcache.to_float(getattr(event, "day_low_price", None))
                    if high is not None or low is not None:
                        conn.execute(
                            "INSERT INTO stream_summary (symbol, trade_date, day_open, day_high, "
                            "day_low, day_close, prev_day_close, updated_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT(symbol, trade_date) DO UPDATE SET "
                            "day_open=excluded.day_open, day_high=excluded.day_high, "
                            "day_low=excluded.day_low, day_close=excluded.day_close, "
                            "prev_day_close=excluded.prev_day_close, updated_at=excluded.updated_at",
                            (
                                event.event_symbol,
                                _et_date(ts),
                                streamcache.to_float(getattr(event, "day_open_price", None)),
                                high,
                                low,
                                streamcache.to_float(getattr(event, "day_close_price", None)),
                                streamcache.to_float(getattr(event, "prev_day_close_price", None)),
                                ts,
                            ),
                        )
                        wrote = True
                if wrote:
                    self._maybe_commit(state)
                    self._touch(state, ts)
            except Exception as exc:
                self.log.warning("Summary write error: %s", exc)

    # -- per-symbol ATM/GEX window ---------------------------------------------------------------
    async def _fetch_dte0_chain(self, underlying: str) -> dict:
        from tastytrade.instruments import get_option_chain

        session = self.session_factory()
        chain = await get_option_chain(session, underlying)
        if not chain:
            return {}
        nearest = min(chain.keys(), key=lambda e: abs((e - date.today()).days))
        return {o.streamer_symbol: o for o in chain[nearest] if getattr(o, "streamer_symbol", None)}

    async def _fetch_dte0_chain_with_retry(self, symbol: str, state: _State) -> dict | None:
        """Retry a failed chain fetch with the same doubling backoff `run_async` uses for
        reconnects, instead of giving up after one attempt. A transient broker-side hiccup (an
        HTML error page instead of JSON, a momentary auth blip) previously left a symbol's window
        permanently disabled until the next full DXLink reconnect — which can be an hour or more
        away — while every OTHER symbol kept ticking fine and masked it from the daemon's own
        aggregate staleness check. `stream_symbol_health` is updated on every attempt (error set on
        failure, cleared + `chain_loaded_at` stamped on success) so a caller reading the cache can
        see this specific symbol's state even while the retry loop is still running."""
        delay = _RECONNECT_BASE
        for attempt in range(1, _CHAIN_FETCH_MAX_ATTEMPTS + 1):
            self.log.info(
                "[%s] Fetching 0DTE option chain… (attempt %d/%d)", symbol, attempt, _CHAIN_FETCH_MAX_ATTEMPTS
            )
            try:
                chain = await self._fetch_dte0_chain(symbol)
                self.log.info("[%s] 0DTE chain loaded: %d options", symbol, len(chain))
                streamcache.write_chain(state.conn, chain)
                streamcache.upsert_symbol_health(
                    state.conn, symbol, chain_loaded_at=datetime.now(UTC).isoformat(), chain_fetch_error=None
                )
                return chain
            except Exception as exc:
                streamcache.upsert_symbol_health(state.conn, symbol, chain_fetch_error=str(exc))
                if attempt == _CHAIN_FETCH_MAX_ATTEMPTS:
                    self.log.error(
                        "[%s] chain fetch failed %d/%d times, giving up: %s — window disabled",
                        symbol,
                        attempt,
                        _CHAIN_FETCH_MAX_ATTEMPTS,
                        exc,
                    )
                    return None
                self.log.warning(
                    "[%s] chain fetch attempt %d/%d failed: %s — retrying in %.0fs",
                    symbol,
                    attempt,
                    _CHAIN_FETCH_MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RECONNECT_MAX)
        return None  # unreachable, but keeps type-checkers honest

    async def _symbol_refresher(
        self, streamer, state: _State, symbol: str, Quote, Greeks, Summary, Trade
    ) -> None:
        chain = await self._fetch_dte0_chain_with_retry(symbol, state)
        if chain is None:
            return
        state.chains[symbol] = chain

        state.window_syms.setdefault(symbol, [])
        while not state.stop_event.is_set():
            price = streamcache.current_underlying_price(state.conn, symbol)
            if price is None:
                await asyncio.sleep(1)
                continue
            strike_count = (
                self._window_strike_count_for(symbol)
                if self._window_strike_count_for
                else self.window_strike_count
            )
            center = state.centers.get(symbol)
            prev_strike_count = state.window_strike_counts.get(symbol)
            # Recompute on a price move past the refresh threshold OR a changed strike count (e.g. a
            # consumer module widening its window_hints mid-session in response to missing_leg_quotes)
            # -- a widen-only request at an unchanged price must not sit unapplied until the next move.
            if (
                center is None
                or abs(price - center) >= self.window_refresh_pts
                or strike_count != prev_strike_count
            ):
                new_syms = streamcache.atm_window_syms(state.chains[symbol], price, strike_count)
                state.window_strike_counts[symbol] = strike_count
                current_syms = state.window_syms.get(symbol, [])
                if new_syms != current_syms:
                    old_set, new_set = set(current_syms), set(new_syms)
                    add, remove = new_set - old_set, old_set - new_set
                    try:
                        if add:
                            add_list = list(add)
                            await streamer.subscribe(Quote, add_list)
                            await streamer.subscribe(Greeks, add_list)
                            await streamer.subscribe(Summary, add_list)
                            await streamer.subscribe(Trade, add_list)
                        if remove:
                            # Protect the injected set AND any other live window's symbols — on a
                            # 0DTE Friday an extra-expiration window can hold the same date this
                            # nearest window serves, and a re-centre here must not tear it down.
                            safe_remove = (
                                remove - self._protected_symbols() - self._window_syms_except(state, symbol)
                            )
                            if safe_remove:
                                srl = list(safe_remove)
                                await streamer.unsubscribe(Quote, srl)
                                await streamer.unsubscribe(Greeks, srl)
                                await streamer.unsubscribe(Summary, srl)
                                await streamer.unsubscribe(Trade, srl)
                        state.window_syms[symbol] = new_syms
                        streamcache.upsert_status(
                            state.conn, subscribed_symbols=self._total_subscribed(state)
                        )
                        self.log.info(
                            "[%s] window re-centered at %.2f (+%d/-%d symbols, total: %d)",
                            symbol,
                            price,
                            len(add),
                            len(remove),
                            len(new_syms),
                        )
                    except Exception as exc:
                        self.log.warning("[%s] window update error: %s", symbol, exc)
                state.centers[symbol] = price
            if self._expirations_for is not None:
                try:
                    await self._refresh_extra_windows(
                        streamer, state, symbol, price, strike_count, Quote, Greeks, Summary, Trade
                    )
                except Exception as exc:
                    self.log.warning("[%s] extra-expiration refresh error: %s", symbol, exc)
            await asyncio.sleep(self.window_poll_s)

    # -- extra requested expirations (beyond the nearest) ----------------------------------------
    def _window_syms_except(self, state: _State, key: str) -> set[str]:
        """Symbols any OTHER live window still wants — never unsubscribed on this window's behalf.
        Extra-expiration windows share `window_syms` under composite `SYMBOL@date` keys, so one dict
        answers for every window."""
        out: set[str] = set()
        for other_key, syms in state.window_syms.items():
            if other_key != key:
                out.update(syms)
        return out

    async def _fetch_full_chain(self, underlying: str) -> dict[str, dict]:
        """The whole listed chain for an underlying, keyed by ISO expiration date. One attempt, no
        backoff loop — the extra-expiration refresher calls this on a cooldown cadence, so the retry
        IS the next cooldown lapse (unlike the nearest-window fetch, whose 2-minute backoff guards a
        once-per-connection call)."""
        from tastytrade.instruments import get_option_chain

        session = self.session_factory()
        chain = await get_option_chain(session, underlying)
        out: dict[str, dict] = {}
        for exp, options in (chain or {}).items():
            slice_map = {o.streamer_symbol: o for o in options if getattr(o, "streamer_symbol", None)}
            if slice_map:
                out[exp.isoformat()] = slice_map
        return out

    def _wanted_extra_expirations(self, symbol: str) -> list[str]:
        """Valid, still-current ISO dates the injected policy wants for this symbol. The callable is
        consumer code reading registry files, so a bad read or a junk entry costs this pass, never
        the task. A date is dropped only once it is past (ET) — an expiration is its own last valid
        day."""
        try:
            raw = self._expirations_for(symbol) or []
        except Exception as exc:
            self.log.warning("[%s] expirations_for error: %s", symbol, exc)
            return []
        today = datetime.now(tz=_ET).date()
        wanted: set[str] = set()
        for value in raw:
            try:
                parsed = date.fromisoformat(str(value).strip())
            except ValueError:
                continue
            if parsed >= today:
                wanted.add(parsed.isoformat())
        return sorted(wanted)

    async def _refresh_extra_windows(
        self, streamer, state: _State, symbol: str, price: float, strike_count: int,
        Quote, Greeks, Summary, Trade,
    ) -> None:
        """Maintain an ATM window per extra requested expiration, beside the nearest-expiration one.

        Called every window pass from `_symbol_refresher` (same task — one task per symbol
        serializes every subscribe/unsubscribe for it, so windows can never race each other). Each
        served date gets its chain slice written to `stream_chain`, a health row under the composite
        `SYMBOL@date` key, and the same Quote/Greeks/Summary/Trade window the nearest expiration
        gets, re-centred off the shared spot with the same strike count (window_hints apply). A
        wanted date the broker does not list yet gets a health error and a re-check on the fetch
        cooldown; a date that rolled past or left the request is unsubscribed, minus anything the
        protected set or another window still wants."""
        wanted = self._wanted_extra_expirations(symbol)
        prefix = f"{symbol}@"

        for key in [k for k in state.window_syms if k.startswith(prefix)]:
            if key[len(prefix):] not in wanted:
                await self._retire_extra_window(streamer, state, key, Quote, Greeks, Summary, Trade)
        if not wanted:
            return

        known = state.full_chains.get(symbol) or {}
        missing = [d for d in wanted if d not in known]
        now = time.time()
        if missing and now - state.last_full_fetch.get(symbol, 0.0) >= _EXTRA_CHAIN_REFETCH_COOLDOWN_S:
            state.last_full_fetch[symbol] = now
            try:
                known = await self._fetch_full_chain(symbol)
                state.full_chains[symbol] = known
            except Exception as exc:
                for d in missing:
                    streamcache.upsert_symbol_health(state.conn, f"{symbol}@{d}", chain_fetch_error=str(exc))
                self.log.warning("[%s] full chain fetch failed: %s", symbol, exc)
                return
            for d in [d for d in wanted if d not in known]:
                streamcache.upsert_symbol_health(
                    state.conn, f"{symbol}@{d}", chain_fetch_error="expiration not listed"
                )
                self.log.warning("[%s] requested expiration %s not listed yet", symbol, d)

        nearest_syms = set(state.chains.get(symbol) or {})
        for d in wanted:
            slice_map = known.get(d)
            if not slice_map:
                continue  # unlisted — health row above, re-checked on the cooldown
            if set(slice_map) == nearest_syms:
                continue  # the nearest-expiration window already serves this exact date (0DTE Friday)
            key = f"{symbol}@{d}"
            first_build = key not in state.window_syms
            center = state.centers.get(key)
            prev_strike_count = state.window_strike_counts.get(key)
            if (
                center is not None
                and abs(price - center) < self.window_refresh_pts
                and strike_count == prev_strike_count
            ):
                continue
            new_syms = streamcache.atm_window_syms(slice_map, price, strike_count)
            state.window_strike_counts[key] = strike_count
            current_syms = state.window_syms.get(key, [])
            if new_syms != current_syms:
                old_set, new_set = set(current_syms), set(new_syms)
                add, remove = new_set - old_set, old_set - new_set
                try:
                    if add:
                        add_list = list(add)
                        await streamer.subscribe(Quote, add_list)
                        await streamer.subscribe(Greeks, add_list)
                        await streamer.subscribe(Summary, add_list)
                        await streamer.subscribe(Trade, add_list)
                    if remove:
                        safe_remove = (
                            remove - self._protected_symbols() - self._window_syms_except(state, key)
                        )
                        if safe_remove:
                            srl = list(safe_remove)
                            await streamer.unsubscribe(Quote, srl)
                            await streamer.unsubscribe(Greeks, srl)
                            await streamer.unsubscribe(Summary, srl)
                            await streamer.unsubscribe(Trade, srl)
                    state.window_syms[key] = new_syms
                    streamcache.upsert_status(state.conn, subscribed_symbols=self._total_subscribed(state))
                    self.log.info(
                        "[%s] extra window %s re-centered at %.2f (+%d/-%d symbols, total: %d)",
                        symbol,
                        d,
                        price,
                        len(add),
                        len(remove),
                        len(new_syms),
                    )
                except Exception as exc:
                    self.log.warning("[%s] extra window %s update error: %s", symbol, d, exc)
            if first_build and key in state.window_syms:
                streamcache.write_chain(state.conn, slice_map)
                streamcache.upsert_symbol_health(
                    state.conn, key, chain_loaded_at=datetime.now(UTC).isoformat(), chain_fetch_error=None
                )
            state.centers[key] = price

    async def _retire_extra_window(
        self, streamer, state: _State, key: str, Quote, Greeks, Summary, Trade
    ) -> None:
        """Unsubscribe a departed extra-expiration window (its date rolled past, or the request
        shrank), keeping anything the protected set or another live window still wants. Its
        stream_chain rows stay — chain metadata is history, not a subscription."""
        syms = set(state.window_syms.pop(key, []))
        state.centers.pop(key, None)
        state.window_strike_counts.pop(key, None)
        safe_remove = syms - self._protected_symbols() - self._window_syms_except(state, key)
        if safe_remove:
            try:
                srl = list(safe_remove)
                await streamer.unsubscribe(Quote, srl)
                await streamer.unsubscribe(Greeks, srl)
                await streamer.unsubscribe(Summary, srl)
                await streamer.unsubscribe(Trade, srl)
            except Exception as exc:
                self.log.warning("extra window %s retire error: %s", key, exc)
        streamcache.upsert_status(state.conn, subscribed_symbols=self._total_subscribed(state))
        self.log.info("extra window %s retired (-%d symbols)", key, len(safe_remove))

    async def _flush_status(self, state: _State) -> None:
        while not state.stop_event.is_set():
            await asyncio.sleep(5)
            if state.last_event_at:
                try:
                    streamcache.upsert_status(state.conn, last_event_at=state.last_event_at)
                except Exception:
                    pass

    async def _watch_stop(self, state: _State) -> None:
        await state.stop_event.wait()
        raise asyncio.CancelledError("stop requested")

    # -- public entrypoints ----------------------------------------------------------------------
    def stop(self) -> None:
        if self.state is not None:
            self.state.stop_event.set()

    async def run_async(self) -> None:
        """Connect and stream with reconnect/backoff until stopped."""
        conn = streamcache.connect(self.db_path)
        state = _State(conn, self.symbols)
        self.state = state
        self.log.info("Streaming symbols: %s (±%d strikes each)", self.symbols, self.window_strike_count)
        delay = _RECONNECT_BASE
        while not state.stop_event.is_set():
            try:
                await self._run_stream(state)
                delay = _RECONNECT_BASE
            except asyncio.CancelledError:
                if state.stop_event.is_set():
                    break
                self.log.warning("Stream cancelled unexpectedly — will reconnect")
            except Exception as exc:
                if state.stop_event.is_set():
                    break
                # TaskGroup wraps failures in an ExceptionGroup whose str() hides detail — log each.
                if isinstance(exc, BaseExceptionGroup):
                    for i, sub in enumerate(exc.exceptions):
                        self.log.warning(
                            "Stream error sub-exception %d/%d: %s",
                            i + 1,
                            len(exc.exceptions),
                            "".join(traceback.format_exception(type(sub), sub, sub.__traceback__)),
                        )
                self.log.warning("Stream error: %s — reconnecting in %.0fs", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RECONNECT_MAX)
                state.reconnect_count += 1
        conn.close()
        self.log.info("Streamer stopped.")

    def run(self, install_signals: bool = True) -> None:
        """Blocking run: set up SIGTERM/SIGINT (optional) and drive the async reconnect loop."""
        if install_signals:

            def _handle(sig, frame):
                self.log.info("Signal %s received — stopping", sig)
                self.stop()

            try:
                signal.signal(signal.SIGTERM, _handle)
                signal.signal(signal.SIGINT, _handle)
            except ValueError:
                pass  # not on the main thread — caller drives stop() itself
        asyncio.run(self.run_async())
