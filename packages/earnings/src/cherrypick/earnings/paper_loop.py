"""The managed paper loop: one short-lived tick, fired every 60 seconds by the supervisor.

Replaces the two daily one-shots (`run_entries` at 15:45, `run_closes` at 09:45) with a loop that
watches positions through their whole life. Those verbs remain, as manual and backfill commands.

Spawn-per-tick rather than a resident process, the same shape MEIC settled on: each run is a process
that starts, does one bounded piece of work, and exits, so a hung tick cannot take the loop with it.
The phase is derived from the clock rather than held in memory, which is what makes that safe — a
tick knows what to do from the time and the database alone.

    off_hours     nothing, and no row: an out-of-session tick is not a measurement
    forward_scan  ~06:30, once daily: the slow, stable half of screening, pre-market
    pre_open      09:00-09:30, refresh the producer's subscription request
    open_window   09:30 to the execution window, MARK but never act
    management    the execution window to 15:40, mark, decide, and act
    entry         15:45, the forced-sampling entry scan, once per day
    eod           16:00-16:30, write the session's reports

The open window is a phase of its own because the first ten minutes of an earnings name's options
are not priceable: spreads can exceed the edge being managed. Marks are still recorded through it, so
the morning's path survives; decisions reached there are recorded with the gate that held them, and
taken on the first tick that clears.

**The entry scan holds the lock for up to twenty-five minutes**, so positions go unmarked roughly
15:45-16:10. That is accepted: the morning is where management matters, the EOD write still lands
before the digest deadline, and the alternative -- a second writer against the same SQLite book --
trades a documented gap for a class of bug that is much harder to see.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime, timezone
from pathlib import Path

from cherrypick.core import calendar as _calendar
from cherrypick.core import home as _home

from cherrypick.earnings import (
    costs,
    db_paper,
    management,
    provider,
    scanner,
    stream_request,
    symbol_watch,
)
from cherrypick.earnings import strat_test_harness as harness

ET = management.ET

# A tick that cannot get the lock exits OK with a "busy" status. It must never look like a failure:
# the entry scan legitimately holds it for many ticks, and a supervisor logging twenty-five minutes
# of errors for expected behaviour teaches everyone to ignore the log.
LOCK_STALE_SECONDS = 1800

PHASE_OFF_HOURS = "off_hours"
PHASE_FORWARD_SCAN = "forward_scan"
PHASE_PRE_OPEN = "pre_open"
PHASE_OPEN_WINDOW = "open_window"
PHASE_MANAGEMENT = "management"
PHASE_ENTRY = "entry"
PHASE_EOD = "eod"

_MARKET_OPEN = 9 * 60 + 30
_PRE_OPEN = 9 * 60
_MANAGEMENT_END = 15 * 60 + 40
_EOD_START = 16 * 60
_EOD_END = 16 * 60 + 30


def state_file(name: str) -> Path:
    return _home.ensure(_home.state_dir()) / name


def lock_path() -> Path:
    return state_file("earnings_paper_loop.lock")


def _minutes(now: datetime) -> int:
    return now.hour * 60 + now.minute


def _clock_minutes(value, default: int) -> int:
    try:
        hour, minute = (int(x) for x in str(value).split(":"))
    except (TypeError, ValueError):
        return default
    return hour * 60 + minute


def _entry_minutes(config: dict) -> int:
    """When the automated entry scan STARTS.

    Its own key rather than `entry_window_start`, which means something different: that bounds when
    entries may be placed at all and is read by the agent-driven loop too. The scan is one bounded
    job inside that window and needs to begin early enough to finish inside it.
    """
    return _clock_minutes(
        config.get("entry_scan_at") or config.get("entry_window_start", "15:35"), 15 * 60 + 35
    )


def _forward_scan_settings(config: dict) -> tuple[bool, int, int]:
    """`(enabled, minute-of-day, trading days)` for the pre-market forward scan."""
    sw = config.get("symbol_watch") or {}
    return (
        sw.get("enabled", True) is not False,
        _clock_minutes(sw.get("at", "06:30"), 6 * 60 + 30),
        int(sw.get("days", 10) or 10),
    )


def _entry_deadline_minutes(config: dict) -> int:
    """The last minute an entry scan may START.

    Bounded by the configured entry window, not left open: without this a tick at any later hour
    that found no entry heartbeat would run the scan -- opening positions well after the close, on
    a chain nobody can trade, and reporting them as that day's entries. The scan may RUN past this
    (it takes up to twenty-five minutes); it may not begin past it. A day whose scan never started
    is a missed entry, which the watchdog's SLA check already reports as one.
    """
    return _clock_minutes(config.get("entry_window_end", "15:55"), 15 * 60 + 55)


def phase_for(now: datetime, config: dict, *, entry_done: bool, forward_scan_done: bool = True) -> str:
    """What this tick is for. Derived from the clock and two facts from the database, so a tick can
    be reasoned about without knowing anything about the tick before it."""
    if not _calendar.is_trading_day(now.date()):
        return PHASE_OFF_HOURS
    minute = _minutes(now)
    entry_at = _entry_minutes(config)
    scan_enabled, scan_at, _ = _forward_scan_settings(config)

    # The forward scan runs pre-market on purpose. It is the slow, stable half of screening --
    # calendar, winrate, IV/RV, market cap, historical moves -- none of which needs a live session,
    # and all of which the entry scan would otherwise pay for at 15:35 while the clock runs.
    if scan_enabled and scan_at <= minute < _PRE_OPEN and not forward_scan_done:
        return PHASE_FORWARD_SCAN
    if minute < _PRE_OPEN:
        return PHASE_OFF_HOURS
    if minute < _MARKET_OPEN:
        return PHASE_PRE_OPEN
    if entry_at <= minute <= _entry_deadline_minutes(config) and not entry_done:
        return PHASE_ENTRY
    if minute < _exec_window_minutes(config):
        return PHASE_OPEN_WINDOW
    if minute < _MANAGEMENT_END:
        return PHASE_MANAGEMENT
    if _EOD_START <= minute < _EOD_END:
        return PHASE_EOD
    return PHASE_OFF_HOURS


def _exec_window_minutes(config: dict) -> int:
    policy = management.policy_for("", config)
    try:
        hour, minute = (int(x) for x in str(policy["exec_window_start"]).split(":"))
    except (TypeError, ValueError):
        return 9 * 60 + 40
    return hour * 60 + minute


# --------------------------------------------------------------------------- the single-writer lock
@dataclass
class Lock:
    path: Path
    acquired: bool


def acquire_lock() -> Lock:
    """One writer at a time. A stale lock (a tick killed mid-run) is broken after LOCK_STALE_SECONDS
    so a crash cannot wedge the loop until someone notices."""
    path = lock_path()
    try:
        if path.exists():
            age = time.time() - path.stat().st_mtime
            if age < LOCK_STALE_SECONDS:
                return Lock(path, False)
        path.write_text(json.dumps({"pid": os.getpid(), "at": time.time()}), encoding="utf-8")
        return Lock(path, True)
    except OSError:
        return Lock(path, False)


def release_lock(lock: Lock) -> None:
    if lock.acquired:
        try:
            lock.path.unlink(missing_ok=True)
        except OSError:
            pass


# --------------------------------------------------------------------------- heartbeats
def write_heartbeat(phase: str, record: dict) -> None:
    """The SLA heartbeats the watchdog and the dashboard already read.

    Written by the loop now rather than by the orchestrator's wrapper verb, so the entry SLA check
    and the dashboard cards survive the cutover untouched -- the file and its shape are the contract,
    not who writes it.
    """
    name = "earnings_entry.last.json" if phase == "entry" else "earnings_exit.last.json"
    try:
        path = state_file(name)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def append_run_log(record: dict) -> None:
    """Append one JSONL line per completed entry/exit phase to `logs/earnings_paper.log`.

    The heartbeats are a LATEST, deliberately: each is overwritten every run, so they answer "is it
    alive" and nothing else. The run trail was the scheduled verbs' job, and when entry and exit moved
    into this loop at the 2026-08-12 cutover nothing took it over -- the log simply stopped on
    2026-08-11, and "did earnings run today, and what did it decide" became unanswerable from the logs
    even though the loop was running fine. Same file and same one-object-per-line shape the verbs
    wrote, so anything already reading it keeps working.

    Best-effort by construction: a session that cannot write its log still trades.
    """
    try:
        path = _home.logs_dir() / "earnings_paper.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            print(json.dumps(record, default=str), file=fh)
    except OSError:
        pass


# --------------------------------------------------------------------------- marking and managing
def _ns(**kwargs):
    return argparse.Namespace(**kwargs)


def open_positions() -> list[dict]:
    rows = db_paper.cmd_get_open_positions(_ns())["positions"]
    return [r for r in rows if harness._is_strat_test_book(r.get("profile"))]


def refresh_stream_request(positions: list[dict]) -> None:
    """Declare the underlyings of everything currently open.

    **Only safe to GROW this set outside the session**, which is why the entry phase does not call
    it and `pre_open` does. A producer binds its underlyings once, at startup, so the watchdog
    recycles it when the union grows — and a recycle costs a settling window during which NOTHING is
    streaming. Growing the set at 15:45 would therefore blind the 0DTE modules trading into their
    own close, to make symbols available fourteen hours before this module needs them. Shrinking is
    always safe: an over-subscribed producer serves everyone correctly and never triggers a recycle.
    """
    stream_request.register(sorted({p["symbol"] for p in positions if p.get("symbol")}))


def _first_check_of_day(order_id: str, session: str) -> bool:
    marks = db_paper.cmd_get_marks(_ns(order_id=order_id, session_date=session, limit=1))["marks"]
    return not marks


def _rest_snapshot(trade: dict) -> dict:
    """The broker path, used to confirm a close and as the fallback when the cache cannot serve one.

    Deliberately not the per-tick path: it costs a subprocess and a fresh DXLink session per call.
    """
    legs = provider.legs_from_trade(trade)
    if not legs:
        return {"ok": False, "reason": "no_legs_recorded"}
    quote = scanner.fetch_quote_and_expirations(trade["symbol"])
    price = quote.get("price", 0.0) if quote.get("ok") else 0.0
    symbols = [leg["symbol"] for leg in legs]
    entries = scanner.fetch_quotes_by_symbol(trade["symbol"], trade["expiration"], symbols, price)
    if any(s not in entries for s in symbols):
        return {"ok": False, "reason": "missing_leg_quotes", "source": "rest"}
    quotes = {
        s: {
            "bid": entries[s].get("bid"),
            "ask": entries[s].get("ask"),
            "mid": entries[s].get("mid"),
            "iv": entries[s].get("iv"),
            "delta": entries[s].get("delta"),
        }
        for s in symbols
    }
    if any(q["bid"] is None or q["ask"] is None for q in quotes.values()):
        return {"ok": False, "reason": "missing_leg_quotes", "source": "rest"}
    widest = max((provider.spread_pct(q) or 0.0) for q in quotes.values())
    return {
        "ok": True,
        "source": "rest",
        "quotes": quotes,
        "spot": price or None,
        "fresh": len(quotes),
        "stale": 0,
        "max_spread_pct": widest,
    }


def mark_position(trade: dict, config: dict, now: datetime) -> tuple[dict, int | None]:
    """Price one position and record the mark, usable or not. Returns `(snapshot, mark_id)`."""
    policy = management.policy_for(trade.get("strategy") or "", config)
    snap = provider.snapshot(
        trade,
        max_quote_age_seconds=policy.get("quote_max_age_seconds"),
        max_spot_age_seconds=policy.get("spot_max_age_seconds"),
    )
    if not snap.get("ok"):
        # One REST attempt before giving up on the tick: the cache not having a leg is common early
        # in a name's life, and the broker can always price it.
        snap = _rest_snapshot(trade)

    session = now.date().isoformat()
    spec = {
        "order_id": trade["order_id"],
        "session_date": session,
        "marked_at": now.timestamp(),
        "source": snap.get("source"),
        "usable": bool(snap.get("ok")),
        "refusal": None if snap.get("ok") else snap.get("reason"),
        "quotes_fresh": snap.get("fresh"),
        "quotes_stale": snap.get("stale"),
        "max_leg_spread_pct": snap.get("max_spread_pct"),
        "spot": snap.get("spot"),
    }
    if snap.get("ok"):
        legs = provider.legs_from_trade(trade)
        exit_debit = scanner.compute_generic_exit_debit(legs, snap["quotes"])
        if exit_debit is not None:
            spec["exit_debit"] = exit_debit
            spec["unrealized_pnl"] = management.unrealized_pnl(trade, exit_debit)
    result = db_paper.cmd_record_mark(_ns(data=json.dumps(spec)))
    return snap, result.get("mark_id")


def close_position(trade: dict, snap: dict, reason: str, config: dict, now: datetime) -> dict:
    """Record a close at the confirmed price, and stop the producer holding its legs."""
    legs = provider.legs_from_trade(trade)
    exit_debit = scanner.compute_generic_exit_debit(legs, snap["quotes"])
    if exit_debit is None:
        return {"ok": False, "error": "exit_debit_unavailable"}

    quantity = trade.get("quantity") or 1
    leg_quotes = [snap["quotes"][leg["symbol"]] for leg in legs]
    exit_costs = costs.apply_exit_costs({"order": {"legs": legs}}, leg_quotes, quantity, config)
    result = db_paper.cmd_save_close(
        _ns(
            data=json.dumps(
                {
                    "order_id": trade["order_id"],
                    "exit_debit": exit_debit,
                    "pnl": management.unrealized_pnl(trade, exit_debit),
                    "exit_cost": exit_costs["total_cost"],
                    "exit_slippage": exit_costs["slippage"],
                    "exit_iv": harness._avg_sold_iv(legs, snap["quotes"]),
                    "exit_reason": reason,
                    "closed_at": now.timestamp(),
                }
            )
        )
    )
    if result.get("ok"):
        db_paper.cmd_clear_open_legs(_ns(order_id=trade["order_id"]))
    return result


def manage(config: dict, now: datetime, *, phase: str, execute: bool) -> dict:
    """Mark every open position, decide, and act on what the gates allow."""
    positions = open_positions()
    session = now.date().isoformat()
    policy = management.policy_for("", config)
    max_executions = int(policy.get("max_executions_per_tick", 3) or 3)

    marked = actions = fresh = stale = 0
    open_capital = 0.0
    closed: list[dict] = []

    for trade in positions:
        open_capital += trade.get("capital_at_risk") or 0.0
        first_check = _first_check_of_day(trade["order_id"], session)
        snap, mark_id = mark_position(trade, config, now)
        marked += 1
        fresh += snap.get("fresh") or 0
        stale += snap.get("stale") or 0

        sessions_held = db_paper.session_span(trade.get("opened_at"), now.timestamp())
        open_legs = []
        if trade.get("strategy") == "double_calendar":
            open_legs = db_paper.cmd_get_open_legs(_ns(order_id=trade["order_id"])).get("legs", [])

        if not snap.get("ok"):
            _record_event(
                trade,
                "hold",
                snap.get("reason") or "unpriceable",
                now,
                phase,
                executed=False,
                gate="unusable_mark",
                mark_id=mark_id,
            )
            continue

        decision = management.evaluate(
            trade,
            snap,
            config,
            now=now,
            sessions_held=sessions_held,
            is_first_check_of_day=first_check,
            open_legs=open_legs,
        )
        gate = (
            None
            if not decision.closes
            else management.execution_gate(snap, config, trade.get("strategy") or "", now=now)
        )
        if decision.closes and not execute:
            gate = gate or "open_window"
        if decision.closes and actions >= max_executions:
            gate = gate or "tick_execution_cap"

        if not decision.closes or gate:
            _record_event(
                trade,
                decision.action,
                decision.reason,
                now,
                phase,
                executed=False,
                gate=gate,
                detail=decision.detail,
                mark_id=mark_id,
            )
            continue

        # Confirm on the broker's own price before recording a close decided on cached quotes: the
        # decision is the cache's, the price on the ledger should be the one we could have traded.
        confirmed = _rest_snapshot(trade)
        priced = confirmed if confirmed.get("ok") else snap
        result = close_position(trade, priced, decision.reason, config, now)
        if not result.get("ok"):
            db_paper.cmd_record_close_failure(
                _ns(data=json.dumps({"order_id": trade["order_id"], "reason": result.get("error")}))
            )
            _record_event(
                trade,
                decision.action,
                decision.reason,
                now,
                phase,
                executed=False,
                gate="close_failed",
                detail=decision.detail,
                mark_id=mark_id,
            )
            continue

        actions += 1
        closed.append({"order_id": trade["order_id"], "symbol": trade["symbol"], "reason": decision.reason})
        _record_event(
            trade,
            decision.action,
            decision.reason,
            now,
            phase,
            executed=True,
            detail={**decision.detail, "source": priced.get("source")},
            mark_id=mark_id,
        )

    if closed:
        refresh_stream_request(open_positions())

    return {
        "open_positions": len(positions),
        "marks_written": marked,
        "actions_taken": actions,
        "quotes_fresh": fresh,
        "quotes_stale": stale,
        "open_capital": round(open_capital, 2),
        "closed": closed,
        "open_capital_warn": open_capital > float(policy.get("open_capital_warn") or float("inf")),
    }


def _record_event(trade, action, reason, now, phase, *, executed, gate=None, detail=None, mark_id=None):
    db_paper.cmd_record_management_event(
        _ns(
            data=json.dumps(
                {
                    "order_id": trade["order_id"],
                    "occurred_at": now.timestamp(),
                    "session_date": now.date().isoformat(),
                    "phase": phase,
                    "action": action,
                    "reason": reason,
                    "executed": executed,
                    "gate": gate,
                    "detail": detail or {},
                    "mark_id": mark_id,
                }
            )
        )
    )


# --------------------------------------------------------------------------- the tick
def forward_scan_already_ran(session: str) -> bool:
    """Whether today's pre-market forward scan has happened.

    Read from `loop_iterations` rather than a marker file: the row is written by the tick that did
    the work, so the two cannot disagree about whether it ran.
    """
    rows = db_paper.cmd_get_iterations(_ns(session_date=session, limit=200))["iterations"]
    return any(r.get("phase") == PHASE_FORWARD_SCAN and r.get("status") == "ok" for r in rows)


def entry_already_ran(session: str) -> bool:
    try:
        record = json.loads(state_file("earnings_entry.last.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return record.get("date") == session


def run_iteration(config: dict | None = None, now: datetime | None = None) -> dict:
    """One tick. Returns what it did; the caller prints it."""
    now = now or datetime.now(ET)
    config = config if config is not None else scanner._load_config()
    session = now.date().isoformat()
    started = time.time()

    phase = phase_for(
        now,
        config,
        entry_done=entry_already_ran(session),
        forward_scan_done=forward_scan_already_ran(session),
    )
    if phase == PHASE_OFF_HOURS:
        return {"ok": True, "phase": phase, "session": session, "skipped": "outside session"}

    record: dict = {"ok": True, "phase": phase, "session": session}

    if phase == PHASE_FORWARD_SCAN:
        # The slow, stable half of screening, done while nothing is trading: the earnings calendar
        # and every Dolt-derived metric for the next N trading days. The entry scan reads this to
        # narrow its candidate list, and the console's Upcoming surface reads the same snapshot.
        _, _, days = _forward_scan_settings(config)
        result = symbol_watch.refresh_symbol_watch(days=days, config=config)
        found = len(result.get("entries") or result.get("symbols") or [])
        record["forward_scan"] = {"days": days, "symbols": found, "ok": result.get("ok", True)}
        record["ok"] = bool(result.get("ok", True))
        # The run trail gets this phase too, which it did not until 2026-08-25.
        #
        # This is the TOP of the funnel — it bounds the entry universe, so a scan that finds nothing
        # guarantees an empty entry scan hours later. It was the only phase that logged nothing, and
        # that is exactly how eleven starved sessions stayed invisible: the calendar had aged out, so
        # this returned `symbols: 0` every morning, and the only trace anywhere was the entry phase's
        # `opened: []` — which reads identically to "candidates were screened and none cleared".
        #
        # `symbols: 0` beside a healthy calendar means something else entirely (the liquid-universe
        # filter, or the watchlist fetch), so recording the count is what separates the two.
        hb = {
            "date": session,
            "phase": PHASE_FORWARD_SCAN,
            "ok": record["ok"],
            "symbols": found,
            "days": days,
        }
        append_run_log({"ts": datetime.now(timezone.utc).isoformat(), **hb})

    elif phase == PHASE_PRE_OPEN:
        refresh_stream_request(open_positions())
        record["stream_request"] = "refreshed"

    elif phase in (PHASE_OPEN_WINDOW, PHASE_MANAGEMENT):
        outcome = manage(config, now, phase=phase, execute=(phase == PHASE_MANAGEMENT))
        record.update(outcome)
        hb = {"date": session, "phase": "exit", "ok": True, **outcome}
        write_heartbeat("exit", hb)
        append_run_log({"ts": datetime.now(timezone.utc).isoformat(), **hb})

    elif phase == PHASE_ENTRY:
        entry = harness.cmd_run_entries(_ns(date=now.strftime("%m/%d/%Y")))
        record["entry"] = entry
        record["ok"] = bool(entry.get("ok", True))
        hb = {
            "date": session,
            "phase": "entry",
            "ok": record["ok"],
            "error": entry.get("error"),
            "opened": entry.get("opened"),
        }
        write_heartbeat("entry", hb)
        # The heartbeat stays terse -- it is a liveness file. The log carries the whole result,
        # which is where the per-symbol accept/reject detail lives.
        append_run_log({"ts": datetime.now(timezone.utc).isoformat(), **hb, "result": entry})
        # Deliberately NOT refreshing the stream request here. Tonight's new underlyings would grow
        # the union and recycle the producer mid-session, blinding the 0DTE modules into their own
        # close -- to make symbols available fourteen hours before this module marks anything.
        # `pre_open` picks them up tomorrow, ahead of the first mark that needs them.

    elif phase == PHASE_EOD:
        # The per-module EOD reports were retired 2026-08-13: the suite review (packages/review)
        # builds one fact set across every module and renders from that, so a module writing its
        # own prose was a second, unreconciled account of the same session. The phase is kept --
        # it still bounds the session and records an iteration -- it simply writes nothing now.
        record["eod"] = "superseded by packages/review"

    duration_ms = int((time.time() - started) * 1000)
    db_paper.cmd_record_iteration(
        _ns(
            data=json.dumps(
                {
                    "ran_at": now.timestamp(),
                    "session_date": session,
                    "phase": phase,
                    "status": "ok" if record.get("ok", True) else "error",
                    "open_positions": record.get("open_positions"),
                    "marks_written": record.get("marks_written"),
                    "actions_taken": record.get("actions_taken"),
                    "quotes_fresh": record.get("quotes_fresh"),
                    "quotes_stale": record.get("quotes_stale"),
                    "open_capital": record.get("open_capital"),
                    "duration_ms": duration_ms,
                    "note": "open capital above watermark" if record.get("open_capital_warn") else None,
                }
            )
        )
    )
    record["duration_ms"] = duration_ms
    return record


def cmd_once(args) -> dict:
    lock = acquire_lock()
    if not lock.acquired:
        # OK, not an error: the entry scan holds this for many ticks by design.
        return {"ok": True, "status": "busy", "detail": "another tick holds the loop lock"}
    try:
        return run_iteration()
    finally:
        release_lock(lock)


def cmd_status(args) -> dict:
    session = datetime.now(ET).date().isoformat()
    iterations = db_paper.cmd_get_iterations(_ns(session_date=session, limit=1))["iterations"]
    positions = open_positions()
    return {
        "ok": True,
        "session": session,
        "phase": phase_for(
            datetime.now(ET),
            scanner._load_config(),
            entry_done=entry_already_ran(session),
            forward_scan_done=forward_scan_already_ran(session),
        ),
        "last_iteration": iterations[0] if iterations else None,
        "open_positions": len(positions),
        "lock_held": lock_path().exists(),
    }


def cmd_record_break(args) -> dict:
    return db_paper.cmd_record_measurement_break(
        _ns(
            data=json.dumps(
                {
                    "break_date": args.date or _date.today().isoformat(),
                    "key": args.key,
                    "old_value": args.old,
                    "new_value": args.new,
                    "note": args.note,
                }
            )
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Managed earnings paper loop")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("once")
    sub.add_parser("status")

    p_break = sub.add_parser("record-break")
    p_break.add_argument("--key", required=True)
    p_break.add_argument("--date", default=None)
    p_break.add_argument("--old", default=None)
    p_break.add_argument("--new", default=None)
    p_break.add_argument("--note", default=None)

    parser.add_argument("--once", action="store_true", help="run one tick (the default)")
    args = parser.parse_args()

    dispatch = {"once": cmd_once, "status": cmd_status, "record-break": cmd_record_break}
    result = dispatch.get(args.command or "once", cmd_once)(args)
    if sys.stdout is not None:
        json.dump(result, sys.stdout, indent=2, default=str)
        print()


if __name__ == "__main__":
    main()
