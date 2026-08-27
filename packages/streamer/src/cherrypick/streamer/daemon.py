"""cherrypick-streamer daemon — the generic DXLink streaming daemon for the whole suite.

Runs ``cherrypick.core.streamer.ChainStreamer`` to keep the canonical shared stream cache
(``~/.cherrypick/data/marketdata/stream_cache.db``) fresh, so any consumer module — flies, gex, and
MEIC's own readers — can price off live quotes without MEIC's streamer being installed.

This is the generic daemon **lifecycle only** — PID guard, ``--status``/``--stop``, logging — lifted from
MEIC's streamer wrapper. It carries NONE of MEIC's trading policy: no ORB capture, no open-position leg
subscriptions, no account REST poller, no ``127.0.0.1:7699`` HTTP API. Those stay in MEIC's wrapper
(``packages/meic/src/streamer.py``); a live-trading module layers them onto the same shared engine.

Credentials come from the OS keyring under the ``"meicagent"`` service, with read-only fallbacks to the
pre-rename ``"tastytrade-mcp"`` and the suite's shared broker login (``"cherrypick-broker"``), so a box
that already has the suite's tastytrade OAuth stored — under any of the three — needs no re-entry.
"""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
import sys
import time
from logging.handlers import RotatingFileHandler

from cherrypick.core import looplock, streamcache
from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager
from cherrypick.core.streamer import ChainStreamer

from cherrypick.streamer import config as _config
from cherrypick.streamer import orb as _orb
from cherrypick.streamer import registry as _registry

logger = logging.getLogger("cherrypick-streamer")

_SERVICE = "meicagent"
# Read-only fallbacks, in order: the pre-rename service, then the suite's shared broker login
# (cherrypick-broker) — the same chain the module stores use, so a box whose per-module copies were
# migrated into the shared service (core.auth migrate deletes the source) still authenticates.
_LEGACY = ("tastytrade-mcp", SHARED_SERVICE)

# Self-reported staleness threshold (mirrors MEIC's 600s). The orchestrator computes its own, tighter age
# from oldest_event_age_s and does not trust this flag — it exists only for a human running --status.
_STALE_WARN_S = 600

# Age past which a single union underlying's spot counts as DEAD during regular hours. The
# aggregate `underlyings_stale_age_s` below deliberately uses the FRESHEST underlying so one quiet
# name can't false-trip a whole-feed alarm — and the price of that choice was paid 2026-08-17..21,
# when TQQQ's trade subscription died mid-flight and streamed nothing for four sessions while SPX
# kept the aggregate fresh. This per-symbol field is the other half of the bargain: generous enough
# (15 min) that no liquid underlying hits it in RTH, and 5 minutes beyond the producer's own
# self-heal resubscribe (cherrypick.core.streamer, 10 min), so the watchdog only ever sees a
# symbol the self-heal has already failed to revive.
_DEAD_UNDERLYING_S = 900.0


def _in_rth_clock(now_utc: float) -> bool:
    """Weekday 09:35-15:55 ET, clock-only. Holidays are the CALLER'S problem by design: the
    watchdog asks through a holiday-aware session gate (`timeutil.is_session_window`), and this
    daemon deliberately holds no holiday calendar of its own."""
    try:
        from datetime import UTC, datetime
        from zoneinfo import ZoneInfo

        et = datetime.fromtimestamp(now_utc, tz=UTC).astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return False
    if et.weekday() >= 5:
        return False
    minutes = et.hour * 60 + et.minute
    return 9 * 60 + 35 <= minutes <= 15 * 60 + 55


def make_session_factory():
    """A thread-local tastytrade session factory backed by the suite's keyring credentials.

    thread_local: the engine's DXLink loop needs a session bound to its own event loop (tastytrade's
    Session holds a loop-bound httpx client) — see ``cherrypick.core.auth``.
    """
    store = CredentialStore(_SERVICE, legacy_service_names=_LEGACY)
    return SessionManager(store, thread_local=True).get_session


def build_streamer(cfg: dict, symbols: list[str] | None = None) -> ChainStreamer:
    """Wire the shared engine to the subscription registry.

    The engine's underlyings are the registry union at startup, seeded by config `symbols` (the operator
    base set; new *underlyings* need a restart to add a chain window). The registry's `legs` — MEIC's
    open option legs — are dynamic: they flow through the engine's `extra_subscriptions`/
    `protected_symbols` hooks, which the engine re-reads each subscription poll, so a leg registered
    mid-session is picked up without a restart and never dropped when the ATM window re-centres.
    """
    seed = symbols or _config.symbols(cfg)
    reg_symbols = _registry.union_symbols(seed_symbols=seed)
    scfg = cfg.get("streamer", {}) or {}

    def _extra_subscriptions(underlyings: list[str]) -> dict[str, list[str]]:
        # Base underlying subscriptions (matching the engine's own default) PLUS the registered legs on
        # Quote/Greeks so their prices stay fresh beyond the ATM window. union_legs re-reads the registry
        # and re-runs each module's leg_sources query, so an opened/closed position is picked up here.
        #
        # Non-option legs (no leading '.') ALSO get Trade: index legs like overview's VIX/VIX3M/VVIX
        # publish Trade events, never quotes, so Quote-only left them with no price at all — and
        # overview reads every breadth spot from stream_trades (facts.py), so its whole panel froze
        # at the 2026-08-17 subscription drop and nothing brought it back. ETF legs trade constantly
        # in RTH, so the engine's stale-trade self-heal won't false-trip on them; OPTION legs stay
        # off Trade deliberately — sparse prints would read as perpetually stale there.
        legs = _registry.union_legs()
        cash_legs = [leg for leg in legs if not leg.startswith(".")]
        # Quote and Greeks are filtered by what the symbol can actually PUBLISH, not by what it is.
        # Every cash leg carried a Greeks subscription that could never deliver an event, and the
        # index legs carried a Quote subscription with the same problem — an index is a computed
        # level with no order book (the 2026-08-24 entitlement probe: SKEW/VIX9D/VIX/VIX1D all
        # printed Trade, none printed Quote). ETF and single-name legs KEEP Quote: they have a real
        # book and modules price off it. The predicate lives in core.streamcache beside the rest of
        # the symbology so the producer and any reader cannot disagree about it.
        quote_legs = [leg for leg in legs if streamcache.publishes_quotes(leg)]
        greeks_legs = [leg for leg in legs if streamcache.publishes_greeks(leg)]
        # Cash legs get Summary for the same reason they got Trade, one event type later: the daily
        # OHLC (`day_close`) arrives ONLY on Summary, and it is what `gex.regime.harvest_daily_closes`
        # copies into `daily_closes` — the suite's only multi-year series, and the input to every
        # percentile and seasonal reading. Summary was underlyings-only, so the whole vol complex
        # (VIX/VIX3M/VIX9D/VVIX/SKEW) and the commodity proxies, all declared as legs, silently
        # stopped accumulating closes on 2026-08-14. SPY kept its own only because another module
        # happens to declare it as an underlying, which is exactly how the gap stayed invisible.
        # OPTION legs stay off Summary as they stay off Trade: a per-contract daily bar is not a
        # series anything here reads, and it would be thousands of subscriptions for nothing.
        return {
            "Trade": list(underlyings) + cash_legs,
            "Quote": quote_legs,
            "Greeks": greeks_legs,
            "Summary": list(underlyings) + cash_legs,
        }

    def _protected_symbols() -> set[str]:
        return set(_registry.union_legs())

    default_strike_count = int(scfg.get("window_strike_count", 60))

    def _window_strike_count_for(symbol: str) -> tuple[int, int]:
        # A module's widened per-symbol request (e.g. flies escalating after repeated
        # missing_leg_quotes) never narrows the configured default -- only ever widens it, and now
        # per DIRECTION: a hint may be a plain count (symmetric, the common case) or a
        # {"down": N, "up": M} declaration, so a module whose structure sits below spot stops
        # buying the mirror image above it. The max is taken per side, so a directional hint can
        # never narrow the default on the side it is silent about.
        down, up = _registry.union_window_hints().get(symbol, (0, 0))
        return (max(default_strike_count, down), max(default_strike_count, up))

    def _expirations_for(symbol: str) -> list[str]:
        # Extra expirations (e.g. the calendars module's 4DTE/7DTE legs) are dynamic like the legs:
        # the engine re-reads this every window pass, so a request that rolls to next week's dates
        # is served with no restart. Past dates are dropped by the union itself.
        return _registry.union_expirations().get(symbol, [])

    def _history_days_for(symbol: str) -> int:
        # Daily-history depth a consumer indicator needs (e.g. pmcc's Keltner lookback). The engine
        # backfills a stream_summary deficit once per connection from DXLink daily candles — absent
        # dates only, never a row the live Summary feed wrote, never today's partial candle.
        return _registry.union_history_days().get(symbol, 0)

    return ChainStreamer(
        session_factory=make_session_factory(),
        db_path=_config.cache_path(cfg),
        symbols=reg_symbols,
        extra_subscriptions=_extra_subscriptions,
        protected_symbols=_protected_symbols,
        trade_hook=_orb.OpeningRangeTracker(),  # capture each symbol's 9:30-9:35 ET opening range
        expirations_for=_expirations_for,
        history_days_for=_history_days_for,
        window_strike_count=default_strike_count,
        window_strike_count_for=_window_strike_count_for,
        logger=logger,
    )


def _setup_logging(cfg: dict) -> None:
    """File + stdout logging with rotation (10 MB × 5 ≈ 60 MB cap). Handler level INFO drops the DXLink
    SDK's per-message DEBUG firehose regardless of which library logger emits it. Never called on the
    --status / --stop paths so those emit pure JSON to stdout."""
    log_file = _config.log_path(cfg)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    for noisy in ("tastytrade", "httpx", "httpcore", "websockets", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_pid_alive = looplock.pid_alive  # noqa: F401  (re-exported: tests monkeypatch this name)


def running_pid(cfg: dict) -> int | None:
    """The live daemon PID from the PID file, or None (clearing a stale file)."""
    pid_file = _config.pid_path(cfg)
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        pid_file.unlink(missing_ok=True)
        return None
    if _pid_alive(pid):
        return pid
    pid_file.unlink(missing_ok=True)
    return None


def status(cfg: dict) -> dict:
    """A single merged status object the orchestrator watchdog parses in one shot.

    Unlike MEIC's wrapper — which prints two JSON lines, so ``util.first_json``'s whole-buffer parse
    fails and it only ever recovers the first ``{running, pid}`` line — this returns ONE dict carrying
    ``running``/``pid`` AND the staleness/connection fields (``oldest_event_age_s`` / ``stale_age_s`` /
    ``connected_since``) together, which is what the watchdog reads to judge a silent stall.

    Staleness is judged by whichever event feed is freshest (Trade prints are naturally sparse — a
    healthy connection can go minutes without one), matching MEIC's guardrail.
    """
    info: dict = {}
    cache = _config.cache_path(cfg)
    age: float | None = None
    u_age: float | None = None
    symbol_health: dict[str, dict] = {}
    dead_underlyings: dict[str, float] = {}
    if cache.exists():
        conn = sqlite3.connect(f"file:{cache}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM stream_status WHERE id = 1").fetchone()
            if row:
                info.update(dict(row))
            now = time.time()
            newest: float | None = None
            for table in ("stream_trades", "stream_quotes", "stream_greeks"):
                r = conn.execute(f"SELECT MAX(updated_at) AS last FROM {table}").fetchone()
                if r and r["last"] is not None:
                    newest = r["last"] if newest is None else max(newest, r["last"])
            # Per-underlying spot freshness, tracked SEPARATELY from the global `newest` above. Option
            # quotes tick constantly and dominate the global age, masking a dead underlying-spot feed:
            # on 2026-07-22 every underlying's stream_trades froze at 10:05 ET while option quotes kept
            # streaming until 20:00, so the global age never went stale and nothing restarted -- flies
            # and MEIC ran on frozen spot all day. Use the FRESHEST subscribed underlying (so one
            # naturally-quiet name can't false-trip; during RTH at least one liquid underlying always
            # ticks, so this only ages out when the whole spot feed dies).
            underlyings = _registry.union_symbols(_config.symbols(cfg))
            u_newest: float | None = None
            if underlyings:
                placeholders = ",".join("?" * len(underlyings))
                spot_ages = {
                    r["symbol"]: now - r["updated_at"]
                    for r in conn.execute(
                        f"SELECT symbol, updated_at FROM stream_trades WHERE symbol IN ({placeholders})",
                        underlyings,
                    )
                    if r["updated_at"] is not None
                }
                if spot_ages:
                    u_newest = now - min(spot_ages.values())
                # Per-symbol dead-spot detection (see _DEAD_UNDERLYING_S). Only symbols WITH a row
                # can report dead: a union symbol that has never written is the recycle-on-union-
                # growth path's job, and flagging it here would loop the watchdog's restart against
                # a symbol a restart cannot fix.
                if _in_rth_clock(now):
                    dead_underlyings = {
                        sym: round(age, 1) for sym, age in spot_ages.items() if age > _DEAD_UNDERLYING_S
                    }
            # Per-symbol chain-fetch health: the aggregate ages above are freshest-of-any-symbol, so
            # ONE symbol's chain fetch silently failing (window disabled) is invisible whenever other
            # symbols keep ticking fine — this is that symbol's own signal (see
            # cherrypick.core.streamer's _fetch_dte0_chain_with_retry).
            for row in conn.execute(
                "SELECT symbol, chain_loaded_at, chain_fetch_error, updated_at FROM stream_symbol_health"
            ):
                symbol_health[row["symbol"]] = {
                    "chain_loaded_at": row["chain_loaded_at"],
                    "chain_fetch_error": row["chain_fetch_error"],
                    "age_s": round(now - row["updated_at"], 1),
                }
        finally:
            conn.close()
        age = round(now - newest, 1) if newest else None
        u_age = round(now - u_newest, 1) if u_newest else None

    # The PID file — not the cache's stored pid — is authoritative for liveness, so set it last.
    pid = running_pid(cfg)
    info["running"] = pid is not None
    info["pid"] = pid
    info["oldest_event_age_s"] = age
    info["stale_age_s"] = age
    info["underlyings_stale_age_s"] = u_age
    info["stale_warning"] = pid is not None and (age is None or age > _STALE_WARN_S)
    info["symbol_health"] = symbol_health
    info["chain_fetch_errors"] = {
        s: h["chain_fetch_error"] for s, h in symbol_health.items() if h["chain_fetch_error"]
    }
    info["dead_underlyings"] = dead_underlyings
    # What the registry union currently asks beyond each symbol's nearest expiration. The per-date
    # serving state is the `SYMBOL@date` rows already present in symbol_health above.
    try:
        info["extra_expirations"] = _registry.union_expirations()
    except Exception:
        info["extra_expirations"] = {}
    return info


def stop(cfg: dict) -> dict:
    """SIGTERM a running daemon (the engine's signal handler drives a clean shutdown)."""
    pid = running_pid(cfg)
    if pid is None:
        return {"ok": False, "error": "Streamer not running"}
    try:
        os.kill(pid, signal.SIGTERM)
        return {"ok": True, "signal": "SIGTERM", "pid": pid}
    except Exception as exc:  # noqa: BLE001 - report any OS error back as JSON
        return {"ok": False, "error": str(exc)}


def run_daemon(cfg: dict, symbols: list[str] | None = None) -> int:
    """Foreground run: write the PID file, then drive the engine (which installs SIGTERM/SIGINT and its
    own reconnect/backoff loop) until stopped. The single-instance check happens in the CLI before this.
    """
    _setup_logging(cfg)
    streamer = build_streamer(cfg, symbols)

    pid_file = _config.pid_path(cfg)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))
    logger.info("cherrypick-streamer PID %d written to %s", os.getpid(), pid_file)
    logger.info(
        "Streaming %s (registry union) -> %s (±%d strikes each)",
        streamer.symbols,
        _config.cache_path(cfg),
        streamer.window_strike_count,
    )

    try:
        streamer.run()  # blocks with reconnect/backoff until SIGTERM/SIGINT
    finally:
        pid_file.unlink(missing_ok=True)
        logger.info("cherrypick-streamer stopped.")
    return 0
