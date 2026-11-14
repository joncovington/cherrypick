"""Paper session driver: settle what has expired, record the spot, manage what is open, enter what is due.

Everything decided is decided by `engine.py` (through `paper.decide_all`, the same call the backtest's
driver stacks); this layer reads the cache, builds the snapshot (`feed.py`) and persists what came back. No
network, no MCP, no model call, and no order: a paper position is a ledger row. The supervisor drives
`--once` every 60 s; one `run_once`:

1. **Settle**, on any day: every open position whose expiry session's close has posted
   (`provider.posted_close`) settles at it, at intrinsic value. That is usually the next session's
   morning; a close not yet posted waits, and one still missing two sessions on is said in the log, for
   `--settle --date --symbol --price`.
2. **Record the spot**, in session: one sample a minute per symbol, the day's path that the opening range
   and the intraday RSI and stochastic read (the cache keeps only the last print).
3. **Manage**, in session: each open position is asked `engine.manage` (touch stop, profit target, minutes
   before expiry) and closed at the quotes when one is due; otherwise marked every
   `mark_interval_minutes`. A close whose legs do not price is retried on the next tick.
4. **Enter**: each arm of the day whose entry is due is decided by `paper.decide_all` on a snapshot of its
   symbol, once its window opens. A tick that cannot act (a stale feed, a trail that began late) leaves the
   arm pending for the next tick; an arm still pending when its window closes is `missed`, with the last
   reason. **Nothing fills after the window.** Every decision, taken or refused, is a journal row and a
   line of the replay record.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import date, datetime, timedelta

from cherrypick.core import calendar as _cal
from cherrypick.core import home as _home
from cherrypick.core import logs as _logs
from cherrypick.core import looplock
from cherrypick.core.clock import ET, minute_of_day, now_et, now_iso

from . import config as config_mod
from . import db, engine, events, feed, paper, provider, record, stream_request
from .model import Entry

RTH_OPEN_MIN = 9 * 60 + 30
MULT = 100

_logger = logging.getLogger("ensemble_paper_loop")


def log_file():
    return _home.logs_dir("ensemble") / "ensemble_paper.log"


def _log(message: str) -> None:
    _logs.configure(_logger, log_file())
    _logger.info(message)


def _loop_lock_path() -> str:
    return os.path.join(os.path.dirname(db.resolve_path()), "paper_loop.lock")


def _acquire_loop_lock(stale_seconds: int = 180) -> bool:
    return looplock.acquire(_loop_lock_path(), stale_seconds, alive=looplock.pid_alive)


def _release_loop_lock() -> None:
    looplock.release(_loop_lock_path())


def _beat() -> None:
    _home.beat("ensemble")


def close_minute(day: date) -> int:
    h, m = _cal.session_close_hhmm(day).split(":")
    return int(h) * 60 + int(m)


def _stub_arm(pos: dict) -> dict:
    """The arm as the engine's `Position` reads it: the rules the position was entered under."""
    return {"name": pos["arm"], "symbol": pos["symbol"], "exit": json.loads(pos.get("exit_json") or "{}")}


def _position(pos: dict) -> engine.Position:
    return engine.Position(Entry.from_dict(json.loads(pos["entry_json"])), _stub_arm(pos))


# --------------------------------------------------------------------------- the close
def _close_fields(pos: dict, trade: dict, *, spot, when: datetime, source: str, settle_price=None) -> dict:
    """The ledger columns of a finished trade. `exit_value` is derived from the trade's gross and the
    entry value, so the identity `entry_value + exit_value = gross_pnl` holds to the cent."""
    return {
        "status": "closed",
        "exit_reason": trade["exit_kind"],
        "exit_session": trade["exit_session"],
        "exit_minute": trade["exit_minute"],
        "exit_time": when.isoformat(timespec="seconds"),
        "exit_spot": spot,
        "exit_mid": trade["exit_mid"],
        "exit_value": round(trade["gross"] - pos["entry_value"], 2),
        "settle_price": settle_price,
        "settle_source": source,
        "gross_pnl": trade["gross"],
        "fees": trade["fees"],
        "settlement": trade["settlement"],
        "slippage": trade["slippage"]["verdict"],
        "net_pnl": trade["net"]["verdict"],
        "slippage_stress": trade["slippage"]["stress"],
        "net_stress": trade["net"]["stress"],
        "net_mid": trade["net"]["mid"],
    }


def _record_close(conn, pos: dict, fields: dict, *, today: date) -> None:
    db.close_position(conn, pos["position_id"], fields)
    db.record_decision(
        conn,
        trade_date=today.isoformat(),
        arm=pos["arm"],
        symbol=pos["symbol"],
        mode="exit",
        reason=fields["exit_reason"],
        accepted=True,
        detail=json.dumps({"net": fields["net_pnl"], "gross": fields["gross_pnl"]}),
    )
    _log(
        f"{pos['position_id']}: closed ({fields['exit_reason']}), gross {fields['gross_pnl']:.2f}, "
        f"net {fields['net_pnl']:.2f}"
    )


# --------------------------------------------------------------------------- settle
def _sessions_after(first: date, last: date) -> int:
    n, d = 0, first
    while True:
        d = _cal.next_trading_day(d)
        if d > last:
            return n
        n += 1


def settle_one(conn, pos: dict, price: float, source: str, *, when: datetime) -> dict:
    trade = engine.settle_position(_position(pos), price, pos["expiration"])
    fields = _close_fields(pos, trade, spot=price, when=when, source=source, settle_price=price)
    _record_close(conn, pos, fields, today=when.date())
    return fields


def run_settlements(config: dict, conn, *, cache_path: str, when: datetime) -> int:
    """Settle every open position whose expiry's close has posted. Returns how many settled."""
    today = when.date()
    params = config_mod.defaults(config)
    settled = 0
    for pos in db.open_positions(conn):
        expiry = date.fromisoformat(pos["expiration"])
        if expiry > today:
            continue
        price = provider.posted_close(cache_path, pos["symbol"], expiry, today=today)
        if price is None:
            if expiry < today:
                db.record_decision(
                    conn,
                    trade_date=today.isoformat(),
                    arm=pos["arm"],
                    symbol=pos["symbol"],
                    mode="settle",
                    reason="close_not_posted",
                    accepted=False,
                    detail=pos["expiration"],
                )
                late = _sessions_after(expiry, today)
                if late > int(params["settle_alert_sessions"]):
                    _log(
                        f"{pos['position_id']}: the {pos['expiration']} close has not posted {late} sessions "
                        f"on; settle by hand with --settle --date {pos['expiration']} --symbol "
                        f"{pos['symbol']} --price <official close>"
                    )
            continue
        settle_one(conn, pos, price, "stream_summary", when=when)
        settled += 1
    return settled


def run_settle(conn, *, day: str, symbol: str, price: float) -> dict:
    """The manual settlement: every open position on `symbol` expiring `day`, at the official print."""
    out = []
    when = now_et()
    for pos in db.open_positions(conn):
        if pos["expiration"] == day and pos["symbol"] == symbol.upper():
            out.append(
                {"position_id": pos["position_id"], **settle_one(conn, pos, price, "official", when=when)}
            )
    return {"ok": True, "settled": out}


# --------------------------------------------------------------------------- the spot record
def record_spots(config: dict, arms: dict, conn, *, cache_path: str, when: datetime) -> dict[str, float]:
    """One sample per symbol this minute; returns {symbol: spot} of the ones that were fresh."""
    params = config_mod.defaults(config)
    minute = when.hour * 60 + when.minute
    seen = {}
    for sym in config_mod.symbols(config, arms):
        spot = provider.read_spot(cache_path, sym, max_age_seconds=float(params["spot_max_age_seconds"]))
        if spot is not None:
            db.record_spot(conn, sym, when.date().isoformat(), minute, spot)
            seen[sym] = spot
    return seen


# --------------------------------------------------------------------------- manage and mark
def mark_value(pos: dict, rows: list[dict]) -> float:
    """What closing would bring at the mid, whole-position dollars: the legs held long sell, the legs
    held short buy back. `rows` are the legs' quotes in the ledger's leg order."""
    entry = Entry.from_dict(json.loads(pos["entry_json"]))
    total = sum(g.side * (q["bid"] + q["ask"]) / 2.0 for g, q in zip(entry.legs, rows, strict=True))
    return round(total * MULT * entry.quantity, 2)


def _minutes_since(ts: str | None, when: datetime) -> float | None:
    if not ts:
        return None
    try:
        then = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=ET)
    return (when - then).total_seconds() / 60.0


def run_positions(config: dict, conn, *, cache_path: str, when: datetime, force_mark: bool = False) -> dict:
    """Ask `engine.manage` of every open position; close what is due, mark the rest."""
    params = config_mod.defaults(config)
    age = float(params["max_quote_age_seconds"])
    out = {"closed": 0, "marks": 0, "pending_close": 0}
    for pos in db.open_positions(conn):
        if date.fromisoformat(pos["expiration"]) < when.date():
            continue  # expired, waiting on its posted close
        legs = db.legs(conn, pos["position_id"])
        rows = provider.leg_rows(cache_path, legs, max_age_seconds=age)
        spot = provider.read_spot(
            cache_path, pos["symbol"], max_age_seconds=float(params["spot_max_age_seconds"])
        )
        if spot is not None:
            snap = feed.position_snapshot(pos, rows or [], spot, when)
            if rows is None:
                snap.chains.clear()
            sig = engine.manage(_position(pos), snap)
            if sig is not None:
                trade = engine.close_position(_position(pos), sig[0], snap, costs=config)
                if trade is None:
                    out["pending_close"] += 1
                    _log(f"{pos['position_id']}: {sig[0]} is due but the legs do not price yet; retrying")
                else:
                    _record_close(
                        conn,
                        pos,
                        _close_fields(pos, trade, spot=spot, when=when, source="quotes"),
                        today=when.date(),
                    )
                    out["closed"] += 1
                    continue
        since = _minutes_since(db.last_mark_ts(conn, pos["position_id"]), when)
        if not force_mark and since is not None and since < float(params["mark_interval_minutes"]):
            continue
        ts = now_iso()
        if rows is None or spot is None:
            db.record_mark(
                conn,
                position_id=pos["position_id"],
                ts=ts,
                session_date=when.date().isoformat(),
                usable=0,
                refusal="no_fresh_quote" if rows is None else "no_spot",
                spot=spot,
            )
        else:
            value = mark_value(pos, rows)
            db.record_mark(
                conn,
                position_id=pos["position_id"],
                ts=ts,
                session_date=when.date().isoformat(),
                usable=1,
                spot=spot,
                mark_value=value,
            )
            update = {}
            if pos["worst_mark_value"] is None or value < pos["worst_mark_value"]:
                update.update(worst_mark_value=value, worst_mark_time=ts)
            if pos["best_mark_value"] is None or value > pos["best_mark_value"]:
                update.update(best_mark_value=value, best_mark_time=ts)
            if update:
                db.save_position(conn, {"position_id": pos["position_id"], **update})
        out["marks"] += 1
    return out


# --------------------------------------------------------------------------- enter
def ensure_register(conn, todays: dict[str, dict], day: date) -> None:
    for arm in todays.values():
        if db.entry(conn, arm["name"], arm["symbol"], day.isoformat()) is None:
            db.save_entry(
                conn,
                {
                    "arm": arm["name"],
                    "symbol": arm["symbol"],
                    "entry_session": day.isoformat(),
                    "outcome": "pending",
                },
            )


def _refuse(conn, arm: dict, day: date, reason: str, detail=None) -> None:
    text = json.dumps(detail, default=str) if detail is not None else None
    db.record_decision(
        conn,
        trade_date=day.isoformat(),
        arm=arm["name"],
        symbol=arm["symbol"],
        mode="entry",
        reason=reason,
        accepted=False,
        detail=text,
    )
    db.save_entry(
        conn,
        {
            "arm": arm["name"],
            "symbol": arm["symbol"],
            "entry_session": day.isoformat(),
            "outcome": "pending",
            "refusal": reason,
            "detail": text,
        },
    )


def _enter(conn, arm: dict, spec: dict, snap, decision, *, when: datetime) -> str:
    e = decision.entry
    mult = MULT * e.quantity
    pid = f"{arm['name']}:{snap.session}"
    now = when.isoformat(timespec="seconds")
    chain = snap.chains[e.expiry]
    leg_rows = []
    for g in e.legs:
        q = chain.get(g.strike, g.right) or {}
        leg_rows.append(
            {
                "position_id": pid,
                "leg_role": g.name,
                "right": g.right,
                "side": g.side,
                "strike": g.strike,
                "streamer_symbol": q.get("streamer_symbol"),
                "occ_symbol": q.get("occ_symbol"),
                "status": "open",
            }
        )
    db.open_new_position(
        conn,
        position_row={
            "position_id": pid,
            "arm": arm["name"],
            "symbol": arm["symbol"],
            "root": spec["root"],
            "entry_session": snap.session,
            "entry_minute": snap.minute,
            "entry_time": now,
            "expiration": e.expiry,
            "quantity": e.quantity,
            "entry_spot": e.spot,
            "entry_mid": e.prices["mid"],
            "max_loss": e.max_loss,
            "max_profit": e.max_profit,
            "capital": round(e.max_loss * mult, 2),
            "entry_value": round(e.prices["mid"] * mult, 2),
            "entry_fees": e.fees_open,
            "entry_slippage": e.slippage["verdict"],
            "entry_json": json.dumps(e.to_dict()),
            "exit_json": json.dumps(arm.get("exit") or {}),
            "status": "open",
        },
        leg_rows=leg_rows,
        entry_row={
            "arm": arm["name"],
            "symbol": arm["symbol"],
            "entry_session": snap.session,
            "outcome": "entered",
            "refusal": None,
            "position_id": pid,
            "decided_at": now,
        },
    )
    legs = " ".join(f"{'+' if g.side > 0 else '-'}{g.strike:g}{g.right}" for g in e.legs)
    db.record_decision(
        conn,
        trade_date=snap.session,
        arm=arm["name"],
        symbol=arm["symbol"],
        mode="entry",
        reason="enter",
        accepted=True,
        detail=json.dumps({"legs": legs, "quantity": e.quantity, "mid": e.prices["mid"], "expiry": e.expiry}),
    )
    _log(f"{pid}: entered {e.quantity} x {e.expiry} {legs} at {e.prices['mid']:.2f} (spot {e.spot:.2f})")
    return "entered"


def run_entries(
    config: dict, arms: dict[str, dict], conn, *, cache_path: str, when: datetime, evts, replay_to=None
) -> dict:
    """Decide each arm that is due. Returns {arm: 'entered' | reason} for the arms acted on this tick."""
    params = config_mod.defaults(config)
    today, minute = when.date(), when.hour * 60 + when.minute
    specs = config_mod.symbols(config, arms)
    todays = stream_request.active_arms(arms, today)
    ensure_register(conn, todays, today)
    out: dict[str, str] = {}
    for sym, spec in specs.items():
        pending = [
            a
            for a in todays.values()
            if a["symbol"].upper() == sym
            and (db.entry(conn, a["name"], a["symbol"], today.isoformat()) or {}).get("outcome") == "pending"
            and feed.entry_window(a)[0] <= minute <= feed.entry_window(a)[1]
        ]
        if not pending:
            continue
        light, info = feed.snapshot_for(
            cache_path, conn, spec, sym, when, params, pending, events=evts, chains=False
        )
        if light is None:
            for a in pending:
                _refuse(conn, a, today, info["refusal"], info.get("detail"))
                out[a["name"]] = info["refusal"]
            continue
        due = [a for a in pending if engine.entry_due(a, light)[0]]
        ready = []
        for a in due:
            if feed.reads(a)["trail"] and not info["trail_complete"]:
                first = light.trail[0][0] if light.trail else None
                _refuse(conn, a, today, "trail_incomplete", {"first_minute": first})
                out[a["name"]] = "trail_incomplete"
            else:
                ready.append(a)
        if not ready:
            continue
        snap, info = feed.snapshot_for(cache_path, conn, spec, sym, when, params, ready, events=evts)
        if snap is None:
            for a in ready:
                _refuse(conn, a, today, info["refusal"], info.get("detail"))
                out[a["name"]] = info["refusal"]
            continue
        db.record_snapshot(
            conn,
            trade_date=today.isoformat(),
            symbol=sym,
            kind="entry",
            status="ok" if info["expiries"] else "no_chain",
            quotes_fresh=info["fresh"],
            quotes_stale=info["rejected"],
            spot=snap.spot,
        )
        chosen = {a["name"]: a for a in ready}
        decisions = paper.decide_all(chosen, snap, costs=config)
        try:
            record.append(snap, chosen, decisions, now_iso(), directory=replay_to)
        except OSError as exc:  # the record is a convenience; a full disk must not cost a trade
            _log(f"replay record not written: {exc}")
        for d in decisions:
            arm = chosen[d.arm]
            if d.enter:
                out[d.arm] = _enter(conn, arm, spec, snap, d, when=when)
            else:
                last = d.checks[-1] if d.checks else (d.reason, False, None)
                _refuse(conn, arm, today, d.reason, {"check": last[0], "value": last[2]})
                out[d.arm] = d.reason
    return out


def record_misses(conn, todays: dict[str, dict], day: date, minute: int) -> dict[str, str]:
    """Every arm still pending after its window is missed, with the last reason a tick gave."""
    out = {}
    for arm in todays.values():
        row = db.entry(conn, arm["name"], arm["symbol"], day.isoformat())
        if not row or row["outcome"] != "pending" or minute <= feed.entry_window(arm)[1]:
            continue
        reason = row.get("refusal") or (
            "no_breakout" if arm["entry"]["kind"] == "orb" else "no_tick_in_window"
        )
        db.save_entry(
            conn,
            {
                "arm": arm["name"],
                "symbol": arm["symbol"],
                "entry_session": day.isoformat(),
                "outcome": "missed",
                "refusal": reason,
                "decided_at": now_iso(),
            },
        )
        _log(f"{arm['name']} {day.isoformat()}: the window closed unentered ({reason})")
        out[arm["name"]] = "missed"
    return out


# --------------------------------------------------------------------------- the tick
def run_once(
    config: dict,
    arms: dict[str, dict],
    conn,
    *,
    cache_path: str,
    when: datetime | None = None,
    force: bool = False,
    evts=None,
    replay_to=None,
) -> dict:
    _beat()  # liveness is "the loop is turning over", never "it did work"
    when = when or now_et()
    today = when.date()
    settled = run_settlements(config, conn, cache_path=cache_path, when=when)
    if not force and not _cal.is_trading_day(today):
        return {"ok": True, "skipped": "not_a_trading_day", "settled": settled}
    now_min = minute_of_day(when)
    if not force and not (RTH_OPEN_MIN <= now_min < close_minute(today)):
        return {"ok": True, "skipped": "outside_rth", "now_min": now_min, "settled": settled}

    evts = evts if evts is not None else events.load_calendar((config.get("events") or {}).get("path"))
    record_spots(config, arms, conn, cache_path=cache_path, when=when)
    managed = run_positions(config, conn, cache_path=cache_path, when=when)
    entered = run_entries(
        config, arms, conn, cache_path=cache_path, when=when, evts=evts, replay_to=replay_to
    )
    missed = record_misses(conn, stream_request.active_arms(arms, today), today, now_min)
    db.prune_trail(conn, (today - timedelta(days=db.TRAIL_KEEP_DAYS)).isoformat())
    actions = sum(1 for v in entered.values() if v == "entered") + managed["closed"] + settled
    db.record_iteration(
        conn,
        ran_at=time.time(),
        session_date=today.isoformat(),
        phase="entry" if entered else "manage",
        status="ok",
        open_positions=db.open_position_count(conn),
        marks_written=managed["marks"],
        actions_taken=actions,
        note=json.dumps({"entries": entered, "missed": missed}) if (entered or missed) else None,
    )
    return {
        "ok": True,
        "entries": entered,
        "missed": missed,
        "managed": managed,
        "settled": settled,
    }


def _window_text(arm: dict) -> str:
    first, last = feed.entry_window(arm)
    return f"{first // 60:02d}:{first % 60:02d}-{last // 60:02d}:{last % 60:02d} ET"


def run_status(config: dict, arms: dict[str, dict], conn, *, cache_path: str) -> dict:
    """The watchdog's health object: files only, no broker, no network."""
    when = now_et()
    today = when.date()
    overdue = [
        p["position_id"] for p in db.open_positions(conn) if date.fromisoformat(p["expiration"]) < today
    ]
    todays = stream_request.active_arms(arms, today)
    return {
        "ok": True,
        "date": today.isoformat(),
        "arms": sorted(config_mod.enabled(arms)),
        "arms_today": {n: _window_text(a) for n, a in sorted(todays.items())},
        "symbols": list(config_mod.symbols(config, arms)),
        "open_positions": [
            {k: p[k] for k in ("position_id", "symbol", "expiration", "quantity", "entry_value")}
            for p in db.open_positions(conn)
        ],
        "today_entries": db.entries(conn, session=today.isoformat()),
        "awaiting_close": overdue,
        "stream_cache": cache_path,
        "stream_cache_present": os.path.exists(cache_path),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cherrypick-ensemble paper session driver")
    ap.add_argument("--config")
    ap.add_argument("--db")
    ap.add_argument("--stream-cache")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, metavar="SECONDS")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true", help="act now, outside the session (testing only)")
    ap.add_argument("--settle", action="store_true", help="settle by hand at the official close")
    ap.add_argument("--date")
    ap.add_argument("--symbol")
    ap.add_argument("--price", type=float)
    args = ap.parse_args(argv)

    config = config_mod.load_config(args.config)
    try:
        arms = config_mod.load_arms(config)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": f"arms: {exc}"}))
        return 2
    cache_path = args.stream_cache or provider.stream_cache_path(config)
    db_path = db.resolve_path(args.db)
    conn = db.connect(db_path)
    stream_request.register(config, arms, conn, db_path, cache_path=cache_path)

    if args.status:
        print(json.dumps(run_status(config, arms, conn, cache_path=cache_path), indent=2, default=str))
        return 0
    if args.settle:
        if not (args.date and args.symbol and args.price):
            ap.error("--settle needs --date, --symbol and --price")
        print(json.dumps(run_settle(conn, day=args.date, symbol=args.symbol, price=args.price), indent=2))
        return 0
    if not (args.once or args.interval):
        ap.error("choose one of --once, --interval, --status, --settle")
    if not _acquire_loop_lock():
        print(json.dumps({"ok": True, "skipped": "another paper loop is already running"}))
        return 0
    try:
        if args.once:
            print(
                json.dumps(
                    run_once(config, arms, conn, cache_path=cache_path, force=args.force),
                    indent=2,
                    default=str,
                )
            )
            return 0
        _log(f"loop starting, interval {args.interval}s, cache {cache_path}")
        while True:
            now = now_et()
            if not args.force and minute_of_day(now) >= close_minute(now.date()):
                break
            try:
                run_once(config, arms, conn, cache_path=cache_path, force=args.force)
                stream_request.register(config, arms, conn, db_path, cache_path=cache_path)
            except Exception as exc:  # noqa: BLE001
                _log(f"iteration error (continuing): {type(exc).__name__}: {exc}")
            time.sleep(args.interval)
        _log("session closed")
        return 0
    finally:
        _release_loop_lock()


if __name__ == "__main__":
    raise SystemExit(main())
