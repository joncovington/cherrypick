"""cherrypick.core.live.alerts -- the order-alert inbox and daemon loop a live module runs while armed.

Hoisted from flies' `alert_daemon.py`/`alerts_db.py` on 2026-10-15 so MEIC could run the same thing
without a second copy; flies still carries its own until it is folded onto this (a separate change,
kept off the day its live pilot was running). The contract is flies', unchanged:

- The daemon subscribes to the designated ACCOUNT's order alerts and appends each one to a WAL-mode
  inbox beside the module's live ledger. It never decides, places, cancels or writes the ledger.
- Nothing may DEPEND on it. Every fill is still confirmed by the live loop's own `broker.status()`
  poll; the inbox only says "this order changed -- worth asking now?". If the daemon dies, stalls or
  never starts, fills are confirmed later, never missed.
- It lives for the armed day: the supervisor keeps it up as an arm-record companion, and it exits on
  its own at the module's disarm time so an authenticated session never outlives the day.

What a module adds is `on_alert`, called for each recorded alert: MEIC spawns one live tick when an
order its ledger knows changes state, so a fill is confirmed in seconds rather than at the next
minute. A failing `on_alert` is logged and ignored -- the stream outlives its listeners.

The inbox is also what the orchestrator's `live-fill-alerts` job reads to push a LIVE fill alert
within seconds (`fills_after`). Pushing stays in the orchestrator, never in a module.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any

INBOX_FILENAME = "live_alerts.db"
BUSY_TIMEOUT_MS = 5000
LISTEN_SLICE_SECONDS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS order_alerts (
    order_id    TEXT NOT NULL,
    status      TEXT,
    price       TEXT,
    filled      INTEGER,
    cancellable INTEGER,
    received_at TEXT NOT NULL,
    PRIMARY KEY (order_id, received_at)
);
CREATE INDEX IF NOT EXISTS idx_order_alerts_received ON order_alerts(received_at);
"""


# --------------------------------------------------------------------------- the inbox


def connect(path: str) -> sqlite3.Connection:
    """WAL + busy_timeout: ONE writer (the daemon), any number of readers who never block it."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.executescript(_SCHEMA)
    return conn


def record_alert(conn, alert: dict, received_at: str) -> None:
    """Append one alert in the `{order_id, status, price, filled, cancellable}` shape
    `core.broker._serialize_placed_order` produces. A redelivered duplicate is ignored."""
    conn.execute(
        "INSERT OR IGNORE INTO order_alerts (order_id, status, price, filled, cancellable, received_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            str(alert.get("order_id")),
            alert.get("status"),
            alert.get("price"),
            int(bool(alert.get("filled"))),
            int(bool(alert.get("cancellable"))),
            received_at,
        ),
    )
    conn.commit()


def prune_before(conn, cutoff: str) -> int:
    cur = conn.execute("DELETE FROM order_alerts WHERE received_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def fills_after(path: str, after: str | None) -> list[dict]:
    """Every FILLED alert received after `after` (exclusive), oldest first, read-only. An inbox that
    does not exist yet has none. Status is the broker's own word, lower-cased ("filled")."""
    if not os.path.exists(path):
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT order_id, status, price, received_at FROM order_alerts "
                "WHERE lower(status) = 'filled' AND (? IS NULL OR received_at > ?) ORDER BY received_at",
                (after, after),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- the daemon


class AlertDaemon:
    """One module's daemon: its PID file, heartbeat and log beside the module's live ledger."""

    def __init__(self, module: str, data_dir: str, log_path: str):
        self.module = module
        self.data_dir = data_dir
        self.log_path = log_path
        self.inbox_path = os.path.join(data_dir, INBOX_FILENAME)
        self.pid_path = os.path.join(data_dir, "live_alert_daemon.pid")
        self.status_path = os.path.join(data_dir, "live_alert_daemon.status.json")
        self.logger = logging.getLogger(f"{module}.alert_daemon")

    def _setup_logging(self) -> None:
        if self.logger.handlers:
            return
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        handler = RotatingFileHandler(
            self.log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
        for noisy in ("tastytrade", "httpx", "httpcore", "websockets", "asyncio"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    def running_pid(self) -> int | None:
        from cherrypick.core import looplock

        try:
            with open(self.pid_path, encoding="utf-8") as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            return None
        if looplock.pid_alive(pid):
            return pid
        _unlink(self.pid_path)
        return None

    def _write_status(self, **fields) -> None:
        try:
            with open(self.status_path, "w", encoding="utf-8") as f:
                json.dump(fields, f)
        except OSError:
            pass

    def status(self) -> dict:
        try:
            with open(self.status_path, encoding="utf-8") as f:
                own = json.load(f)
        except (OSError, ValueError):
            own = {}
        pid = self.running_pid()
        return {"running": pid is not None, "pid": pid, **own}

    def stop(self) -> dict:
        pid = self.running_pid()
        if pid is None:
            return {"ok": True, "stopped": False, "detail": "not running"}
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, SystemError) as exc:
            return {"ok": False, "stopped": False, "detail": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "stopped": True, "pid": pid}

    def run(
        self,
        broker: Any,
        *,
        deadline_seconds: float | None,
        on_alert: Callable[[dict], None] | None = None,
        now_iso: Callable[[], str] | None = None,
        listen_slice: float = LISTEN_SLICE_SECONDS,
        max_seconds: float | None = None,
        clock_fn: Callable[[], float] = time.monotonic,
        conn=None,
    ) -> dict:
        """Subscribe in slices, record every alert, call `on_alert`, until the deadline, SIGTERM or
        `max_seconds`. `wait_for_order_alerts` fails closed to [] on any websocket/auth error, so a
        dropped connection is an empty slice and the next one reconnects."""
        self._setup_logging()
        now_iso = now_iso or (lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
        owns_conn = conn is None
        conn = conn or connect(self.inbox_path)
        deadline = None
        for candidate in (deadline_seconds, max_seconds):
            if candidate is not None:
                deadline = candidate if deadline is None else min(deadline, candidate)
        stopping = {"flag": False}

        def _on_signal(_signum, _frame):
            stopping["flag"] = True

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _on_signal)
            except (ValueError, OSError):
                pass
        try:
            pruned = prune_before(conn, now_iso()[:10])
        except Exception as exc:  # noqa: BLE001 -- a failed prune must never stop the daemon
            pruned = 0
            self.logger.warning("inbox prune failed (%s: %s)", type(exc).__name__, exc)
        started = clock_fn()
        connected_since = now_iso()
        seen, last_at = 0, None
        self.logger.info("%s alert daemon started (pruned %d stale alert(s))", self.module, pruned)
        self._write_status(
            connected_since=connected_since, alerts_seen=0, last_alert_at=None, heartbeat_at=connected_since
        )
        try:
            while not stopping["flag"]:
                elapsed = clock_fn() - started
                if deadline is not None and elapsed >= deadline:
                    self.logger.info("alert daemon reached its deadline -- exiting")
                    break
                remaining = (
                    listen_slice if deadline is None else max(0.0, min(listen_slice, deadline - elapsed))
                )
                if remaining <= 0:
                    break
                for alert in broker.wait_for_order_alerts(set(), remaining) or []:
                    received_at = now_iso()
                    try:
                        record_alert(conn, alert, received_at)
                    except Exception as exc:  # noqa: BLE001 -- one bad row must not kill the stream
                        self.logger.warning("inbox write failed (%s: %s)", type(exc).__name__, exc)
                        continue
                    seen += 1
                    last_at = received_at
                    self.logger.info("alert: order %s -> %s", alert.get("order_id"), alert.get("status"))
                    if on_alert is not None:
                        try:
                            on_alert(alert)
                        except Exception as exc:  # noqa: BLE001 -- the stream outlives its listeners
                            self.logger.warning("on_alert failed (%s: %s)", type(exc).__name__, exc)
                self._write_status(
                    connected_since=connected_since,
                    alerts_seen=seen,
                    last_alert_at=last_at,
                    heartbeat_at=now_iso(),
                )
        finally:
            if owns_conn:
                conn.close()
            self.logger.info("alert daemon stopped (%d alert(s) recorded)", seen)
        return {"ok": True, "alerts_seen": seen, "connected_since": connected_since}

    def main(
        self,
        *,
        make_broker: Callable[[], Any],
        deadline_seconds: Callable[[], float | None],
        on_alert=None,
        argv: Iterable[str] | None = None,
    ) -> int:
        """The module's `__main__`: --status, --stop, or run under a PID file."""
        import argparse

        ap = argparse.ArgumentParser(prog=f"{self.module}-alert-daemon")
        ap.add_argument("--status", action="store_true")
        ap.add_argument("--stop", action="store_true")
        ap.add_argument("--max-seconds", type=float, default=None)
        args = ap.parse_args(list(argv) if argv is not None else None)
        if args.status:
            print(json.dumps(self.status(), indent=2))
            return 0
        if args.stop:
            print(json.dumps(self.stop(), indent=2))
            return 0
        existing = self.running_pid()
        if existing is not None:
            print(json.dumps({"ok": False, "detail": f"already running (pid {existing})"}))
            return 1
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.pid_path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        try:
            out = self.run(
                make_broker(),
                deadline_seconds=deadline_seconds(),
                on_alert=on_alert,
                max_seconds=args.max_seconds,
            )
            print(json.dumps(out))
            return 0
        finally:
            _unlink(self.pid_path)


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
