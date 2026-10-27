#!/usr/bin/env python3
"""MEIC LIVE loop — the paper loop's sibling, pinned to one symbol. INERT BY DEFAULT.

This is the rung-1 measurement pilot from the live-loop plan: the same market-data fetch
`paper_loop.py` already uses (reused directly, not duplicated), the same pure decision
functions `paper.py` runs in paper every tick (`evaluate_entry`, `evaluate_open_trade`,
`force_close_active`, `settlement_active`), and the same DB-write helpers paper already uses
(`paper._save_trade`, `_update_trade`, `_apply_exit_decision`, `_get_open_trades`,
`_profile_day_stats`) -- pointed at the live ledger (`meic_trades.db`, `paths.live_db_path()`)
instead of the paper one. Paper and live can therefore never silently diverge on entry/stop
logic: they call the identical functions. Only order construction (`live_orders.py`) and
submission are new.

It will not place a live order today, by construction:

  - `readiness()` must come back empty: `enable_live_trading` true, `live.symbol` set,
    a non-empty `live.gate0_confirmed` human attestation and a designated account.
  - The suite halt flag (`state/halt-live.flag`) and the daily-loss breaker block NEW ICs only
    (`entry_blockers`, 2026-10-08): fills are still confirmed and open ICs still stopped and
    force-closed, because a close must always get through.
  - Even then, `--dry-run` (the default!) preflights every order against the real account and
    places nothing -- running the loop with `--dry-run --once` during market hours is a repeat
    of the rung-0 smoke (`live_smoke.py`) but through the actual loop code path.
  - `--live` additionally requires every readiness gate.

Scaffold boundaries (deliberate, rung-1 only -- see docs/live-trading-plan.md once written):
no ORB debit spreads, no multi-symbol (one pinned `live.symbol`), no working-order repricing.

Arming (2026-10-14, the flies/bwb posture): `/live-meic-start` arms ONE day by writing the arm
record (`--install-task`); the orchestrator's supervisor runs `meic-live` ticks only while that
record is today's, and each `--live` tick deletes it once it is stale or past `live.disarm_time`.
The orchestrator still never arms anything -- it reads the record a human's YES wrote.

Fill confirmation (2026-09-17). Until then every accepted placement was recorded as a fill at
the submitted limit -- an entry became an OPEN row the moment the broker accepted the order,
with the price asked for as its credit. Now an entry is saved `status='pending'` and the next
tick (and every tick after) asks the broker; a confirmed fill flips it to `open` with the ACTUAL
net credit and `fill_confirmed_at`, and an order that dies unfilled (cancelled / rejected /
expired) becomes `cancelled` with no P&L and frees its slot. The per-side credits follow the
fill too (2026-10-12, `entry_fill_credits`): from the broker's own leg fills where all four are
reported, else the fill split in the modelled ratio -- so a live stop or expiry is charged against
what each side actually took in. A pending row still counts toward
`max_concurrent_ics` -- it is a position at risk -- and is not managed for exits until it fills.
Exits are recorded on confirmation too (2026-09-17, second pass). A close order's decision is
stashed on the row (`pending_exit_json`) and the side marked `{side}_stop_fill_status='pending'`;
the ledger's exit accounting (`paper._apply_exit_decision`, the same function paper runs) is
applied only when the broker confirms the fill, with the ACTUAL price in the modeled price's
place. A close still working on the next tick is cancelled and replaced at the stop rule off
fresh quotes -- paper fills a stop instantly at the limit; live re-prices every minute, and the
gap between the two is the pilot's measurement. A close that dies unfilled simply clears its
marker: the side was never recorded closed, so the next tick re-evaluates it under the same rule.
Nothing is reopened because nothing was closed early. An unfilled end-of-day force-close on a
cash-settled side falls through to settlement, recorded as expired, the path a held side takes.

The submission seam is `cherrypick.core.execution.Broker` (2026-09-17): one session on one
process-wide loop, the module's own gates re-checked on every live submit, the deploy governor
applied. It replaced an adapter that shelled out to `tt.py execute_trade` and scraped stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from cherrypick.core import calendar as _cal  # noqa: E402
from cherrypick.core import execution as _execution  # noqa: E402
from cherrypick.core import home as _home  # noqa: E402
from cherrypick.core import killcriteria as _kill  # noqa: E402
from cherrypick.core import ledgers as _ledgers  # noqa: E402
from cherrypick.core import live as _live  # noqa: E402
from cherrypick.core import livepositions as _livepos  # noqa: E402
from cherrypick.core import looplock as _looplock  # noqa: E402
from cherrypick.core.streamcache import occ_to_streamer_symbol as _occ_to_streamer  # noqa: E402

from cherrypick.meic import credentials as _creds  # noqa: E402
from cherrypick.meic import (
    live_orders,  # noqa: E402
    paper,  # noqa: E402  (the pure decision functions + paper-DB helpers this loop reuses)
)
from cherrypick.meic import (
    paper_loop as _pl,  # noqa: E402  (market-data fetch helpers -- reused, not duplicated)
)
from cherrypick.meic import paths as _paths  # noqa: E402
from cherrypick.meic import regime as _regime  # noqa: E402
from cherrypick.meic import stream_request as _stream_request  # noqa: E402


def halt_flag_path() -> str:
    """The suite-wide live kill switch; presence is the signal. Resolved through
    `cherrypick.core.home.halt_flag_path` -- the hand-rolled copy this replaced skipped `$VAR`
    expansion, so a `CHERRYPICK_HOME` the orchestrator understood could point this loop at a
    file nobody ever touched."""
    return str(_home.halt_flag_path())


def _designated_account() -> str | None:
    return _creds.store.designated_account()


def readiness(config: dict, *, designated: str | None) -> list[str]:
    """The unmet live gates, checked every tick -- empty means the loop may act. Pure.

    `enable_live_trading` is MEIC's existing single kill switch (already enforced by
    `tt.py`'s own `cmd_execute_trade`) -- there is no second `live.enabled` flag the way
    flies has one, since flies had no other kill switch before its live scaffold existed.

    ACCOUNT gates only. The halt flag and the daily-loss breaker are entry blockers
    (`entry_blockers`, 2026-10-08): they stop new ICs, never a stop-loss close or a force-close --
    before that date both returned the tick ahead of `run_once`, and the halt also gated every submit
    at the seam, so a halt left open 0DTE ICs with no stop management at all.

    A `live.arm` that names no arm, or an arm that follows live itself, is refused here (2026-10-14):
    the only impurity, a registry read, happens only when an arm is named."""
    live = config.get("live") or {}
    unmet = []
    if not config.get("enable_live_trading"):
        unmet.append("enable_live_trading is false")
    if not str(live.get("symbol") or "").strip():
        unmet.append("live.symbol is unset -- pin the one symbol this rung trades")
    if not str(live.get("gate0_confirmed") or "").strip():
        unmet.append("live.gate0_confirmed is empty -- a human must attest Gate 0 (who/when)")
    problem = paper.live_arm_problem(config)
    if problem:
        unmet.append(problem)
    if not designated:
        unmet.append("no designated account -- run `cherrypick account --module meic --set <last4>`")
    return unmet


def daily_loss_tripped(db_path: str, day: str, limit_dollars: float | None) -> bool:
    """The daily-loss breaker over the LIVE ledger: today's GROSS P&L, `ic_trades.pnl`, which is
    before fees on this ledger (fees sit in their own column), summed across every status -- a
    still-open IC with one stopped side already contributes via `_apply_exit_decision`'s running
    total -- at or below -limit halts new entries. So it trips on gross, not net. Direct query,
    same style as `paper.py::_profile_day_stats`."""
    if not limit_dollars:
        return False
    import sqlite3

    try:
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM ic_trades WHERE trade_date = ?", (day,)
        ).fetchone()
        con.close()
    except sqlite3.Error:
        # Fails CLOSED: an unreadable ledger cannot show the day's loss is within the limit, and the
        # breaker only ever blocks new entries -- closes go through regardless.
        return True
    return float(row[0] or 0.0) <= -abs(limit_dollars)


def kill_verdict(config: dict, db_path: str) -> dict:
    """`core.killcriteria.gate` over the live ledger's closed ICs. An unreadable ledger fails CLOSED,
    as the daily-loss breaker does: it cannot show the arm is inside its criteria.

    A closed live IC since the criteria were written with no net (`pnl` or `fees` NULL -- the
    2026-06-29 stop was recorded that way) also blocks, from 2026-10-25: the verdict skips a row with
    no net (n/r is not a zero), so its loss would be invisible to the drawdown and the streak. It
    does not latch -- recording the row's `pnl` and `fees` is the fix -- and, like every kill verdict,
    it stops new entries only. Earnings refuses on the same rows (`tt.kill_refusal`)."""
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            records = _ledgers.READERS["meic_ic"](con)
        finally:
            con.close()
    except sqlite3.Error as exc:
        return {"state": _kill.BLOCKED, "reasons": [f"live ledger unreadable: {exc}"]}
    criteria = _live_cfg(config).get("kill")
    verdict = _kill.gate("meic", EXECUTION_MODE, records, criteria)
    if verdict["state"] == _kill.BLOCKED:
        return verdict
    blind = _kill.unrecorded(records, EXECUTION_MODE, criteria)
    if blind:
        ids = ", ".join(f"{r.get('position_id') or '?'} ({r.get('session') or '?'})" for r in blind)
        return {
            **verdict,
            "state": _kill.BLOCKED,
            "reasons": [
                f"{len(blind)} closed live IC(s) since the criteria were written have no net (pnl or"
                f" fees unrecorded: {ids}); record their pnl and fees in the live ledger before another entry"
            ],
        }
    return verdict


def entry_blockers(config: dict, db_path: str, day: str, *, halt_present: bool) -> list[str]:
    """Why no NEW IC may be opened this tick; empty means entries may proceed. Fill confirmation and
    the management of open ICs -- stops, force-closes -- run regardless."""
    blockers = []
    if halt_present:
        blockers.append("halt_flag_present")
    if daily_loss_tripped(db_path, day, (config.get("live") or {}).get("daily_loss_halt_dollars")):
        blockers.append("daily_loss_breaker")
    return blockers


EXECUTION_MODE = "live"

# --------------------------------------------------------------------------- arming (2026-10-14)
#
# The flies/bwb posture, through `cherrypick.core.live`: live is armed FOR ONE DAY by
# `/live-meic-start` (status readout, disclaimer, literal YES), which writes the arm record the
# supervisor turns into the `meic-live` job. Every `--live` tick checks the record first and
# deletes it once it is not today's or the clock is past `live.disarm_time`. Before this,
# `enable_live_trading` was the only switch and stayed on until someone edited the file. It stays
# the master switch (tt.py enforces it too); the arm record is what makes a day live.

ARMED_BY = "live-meic-start"
DEFAULT_DISARM = "17:00"
DEFAULT_MAX_STRUCTURES = 1


def _live_cfg(config: dict) -> dict:
    return config.get("live") or {}


def _data_dir() -> str:
    return os.path.dirname(str(_paths.live_db_path()))


def _once_lock_path() -> str:
    return os.path.join(_data_dir(), "live_once.lock")


def _orphans_path() -> str:
    return os.path.join(_data_dir(), "live_orphans.json")


def _hold_path() -> str:
    return os.path.join(_data_dir(), "live_held.json")


def _now_min(now: datetime) -> int:
    return now.hour * 60 + now.minute


def disarm_min(config: dict) -> int:
    return paper._time_to_minutes(_live_cfg(config).get("disarm_time") or DEFAULT_DISARM)


def arm_stamp_date() -> str | None:
    return _live.arm_record_date("meic")


def should_disarm(config: dict, now_min: int, today: str) -> str | None:
    return _live.should_disarm(
        arm_stamp_date(),
        today=today,
        now_min=now_min,
        disarm_min=disarm_min(config),
        disarm_label=_live_cfg(config).get("disarm_time") or DEFAULT_DISARM,
    )


def _spawn_first_tick() -> None:
    """One detached `--once --live` right away, so arming does not wait a full interval."""
    import subprocess

    exe = sys.executable
    if os.name == "nt" and exe.lower().endswith("python.exe") and os.path.exists(exe[:-10] + "pythonw.exe"):
        exe = exe[:-10] + "pythonw.exe"
    flags = 0x00000008 | 0x08000000 | 0x00000200 if os.name == "nt" else 0  # DETACHED|NO_WINDOW|NEW_GROUP
    subprocess.Popen(
        [exe, "-m", "cherrypick.meic.live_loop", "--once", "--live"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        start_new_session=(os.name != "nt"),
    )


def alert_daemon_companion(config: dict | None) -> dict | None:
    """The order-alert daemon as an arm-record companion (2026-10-15, flies' shape), or None when
    `live.use_order_alert_daemon` is off. The supervisor keeps it up while armed and until
    `disarm_time`, restarting it on death and on a heartbeat silent for four of its 30 s slices."""
    live = _live_cfg(config or {})
    if not live.get("use_order_alert_daemon"):
        return None
    from cherrypick.meic import alert_daemon

    return {
        "argv": ["-m", "cherrypick.meic.alert_daemon"],
        "heartbeat": alert_daemon.daemon().status_path,
        "silence_seconds": 120,
        "until": live.get("disarm_time") or DEFAULT_DISARM,
    }


def install_task(config: dict | None = None) -> dict:
    """Arm for TODAY: the arm record, under a live supervisor, and nothing else. With the alert daemon
    on, the record names it as a companion and the supervisor keeps it up too."""
    now = _pl._now_et()
    companion = alert_daemon_companion(config if config is not None else paper.load_base_config())
    return _live.arm(
        "meic",
        date=now.strftime("%Y-%m-%d"),
        at=now.isoformat(timespec="seconds"),
        armed_by=ARMED_BY,
        spawn_first_tick=_spawn_first_tick,
        companions={"alert-daemon": companion} if companion else None,
    )


def uninstall_task() -> dict:
    return _live.disarm("meic")


# --------------------------------------------------------------------------- caps and sweep


def max_structures_per_day(config: dict) -> int | None:
    """New ICs live may open in one session (2026-10-14, flies/bwb's cap). A cancelled entry never
    established anything and does not spend it; a pending one does -- it is a position at risk.

    Absent: the default of 1. Set to null (2026-10-15): no count cap at all, so live is sized by
    `max_open_bp_dollars` alone -- every entry still has to fit under it with what is open."""
    live = _live_cfg(config)
    if "max_structures_per_day" in live and live["max_structures_per_day"] is None:
        return None
    return int(live.get("max_structures_per_day", DEFAULT_MAX_STRUCTURES))


def entries_today(db_path: str, symbol: str, day: str) -> int:
    """Today's live ICs on `symbol` that were not cancelled. An unreadable ledger answers with a
    count no cap admits: it cannot show the budget is unspent, and this only blocks entries."""
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM ic_trades WHERE trade_date = ? AND symbol = ? AND status != 'cancelled'",
                (day, symbol),
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return 10**9
    return int(row[0] or 0)


_ORDER_ID_COLUMNS = (
    "put_spread_entry_order_id",
    "call_spread_entry_order_id",
    "put_stop_order_id",
    "call_stop_order_id",
)


def _known_order_ids(db_path: str) -> list[str]:
    """Every broker order id this ledger has recorded: the entry and each side's close. A ledger
    with no file or no table yet has recorded nothing -- an empty set, not a failure."""
    import sqlite3

    if not os.path.exists(db_path):
        return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cols = " UNION ".join(f"SELECT {c} FROM ic_trades WHERE {c} IS NOT NULL" for c in _ORDER_ID_COLUMNS)
        return [str(r[0]) for r in con.execute(cols).fetchall()]
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    finally:
        con.close()


def _sweep_orphans(broker, db_path: str, symbol: str, log) -> int | None:
    """Broker truth against ledger belief (`core.execution.sweep_orphans`), counting the other live
    loops' published orders as known -- flies and bwb trade SPX in this account too. Detection only;
    None means the sweep could not look, and unknown blocks new entries."""
    import sqlite3

    try:
        known = _known_order_ids(db_path)
    except sqlite3.Error as exc:
        log(f"orphan sweep: live ledger unreadable ({exc}) -- will retry next tick")
        return None
    return _execution.sweep_orphans(
        broker,
        known,
        symbol=symbol,
        path=_orphans_path(),
        now_iso=_pl._now_et().isoformat(timespec="seconds"),
        log=log,
        module="meic",
    )


def read_orphans() -> list[dict]:
    return _execution.read_orphans(_orphans_path())


# --------------------------------------------------------------------------- status


class LedgerUnreadable(RuntimeError):
    """The live ledger exists but could not be read: not the same thing as holding nothing."""


def _ledger_rows(db_path: str, where: str, params: tuple) -> list[dict]:
    """`ic_trades` rows matching `where`. A ledger that does not exist yet holds nothing; one that
    exists and cannot be read raises `LedgerUnreadable` (2026-10-27). Until then it read as empty,
    so a locked or corrupt ledger told the watchdog the day was settled and nothing was overdue."""
    import sqlite3

    if not os.path.exists(db_path):
        return []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(f"SELECT * FROM ic_trades WHERE {where}", params)]
        finally:
            con.close()
    except sqlite3.Error as exc:
        raise LedgerUnreadable(f"live ledger unreadable: {type(exc).__name__}: {exc}") from exc


def overdue_settlement(db_path: str, today: str) -> list[dict]:
    """Past sessions the live ledger still holds open (or pending) ICs for, oldest first. A 0DTE
    IC settles inside its own session's ticks, so one left open is a session nothing finished."""
    out: dict[str, dict] = {}
    for t in _ledger_rows(
        db_path, "trade_date < ? AND status IN ('pending', 'open', 'partial', 'partial_entry')", (today,)
    ):
        row = out.setdefault(
            t["trade_date"], {"session": t["trade_date"], "positions": 0, "pending_entries": 0}
        )
        row["pending_entries" if t["status"] == "pending" else "positions"] += 1
    return [out[k] for k in sorted(out)]


class _NoBroker:
    """The broker a settlement of a PAST session needs: nothing is placed, and a close order left
    working from that day died at its close as a Day order, so cancelling it is already true."""

    def cancel(self, order_id):
        return {"ok": True, "order_id": order_id}


def settle_session(
    config: dict, db_path: str, session: str, symbol: str, price: float, source: str, *, log
) -> dict:
    """Settle `symbol`'s ICs still held from `session` at `price` -- through `_manage_open_trades`,
    the path a live tick takes at the bell, so a catch-up and a hand-settled day write exactly what
    the tick would have. `source` must be official (`core.settlement.OFFICIAL_SOURCES`)."""
    base_config = paper.load_base_config()
    settle_min = paper._time_to_minutes(base_config.get("expiration_settlement_time", "16:00")) + 1
    snapshot = {
        "symbol": symbol,
        "date": session,
        "expiration": session,
        "now_et": f"{settle_min // 60:02d}:{settle_min % 60:02d}",
        "underlying_price": price,
        "leg_quotes": {},
    }
    return _manage_open_trades(
        symbol,
        snapshot,
        paper.live_params(config),
        db_path,
        _NoBroker(),
        live=True,
        log=log,
        settle_price=lambda _s: (price, source),
    )


def settle_overdue(config: dict, db_path: str, today: str, *, close_fn=None, log=print) -> dict:
    """Settle every past session the live ledger still holds open, at THAT session's official close
    (`core.settlement.dated_index_close`, source `yahoo_daily`) -- never a provisional price, the
    flies/bwb rule. A session with a pending entry (its order died at that close) is left for a
    person, and one with no posted close found is left open and keeps alerting."""
    from cherrypick.core import settlement as _settlement

    close_fn = close_fn or _settlement.dated_index_close
    done, left = [], []
    try:
        overdue = overdue_settlement(db_path, today)
    except LedgerUnreadable as exc:
        return {"ok": False, "error": str(exc), "settled": [], "left": []}
    for item in overdue:
        session = item["session"]
        if item["pending_entries"]:
            left.append({"session": session, "reason": "pending_entries", "count": item["pending_entries"]})
        if not item["positions"]:
            continue
        try:
            held = _ledger_rows(
                db_path, "trade_date = ? AND status IN ('open', 'partial', 'partial_entry')", (session,)
            )
        except LedgerUnreadable as exc:
            return {"ok": False, "error": str(exc), "settled": done, "left": left}
        for symbol in sorted({t["symbol"] for t in held}):
            price = close_fn(symbol, session)
            if price is None:
                left.append({"session": session, "symbol": symbol, "reason": "no_official_close"})
                continue
            counts = settle_session(config, db_path, session, symbol, price, "yahoo_daily", log=log)
            done.append({"session": session, "symbol": symbol, "price": price, **counts})
    return {"ok": True, "settled": done, "left": left}


def run_status(config: dict, *, designated: str | None) -> dict:
    """One JSON object for the arming command and the watchdog -- files and the ledger only, never
    the broker. Keys the watchdog reads: armed_for, orphaned_orders, broker_held, broker_health,
    overdue_settlement, session_settled, open_positions."""
    now = _pl._now_et()
    today = now.strftime("%Y-%m-%d")
    db_path = str(_paths.live_db_path())
    live = _live_cfg(config)
    symbol = live.get("symbol")
    params = paper.live_params(config)
    unreadable = None
    try:
        open_rows = _ledger_rows(
            db_path, "trade_date = ? AND status IN ('open', 'partial', 'partial_entry')", (today,)
        )
        pending = _ledger_rows(db_path, "status = 'pending'", ())
        overdue = overdue_settlement(db_path, today)
    except LedgerUnreadable as exc:
        # Never a clean day: the watchdog reads `ledger_unreadable` as CRITICAL, and `session_settled`
        # stays False so nothing downstream reads the book as flat.
        unreadable, open_rows, pending, overdue = str(exc), [], [], []
    return {
        "ok": True,
        "date": today,
        "armed_for": arm_stamp_date(),
        "disarm_time": live.get("disarm_time") or DEFAULT_DISARM,
        "enable_live_trading": bool(config.get("enable_live_trading")),
        "unmet_gates": readiness(config, designated=designated),
        "symbol": symbol,
        "arm": paper.live_arm_name(config),
        "entry_window": [
            params.get("paper_entry_window_start") or params.get("entry_window_start"),
            params.get("entry_window_end"),
        ],
        "max_open_bp_dollars": params.get("max_open_bp_dollars"),
        "max_structures_per_day": max_structures_per_day(config),
        "entries_today": entries_today(db_path, symbol, today) if symbol else 0,
        "per_side_stop_management": params.get("per_side_stop_management"),
        "open_positions": None if unreadable else len(open_rows),
        "pending_orders": [t["ic_order_id"] for t in pending],
        "session_settled": False
        if unreadable
        else not open_rows and not [t for t in pending if t["trade_date"] == today],
        "ledger_unreadable": unreadable,
        "halt_flag": os.path.exists(halt_flag_path()),
        "breaker_tripped": daily_loss_tripped(db_path, today, live.get("daily_loss_halt_dollars")),
        "orphaned_orders": read_orphans(),
        "broker_held": _execution.read_hold(_hold_path()),
        "broker_health": _execution.read_broker_health(os.path.join(_data_dir(), _execution.HEALTH_FILENAME)),
        "overdue_settlement": overdue,
        "supervisor_fresh": _live.supervisor_heartbeat_fresh(),
        "live_db": db_path,
    }


_extract_order_id = _execution.order_id_of  # one reading of a placement result, suite-wide


def _closing_sides(trade: dict) -> list[str]:
    """Sides with a close order working at the broker: submitted, not yet confirmed either way."""
    return [
        s
        for s in ("put", "call")
        if trade.get(f"{s}_stop_fill_status") == "pending" and trade.get(f"{s}_stop_order_id")
    ]


def _trade_row(ic_order_id: str, db_path: str) -> dict | None:
    """The row as it stands now, read directly -- the exit accounting must see the latest fees,
    P&L and stop costs, not the copy the tick started with."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM ic_trades WHERE ic_order_id = ?", (ic_order_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _trades_with_working_orders(symbol: str, trade_date: str, db_path: str) -> list[dict]:
    """Every row with an order the broker still owes an answer on -- a pending entry, or a close
    working on either side -- whatever the row's status. Read directly rather than through
    `get_open_trades`, whose status set would drop a row whose first side's close has already
    been recorded."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM ic_trades WHERE symbol = ? AND trade_date = ? AND execution_mode = ? AND ("
            "status = 'pending' OR put_stop_fill_status = 'pending' OR call_stop_fill_status = 'pending')",
            (symbol, trade_date, EXECUTION_MODE),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _exit_for_side(stashed: dict, side: str, price: float | None) -> dict:
    """The per-side decision to apply once `side`'s close has filled at `price`: the stashed
    decision with its action narrowed to this side and the actual price in the modeled one's
    place. A stop becomes `stop_<side>`; a force-close stays `force_close` with only this side
    open. The other side's modeled slippage is dropped so it is not charged on this side's fill."""
    other = "call" if side == "put" else "put"
    base = dict(stashed.get("decision") or {})
    action = str(stashed.get("action") or base.get("action") or "")
    out = {**base, f"{side}_exit_price": price, f"{other}_exit_slippage": None}
    if action == "force_close":
        out.update({"action": "force_close", "put_open": side == "put", "call_open": side == "call"})
    else:
        out["action"] = f"stop_{side}"
    return out


def _submit_close(
    trade: dict, side: str, spec: dict, decision: dict, broker, *, live: bool, db_path: str, log
) -> bool:
    """Place one side's close and, live, mark the side closing with its decision stashed. Nothing
    is recorded closed here -- `_confirm_fills` does that on the broker's word."""
    result = broker.place(spec, live=live)
    log(
        f"close {side} ({'LIVE' if live else 'dry-run'}) for {trade['ic_order_id']}: {json.dumps(result, default=str)[:300]}"
    )
    if not result.get("ok"):
        return False
    if not live:
        return True
    oid = _extract_order_id(result)
    if oid is None:
        log(f"WARNING: close {side} for {trade['ic_order_id']} returned no order id; cannot track it")
        return False
    paper._update_trade(
        trade["ic_order_id"],
        {
            f"{side}_stop_order_id": oid,
            f"{side}_stop_fill_status": "pending",
            # The stash carries the limit ASKED for this side, never the modeled price: it is the
            # fallback if a fill comes back without a parseable price, and the model is not a fill.
            "pending_exit_json": json.dumps(
                {"action": decision["action"], "decision": {**decision, f"{side}_exit_price": spec["price"]}}
            ),
        },
        db_path,
    )
    return True


def _sides_to_close(decision: dict) -> list[str]:
    """Which side(s) an `evaluate_open_trade` decision requires closing -- force_close's
    `put_open`/`call_open` flags mean 'this side is still open', i.e. needs a close order."""
    action = decision["action"]
    if action == "stop_put":
        return ["put"]
    if action == "stop_call":
        return ["call"]
    if action == "stop_both":
        return ["put", "call"]
    if action == "force_close":
        return [s for s in ("put", "call") if decision.get(f"{s}_open")]
    return []


def official_close(broker, symbol: str) -> tuple[float | None, str]:
    """Today's settlement print for `symbol` (`core.settlement.official_index_close`): tastytrade's
    posted close, else Yahoo's or Barchart's post-close quote, else a provisional tick or nothing.
    Run on the broker's own loop (`Broker.run`) -- the cached session is bound to it, and a second
    loop would hang it. Never raises: a fetch that fails is 'no source', and the next tick asks."""
    from cherrypick.core import settlement as _settlement

    from cherrypick.meic.session import get_session

    if not hasattr(broker, "run"):
        return None, "no_broker_loop"
    try:
        return broker.run(_settlement.official_index_close(get_session(), symbol), _counts_as_contact=False)
    except Exception as exc:  # noqa: BLE001 -- a missing print is retried, never guessed
        return None, f"error: {type(exc).__name__}"


def _manage_open_trades(
    symbol: str,
    snapshot: dict,
    params: dict,
    db_path: str,
    broker,
    *,
    live: bool,
    log,
    settle_price=None,
) -> dict:
    """Mark-to-market + exit every open live IC on `symbol`, submitting a real close order for
    any actionable decision. Reuses `paper.evaluate_open_trade` (the exact function paper_loop
    calls) and `paper._apply_exit_decision` (the exact write path), with the decision's modeled
    exit price(s) overridden by what was actually submitted before the write.

    Settlement (2026-10-15, the flies/bwb rule): a held side settles ONLY on an official print,
    `settle_price(symbol) -> (price, source)` -- today's posted close by default, a past session's
    for the catch-up. Until one is posted the IC is left open and the next tick asks again; it was
    settled at whatever spot the 16:00 tick happened to read until this date."""
    open_ics = paper._get_open_trades(symbol, EXECUTION_MODE, snapshot["date"], db_path)
    base_config = paper.load_base_config()
    is_cash = paper._is_cash_settled(symbol, base_config)
    force_close, force_close_reason = paper.force_close_active(snapshot, base_config, is_cash)
    settle = paper.settlement_active(snapshot, base_config, is_cash)
    counts = {"stopped": 0, "force_closed": 0, "expired": 0, "held": 0, "closing": 0, "order_failed": 0}
    stop_limit_ratio = params.get("stop_limit_ratio", 1.02)
    leg_quotes = snapshot.get("leg_quotes", {})
    underlying = snapshot.get("underlying_price")
    if settle and any(t.get("status") != "pending" for t in open_ics):
        from cherrypick.core import settlement as _settlement

        price, source = settle_price(symbol) if settle_price else official_close(broker, symbol)
        counts["settle_source"] = source
        if price is None or not _settlement.is_official_source(source):
            # No posted close yet: nothing is settled and nothing else is done to a held IC after
            # the settlement time -- its quotes are dead, so a stop decided off them would be wrong.
            log(f"settlement: no official {symbol} close yet ({source}) -- open ICs left for the next tick")
            counts["awaiting_official_close"] = sum(1 for t in open_ics if t.get("status") != "pending")
            open_ics = [t for t in open_ics if t.get("status") == "pending"]
        else:
            underlying = price
            counts["settle_price"] = price

    for trade in open_ics:
        if trade.get("status") == "pending":
            # Placed, not yet confirmed filled: nothing is held, so nothing can be closed.
            # `_confirm_fills` resolves it; until then it only counts toward the concurrency cap.
            counts["held"] += 1
            continue
        decision = paper.evaluate_open_trade(
            trade,
            leg_quotes,
            params,
            force_close,
            underlying_price=underlying,
            is_cash_settled=is_cash,
            force_close_reason=force_close_reason,
            settle=settle,
        )
        action = decision["action"]
        if action == "hold":
            counts["held"] += 1
            paper._apply_exit_decision(trade, decision, symbol, db_path)
            continue
        if action == "expire":
            # Cash-settled left-to-expire: nothing to submit, settlement is automatic. A close
            # still working is cancelled first -- settlement supersedes it (an unfilled end-of-day
            # close on a cash-settled side falls through to expiry by design).
            for side in _closing_sides(trade):
                res = (
                    broker.cancel(trade[f"{side}_stop_order_id"])
                    if hasattr(broker, "cancel")
                    else {"ok": True}
                )
                if res.get("ok"):
                    paper._update_trade(
                        trade["ic_order_id"], {f"{side}_stop_fill_status": "cancelled"}, db_path
                    )
                    log(f"cancelled working {side} close for {trade['ic_order_id']} -- settling instead")
            counts["expired"] += 1
            paper._apply_exit_decision(trade, decision, symbol, db_path)
            continue

        # Every close action (stop_call / stop_put / stop_both / force_close) reduces to one or
        # two independent per-side 2-leg close orders -- force_close is simply "close whichever
        # side(s) are still open", not a distinct order shape. A side whose close is already
        # working is skipped: `_confirm_fills` owns it (confirm, or cancel-and-replace) until the
        # broker answers.
        closing = _closing_sides(trade)
        sides = [side for side in _sides_to_close(decision) if side not in closing]
        if not sides:
            counts["closing"] += 1
            continue
        try:
            specs = {
                side: live_orders.stop_close_spec(trade, side, leg_quotes, stop_limit_ratio) for side in sides
            }
        except ValueError as exc:
            log(f"CRITICAL: could not build close order for {trade['ic_order_id']} ({action}): {exc}")
            counts["order_failed"] += 1
            continue

        placed = {
            side: _submit_close(trade, side, spec, decision, broker, live=live, db_path=db_path, log=log)
            for side, spec in specs.items()
        }
        if not all(placed.values()):
            log(f"CRITICAL: close order failed for {trade['ic_order_id']} ({action}) -- position stays open")
            counts["order_failed"] += 1
            continue
        if not live:
            # A dry run placed nothing, so there is nothing to confirm; the ledger stays as it was.
            counts["force_closed" if action == "force_close" else "stopped"] += 1
            continue
        # Live: recorded on CONFIRMATION (see the module docstring), never here.
        counts["closing"] += 1

    return counts


def _manage_entry(
    symbol: str, snapshot: dict, params: dict, db_path: str, broker, *, live: bool, log
) -> dict:
    """Evaluate and, if admitted, submit one new IC entry for `symbol`. Reuses
    `paper.evaluate_entry` (the exact function paper_loop calls); on a real fill, builds the
    ic_trades row via `paper.synthetic_entry_fill` and overrides the synthetic order id / net
    credit with what was actually submitted before saving."""
    # No new live IC on the last trading day of a quarter, all day (2026-09-30). Here and not in
    # `paper.evaluate_entry`, which paper shares: its own quarterly rule is a partial-day one.
    if _cal.is_quarterly_expiry(_cal.date.fromisoformat(str(snapshot["date"]))):
        return {"entry": "skipped", "reason": _live.QUARTER_END_REASON}
    open_ics = paper._get_open_trades(symbol, EXECUTION_MODE, snapshot["date"], db_path)
    if len(open_ics) >= params["max_concurrent_ics"]:
        return {"entry": "skipped", "reason": "max_concurrent_ics_reached"}
    todays_entries, last_entry_min = paper._profile_day_stats(
        EXECUTION_MODE, snapshot["date"], db_path, symbol=symbol
    )
    entered, reason, chosen = paper.evaluate_entry(
        snapshot,
        params,
        open_ics,
        account_open_count=len(open_ics),
        todays_entry_count=todays_entries,
        last_entry_min=last_entry_min,
    )
    if not entered:
        return {"entry": "skipped", "reason": reason, "_chosen": chosen}

    quantity = int(params.get("quantity", 1))
    try:
        spec = live_orders.entry_spec(chosen, quantity)
    except ValueError as exc:
        log(f"CRITICAL: could not build entry order: {exc}")
        return {"entry": "skipped", "reason": f"order_build_failed: {exc}", "_chosen": chosen}

    # flies and bwb trade SPX in this account too (2026-10-15): a leg opposite one they hold or have
    # working would net at the broker into a position neither ledger describes. Refused, not repaired;
    # the next tick evaluates afresh.
    clash = _livepos.cross_module_conflict("meic", symbol, spec)
    if clash is not None:
        log(f"entry refused: {_livepos.describe(clash)} sits opposite another live loop's leg")
        return {
            "entry": "skipped",
            "reason": f"cross_module_sign_conflict: {_livepos.describe(clash)}",
            "_chosen": chosen,
        }

    result = broker.place(spec, live=live)
    log(f"entry order ({'LIVE' if live else 'dry-run'}): {json.dumps(result, default=str)[:300]}")
    if not result.get("ok"):
        return {"entry": "skipped", "reason": f"broker_rejected: {result.get('error')}", "_chosen": chosen}

    if not live:
        # A dry-run preflight opened nothing -- the live ledger must stay untouched.
        return {"entry": "dry_run", "net_credit": spec["price"]}

    order_id = _extract_order_id(result)
    # The row holds what the ORDER holds (2026-10-12): it was always written at quantity 1.
    row = paper.synthetic_entry_fill(
        snapshot, EXECUTION_MODE, chosen, params, EXECUTION_MODE, quantity=quantity
    )
    if order_id is None:
        log(f"WARNING: live placement had no extractable order id for {symbol}; keeping synthetic id")
    else:
        row["ic_order_id"] = f"LIVE-{symbol}-{order_id}"
    # The price asked for, until the broker says what filled (`_confirm_fills`). The row is
    # PENDING, not open: it holds a slot (a working order is a position at risk) and nothing else.
    row["net_credit"] = spec["price"]
    # The mid the limit was asked against: the modeled credit is mid minus the haircut, and the
    # row carries the haircut in dollars, so the mid is recoverable here and nowhere later.
    row["entry_mid_at_submit"] = round(
        float(chosen["net_credit"]) + float(row["slippage_dollars"]) / (100.0 * quantity), 4
    )
    row["status"] = "pending"
    row["fill_confirmed_at"] = None
    row["put_spread_entry_order_id"] = order_id
    row["call_spread_entry_order_id"] = order_id
    save_result = paper._save_trade(row, db_path)
    return {
        "entry": "placed",
        "ic_order_id": row["ic_order_id"],
        "net_credit": row["net_credit"],
        "order_id": order_id,
        "save_result": save_result,
        "_chosen": chosen,
    }


_EPS = 1e-9


def _leg_average(fills: list[dict], symbol: str | None, verb: str) -> float | None:
    """The quantity-weighted average price `symbol` filled at on `verb` ("buy"/"sell") actions, or
    None when no fill of it on that verb carries a parseable price. A broker symbol is OCC; the row
    keeps the DXLink streamer symbol, so both spellings are matched."""
    if not symbol:
        return None
    notional = qty_total = 0.0
    for f in fills:
        sym = str(f.get("symbol") or "")
        if symbol not in (sym, sym.strip(), _occ_to_streamer(sym)):
            continue
        if verb not in str(f.get("action") or "").lower():
            continue
        try:
            price, qty = float(f.get("fill_price")), float(f.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        notional += price * qty
        qty_total += qty
    return notional / qty_total if qty_total else None


def entry_fill_credits(trade: dict, status: dict | None, booked: float | None) -> dict:
    """The net and per-side credits a confirmed live IC entry books, and where the split came from.
    Pure (2026-10-12).

    Until this, a fill replaced `net_credit` and left `put_credit`/`call_credit` at the credits
    modelled at quote time (mid less the haircut). Stops and expiry charge each side against its own
    credit (`paper._apply_exit_decision`: side P&L = side credit - side exit), so a live row's P&L
    summed sides nobody had received and did not add up to its own net.

    **Broker legs first** (`side_credit_source='broker_legs'`). `status["fills"]` is the broker's
    per-leg executions (`core.broker`); each side's credit is its short leg's average sale price less
    its long leg's average purchase price, and the net is their sum -- what was actually received,
    including any improvement on the order's LIMIT, which is all `status["price"]` (and so `booked`)
    carries. Used only when all four legs have a parseable fill on the expected action and the sum
    is not below the limit: a credit limit never fills worse than its limit, so a sum that says it
    did is an incomplete read (a partial leg list, a parse miss), and the limit is kept instead --
    the rule flies' `fill_model.booked_price` applies to the same field.

    **Otherwise proportional** (`'proportional'`): `booked` split in the ratio of the modelled side
    credits on the row, the put share rounded and the call taking the remainder, so the sides add to
    the net exactly. A modelled split that is not two positive credits cannot apportion anything
    and halves the net instead. The model decides the split only, never the amount."""
    model_put = float(trade.get("put_credit") or 0.0)
    model_call = float(trade.get("call_credit") or 0.0)
    limit = trade.get("net_credit")
    fills = list((status or {}).get("fills") or [])
    if fills:
        sp = _leg_average(fills, trade.get("put_symbol"), "sell")
        lp = _leg_average(fills, trade.get("long_put_symbol"), "buy")
        sc = _leg_average(fills, trade.get("call_symbol"), "sell")
        lc = _leg_average(fills, trade.get("long_call_symbol"), "buy")
        if None not in (sp, lp, sc, lc):
            put, call = round(sp - lp, 4), round(sc - lc, 4)
            net = round(put + call, 4)
            if net > 0 and (limit is None or net >= float(limit) - _EPS):
                return {
                    "net_credit": net,
                    "put_credit": put,
                    "call_credit": call,
                    "side_credit_source": "broker_legs",
                }
    if booked is None:
        booked = limit
    if booked is None:
        return {"side_credit_source": None}
    net = round(float(booked), 4)
    if model_put > 0 and model_call > 0:
        put = round(net * model_put / (model_put + model_call), 4)
    else:
        put = round(net / 2.0, 4)
    return {
        "net_credit": net,
        "put_credit": put,
        "call_credit": round(net - put, 4),
        "side_credit_source": "proportional",
    }


def exit_fill_price(
    trade: dict, side: str, status: dict | None, booked: float | None, limit: float | None
) -> tuple[float | None, str]:
    """The per-share debit one side's confirmed close books, and where it came from. Pure
    (2026-10-12) -- `entry_fill_credits`' rule, applied to the close.

    Until this a close was booked at `status["price"]`, which is the order's LIMIT (the
    cushioned crossing price, `stop_limit_ratio` over it), so every live stop was recorded at
    least as bad as asked even when the broker filled it better.

    **Broker legs first** (`'broker_legs'`): the short leg's average purchase less the long leg's
    average sale, from `status["fills"]`. Used only when both legs have a parseable fill on the
    expected action (buy the short back, sell the long) and the debit is not above `limit`: a
    debit limit never fills worse than itself, so a sum that says it did is an incomplete read and
    the booked price is kept. **Otherwise `'booked'`**: `booked` as `fill_state` read it."""
    short_key, long_key = (
        ("put_symbol", "long_put_symbol") if side == "put" else ("call_symbol", "long_call_symbol")
    )
    fills = list((status or {}).get("fills") or [])
    if fills:
        bought = _leg_average(fills, trade.get(short_key), "buy")
        sold = _leg_average(fills, trade.get(long_key), "sell")
        if bought is not None and sold is not None:
            debit = round(bought - sold, 4)
            if debit >= 0 and (limit is None or debit <= float(limit) + _EPS):
                return debit, "broker_legs"
    return booked, "booked"


def _confirm_fills(
    symbol: str, snapshot: dict, db_path: str, broker, *, log, params: dict | None = None
) -> dict:
    """Ask the broker about every order this ledger is still waiting on, and record the answer.

    Entries: a `pending` row with an entry order id becomes `open` at the ACTUAL net credit once
    filled (`fill_confirmed_at` stamped), or `cancelled` with zero P&L if the order died -- the
    slot it held is freed either way.

    Closes (2026-09-17): a side whose close order FILLED has its stashed exit decision applied
    now, through `paper._apply_exit_decision`, with the actual fill price -- the first moment the
    ledger records the side closed. A close that DIED (cancelled / rejected / expired) just clears
    its marker: nothing was recorded, so the next `_manage_open_trades` re-evaluates the side
    under the same rule. A close still WORKING is cancelled and replaced at the stop rule off
    this tick's quotes -- live's minute-by-minute stand-in for paper's instant fill at the limit.
    """
    counts = {
        "entries_confirmed": 0,
        "entries_unrecorded": 0,
        "entries_cancelled": 0,
        "closes_filled": 0,
        "closes_dead": 0,
        "closes_repriced": 0,
    }
    now = str(paper._now_et())
    leg_quotes = snapshot.get("leg_quotes", {})
    ratio = (params or {}).get("stop_limit_ratio", 1.02)
    for trade in _trades_with_working_orders(symbol, snapshot["date"], db_path):
        ic_order_id = trade["ic_order_id"]
        entry_oid = trade.get("put_spread_entry_order_id")
        if trade.get("status") == "pending" and entry_oid:
            status = broker.status(entry_oid)
            state, price = _execution.fill_state(status, fallback_price=trade.get("net_credit"))
            if state == "filled":
                # The sides follow the fill (2026-10-12): broker leg prices where they are complete,
                # else the fill split in the modelled ratio. `price` becomes the net they sum to.
                credits = entry_fill_credits(trade, status, price)
                if credits.get("net_credit") is not None:
                    price = credits["net_credit"]
                fields = {"status": "open", "net_credit": price, "fill_confirmed_at": now}
                if credits.get("side_credit_source") is not None:
                    fields.update(
                        put_credit=credits["put_credit"],
                        call_credit=credits["call_credit"],
                        side_credit_source=credits["side_credit_source"],
                        put_credit_fraction=_regime.put_credit_fraction(credits["put_credit"], price),
                    )
                mid = trade.get("entry_mid_at_submit")
                if mid is not None and price is not None:
                    # Measured, not modeled: what the fill conceded to the spread against the mid
                    # the limit was asked from. Negative is price improvement. Exit sides stay
                    # modeled (their asked price is a crossing limit, not a mid) and say so.
                    qty = int(trade.get("quantity") or 1)
                    fields["slippage_dollars"] = round((float(mid) - float(price)) * 100.0 * qty, 4)
                saved = paper._update_trade(ic_order_id, fields, db_path)
                if saved.get("ok") is False:
                    # 2026-10-15: a live ledger missing `side_credit_source` refused every confirm,
                    # the tick still counted it, and two filled XSP condors sat `pending` -- out of
                    # expected_legs (a CRITICAL positions mismatch) and out of settlement. The row
                    # stays pending, so the next tick retries; say so loudly instead of counting it.
                    log(
                        f"CRITICAL: entry FILLED {ic_order_id} at {price} but the ledger refused the "
                        f"write ({saved.get('error')}) -- still pending, retried next tick"
                    )
                    counts["entries_unrecorded"] += 1
                    continue
                log(
                    f"entry FILLED {ic_order_id}: asked {trade.get('net_credit')}, filled {price} "
                    f"(put {credits.get('put_credit')}, call {credits.get('call_credit')}, "
                    f"{credits.get('side_credit_source')})"
                )
                counts["entries_confirmed"] += 1
            elif state != "working":
                paper._update_trade(
                    ic_order_id,
                    {
                        "status": "cancelled",
                        "exit_time": now,
                        "exit_reason": f"entry_{state}",
                        "pnl": 0,
                        "fees": 0,
                    },
                    db_path,
                )
                log(f"entry {state.upper()} {ic_order_id} -- never established, slot freed")
                counts["entries_cancelled"] += 1
            continue
        for side in _closing_sides(trade):
            oid = trade[f"{side}_stop_order_id"]
            stashed = {}
            try:
                stashed = json.loads(trade.get("pending_exit_json") or "{}")
            except ValueError:
                stashed = {}
            modeled = (stashed.get("decision") or {}).get(f"{side}_exit_price")
            status = broker.status(oid)
            state, price = _execution.fill_state(status, fallback_price=modeled)
            if state == "filled":
                # The broker's own leg fills where they are complete, else the booked price
                # (2026-10-12). `modeled` is the limit asked for this side: the stash carries it.
                price, source = exit_fill_price(trade, side, status, price, modeled)
                fresh = _trade_row(ic_order_id, db_path) or trade
                paper._apply_exit_decision(fresh, _exit_for_side(stashed, side, price), symbol, db_path)
                paper._update_trade(
                    ic_order_id,
                    {f"{side}_stop_fill_status": "filled", f"{side}_exit_fill_source": source},
                    db_path,
                )
                log(f"{side} close FILLED {ic_order_id} at {price} ({source}; limit {modeled})")
                counts["closes_filled"] += 1
            elif state != "working":
                # Never recorded closed, so nothing to reopen: clear the marker and let the next
                # evaluation decide the side again under the same rule.
                paper._update_trade(ic_order_id, {f"{side}_stop_fill_status": state}, db_path)
                log(
                    f"{side} close {state.upper()} {ic_order_id} -- side is open again, re-evaluated next tick"
                )
                counts["closes_dead"] += 1
            else:
                # Still working: cancel and replace at the stop rule off THIS tick's quotes.
                try:
                    spec = live_orders.stop_close_spec(trade, side, leg_quotes, ratio)
                except ValueError as exc:
                    log(
                        f"{side} close for {ic_order_id} still working; cannot re-price ({exc}) -- left resting"
                    )
                    continue
                cancelled = broker.cancel(oid) if hasattr(broker, "cancel") else {"ok": False}
                if not cancelled.get("ok"):
                    # A cancel that fails is usually the fill racing it: the next tick's status
                    # poll resolves it. Never place a second order on top of one we could not cancel.
                    log(
                        f"{side} close for {ic_order_id}: cancel refused ({cancelled.get('error')}) -- re-polling next tick"
                    )
                    continue
                decision = stashed.get("decision") or {"action": f"stop_{side}"}
                decision = {**decision, f"{side}_exit_price": spec["price"]}
                if _submit_close(trade, side, spec, decision, broker, live=True, db_path=db_path, log=log):
                    counts["closes_repriced"] += 1
                else:
                    paper._update_trade(ic_order_id, {f"{side}_stop_fill_status": "cancelled"}, db_path)
                    log(
                        f"CRITICAL: {side} close for {ic_order_id} cancelled but the replacement failed -- side open, re-evaluated next tick"
                    )
    return counts


def run_once(
    config: dict,
    snapshot: dict,
    db_path: str,
    broker,
    *,
    live: bool,
    log=print,
    entry_block: list[str] | None = None,
    settle_price=None,
) -> dict:
    """One live iteration for the pinned `live.symbol`. `broker` is the injected submission
    seam -- an object with `place(spec, live) -> {ok, response?, error?}`. It trades
    `paper.live_params`: `live.arm` (the top level when unset) with the live block's overrides."""
    symbol = (config.get("live") or {}).get("symbol")
    params = paper.live_params(config)
    # Broker truth against ledger belief, before anything else (2026-10-14, the flies/bwb sweep):
    # a working order this ledger never recorded is the one crash window nothing else covers.
    # Unknown (None) blocks entries exactly as a found orphan does; neither touches a close.
    orphans = _sweep_orphans(broker, db_path, symbol, log)
    # What this loop holds or has working, for flies' and bwb's entries to check against (2026-10-15).
    _livepos.publish_open_legs(
        "meic",
        expected_legs(config, db_path, include_pending=True)["legs"],
        now_iso=_pl._now_et().isoformat(timespec="seconds"),
    )
    # Broker truth first: what filled, what died. Only the live ledger has orders to confirm.
    fills = _confirm_fills(symbol, snapshot, db_path, broker, log=log, params=params) if live else {}
    manage = _manage_open_trades(
        symbol, snapshot, params, db_path, broker, live=live, log=log, settle_price=settle_price
    )
    block = list(entry_block or [])
    if orphans is None:
        block.append("orphan_sweep_failed")
    elif orphans:
        block.append(f"orphaned_orders ({orphans})")
    # The arm's written kill criteria (B1): reduce, then block and latch. Entries only.
    kill = kill_verdict(config, db_path)
    if kill["state"] == _kill.BLOCKED:
        block.append("kill_criteria: " + "; ".join(kill["reasons"]))
    else:
        params = {
            **params,
            "quantity": _kill.sized(int(params.get("quantity", 1)), kill, _live_cfg(config).get("kill")),
        }
        if kill["state"] == _kill.REDUCED:
            log(f"kill criteria reduce entries to {params['quantity']}: {'; '.join(kill['reasons'])}")
    if block:
        # The halt flag, the daily-loss breaker or the sweep: no new IC. Everything above has run.
        log(f"new entries blocked ({', '.join(block)}) -- open ICs still managed")
        entry = {"blocked": block}
    elif (cap := max_structures_per_day(config)) is not None and entries_today(
        db_path, symbol, snapshot["date"]
    ) >= cap:
        entry = {"entry": "skipped", "reason": "max_structures_per_day_reached"}
    else:
        entry = _manage_entry(symbol, snapshot, params, db_path, broker, live=live, log=log)
        if entry.get("entry") == "placed":
            # The working order, now, not at the next tick: the other loops check against it from here.
            _livepos.publish_open_legs(
                "meic",
                expected_legs(config, db_path, include_pending=True)["legs"],
                now_iso=_pl._now_et().isoformat(timespec="seconds"),
            )
    chosen = entry.pop("_chosen", None)
    if live:
        _record_attempt(config, snapshot, db_path, symbol, entry, chosen)
    return {"symbol": symbol, "live": live, "orphans": orphans, **fills, **manage, "entry": entry}


def _record_attempt(config: dict, snapshot: dict, db_path: str, symbol: str, entry: dict, chosen) -> None:
    """One `entry_attempts` row per live tick, so the live ledger says what the loop decided today.

    Before this (2026-10-23) the live tick journalled nothing until an IC was placed: an armed day
    with no trade yet left the ledger's newest date at the last trade, and every console page that
    resolves "latest session" from the journals showed that old session under today. Paper's own
    writer, so the schema and outcome taxonomy are one; best effort and never fatal, as there.
    Live ticks only -- a dry run must leave the live ledger untouched. `filled` here means admitted
    and submitted (paper's meaning); the broker's word on the fill is the ic_trades row's.
    """
    if "blocked" in entry:
        reason = "entry_blocked: " + ", ".join(entry["blocked"])
    else:
        reason = str(entry.get("reason") or entry.get("entry") or "")
    paper._record_entry_attempt(
        db_path,
        snapshot=snapshot,
        profile=(config.get("live") or {}).get("arm") or EXECUTION_MODE,
        symbol=symbol,
        reason=reason,
        entered=entry.get("entry") == "placed",
        chosen=chosen,
        ic_order_id=entry.get("ic_order_id"),
    )


def make_broker(config: dict, designated: str | None) -> _execution.Broker:
    """The live submission seam: `cherrypick.core.execution.Broker` with this module's session,
    designated account, readiness gates (re-checked on every live submit), serializer and deploy
    cap injected. Replaced (2026-09-17) an adapter that shelled out to `tt.py execute_trade` and
    scraped the last JSON line of its stdout."""
    from cherrypick.meic import tt as _tt
    from cherrypick.meic.session import get_session

    return _execution.Broker(
        get_session=get_session,
        designated_account=lambda: designated,
        # Account gates only: a close must always get through (tt.py's own rule), so the halt
        # flag is enforced on ENTRIES, in run_once, never here.
        live_gates=lambda: readiness(config, designated=designated),
        serialize=_tt._serialize,
        deploy_limit_pct=config.get("account_deploy_limit_pct") or None,
        # An uncertain submit's hold outlives this tick's process (2026-10-08): each tick is its own
        # process, and an in-memory hold let the next tick submit the duplicate it exists to prevent.
        hold_path=_hold_path(),
    )


def _build_snapshot(cfg: dict, symbol: str):
    """Reuses `paper_loop.py`'s own market-data fetch helpers for one pinned symbol, rather
    than duplicating ~100 lines of fetch logic. Returns (snapshot, error_or_None)."""
    now = _pl._now_et()
    today = now.strftime("%Y-%m-%d")
    now_et = now.strftime("%H:%M")
    vix = _pl._fetch_vix()
    vix1d = _pl._run_json(_pl._TT + ["get_vix1d"]).get("last")
    vix1d_ratio = round(vix1d / vix, 3) if (vix1d and vix) else None
    # The candidate menu is built from what live trades, not the top level: a live block that
    # narrows the widths must narrow what is priced, or entry would see widths it may not take.
    params = paper.live_params(cfg)
    delta_target = _pl._delta_target(params, vix)
    session = _pl._session_quality(now)

    price, ivr, ivp = _pl._fetch_overview(symbol)
    if price is None:
        return None, "no price"
    widths = paper._profile_widths_for_symbol(params, symbol) or []
    # The arm's own short-delta band too (2026-10-23): `_select_candidates` hands an arm with a
    # `short_delta_target` only candidates built at it, so a menu priced at the VIX-banded default
    # alone was empty whenever the two differed (pm-200's 0.16 against 0.14 at VIX 20).
    deltas = [delta_target]
    arm_delta = params.get("short_delta_target")
    if arm_delta is not None and abs(arm_delta - delta_target) >= 1e-6:
        deltas.append(arm_delta)
    candidates, leg_quotes, cand_err = _pl._build_candidates(
        symbol, price, widths, deltas, delta_target, today, delta_source=params.get("delta_source", "stream")
    )
    if cand_err and not candidates:
        return None, cand_err
    gex = _pl._run_json(_pl._TT + ["get_gex", "--symbol", symbol])
    atr_lookback = int(params.get("regime_atr_lookback_days", 5))
    snapshot = {
        "symbol": symbol,
        "date": today,
        "now_et": now_et,
        "expiration": today,
        "dte": 0,
        "underlying_price": price,
        "iv_rank": ivr,
        "iv_pct": ivp,
        "iv_rank_source": "native",
        "vix": vix,
        "vix1d_ratio": vix1d_ratio,
        "atr_5day": _pl._fetch_atr(symbol, atr_lookback),
        "intraday_range_pct": _pl._fetch_intraday_range_pct(symbol),
        "session_quality": session,
        "gex": gex if gex.get("ok") else {"ok": False},
        "candidates": candidates,
        "leg_quotes": leg_quotes,
    }
    return snapshot, None


def expected_legs(cfg: dict, db_path: str, *, include_pending: bool = False) -> dict:
    """The contracts this LIVE ledger says the broker holds right now, for the orchestrator's
    positions-vs-ledger check (`cherrypick.core.livepositions`, 2026-10-08). Read-only.

    Counted: each side still held of an `open`/`partial` IC -- held means no recorded stop cost,
    the same rule the exit path uses -- as its short and its long (`paper.ic_legs`, wings derived).
    A `pending` entry has filled nothing yet and is not counted; a working stop's close is counted as
    still held until its fill is confirmed. `pending` counts both.

    `include_pending` (2026-10-15) counts a pending entry's legs too: what the other live loops must
    not open against is what this one holds OR could hold the moment a working order fills."""
    import sqlite3

    legs, pending, symbols = [], 0, set()
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = [
                dict(r)
                for r in con.execute(
                    "SELECT * FROM ic_trades WHERE status IN ('pending', 'open', 'partial', 'partial_entry')"
                )
            ]
        finally:
            con.close()
    except sqlite3.Error:
        rows = []  # no live ledger yet: nothing held
    for t in rows:
        symbols.add(t["symbol"])
        if t["status"] == "pending":
            pending += 1
            if not include_pending:
                continue
        if t.get("put_stop_fill_status") == "pending" or t.get("call_stop_fill_status") == "pending":
            pending += 1
        held = {"P": t.get("put_stop_cost") is None, "C": t.get("call_stop_cost") is None}
        qty = int(t.get("quantity") or 1)
        for _, right, strike, sign in paper.ic_legs(t):
            if held[right]:
                legs.append(
                    {
                        "underlying": t["symbol"],
                        "expiry": str(t.get("expiration")),
                        "right": right,
                        "strike": strike,
                        "qty": sign * qty,
                    }
                )
    live_symbol = (cfg.get("live") or {}).get("symbol")
    return {
        "ok": True,
        "module": "meic",
        "underlyings": sorted(symbols | ({live_symbol} if live_symbol else set())),
        # Armed means today's arm record (2026-10-14), not the master switch, which stays on.
        "armed_today": arm_stamp_date() == _pl._now_et().strftime("%Y-%m-%d"),
        "legs": legs,
        "pending": pending,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="Single iteration (the trading mode)")
    ap.add_argument(
        "--expected-legs",
        action="store_true",
        help="the filled open legs the live ledger holds (JSON; read-only)",
    )
    ap.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Preflight orders, place nothing (DEFAULT -- this is the rung-1 dry-run smoke)",
    )
    ap.add_argument(
        "--live",
        dest="dry_run",
        action="store_false",
        help="Place real orders. Requires today's arm record, every readiness gate and the breaker clear.",
    )
    ap.add_argument(
        "--status", action="store_true", help="armed state, gates, caps, orphans, holds (JSON; no broker)"
    )
    ap.add_argument("--install-task", action="store_true", help="arm for TODAY (the /live-meic-start path)")
    ap.add_argument("--uninstall-task", action="store_true", help="disarm now")
    ap.add_argument(
        "--settle-overdue",
        action="store_true",
        help="settle past sessions still open, each at its own official close (the hourly catch-up job)",
    )
    ap.add_argument("--settle", action="store_true", help="settle one session by hand: --date and --price")
    ap.add_argument("--date", help="YYYY-MM-DD, with --settle")
    ap.add_argument("--price", type=float, help="the official settlement print, with --settle")
    ap.add_argument("--symbol", help="with --settle: only this symbol (default: every symbol held that day)")
    args = ap.parse_args(argv)

    cfg = paper.load_base_config()

    def out(payload: dict) -> int:
        print(json.dumps(payload, indent=2, default=str))
        return 0 if payload.get("ok", True) else 1

    if args.install_task:
        return out(install_task(cfg))
    if args.uninstall_task:
        return out(uninstall_task())
    if args.settle_overdue or args.settle:
        # Under the tick's own lock: a tick and a settlement writing the same rows is P&L corruption.
        if not _looplock.acquire(_once_lock_path(), 180, alive=_looplock.pid_alive):
            return out({"ok": False, "error": "a live tick is running; try again in a minute"})
        try:
            db_path = str(_paths.live_db_path())
            if args.settle_overdue:
                return out(settle_overdue(cfg, db_path, _pl._now_et().strftime("%Y-%m-%d"), log=_quiet))
            if not args.date or args.price is None:
                return out({"ok": False, "error": "--settle needs --date and --price (the official print)"})
            try:
                held = _ledger_rows(
                    db_path, "trade_date = ? AND status IN ('open', 'partial', 'partial_entry')", (args.date,)
                )
            except LedgerUnreadable as exc:
                return out({"ok": False, "error": str(exc)})
            symbols = [args.symbol.upper()] if args.symbol else sorted({t["symbol"] for t in held})
            settled = [
                {
                    "symbol": s,
                    **settle_session(cfg, db_path, args.date, s, args.price, "official", log=_quiet),
                }
                for s in symbols
            ]
            return out({"ok": True, "session": args.date, "price": args.price, "settled": settled})
        finally:
            _looplock.release(_once_lock_path())
    if args.status:
        return out(run_status(cfg, designated=_designated_account()))
    if args.expected_legs:
        print(json.dumps(expected_legs(cfg, str(_paths.live_db_path())), default=str))
        return 0
    if not args.once:
        ap.error("--once is required (or --status, --expected-legs, --install-task, --uninstall-task)")
    live = not args.dry_run
    if live:
        # The dead-man's switch, before any broker read: a record that is not today's, or a clock
        # past disarm_time, deletes the record and ends the tick. Manual live ticks need an arm too.
        now = _pl._now_et()
        reason = should_disarm(cfg, _now_min(now), now.strftime("%Y-%m-%d"))
        if reason:
            uninstall_task()
            return out({"ok": True, "disarmed": reason})
    designated = _designated_account()
    unmet = readiness(cfg, designated=designated)
    if live and unmet:
        print(json.dumps({"ok": False, "error": "live gates unmet", "unmet": unmet}))
        return 1
    # One tick at a time: a slow broker call must not let the next minute's tick submit beside it.
    if not _looplock.acquire(_once_lock_path(), 180, alive=_looplock.pid_alive):
        return out({"ok": True, "skipped": "another live tick is running"})
    try:
        return _tick(cfg, designated, unmet, live)
    finally:
        _looplock.release(_once_lock_path())


def _tick(cfg: dict, designated: str | None, unmet: list[str], live: bool) -> int:
    if unmet:
        print(f"note: dry-run with unmet live gates: {unmet}", file=sys.stderr)
    for warning in _pl._live_config_warnings(cfg, paper.load_profiles()):
        print(f"warning: live config: {warning}", file=sys.stderr)

    symbol = (cfg.get("live") or {}).get("symbol")
    if not symbol:
        print(json.dumps({"ok": False, "error": "live.symbol is unset"}))
        return 1

    # Declare the pinned symbol and the LIVE ledger's open legs to the streamer before anything
    # is priced -- best-effort, never fatal (see stream_request.register_live). Dry-run included:
    # the request file is what keeps an already-open live IC's legs quoted between ticks, and a
    # dry-run tick that manages real positions needs them fresh exactly as much as a live one.
    _stream_request.register_live(cfg)

    snapshot, err = _build_snapshot(cfg, symbol)
    if snapshot is None:
        print(json.dumps({"ok": False, "error": f"no snapshot: {err}"}))
        return 1

    db_path = str(_paths.live_db_path())
    _ensure_live_schema(db_path)
    # The halt flag and the daily-loss breaker stop NEW ICs only; open ones are still managed.
    blockers = entry_blockers(cfg, db_path, snapshot["date"], halt_present=os.path.exists(halt_flag_path()))
    summary = run_once(cfg, snapshot, db_path, make_broker(cfg, designated), live=live, entry_block=blockers)
    line = json.dumps(
        {"ok": True, "at": datetime.now().isoformat(timespec="seconds"), **summary}, default=str
    )
    print(line)
    _append_log(line)
    return 0


def _ensure_live_schema(db_path: str) -> None:
    """Bring the live ledger's schema up to date before the tick writes to it -- the paper loop's
    `_ensure_paper_schema`, which the live loop never had. db.py's additive migrations run only from
    `init_db`, so a column added after the live ledger was created (`side_credit_source`, the exit
    fill sources, 2026-10-12) was missing from it, and on 2026-10-15 every fill confirm failed.
    Idempotent and in-process (`db.call` captures init_db's JSON, so stdout keeps its one line).
    Non-fatal: the confirm's own write check is the backstop that makes a failure loud."""
    try:
        res = paper._db(["init_db"], db_path)
        if res.get("ok") is False:
            _quiet(f"CRITICAL: live schema ensure failed: {res.get('error')}")
    except Exception as exc:  # noqa: BLE001 -- schema maintenance never costs the tick
        _quiet(f"CRITICAL: live schema ensure errored: {type(exc).__name__}: {exc}")


def _quiet(message: str) -> None:
    """A settlement's log lines go to the live log, not stdout -- stdout is the one JSON result."""
    _append_log(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "note": message}))


def log_file():
    """One JSON line per tick (2026-10-14): what the watchdog's freshness fallback and a person
    tailing the loop read. Account numbers are masked at the sink, the flies/bwb rule."""
    return _paths.log_path("meic_live.log")


def _append_log(line: str) -> None:
    from cherrypick.core import redact as _redact

    try:
        path = log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(_redact.redact_accounts(line) + "\n")
    except OSError:
        pass  # a log that cannot be written must never cost the tick


if __name__ == "__main__":
    sys.exit(main())
