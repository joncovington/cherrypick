"""MEICAgent DXLink Streamer Daemon.

Maintains a single persistent WebSocket to tastytrade's DXLink feed and writes
the latest Quote, Greeks, and Trade events to stream_cache.db in the data home, for every
symbol configured in config.json's `symbols` list. Each traded symbol gets its
own subscription window (near-the-money option strikes) on the same connection —
that window doubles as both the entry-strike-selection data and that symbol's
own GEX profile, so GEX is computed per-symbol automatically.

As of the 2026-07-21 cutover the suite's market-data producer is the standalone streamer
(packages/streamer), which writes the shared cache. This module is kept for rollback only: run it if
the standalone streamer is NOT the producer, and it refuses to start while that producer runs.

Usage:
  python src/streamer.py                       # rollback only — the standalone streamer is the producer
  python src/streamer.py --symbol XSP --symbol SPX  # override traded symbols for this run
  python src/streamer.py --stop                # send SIGTERM to a running streamer
  python src/streamer.py --status               # print cache status and exit

tt.py reads from the shared cache (age < 10s), else opens a fresh broker connection. The MEIC
"sidecar" this module used to run -- a REST account poller and a 127.0.0.1:7699 HTTP API in front of
tt.py -- was removed on 2026-10-18: it shipped off, nothing but tt.py's own probe ever called it, and
the shared cache and the direct broker path answer everything it did.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sqlite3
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Allow running as `python src/streamer.py` from the project root.
sys.path.insert(0, os.path.dirname(__file__))

# One ET for the suite — see cherrypick.core.clock.
from cherrypick.core.clock import ET as _ET  # noqa: E402
from cherrypick.core.streamer import ChainStreamer  # noqa: E402

from cherrypick.meic import paths as _paths  # noqa: E402
from cherrypick.meic.session import get_session  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# src/cherrypick/meic/streamer.py -> package root is four parents up.
_ROOT = Path(__file__).resolve().parents[3]
_CACHE_DB = _paths.stream_cache_path()  # canonical shared cache ~/.cherrypick/data/marketdata/ (READ-ONLY here now — the standalone streamer owns it)
_PID_FILE = _paths.data_path("streamer.pid")
_TRADES_DB = _paths.live_db_path()
_LOG_FILE = _paths.log_path("streamer.log")

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
    for _noisy in ("tastytrade", "httpx", "httpx2", "httpcore", "websockets", "asyncio"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache DB
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Symbol resolution
# ---------------------------------------------------------------------------


def _load_config() -> dict:
    p = _paths.config_path()  # home-first (~/.cherrypick/config/meic.json), in-repo fallback
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

_SYMBOL_POLL_S = 30.0  # how often to re-check open trades (passed as the engine's subscription poll)

# Per-symbol subscription window. Every traded symbol gets exactly one window sized to the
# GEX requirement (wider than the ATM-only requirement of ~15 strikes) — GEX profile accuracy
# needs strikes further from the money than entry strike selection does, and a single window
# per symbol comfortably covers both needs rather than maintaining two separate windows.
# (Reconnect backoff + window re-center cadence now live in cherrypick.core.streamer, whose defaults
# match the values MEIC used here; only the strike count and poll interval are passed through.)
_WINDOW_STRIKE_COUNT = 20  # strikes each side of center (~40 strikes × 2 types = ~80 symbols)


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
# Main loop with reconnection
# ---------------------------------------------------------------------------


async def _main_loop(symbols: list[str]) -> None:
    # The persistent DXLink engine now lives in cherrypick.core.streamer; this daemon wires MEIC's
    # policy into it (open-position leg subscriptions, protected legs, ORB capture).
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


def _running_pid(pid_file: Path = _PID_FILE) -> int | None:
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        pid_file.unlink(missing_ok=True)
        return None

    # os.kill(pid, 0) is unreliable on Windows (raises SystemError for some
    # process states). Use psutil when available, else Windows OpenProcess.
    alive = False
    try:
        import psutil  # type: ignore

        alive = psutil.pid_exists(pid)
    except ImportError:
        # Windows asks OpenProcess; POSIX asks os.kill(pid, 0). It used to reach POSIX only through the
        # AttributeError of a missing ctypes.windll (2026-10-08 OS audit) -- correct by accident.
        try:
            if os.name != "nt":
                raise OSError("not windows")
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
    pid_file.unlink(missing_ok=True)
    return None


def _standalone_producer_pid() -> int | None:
    """Liveness of the standalone streamer producer (packages/streamer), the suite's producer since the
    2026-07-21 cutover. It writes its PID file colocated with the shared cache it manages
    (`cache_path().parent / "streamer.pid"`, see `cherrypick.streamer.config.pid_path`) — since this
    module's `_CACHE_DB` points at that same canonical shared cache, checking the sibling PID file here
    needs no import of the `streamer` package. One-producer enforcement for full-streamer mode: two
    writers into the same cache means two DXLink connections into one account.
    """
    return _running_pid(_CACHE_DB.parent / "streamer.pid")


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
        newest = (
            max(x["last"] for x in (qtrades, qquotes, qgreeks) if x["last"] is not None)
            if any(x["last"] for x in (qtrades, qquotes, qgreeks))
            else None
        )
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


def _cmd_stop(pid_file: Path = _PID_FILE) -> None:
    pid = _running_pid(pid_file)
    if pid is None:
        print(json.dumps({"ok": False, "error": "Not running"}))
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

    parser = argparse.ArgumentParser(description="MEICAgent DXLink streamer daemon")
    parser.add_argument("--stop", action="store_true", help="Stop a running daemon")
    parser.add_argument("--status", action="store_true", help="Print status and exit")
    parser.add_argument(
        "--symbol",
        action="append",
        default=None,
        help="Override a traded symbol (repeatable, e.g. --symbol XSP --symbol SPX). "
        "Default: 'symbols' (or deprecated 'symbol') from config.json.",
    )
    args = parser.parse_args()

    if args.status:
        _cmd_status()
        return
    if args.stop:
        _cmd_stop()
        return

    existing_pid = _running_pid()
    if existing_pid is not None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"Streamer already running (pid {existing_pid}). "
                    f"Run 'python src/streamer.py --stop' first, or --status to inspect it.",
                }
            )
        )
        raise SystemExit(1)

    standalone_pid = _standalone_producer_pid()
    if standalone_pid is not None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"Standalone streamer producer already running (pid {standalone_pid}) and "
                    "writing the same shared cache. Exactly one producer may write it at a time — "
                    "starting this full-streamer mode alongside it would open a second DXLink "
                    "connection into the account. Stop the standalone producer first "
                    "('python packages/streamer/run.py --stop').",
                }
            )
        )
        raise SystemExit(1)

    cfg = _load_config()
    symbols = _configured_symbols(cfg, cli_override=args.symbol)

    _setup_logging()
    logger.info("Starting MEICAgent DXLink streamer — symbols: %s", symbols)

    asyncio.run(_main_loop(symbols))


if __name__ == "__main__":
    main()
