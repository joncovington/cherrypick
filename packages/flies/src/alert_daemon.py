"""The order-alert daemon: one tastytrade account-alert websocket, held open for the trading day.

WHY THIS EXISTS, AND WHY IT IS ALLOWED TO DIE
---------------------------------------------
`live_loop.py` is deliberately stateless -- every tick and watch burst is a fresh process, because
"a resident trading daemon already proved fragile on Windows in MEIC" (its own docstring). This
daemon does NOT change that. It never decides anything, never places or cancels an order, and
never writes to the live ledger. All it does is subscribe to the designated account's order alerts
and append each one to the WAL-mode inbox (`alerts_db.py`), where the tick and the watcher read it
as a cheap local hint: "this order changed -- worth asking the broker now?"

Every fill is still confirmed by the live loop's ordinary `_confirm_*_fill` -> `broker.status()`
call, and the loop's `fill_heartbeat_seconds` poll still runs on its own schedule. So if this
process crashes, stalls, silently stops delivering, or was simply never started, nothing breaks:
fills are confirmed later rather than never. That property is the entire safety argument -- keep
it true. Anything that would make a fill *depend* on this daemon belongs in the live loop instead.

LIFECYCLE
---------
Tied to arming, not always-on: `/live-flies-start` starts it, disarm stops it, and it exits on its
own at `live.disarm_time` so a missed stop can't leave an authenticated broker session connected
overnight. That mirrors the live loop's per-day-arming contract rather than the suite's always-on
`packages/streamer` daemon (which is watchdog-supervised precisely because market data is needed
whenever the market is open -- order alerts are only needed while something is armed).

Modeled on `packages/streamer/src/daemon.py`: PID file + a Windows-safe liveness probe, rotating
file logs, `--status` (one line of JSON) and `--stop` (SIGTERM to the PID-file pid).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from logging.handlers import RotatingFileHandler

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "_core")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import alerts_db  # noqa: E402
import clock  # noqa: E402
import db as dbmod  # noqa: E402
from cli import load_config  # noqa: E402

_logger = logging.getLogger("flies.alert_daemon")
# How long a single listen call blocks before the loop wakes to re-check the disarm clock and the
# stop flag. Short enough that --stop and disarm feel immediate; long enough that a quiet market
# isn't a busy-wait.
LISTEN_SLICE_SECONDS = 30
# Alerts older than this are dropped on start: the inbox is a hand-off buffer, not a record.
PRUNE_AFTER_HOURS = 24


def _data_dir() -> str:
    return os.path.dirname(dbmod.live_db_path())


def pid_path() -> str:
    return os.path.join(_data_dir(), "live_alert_daemon.pid")


def status_path() -> str:
    """Heartbeat file -- the daemon's own view of itself, written as it runs. Liveness alone can't
    distinguish 'connected and quiet' from 'process alive, websocket silently dead', which is the
    exact failure the suite's market-data watchdog learned to check for via staleness rather than
    a bare `running` flag."""
    return os.path.join(_data_dir(), "live_alert_daemon.status.json")


def log_path() -> str:
    from cherrypick.core import home as _home

    return str(_home.logs_dir() / "flies" / "flies_alert_daemon.log")


def _setup_logging() -> None:
    if _logger.handlers:
        return
    path = log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    _logger.addHandler(fh)
    _logger.setLevel(logging.INFO)
    for noisy in ("tastytrade", "httpx", "httpcore", "websockets", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _pid_alive(pid: int) -> bool:
    """os.kill(pid, 0) is unreliable on Windows (raises SystemError for some process states) --
    same probe chain packages/streamer/src/daemon.py already settled on: psutil, then the Win32
    OpenProcess probe, then os.kill as a last resort."""
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
    except Exception:  # noqa: BLE001
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except (OSError, SystemError):
            return False


def running_pid() -> int | None:
    """The live daemon's PID, or None -- clearing a stale PID file on the way past."""
    path = pid_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        _unlink(path)
        return None
    if _pid_alive(pid):
        return pid
    _unlink(path)
    return None


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _write_status(**fields) -> None:
    try:
        with open(status_path(), "w", encoding="utf-8") as f:
            json.dump(fields, f)
    except OSError:
        pass  # a status file we can't write is not a reason to drop the connection


def read_status() -> dict:
    try:
        with open(status_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def status() -> dict:
    """What `--status` prints, and what `live_loop.py --status` embeds. `running` is authoritative
    (the PID probe); everything else is the daemon's own last self-report, which is what makes a
    silently-dead websocket visible -- a stale `last_alert_at`/`heartbeat_at` on a live process."""
    pid = running_pid()
    return {"running": pid is not None, "pid": pid, **read_status()}


def stop() -> dict:
    pid = running_pid()
    if pid is None:
        return {"ok": True, "stopped": False, "detail": "not running"}
    try:
        os.kill(pid, signal.SIGTERM)
    except (OSError, SystemError) as exc:
        return {"ok": False, "stopped": False, "detail": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "stopped": True, "pid": pid}


def _disarm_deadline(config: dict) -> float | None:
    """Wall-clock monotonic deadline for `live.disarm_time` today, or None if unset/unparseable.
    Belt-and-braces against an authenticated session outliving the trading day."""
    import engine

    live_cfg = config.get("live") or {}
    raw = live_cfg.get("disarm_time")
    if not raw:
        return None
    try:
        target_min = engine.time_to_minutes(raw)
    except (ValueError, AttributeError):
        return None
    now = clock.now_et()
    now_min = now.hour * 60 + now.minute
    return max(0.0, (target_min - now_min) * 60.0)


def run_daemon(
    config: dict,
    *,
    broker=None,
    conn=None,
    listen_slice: int = LISTEN_SLICE_SECONDS,
    max_seconds: float | None = None,
    clock_fn=time.monotonic,
) -> dict:
    """Subscribe once, append every alert to the inbox, until disarm/SIGTERM/`max_seconds`.

    `broker`/`conn` are injected for tests; `max_seconds` bounds a test run (production leaves it
    None and relies on the disarm deadline and the stop signal).

    The listen call is sliced rather than opened once for the whole day so the loop regains control
    on a fixed cadence to re-check the disarm clock and refresh the heartbeat. Each slice returns
    whatever alerts arrived in that window -- `wait_for_order_alerts` already fails closed to `[]`
    on any websocket/auth error, so a dropped connection just means an empty slice and the next one
    reconnects. That is the reconnect strategy: retry on the next slice, forever, because a failure
    here costs latency and nothing else."""
    _setup_logging()
    import live_loop

    live_cfg = config.get("live") or {}
    broker = broker or live_loop.BrokerAdapter(config)
    owns_conn = conn is None
    conn = conn or alerts_db.connect()

    started = clock_fn()
    disarm_in = _disarm_deadline(config)
    deadline = None
    for candidate in (disarm_in, max_seconds):
        if candidate is not None:
            deadline = candidate if deadline is None else min(deadline, candidate)

    stopping = {"flag": False}

    def _on_signal(_signum, _frame):
        stopping["flag"] = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass  # not the main thread (tests) -- max_seconds/deadline still bound the loop

    cutoff = clock.now_iso()[:10]  # today's date; prune anything from a prior day
    try:
        pruned = alerts_db.prune_before(conn, cutoff)
    except Exception as exc:  # noqa: BLE001 -- a failed prune must never stop the daemon starting
        pruned = 0
        _logger.warning("inbox prune failed (%s: %s) -- continuing", type(exc).__name__, exc)

    connected_since = clock.now_iso()
    alerts_seen = 0
    last_alert_at = None
    _logger.info("alert daemon started (pruned %d stale alert(s), arm=%s)", pruned, live_cfg.get("arm"))
    _write_status(
        connected_since=connected_since, alerts_seen=0, last_alert_at=None, heartbeat_at=connected_since
    )

    try:
        while not stopping["flag"]:
            elapsed = clock_fn() - started
            if deadline is not None and elapsed >= deadline:
                _logger.info("alert daemon reached its deadline (%.0fs) -- exiting", deadline)
                break
            remaining = listen_slice if deadline is None else max(0.0, min(listen_slice, deadline - elapsed))
            if remaining <= 0:
                break

            # Subscribing to the ACCOUNT, not to specific order ids: the daemon has no ledger view
            # and shouldn't need one. It records whatever the account reports and lets the readers
            # decide which order ids they care about -- the same "account is truth, the ledger is
            # belief" split the orphan sweep already draws.
            alerts = broker.wait_for_order_alerts(set(), remaining)
            for alert in alerts or []:
                received_at = clock.now_iso()
                try:
                    alerts_db.record_alert(conn, alert, received_at)
                except Exception as exc:  # noqa: BLE001 -- one bad row must not kill the stream
                    _logger.warning("inbox write failed (%s: %s)", type(exc).__name__, exc)
                    continue
                alerts_seen += 1
                last_alert_at = received_at
                _logger.info(
                    "alert: order %s -> %s (filled=%s)",
                    alert.get("order_id"),
                    alert.get("status"),
                    alert.get("filled"),
                )
            _write_status(
                connected_since=connected_since,
                alerts_seen=alerts_seen,
                last_alert_at=last_alert_at,
                heartbeat_at=clock.now_iso(),
            )
    finally:
        if owns_conn:
            conn.close()
        _logger.info("alert daemon stopped (%d alert(s) recorded)", alerts_seen)

    return {"ok": True, "alerts_seen": alerts_seen, "connected_since": connected_since}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="flies-alert-daemon", description=__doc__)
    parser.add_argument("--status", action="store_true", help="print daemon status as JSON and exit")
    parser.add_argument("--stop", action="store_true", help="SIGTERM a running daemon and exit")
    parser.add_argument("--config", default=None)
    parser.add_argument("--max-seconds", type=float, default=None, help="bound the run (testing)")
    args = parser.parse_args(argv)

    if args.status:
        print(json.dumps(status(), indent=2))
        return 0
    if args.stop:
        print(json.dumps(stop(), indent=2))
        return 0

    existing = running_pid()
    if existing is not None:
        print(json.dumps({"ok": False, "detail": f"already running (pid {existing})"}))
        return 1

    config = load_config(args.config)
    os.makedirs(_data_dir(), exist_ok=True)
    with open(pid_path(), "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    try:
        out = run_daemon(config, max_seconds=args.max_seconds)
        print(json.dumps(out))
        return 0
    finally:
        _unlink(pid_path())


if __name__ == "__main__":
    raise SystemExit(main())
