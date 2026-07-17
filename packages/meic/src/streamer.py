"""MEICAgent DXLink Streamer Daemon.

Maintains a single persistent WebSocket to tastytrade's DXLink feed and writes
the latest Quote, Greeks, and Trade events to stream_cache.db in the data home, for every
symbol configured in config.json's `symbols` list. Each traded symbol gets its
own subscription window (near-the-money option strikes) on the same connection —
that window doubles as both the entry-strike-selection data and that symbol's
own GEX profile, so GEX is computed per-symbol automatically.

Usage:
  python src/streamer.py                       # start daemon (foreground), symbols from config.json
  python src/streamer.py --symbol XSP --symbol SPX  # override traded symbols for this run
  python src/streamer.py --stop                # send SIGTERM to running daemon
  python src/streamer.py --status               # print cache status and exit

The main loop in tt.py reads from the cache (age < 10s) before falling
back to a fresh DXLink connection. The dashboard reads it for live P&L.
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import json
import logging
import os
import signal
import socketserver
import sqlite3
import sys
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytz

# Allow running as `python src/streamer.py` from the project root, and put the cherrypick-core submodule
# (src/_core) on sys.path so `import cherrypick.core...` resolves without an install — the persistent
# DXLink engine and cache schema now live in the shared core.
sys.path.insert(0, os.path.dirname(__file__))
_CORE = os.path.join(os.path.dirname(__file__), "_core")
if os.path.isdir(_CORE) and _CORE not in sys.path:
    sys.path.insert(0, _CORE)

_ET = pytz.timezone("America/New_York")

from cherrypick.core import streamcache  # noqa: E402
from cherrypick.core.streamer import ChainStreamer  # noqa: E402

import paths as _paths  # noqa: E402
from session import get_session  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent.parent
_CACHE_DB  = _paths.stream_cache_path()      # ~/.cherrypick/data/meic/ (or MEIC_DATA_DIR)
_PID_FILE  = _paths.data_path("streamer.pid")
_TRADES_DB = _paths.live_db_path()
_LOG_FILE  = _paths.log_path("streamer.log")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Rotate at 10 MB, keep 5 backups (≈60 MB cap) so the log can't grow without bound —
    # it had reached multiple GB because the DXLink SDK logs every raw Quote/Trade/Greeks
    # message at DEBUG. Handler level INFO drops that firehose regardless of which library
    # logger emits it (the level check on a propagated record happens at the handler).
    fh = RotatingFileHandler(_LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    # Belt-and-suspenders: silence the chattiest third-party loggers at the source
    # (the DXLink message dump, per-poll HTTP request lines, websocket frames).
    for _noisy in ("tastytrade", "httpx", "httpcore", "websockets", "asyncio"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache DB
# ---------------------------------------------------------------------------



# REST cache TTLs (seconds) per key
_REST_TTL: dict[str, float] = {
    "account_info":    20.0,
    "positions":       20.0,
    "working_orders":  10.0,
    "market_overview": 60.0,
}


def _write_rest_cache(conn: sqlite3.Connection, key: str, data: dict) -> None:
    conn.execute(
        "INSERT INTO stream_rest_cache (key, data_json, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET data_json=excluded.data_json, updated_at=excluded.updated_at",
        (key, json.dumps(data, default=str), time.time()),
    )
    conn.commit()


def _read_rest_cache(conn: sqlite3.Connection, key: str) -> dict | None:
    row = conn.execute(
        "SELECT data_json, updated_at FROM stream_rest_cache WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    ttl = _REST_TTL.get(key, 30.0)
    if (time.time() - row["updated_at"]) > ttl:
        return None
    return json.loads(row["data_json"])




# ---------------------------------------------------------------------------
# Symbol resolution
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    p = _paths.config_path()   # home-first (~/.cherrypick/config/meic.json), in-repo fallback
    if p.exists():
        return json.loads(p.read_text())
    return {}


def _open_trade_streamer_symbols() -> list[str]:
    """Return streamer symbols for all legs of currently open ICs."""
    if not _TRADES_DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(_TRADES_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT put_symbol, call_symbol, long_put_symbol, long_call_symbol "
            "FROM ic_trades WHERE status IN ('pending','open','partial','partial_entry')"
        ).fetchall()
        conn.close()
        symbols = []
        for r in rows:
            for col in ("put_symbol", "call_symbol", "long_put_symbol", "long_call_symbol"):
                sym = r[col]
                if sym:
                    symbols.append(sym)
        return list(set(symbols))
    except Exception as exc:
        logger.warning("Failed to load open trade symbols: %s", exc)
        return []


def _resolve_subscriptions(symbols: list[str]) -> dict[str, list[str]]:
    """Return {event_type: [streamer_symbols]} for current open positions."""
    option_syms = _open_trade_streamer_symbols()
    return {
        "Trade": list(symbols),
        "Quote": option_syms,
        "Greeks": option_syms,
        # Seed Summary with each underlying so the channel opens at startup even with no open
        # trades. Each underlying's Summary event carries OHLC/volume (not OI); option OI arrives
        # once each symbol's window is subscribed by the core streamer engine.
        "Summary": list(symbols) + option_syms,
    }


# ---------------------------------------------------------------------------
# Streamer daemon
# ---------------------------------------------------------------------------

_SYMBOL_POLL_S    = 30.0    # how often to re-check open trades (passed as the engine's subscription poll)
_HTTP_PORT        = 7699    # streamer API port

# Per-symbol subscription window. Every traded symbol gets exactly one window sized to the
# GEX requirement (wider than the ATM-only requirement of ~15 strikes) — GEX profile accuracy
# needs strikes further from the money than entry strike selection does, and a single window
# per symbol comfortably covers both needs rather than maintaining two separate windows.
# (Reconnect backoff + window re-center cadence now live in cherrypick.core.streamer, whose defaults
# match the values MEIC used here; only the strike count and poll interval are passed through.)
_WINDOW_STRIKE_COUNT = 20   # strikes each side of center (~40 strikes × 2 types = ~80 symbols)

_NOMINAL_WING_PRICE = 0.01  # fallback price for a long wing leg with no quote at all

# Shared state for the HTTP server thread
_rest_loop: asyncio.AbstractEventLoop | None = None      # dedicated REST loop

# Default arg values for each command (mirrors argparse defaults in tt.py)
_CMD_DEFAULTS: dict[str, dict] = {
    "get_connection_status": {},
    "list_accounts": {},
    "get_account_info":    {"account_number": None},
    "get_positions":       {"account_number": None},
    "get_market_overview": {"quotes_timeout": 4.0},
    "get_quote":           {"timeout": 6.0},
    "get_option_chain":    {"expiration": None, "include_greeks": False, "include_quotes": False,
                            "strike_count": 15, "around_price": None,
                            "greeks_timeout": 6.0, "quotes_timeout": 6.0},
    "get_strategies":      {"target_dte": 0, "wing_width": 5, "short_delta": 0.15,
                            "around_price": None, "greeks_timeout": 6.0, "quotes_timeout": 6.0},
    "get_working_orders":  {"account_number": None},
    "execute_trade":       {"account_number": None, "dry_run": True},
    "adjust_order":        {"account_number": None, "dry_run": True},
    "close_position":      {"account_number": None},
    "stream_status":       {},
    "stream_subscribe":    {"timeout": 6.0},
    "get_gex":             {"strike_count": 20, "around_price": None},
}


class _OrbTracker:
    """MEIC-specific trade hook: capture the 9:30-9:35 ET opening range from live underlying Trade
    events and persist it once the window closes. Wired into the core engine as its trade_hook, holding
    its own per-symbol range state — the engine itself knows nothing about ORB. Runs on every Trade tick
    (not periodically) so it never misses an intrabar high/low; the reason ORB lives in the always-on
    streamer rather than the AI loop, whose cadence isn't guaranteed to land in the 9:30-9:35 window.
    """

    def __init__(self) -> None:
        self.orb_high: dict[str, float] = {}
        self.orb_low: dict[str, float] = {}
        self.orb_captured: set[str] = set()  # symbols already persisted to orb_ranges today

    def __call__(self, engine, symbol: str, price: float | None, ts: float) -> None:
        if price is None or symbol not in engine.symbols:
            return
        et_now = datetime.fromtimestamp(ts, tz=_ET)
        window_start = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
        window_end = et_now.replace(hour=9, minute=35, second=0, microsecond=0)
        if window_start <= et_now < window_end:
            h = self.orb_high.get(symbol)
            low = self.orb_low.get(symbol)
            self.orb_high[symbol] = price if h is None else max(h, price)
            self.orb_low[symbol] = price if low is None else min(low, price)
            return
        if et_now >= window_end and symbol not in self.orb_captured:
            h, low = self.orb_high.get(symbol), self.orb_low.get(symbol)
            if h is None or low is None:
                return  # streamer wasn't running during the window today — nothing to persist
            try:
                conn = engine.state.conn
                conn.execute(
                    "INSERT INTO orb_ranges (symbol, trade_date, orb_high, orb_low, captured_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(symbol, trade_date) DO NOTHING",
                    (symbol, et_now.strftime("%Y-%m-%d"), h, low, ts),
                )
                conn.commit()
                self.orb_captured.add(symbol)
                logger.info("[%s] ORB range captured: high=%.2f low=%.2f", symbol, h, low)
            except Exception as exc:
                logger.warning("[%s] ORB persist error: %s", symbol, exc)


# ---------------------------------------------------------------------------
# Dedicated REST event loop (separate from DXLink loop)
# ---------------------------------------------------------------------------

_REST_POLL_KEYS_STATIC = [
    ("account_info",    "get_account_info",    {"account_number": None}),
    ("positions",       "get_positions",       {"account_number": None}),
    ("working_orders",  "get_working_orders",  {"account_number": None}),
]
_REST_POLL_INTERVAL = 15.0


async def _rest_poller(symbols: list[str]) -> None:
    """Polls REST endpoints on a fixed cadence and writes results to stream_rest_cache.

    Opens its own SQLite connection rather than sharing state.conn with the main DXLink
    event loop thread. The two threads write independently and at different cadences
    (frequent small writes from DXLink listeners vs. a 15s REST poll here); sharing one
    connection meant a write on either thread could block the other's synchronous
    conn.execute() for up to sqlite3's default 5s busy timeout (observed live as a
    "database is locked" warning from this poller — see stall incident 2026-07-02).
    Since the DXLink SDK's connection-keepalive heartbeat runs on that same event loop,
    a long enough block there risks starving it past DXLink's 60s keepaliveTimeout,
    which would silently kill the persistent connection with no client-side exception.
    A dedicated connection removes this cross-thread contention entirely.
    """
    import tt
    conn = streamcache.connect(_CACHE_DB)
    fn_map = {
        "get_account_info":    tt.cmd_get_account_info,
        "get_positions":       tt.cmd_get_positions,
        "get_working_orders":  tt.cmd_get_working_orders,
        "get_market_overview": tt.cmd_get_market_overview,
    }
    # VIX is always needed for regime detection regardless of which symbols are traded.
    overview_symbols = list(dict.fromkeys([*symbols, "VIX"]))
    poll_keys = [
        *_REST_POLL_KEYS_STATIC,
        ("market_overview", "get_market_overview", {"symbols": overview_symbols, "quotes_timeout": 4.0}),
    ]
    while True:
        for key, cmd, ns_args in poll_keys:
            fn = fn_map[cmd]
            try:
                result = await fn(argparse.Namespace(**ns_args))
                result["_polled_at"] = time.time()
                _write_rest_cache(conn, key, result)
            except Exception as exc:
                logger.warning("REST poller [%s] error: %s", key, exc)
        await asyncio.sleep(_REST_POLL_INTERVAL)


def _start_rest_loop(symbols: list[str]) -> None:
    """Spin up a dedicated asyncio event loop in a background thread for REST commands."""
    global _rest_loop

    def _run() -> None:
        global _rest_loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _rest_loop = loop
        loop.create_task(_rest_poller(symbols))
        loop.run_forever()

    t = threading.Thread(target=_run, daemon=True, name="streamer-rest-loop")
    t.start()
    # Wait up to 2s for the loop to be ready
    deadline = time.time() + 2.0
    while _rest_loop is None and time.time() < deadline:
        time.sleep(0.01)
    logger.info("REST event loop started (thread: streamer-rest-loop)")


# ---------------------------------------------------------------------------
# HTTP API server
# ---------------------------------------------------------------------------

class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class _ApiHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/api":
            self._respond(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._respond(400, {"ok": False, "error": "invalid JSON"})
            return
        command = body.get("command", "")
        args = body.get("args", {})
        result = self._dispatch(command, args)
        self._respond(200, result)

    def _dispatch(self, command: str, args: dict) -> dict:
        # ── Tier 1: pure sync / stream-cache reads (no event loop) ──────────
        if command == "stream_status":
            import tt
            return tt.cmd_stream_status(argparse.Namespace())

        if command == "get_quote":
            return self._sync_get_quote(args)

        if command == "get_option_chain":
            result = self._sync_get_option_chain(args)
            if result is not None:
                return result
            # partial cache miss — fall through

        if command == "get_strategies":
            result = self._sync_get_strategies(args)
            if result is not None:
                return result

        # ── Tier 2: REST-cache reads (polled every 15s by _rest_poller) ─────
        _REST_CACHE_KEY = {
            "get_account_info":    "account_info",
            "get_positions":       "positions",
            "get_working_orders":  "working_orders",
            "get_market_overview": "market_overview",
        }
        cache_key = _REST_CACHE_KEY.get(command)
        if cache_key is not None:
            conn = self._db()
            try:
                cached = _read_rest_cache(conn, cache_key)
            finally:
                conn.close()
            if cached is not None:
                if command == "get_market_overview" and cached.get("ok") and "metrics" in cached:
                    requested = {s.upper() for s in args.get("symbols", [])}
                    if requested:
                        filtered = [m for m in cached["metrics"] if (m or {}).get("symbol", "").upper() in requested]
                        if len(filtered) == len(requested):
                            result = dict(cached)
                            result["metrics"] = filtered
                            result["source"] = "rest_cache"
                            return result
                        # one or more requested symbols aren't cached yet — fall through to a live call
                    else:
                        cached["source"] = "rest_cache"
                        return cached
                else:
                    cached["source"] = "rest_cache"
                    return cached
            # Cache cold (first poll not yet complete), or requested symbols not covered — fall through to REST loop

        # ── Tier 3: live REST calls via dedicated REST loop ──────────────────
        global _rest_loop
        if _rest_loop is None:
            return {"ok": False, "error": "REST event loop not ready"}
        if command not in _CMD_DEFAULTS:
            return {"ok": False, "error": f"unknown command: {command}"}

        import tt
        fn_map = {
            "get_connection_status": tt.cmd_get_connection_status,
            "list_accounts":         tt.cmd_list_accounts,
            "get_account_info":      tt.cmd_get_account_info,
            "get_positions":         tt.cmd_get_positions,
            "get_market_overview":   tt.cmd_get_market_overview,
            "get_quote":             tt.cmd_get_quote,
            "get_option_chain":      tt.cmd_get_option_chain,
            "get_strategies":        tt.cmd_get_strategies,
            "get_working_orders":    tt.cmd_get_working_orders,
            "execute_trade":         tt.cmd_execute_trade,
            "adjust_order":          tt.cmd_adjust_order,
            "close_position":        tt.cmd_close_position,
            "stream_subscribe":      tt.cmd_stream_subscribe,
            "get_gex":               tt.cmd_get_gex,
        }
        fn = fn_map.get(command)
        if fn is None:
            return {"ok": False, "error": f"unimplemented: {command}"}
        merged = {**_CMD_DEFAULTS.get(command, {}), **args}
        ns = argparse.Namespace(**merged)
        future = asyncio.run_coroutine_threadsafe(fn(ns), _rest_loop)
        try:
            return future.result(timeout=30)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Sync cache readers — bypass asyncio, read SQLite in handler thread
    # ------------------------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(_CACHE_DB), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _sync_get_quote(self, args: dict) -> dict:
        sym = args.get("symbol", "").strip().upper()
        now = time.time()
        conn = self._db()
        try:
            row = conn.execute(
                "SELECT last, updated_at FROM stream_trades WHERE symbol = ?", (sym,)
            ).fetchone()
            if row and row["last"] is not None and (now - row["updated_at"]) < 10:
                return {"ok": True, "symbol": sym, "last": float(row["last"]),
                        "source": "stream_cache"}
            return {"ok": True, "symbol": sym, "last": None,
                    "note": "not in stream cache — streamer may not have this symbol subscribed"}
        finally:
            conn.close()

    def _sync_get_option_chain(self, args: dict) -> dict | None:
        """Return chain from cache only; None if any requested data is missing."""
        sym = args.get("symbol", "").strip().upper()
        expiration = args.get("expiration")
        include_quotes = args.get("include_quotes", False)
        include_greeks = args.get("include_greeks", False)
        strike_count = args.get("strike_count", 15)
        around_price = args.get("around_price")
        now = time.time()
        conn = self._db()
        try:
            # Resolve expiration from cache when not supplied
            if not expiration:
                row = conn.execute(
                    "SELECT expiration FROM stream_chain WHERE underlying_symbol = ? "
                    "ORDER BY ABS(JULIANDAY(expiration) - JULIANDAY('now')) LIMIT 1",
                    (sym,),
                ).fetchone()
                if not row:
                    return None
                expiration = row["expiration"]

            chain_rows = conn.execute(
                "SELECT data_json, updated_at FROM stream_chain WHERE expiration = ? AND underlying_symbol = ?",
                (expiration, sym),
            ).fetchall()
            if not chain_rows or (now - min(r["updated_at"] for r in chain_rows)) > 4 * 3600:
                return None

            options = [json.loads(r["data_json"]) for r in chain_rows]

            # ATM window filter
            if strike_count is not None:
                strikes = sorted({float(o.get("strike_price", 0)) for o in options})
                center = around_price if around_price else (strikes[len(strikes)//2] if strikes else None)
                if center and strikes:
                    nearest = min(range(len(strikes)), key=lambda i: abs(strikes[i] - center))
                    keep = set(strikes[max(0, nearest-strike_count): nearest+strike_count+1])
                    options = [o for o in options if float(o.get("strike_price", 0)) in keep]

            # Attach quotes and greeks from cache
            syms = [o["streamer_symbol"] for o in options if o.get("streamer_symbol")]
            quotes_map: dict = {}
            greeks_map: dict = {}
            if include_quotes and syms:
                ph = ",".join("?" * len(syms))
                for row in conn.execute(
                    f"SELECT symbol, bid, ask, mid, updated_at FROM stream_quotes WHERE symbol IN ({ph})",
                    syms,
                ).fetchall():
                    if (now - row["updated_at"]) < 10:
                        quotes_map[row["symbol"]] = dict(row)
                if len(quotes_map) < len(syms):
                    return None  # partial cache — fall back to async

            if include_greeks and syms:
                ph = ",".join("?" * len(syms))
                for row in conn.execute(
                    f"SELECT symbol, delta, gamma, theta, iv, updated_at FROM stream_greeks WHERE symbol IN ({ph})",
                    syms,
                ).fetchall():
                    # Greeks publish far less often than Quotes/Trades on DXLink.
                    if (now - row["updated_at"]) < 2700:
                        greeks_map[row["symbol"]] = dict(row)
                if len(greeks_map) < len(syms):
                    return None

            # Merge into options
            for o in options:
                s = o.get("streamer_symbol")
                if s and s in quotes_map:
                    q = quotes_map[s]
                    o["bid"], o["ask"], o["mid"] = q["bid"], q["ask"], q["mid"]
                if s and s in greeks_map:
                    g = greeks_map[s]
                    o["delta"], o["gamma"], o["theta"], o["iv"] = (
                        g["delta"], g["gamma"], g["theta"], g["iv"])

            return {
                "ok": True, "symbol": sym,
                "chain": {expiration: options},
                "source": "stream_cache",
                "greeks_included": include_greeks,
                "quotes_included": include_quotes,
            }
        finally:
            conn.close()

    def _sync_get_strategies(self, args: dict) -> dict | None:
        """Build an IC candidate from cache; None if greeks or chain are missing."""
        from datetime import date as _date
        sym = args.get("symbol", "").strip().upper()
        wing_width = int(args.get("wing_width", 5))
        short_delta = float(args.get("short_delta", 0.15))
        now = time.time()
        conn = self._db()
        try:
            # Nearest expiration from cache
            row = conn.execute(
                "SELECT expiration FROM stream_chain WHERE underlying_symbol = ? "
                "ORDER BY ABS(JULIANDAY(expiration) - JULIANDAY('now')) LIMIT 1",
                (sym,),
            ).fetchone()
            if not row:
                return None
            expiration = row["expiration"]

            chain_rows = conn.execute(
                "SELECT data_json FROM stream_chain WHERE expiration = ? AND underlying_symbol = ?",
                (expiration, sym),
            ).fetchall()
            if not chain_rows:
                return None

            options = [json.loads(r["data_json"]) for r in chain_rows]
            calls = sorted((o for o in options if "C" in o.get("option_type", "")),
                           key=lambda o: float(o.get("strike_price", 0)))
            puts  = sorted((o for o in options if "P" in o.get("option_type", "")),
                           key=lambda o: float(o.get("strike_price", 0)))
            if not calls or not puts:
                return None

            # Load greeks for strike selection
            all_syms = [o["streamer_symbol"] for o in options if o.get("streamer_symbol")]
            ph = ",".join("?" * len(all_syms))
            greeks_map = {}
            for r in conn.execute(
                f"SELECT symbol, delta, updated_at FROM stream_greeks WHERE symbol IN ({ph})",
                all_syms,
            ).fetchall():
                # Greeks update far less often than Quotes/Trades on DXLink — a 10s
                # window would treat every strike as stale and force a non-delta fallback.
                if (now - r["updated_at"]) < 2700:
                    greeks_map[r["symbol"]] = r["delta"]

            def closest_delta(opts, target):
                best, best_diff = None, float("inf")
                for o in opts:
                    d = greeks_map.get(o.get("streamer_symbol"))
                    if d is None:
                        continue
                    diff = abs(float(d) - target)
                    if diff < best_diff:
                        best_diff, best = diff, o
                return best

            def nearest_by_strike(opts, target_strike, exclude_idx=None):
                # wing_width is a point/dollar distance, not a strike-count offset — strike
                # spacing varies by symbol (e.g. $1 near-the-money on XSP vs $5 on SPX), so
                # walking N array slots would silently produce the wrong-width spread.
                best, best_diff = None, float("inf")
                for i, o in enumerate(opts):
                    if i == exclude_idx:
                        continue
                    try:
                        s = float(o.get("strike_price", 0))
                    except (TypeError, ValueError):
                        continue
                    diff = abs(s - target_strike)
                    if diff < best_diff:
                        best_diff, best = diff, o
                return best

            sc = closest_delta(calls, short_delta)
            sp = closest_delta(puts, -short_delta)
            if sc is None or sp is None:
                return None  # no greeks — fall back to async
            sc_idx = calls.index(sc)
            sp_idx = puts.index(sp)
            sc_strike = float(sc.get("strike_price", 0))
            sp_strike = float(sp.get("strike_price", 0))
            lc = nearest_by_strike(calls, sc_strike + wing_width, exclude_idx=sc_idx) or calls[-1]
            lp = nearest_by_strike(puts, sp_strike - wing_width, exclude_idx=sp_idx) or puts[0]

            # Quotes for credit estimate
            leg_syms = [leg.get("streamer_symbol") for leg in (sp, lp, sc, lc) if leg.get("streamer_symbol")]
            ph2 = ",".join("?" * len(leg_syms))
            quotes_map = {}
            stale_quotes_map = {}
            for r in conn.execute(
                f"SELECT symbol, bid, ask, mid, updated_at FROM stream_quotes WHERE symbol IN ({ph2})",
                leg_syms,
            ).fetchall():
                stale_quotes_map[r["symbol"]] = r["mid"]
                if (now - r["updated_at"]) < 10:
                    quotes_map[r["symbol"]] = r["mid"]

            mids = [quotes_map.get(leg.get("streamer_symbol")) for leg in (sp, lp, sc, lc)]

            # Long wing legs (bought for protection, not sold for credit) trade so rarely
            # a fresh quote often never arrives — fall back to the last-known price (even
            # minutes stale) or a nominal $0.01, since a contract nobody trades isn't
            # repricing meaningfully anyway. Short legs keep the strict freshness check.
            long_wing_fallback_used = False
            for idx, leg in ((1, lp), (3, lc)):
                if mids[idx] is None:
                    leg_sym = leg.get("streamer_symbol")
                    mids[idx] = stale_quotes_map.get(leg_sym) if leg_sym else None
                    if mids[idx] is None:
                        mids[idx] = _NOMINAL_WING_PRICE
                    long_wing_fallback_used = True

            net_credit = None
            if mids[0] is not None and mids[2] is not None:
                net_credit = round(float(mids[0]) + float(mids[2]) - float(mids[1]) - float(mids[3]), 4)
            mult = float(sp.get("shares_per_contract") or 100)
            dte = (_date.fromisoformat(expiration) - _date.today()).days

            def _leg(o, mid):
                return {**o, "mid": mid}

            return {
                "ok": True, "symbol": sym, "strategy": "iron_condor",
                "expiration": expiration, "dte": dte,
                "estimated_pop": round(max(0.0, 1.0 - 2.0 * short_delta), 3),
                "net_credit": net_credit,
                "contract_multiplier": mult,
                "net_credit_per_contract": round(net_credit * mult, 2) if net_credit else None,
                "quotes_complete": mids[0] is not None and mids[2] is not None,
                "long_wing_fallback_used": long_wing_fallback_used,
                "greeks_used_for_strike_selection": bool(greeks_map),
                "source": "stream_cache",
                "legs": {
                    "short_put":  _leg(sp, mids[0]),
                    "long_put":   _leg(lp, mids[1]),
                    "short_call": _leg(sc, mids[2]),
                    "long_call":  _leg(lc, mids[3]),
                },
            }
        finally:
            conn.close()

    def _respond(self, code: int, data: dict) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass  # suppress per-request access logs


def _start_http_server() -> None:
    try:
        server = _ThreadingHTTPServer(("127.0.0.1", _HTTP_PORT), _ApiHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True, name="streamer-http")
        t.start()
        logger.info("Streamer HTTP API on http://127.0.0.1:%d/api", _HTTP_PORT)
    except OSError as exc:
        logger.warning("Could not start HTTP server on port %d: %s", _HTTP_PORT, exc)


# ---------------------------------------------------------------------------
# Main loop with reconnection
# ---------------------------------------------------------------------------

async def _main_loop(symbols: list[str]) -> None:
    # The persistent DXLink engine now lives in cherrypick.core.streamer; this daemon wires MEIC's
    # policy into it (open-position leg subscriptions, protected legs, ORB capture) and keeps the two
    # MEIC-only subsystems the engine knows nothing about: the tt.py-backed REST poller and the
    # 127.0.0.1:7699 HTTP API. Both read the same cache the engine writes, via their own connections.
    orb = _OrbTracker()
    streamer = ChainStreamer(
        session_factory=get_session,
        db_path=_CACHE_DB,
        symbols=symbols,
        extra_subscriptions=_resolve_subscriptions,
        protected_symbols=lambda: set(_open_trade_streamer_symbols()),
        trade_hook=orb,
        window_strike_count=_WINDOW_STRIKE_COUNT,
        subscription_poll_s=_SYMBOL_POLL_S,
        logger=logger,
    )

    def _handle_signal(sig, frame):
        logger.info("Signal %s received — stopping", sig)
        streamer.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    _start_rest_loop(symbols)
    _start_http_server()

    _PID_FILE.parent.mkdir(exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()))
    logger.info("Streamer PID %d written to %s", os.getpid(), _PID_FILE)
    logger.info("Trading symbols: %s (\u00b1%d strikes each, GEX included)", symbols, _WINDOW_STRIKE_COUNT)

    try:
        await streamer.run_async()
    finally:
        _PID_FILE.unlink(missing_ok=True)
        logger.info("Streamer stopped.")


# ---------------------------------------------------------------------------
# CLI: start / stop / status
# ---------------------------------------------------------------------------

def _running_pid() -> int | None:
    if not _PID_FILE.exists():
        return None
    try:
        pid = int(_PID_FILE.read_text().strip())
    except ValueError:
        _PID_FILE.unlink(missing_ok=True)
        return None

    # os.kill(pid, 0) is unreliable on Windows (raises SystemError for some
    # process states). Use psutil when available, else Windows OpenProcess.
    alive = False
    try:
        import psutil  # type: ignore
        alive = psutil.pid_exists(pid)
    except ImportError:
        try:
            import ctypes
            SYNCHRONIZE = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                alive = True
        except Exception:
            try:
                os.kill(pid, 0)
                alive = True
            except PermissionError:
                alive = True
            except (OSError, SystemError):
                alive = False

    if alive:
        return pid
    _PID_FILE.unlink(missing_ok=True)
    return None


def _cmd_status() -> None:
    pid = _running_pid()
    print(json.dumps({"running": pid is not None, "pid": pid}))
    if not _CACHE_DB.exists():
        return
    conn = sqlite3.connect(str(_CACHE_DB))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM stream_status WHERE id = 1").fetchone()
    if row:
        now = time.time()
        qtrades = conn.execute("SELECT COUNT(*) AS n, MAX(updated_at) AS last FROM stream_trades").fetchone()
        qquotes = conn.execute("SELECT COUNT(*) AS n, MAX(updated_at) AS last FROM stream_quotes").fetchone()
        qgreeks = conn.execute("SELECT COUNT(*) AS n, MAX(updated_at) AS last FROM stream_greeks").fetchone()
        conn.close()
        info = dict(row)
        info["trade_symbols"] = qtrades["n"]
        info["quote_symbols"] = qquotes["n"]
        info["greek_symbols"] = qgreeks["n"]
        # info["subscribed_symbols"] (from the stream_status row above) is the total
        # across every traded symbol's window — there's no longer a separate GEX-only
        # channel to report, since every traded symbol's window doubles as its GEX window.
        # Trade prints (underlying fills) are naturally sparse — a healthy connection
        # can go many minutes without one — so staleness must be judged by whichever
        # feed is freshest, not the one that happens to be quietest.
        newest = max(
            x["last"] for x in (qtrades, qquotes, qgreeks) if x["last"] is not None
        ) if any(x["last"] for x in (qtrades, qquotes, qgreeks)) else None
        info["oldest_event_age_s"] = round(now - newest, 1) if newest else None

        # Stale-cache guardrail: flags a silently-dead persistent connection
        # (running=true but no fresh events) — see stall incident 2026-07-01.
        # oldest_event_age_s (numeric, computed above from stream_* updated_at
        # columns) is used rather than the TEXT last_event_at column.
        stale = pid is not None and (info["oldest_event_age_s"] is None or info["oldest_event_age_s"] > 600)
        info["stale_warning"] = stale
        info["stale_age_s"] = info["oldest_event_age_s"]
        print(json.dumps(info, default=str))
    else:
        conn.close()


def _cmd_stop() -> None:
    pid = _running_pid()
    if pid is None:
        print(json.dumps({"ok": False, "error": "Streamer not running"}))
        return
    try:
        os.kill(pid, signal.SIGTERM)
        print(json.dumps({"ok": True, "signal": "SIGTERM", "pid": pid}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))


def _configured_symbols(cfg: dict, cli_override: list[str] | None = None) -> list[str]:
    """Resolve the traded-symbol list: CLI override > config 'symbols' > deprecated
    single-symbol 'symbol' key (as a one-element list) > default ["XSP"]."""
    if cli_override:
        return [s.strip().upper() for s in cli_override if s.strip()]
    if cfg.get("symbols"):
        return [str(s).strip().upper() for s in cfg["symbols"] if str(s).strip()]
    if cfg.get("symbol"):
        return [str(cfg["symbol"]).strip().upper()]
    return ["XSP"]


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="MEICAgent DXLink streamer daemon")
    parser.add_argument("--stop", action="store_true", help="Stop a running daemon")
    parser.add_argument("--status", action="store_true", help="Print status and exit")
    parser.add_argument("--symbol", action="append", default=None,
                        help="Override a traded symbol (repeatable, e.g. --symbol XSP --symbol SPX). "
                             "Default: 'symbols' (or deprecated 'symbol') from config.json.")
    args = parser.parse_args()

    if args.status:
        _cmd_status()
        return
    if args.stop:
        _cmd_stop()
        return

    existing_pid = _running_pid()
    if existing_pid is not None:
        print(json.dumps({
            "ok": False,
            "error": f"Streamer already running (pid {existing_pid}). "
                     f"Run 'python src/streamer.py --stop' first, or --status to inspect it.",
        }))
        raise SystemExit(1)

    cfg = _load_config()
    symbols = _configured_symbols(cfg, cli_override=args.symbol)

    _setup_logging()
    logger.info("Starting MEICAgent DXLink streamer — symbols: %s", symbols)

    asyncio.run(_main_loop(symbols))


if __name__ == "__main__":
    main()
