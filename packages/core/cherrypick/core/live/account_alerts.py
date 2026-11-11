"""cherrypick.core.live.account_alerts -- the account's order events, from the stream and from REST.

One service for the designated account (2026-11-02), kept up by the orchestrator's supervisor through
every trading session whether or not a live loop is armed. It replaced the per-module order-alert
daemons, which ran only while a module was armed: a resting stop that filled after the disarm, or a
manual order, was heard by nobody.

- It records every order status it sees -- placed, working, filled, ended unfilled -- into one inbox
  (`account_alerts.db`), once per (order, status), from whichever source saw it first: the
  account-alert websocket, listened to in slices, or a REST read of today's orders after every slice.
  A status only REST saw is the stream having missed it; a fill like that is counted in the status
  file, so the watchdog can say the stream is unreliable.
- It is read-only. It holds an authenticated session and reads orders; it places, cancels and decides
  nothing, and no ledger depends on it. Every fill is still confirmed by the owning loop's own
  `broker.status()` read; this is early word for the orchestrator's `live-fill-alerts` to push.
- It imports no module. The designated account is read from the keyring service it is given
  (`--keyring-service`), with the suite's shared OAuth secrets behind it.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from cherrypick.core import home as _home

PACKAGE = "account-alerts"
INBOX_FILENAME = "account_alerts.db"
STATUS_FILENAME = "account_alerts.status.json"
LISTEN_SLICE_SECONDS = 30.0
# A slice that comes back this fast with nothing is the stream failing closed (auth, a refused
# connection), not a quiet market: wait before the next, or a dead stream becomes a busy loop.
FAST_EMPTY_SECONDS = 2.0
FAILED_SLICE_PAUSE_SECONDS = 5.0
BUSY_TIMEOUT_MS = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS order_events (
    order_id            TEXT NOT NULL,
    status              TEXT NOT NULL,
    price               TEXT,
    underlying_symbol   TEXT,
    external_identifier TEXT,
    source              TEXT NOT NULL,
    received_at         TEXT NOT NULL,
    PRIMARY KEY (order_id, status)
);
CREATE INDEX IF NOT EXISTS idx_order_events_received ON order_events(received_at);
"""


def data_dir() -> str:
    return str(_home.data_dir("live"))


def _is_main(login: str | None) -> bool:
    return login in (None, "", "main")


def _suffixed(filename: str, login: str | None) -> str:
    """`account_alerts.db` for the main login (the path it always had), `account_alerts.<login>.db`
    for an extra one: one inbox and status file per login, never shared."""
    if _is_main(login):
        return filename
    stem, _, ext = filename.partition(".")
    return f"{stem}.{login}.{ext}"


def package_name(login: str | None = None) -> str:
    """The heartbeat name: `account-alerts`, or `account-alerts.<login>` (its supervisor job id)."""
    return PACKAGE if _is_main(login) else f"{PACKAGE}.{login}"


def inbox_path(login: str | None = None) -> str:
    return os.path.join(data_dir(), _suffixed(INBOX_FILENAME, login))


def status_path(login: str | None = None) -> str:
    return os.path.join(data_dir(), _suffixed(STATUS_FILENAME, login))


# --------------------------------------------------------------------------- the inbox
def connect(path: str) -> sqlite3.Connection:
    """WAL + busy_timeout: ONE writer (this service), any number of readers who never block it."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.executescript(_SCHEMA)
    return conn


def record(conn, event: dict, source: str, received_at: str) -> bool:
    """One (order, status), the first time either source reports it. True when it was new. The
    status is stored lower-cased, the broker's own word ("filled", "removed")."""
    status = str(event.get("status") or "").strip().lower()
    if event.get("order_id") is None or not status or status == "none":
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO order_events "
        "(order_id, status, price, underlying_symbol, external_identifier, source, received_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            str(event["order_id"]),
            status,
            event.get("price"),
            event.get("underlying_symbol"),
            event.get("external_identifier"),
            source,
            received_at,
        ),
    )
    conn.commit()
    return cur.rowcount == 1


def prune_before(conn, day: str) -> int:
    cur = conn.execute("DELETE FROM order_events WHERE received_at < ?", (day,))
    conn.commit()
    return cur.rowcount


def events_after(path: str, after: int | None) -> list[dict]:
    """Every event recorded after sequence number `after` (exclusive), in the order recorded, each
    with its `seq`, read-only. A sequence, not a time: two events can share a second, and a cursor on
    the time would skip the one recorded after a read. An inbox that does not exist yet has none; one
    that cannot be read returns none rather than raising."""
    if not os.path.exists(path):
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT rowid AS seq, * FROM order_events WHERE rowid > ? ORDER BY rowid",
                (after or 0,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def read_status(login: str | None = None) -> dict:
    """The service's own status file ({} when absent or unreadable): for the watchdog and the console."""
    try:
        with open(status_path(login), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------- the service
def run(
    broker: Any,
    *,
    conn=None,
    now_iso: Callable[[], str] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    listen_slice: float = LISTEN_SLICE_SECONDS,
    max_seconds: float | None = None,
    log: Callable[[str], None] = print,
    login: str | None = None,
) -> dict:
    """Listen in slices and read today's orders after each, recording every new (order, status),
    until SIGTERM or `max_seconds`. `broker` is a `core.execution.Broker`: its
    `wait_for_order_alerts` fails closed to [] and its `orders_today` raises on failure, so a dead
    stream is an empty slice and a dead REST read is counted, never fatal. `login` picks the
    inbox, status file and heartbeat (None = main's, as before)."""
    now_iso = now_iso or (lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
    owns = conn is None
    conn = conn or connect(inbox_path(login))
    stopping = {"flag": False}

    def _on_signal(_signum, _frame):
        stopping["flag"] = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass

    started_at = now_iso()
    state = {
        "started_at": started_at,
        "day": started_at[:10],
        "events": 0,
        "last_stream_event_at": None,
        "last_rest_ok_at": None,
        "rest_failures": 0,
        "last_rest_error": None,
        "stream_missed_fills": [],
    }
    try:
        prune_before(conn, state["day"])
    except sqlite3.Error as exc:
        log(f"inbox prune failed ({type(exc).__name__}: {exc})")
    began = clock()
    # Fills the REST read recorded first. A fill landing between a slice's end and the read is the
    # read's by a few milliseconds, not a miss: only one the NEXT slice does not push either counts.
    rest_first: set[str] = set()
    try:
        while not stopping["flag"]:
            if max_seconds is not None and clock() - began >= max_seconds:
                break
            _home.beat(package_name(login))
            slice_began = clock()
            pushed = broker.wait_for_order_alerts(set(), listen_slice) or []
            failed_fast = not pushed and clock() - slice_began < FAST_EMPTY_SECONDS
            for event in pushed:
                at = now_iso()
                if record(conn, event, "stream", at):
                    state["events"] += 1
                state["last_stream_event_at"] = at
            pushed_fills = {
                str(e.get("order_id"))
                for e in pushed
                if str(e.get("status") or "").strip().lower() == "filled"
            }
            for oid in sorted(rest_first - pushed_fills):
                state["stream_missed_fills"].append(oid)
                log(f"order {oid} filled; only the REST read saw it")
            rest_first = set()
            try:
                for event in broker.orders_today():
                    at = now_iso()
                    if record(conn, event, "rest", at):
                        state["events"] += 1
                        if str(event.get("status") or "").strip().lower() == "filled":
                            rest_first.add(str(event.get("order_id")))
                state["last_rest_ok_at"] = now_iso()
                state["last_rest_error"] = None
            except Exception as exc:  # noqa: BLE001 -- a failed read is counted, never fatal
                state["rest_failures"] += 1
                state["last_rest_error"] = f"{type(exc).__name__}: {exc}"[:300]
            _write_status({**state, "login": login or "main", "heartbeat_at": now_iso()}, login)
            if failed_fast:
                sleep(FAILED_SLICE_PAUSE_SECONDS)
    finally:
        if owns:
            conn.close()
    return {"ok": True, **state}


def _write_status(fields: dict, login: str | None = None) -> None:
    try:
        os.makedirs(data_dir(), exist_ok=True)
        path = status_path(login)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(fields, f)
        os.replace(tmp, path)
    except OSError:
        pass


def make_broker(keyring_service: str):
    """A read-only `Broker` on the account `keyring_service` designates: no live gates, because it
    never submits, and the suite's shared OAuth secrets behind the service's own."""
    from cherrypick.core import execution
    from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

    store = CredentialStore(keyring_service, legacy_service_names=(SHARED_SERVICE,))
    return execution.Broker(
        get_session=SessionManager(store).get_session,
        designated_account=store.designated_account,
        live_gates=lambda: ["account-alerts is read-only"],
    )


def resolve_login_account(accounts: list[str], last4: str | None) -> str | None:
    """The one account an extra login's service watches: the one ending in `last4`, or, with no
    `last4`, the login's only account. None when that is not exactly one -- the service then
    refuses its reads (`AccountNotDesignated`) and says so in its status, never picks the first."""
    if last4:
        matches = [n for n in accounts if str(n).endswith(str(last4)[-4:])]
        return matches[0] if len(matches) == 1 else None
    return accounts[0] if len(accounts) == 1 else None


def make_login_broker(login: str, account_last4: str | None = None):
    """A read-only `Broker` on an extra login's own store (no fallback), watching one account of it
    (`resolve_login_account`). Nothing is designated on an extra login; the account is resolved from
    the login's own account list each time the adapter builds."""
    from cherrypick.core import broker as _broker
    from cherrypick.core import execution
    from cherrypick.core.auth import SessionManager
    from cherrypick.core.auth import logins as _logins

    sessions = SessionManager(_logins.login_store(login))
    adapter: dict[str, Any] = {}

    def _watched() -> str | None:
        # Resolved once, then kept: the account list does not change within a session, and
        # listing it every slice would be one more broker read every 30 seconds.
        if adapter.get("account") is None:
            listed = adapter["broker"].run(_broker.list_accounts(sessions.get_session()))
            numbers = [a.get("account_number") for a in listed if a.get("account_number")]
            adapter["account"] = resolve_login_account(numbers, account_last4)
        return adapter["account"]

    adapter["broker"] = execution.Broker(
        get_session=sessions.get_session,
        designated_account=_watched,
        live_gates=lambda: ["account-alerts is read-only"],
    )
    return adapter["broker"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="account-alerts", description=__doc__.split("\n\n")[0])
    ap.add_argument("--keyring-service", help="the keyring service whose designated account to read")
    ap.add_argument("--login", default=None, help="an extra broker login to watch instead (its own store)")
    ap.add_argument("--account", default=None, help="with --login: the last 4 of the account to watch")
    ap.add_argument("--status", action="store_true", help="print the service's status file and exit")
    ap.add_argument("--max-seconds", type=float, default=None)
    args = ap.parse_args(argv)
    if args.status:
        print(json.dumps(read_status(args.login), indent=2))
        return 0
    if not _is_main(args.login):
        from cherrypick.core.auth import logins as _logins

        login = _logins.check_login_name(args.login)
        out = run(make_login_broker(login, args.account), max_seconds=args.max_seconds, login=login)
        print(json.dumps(out, default=str))
        return 0
    if not args.keyring_service:
        ap.error("--keyring-service is required")
    out = run(make_broker(args.keyring_service), max_seconds=args.max_seconds)
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
