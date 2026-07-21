"""cherrypick-streamer daemon — the generic DXLink streaming daemon for the whole suite.

Runs ``cherrypick.core.streamer.ChainStreamer`` to keep the canonical shared stream cache
(``~/.cherrypick/data/marketdata/stream_cache.db``) fresh, so any consumer module — flies, gex, and
MEIC's own readers — can price off live quotes without MEIC's streamer being installed.

This is the generic daemon **lifecycle only** — PID guard, ``--status``/``--stop``, logging — lifted from
MEIC's streamer wrapper. It carries NONE of MEIC's trading policy: no ORB capture, no open-position leg
subscriptions, no account REST poller, no ``127.0.0.1:7699`` HTTP API. Those stay in MEIC's wrapper
(``packages/meic/src/streamer.py``); a live-trading module layers them onto the same shared engine.

Credentials come from the OS keyring under the suite's shared service (``"meicagent"``, with a read-only
fallback to the pre-rename ``"tastytrade-mcp"``), so a box that already has the suite's tastytrade OAuth
stored needs no re-entry.
"""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CORE = Path(__file__).resolve().parent / "_core"
if _CORE.is_dir() and str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from cherrypick.core.auth import CredentialStore, SessionManager  # noqa: E402
from cherrypick.core.streamer import ChainStreamer  # noqa: E402

import config as _config  # noqa: E402
import orb as _orb  # noqa: E402
import registry as _registry  # noqa: E402

logger = logging.getLogger("cherrypick-streamer")

_SERVICE = "meicagent"
_LEGACY = "tastytrade-mcp"

# Self-reported staleness threshold (mirrors MEIC's 600s). The orchestrator computes its own, tighter age
# from oldest_event_age_s and does not trust this flag — it exists only for a human running --status.
_STALE_WARN_S = 600


def make_session_factory():
    """A thread-local tastytrade session factory backed by the suite's keyring credentials.

    thread_local: the engine's DXLink loop needs a session bound to its own event loop (tastytrade's
    Session holds a loop-bound httpx client) — see ``cherrypick.core.auth``.
    """
    store = CredentialStore(_SERVICE, legacy_service_names=(_LEGACY,))
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
        legs = _registry.union_legs()
        return {"Trade": list(underlyings), "Quote": legs, "Greeks": legs, "Summary": list(underlyings)}

    def _protected_symbols() -> set[str]:
        return set(_registry.union_legs())

    return ChainStreamer(
        session_factory=make_session_factory(),
        db_path=_config.cache_path(cfg),
        symbols=reg_symbols,
        extra_subscriptions=_extra_subscriptions,
        protected_symbols=_protected_symbols,
        trade_hook=_orb.OpeningRangeTracker(),  # capture each symbol's 9:30-9:35 ET opening range
        window_strike_count=int(scfg.get("window_strike_count", 20)),
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


def _pid_alive(pid: int) -> bool:
    # os.kill(pid, 0) is unreliable on Windows (raises SystemError for some process states). Prefer
    # psutil, then the Win32 OpenProcess probe, then os.kill as a last resort.
    try:
        import psutil  # type: ignore
        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass
    try:
        import ctypes
        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except (OSError, SystemError):
            return False


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
        finally:
            conn.close()
        age = round(now - newest, 1) if newest else None

    # The PID file — not the cache's stored pid — is authoritative for liveness, so set it last.
    pid = running_pid(cfg)
    info["running"] = pid is not None
    info["pid"] = pid
    info["oldest_event_age_s"] = age
    info["stale_age_s"] = age
    info["stale_warning"] = pid is not None and (age is None or age > _STALE_WARN_S)
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
    logger.info("Streaming %s (registry union) -> %s (±%d strikes each)", streamer.symbols,
                _config.cache_path(cfg), streamer.window_strike_count)

    try:
        streamer.run()  # blocks with reconnect/backoff until SIGTERM/SIGINT
    finally:
        pid_file.unlink(missing_ok=True)
        logger.info("cherrypick-streamer stopped.")
    return 0
