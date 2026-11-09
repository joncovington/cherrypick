"""Paper session driver: settle what has expired, mark what is open, sell the cycle's puts.

Everything decided is decided by `clock.py` and `engine.py`; this layer reads the cache and
persists what came back. No network, no MCP, no model call on this path. The supervisor drives
`--once` every 60 s; one `run_once`:

1. **Settle**, on any day: every open put whose expiry session's close has posted
   (`provider.posted_close`) settles at it. That is usually the next session's morning, when the
   expiry's close arrives as `prev_day_close`; a close not yet posted waits, and one still missing
   two sessions on is said in the log, for `--settle --date --price` with the official print.
2. **Mark**, in session: each open put every `mark_interval_minutes`, and on every decision-window
   tick. A refused mark is still a row. The worst mark and the first tick an in-the-money put's
   time value fell under `assignment_extrinsic` are kept on the position: early assignment is
   measured, never modelled.
3. **Enter**, on a cycle's entry session (`clock.cycle_entering`), inside the decision window: each
   arm sells one put on each name not yet entered. A tick that cannot act (stale quotes, a 10-delta
   put the window does not bracket) leaves the name pending for the next tick; a name still
   pending when the window closes is `missed`. Nothing fills after the window.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import date, datetime

from cherrypick.core import calendar as _cal
from cherrypick.core import config as _cfg
from cherrypick.core import home as _home
from cherrypick.core import logs as _logs
from cherrypick.core import looplock

from cherrypick.weeklyput import cli as climod
from cherrypick.weeklyput import clock, db, engine, provider, stream_request

RTH_OPEN_MIN = 9 * 60 + 30
STYLES = (engine.CASH, engine.PHYSICAL)
DEFAULTS = {
    "quantity": 1,
    "max_quote_age_seconds": 120,
    "mark_interval_minutes": 30,
    "assignment_extrinsic": 0.05,
    "settle_alert_sessions": 2,
}

_logger = logging.getLogger("weeklyput_paper_loop")


def log_file():
    return _home.logs_dir("weeklyput") / "weeklyput_paper.log"


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
    _home.beat("weeklyput")


# --------------------------------------------------------------------------- config
def defaults(config: dict) -> dict:
    return {**DEFAULTS, **_cfg.section(config, "defaults", label="weeklyput", log=_log)}


def merged_params(config: dict, arm: str) -> dict:
    block = _cfg.registry(config, label="weeklyput").get(arm) or {}
    return {**defaults(config), **{k: v for k, v in block.items() if not k.startswith("_")}, "arm": arm}


def enabled_arms(config: dict) -> list[str]:
    declared = _cfg.registry(config, label="weeklyput", log=_log)
    return [a for a, block in declared.items() if (block or {}).get("enabled", True)]


# --------------------------------------------------------------------------- settle
def _settle_one(conn, pos: dict, price: float, source: str, *, today: date) -> dict:
    res = engine.settle(pos, price, pos["settlement_style"])
    db.save_position(
        conn,
        {
            "position_id": pos["position_id"],
            "status": "closed",
            "settle_session": pos["expiration"],
            "settle_time": clock.now_iso(),
            "settle_price": price,
            "settle_source": source,
            **res,
        },
    )
    db.record_decision(
        conn,
        trade_date=today.isoformat(),
        arm=pos["arm"],
        symbol=pos["symbol"],
        mode="settle",
        reason=res["exit_reason"],
        accepted=True,
        detail=json.dumps({"price": price, "source": source, "net": res["net_pnl"]}),
    )
    _log(
        f"{pos['arm']} {pos['symbol']} {pos['expiration']} {pos['strike']:g}P: {res['exit_reason']} at "
        f"{price:.2f} ({source}), net {res['net_pnl']:.2f} ({res['net_bp']:.1f} bp)"
    )
    return res


def _sessions_after(first: date, last: date) -> int:
    """How many sessions fall after `first`, through `last`."""
    n, d = 0, first
    while True:
        d = _cal.next_trading_day(d)
        if d > last:
            return n
        n += 1


def run_settlements(config: dict, conn, *, cache_path: str, when: datetime) -> int:
    """Settle every open put whose expiry's close has posted. Returns how many settled."""
    today = when.date()
    params = defaults(config)
    settled = 0
    for pos in db.open_positions(conn):
        expiry = date.fromisoformat(pos["expiration"])
        if expiry > today:
            continue
        price = provider.posted_close(cache_path, pos["symbol"], expiry, today=today)
        if price is None:
            late = _sessions_after(expiry, today)
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
            if late > int(params["settle_alert_sessions"]):
                _log(
                    f"{pos['position_id']}: the {pos['expiration']} close has not posted {late} sessions on; "
                    f"settle by hand with --settle --date {pos['expiration']} --symbol {pos['symbol']} "
                    f"--price <official close>"
                )
            continue
        _settle_one(conn, pos, price, "stream_summary", today=today)
        settled += 1
    return settled


def run_settle(conn, *, day: str, symbol: str, price: float) -> dict:
    """The manual settlement: every open put on `symbol` expiring `day`, at the official print."""
    out = []
    for pos in db.open_positions(conn):
        if pos["expiration"] == day and pos["symbol"] == symbol.upper():
            out.append(
                {
                    "position_id": pos["position_id"],
                    **_settle_one(conn, pos, price, "official", today=clock.now_et().date()),
                }
            )
    return {"ok": True, "settled": out}


# --------------------------------------------------------------------------- mark
def _minutes_since(ts: str | None, when: datetime) -> float | None:
    if not ts:
        return None
    try:
        then = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=clock.ET)
    return (when - then).total_seconds() / 60.0


def run_marks(config: dict, conn, *, cache_path: str, when: datetime, force: bool) -> int:
    params = defaults(config)
    written = 0
    for pos in db.open_positions(conn):
        if date.fromisoformat(pos["expiration"]) < when.date():
            continue  # expired, waiting on its close: nothing left to mark
        since = _minutes_since(db.last_mark_ts(conn, pos["position_id"]), when)
        if not force and since is not None and since < float(params["mark_interval_minutes"]):
            continue
        q = provider.leg_mark(
            cache_path, pos["streamer_symbol"], max_quote_age_seconds=float(params["max_quote_age_seconds"])
        )
        spot = provider.read_spot(
            cache_path, pos["symbol"], max_age_seconds=float(params["max_quote_age_seconds"])
        )
        now = clock.now_iso()
        if q is None:
            db.record_mark(
                conn,
                position_id=pos["position_id"],
                ts=now,
                session_date=when.date().isoformat(),
                usable=0,
                refusal="no_fresh_quote",
                spot=spot,
            )
            written += 1
            continue
        m = engine.mid(q.get("bid"), q.get("ask"))
        value = engine.mark_value(m, pos["quantity"])
        ext = engine.extrinsic(m, spot, float(pos["strike"]))
        db.record_mark(
            conn,
            position_id=pos["position_id"],
            ts=now,
            session_date=when.date().isoformat(),
            usable=1 if m is not None else 0,
            refusal=None if m is not None else "unusable_quote",
            bid=q.get("bid"),
            ask=q.get("ask"),
            mid=m,
            spot=spot,
            mark_value=value,
            extrinsic=ext,
        )
        written += 1
        update = {}
        if value is not None and (pos["worst_mark_value"] is None or value < pos["worst_mark_value"]):
            update.update(worst_mark_value=value, worst_mark_time=now)
        in_money = spot is not None and spot < float(pos["strike"])
        if (
            pos["assignment_exposed_at"] is None
            and pos["settlement_style"] == engine.PHYSICAL
            and in_money
            and ext is not None
            and ext < float(params["assignment_extrinsic"])
        ):
            update["assignment_exposed_at"] = now
            _log(f"{pos['position_id']}: in the money with {ext:.3f} of time value left: assignment-exposed")
        if update:
            db.save_position(conn, {"position_id": pos["position_id"], **update})
    return written


# --------------------------------------------------------------------------- enter
def _ensure_pending(conn, config: dict, cycle: date, day: date) -> None:
    for arm in enabled_arms(config):
        for sym in stream_request.names(config):
            if db.entry(conn, arm, sym, cycle.isoformat()) is None:
                db.save_entry(
                    conn,
                    {
                        "arm": arm,
                        "symbol": sym,
                        "expiration": cycle.isoformat(),
                        "entry_session": day.isoformat(),
                        "outcome": "pending",
                    },
                )


def _refuse(conn, arm: str, sym: str, cycle: date, day: date, reason: str, detail=None) -> str:
    db.record_decision(
        conn,
        trade_date=day.isoformat(),
        arm=arm,
        symbol=sym,
        mode="entry",
        reason=reason,
        accepted=False,
        detail=json.dumps(detail, default=str) if detail is not None else None,
    )
    db.save_entry(conn, {"arm": arm, "symbol": sym, "expiration": cycle.isoformat(), "refusal": reason})
    return "pending"


def enter_one(config: dict, conn, arm: str, sym: str, *, cycle: date, day: date, cache_path: str) -> str:
    """One name's sale for one arm on one tick: 'entered' or 'pending'."""
    params = merged_params(config, arm)
    spec = stream_request.names(config)[sym]
    if spec["settlement"] not in STYLES:
        return _refuse(conn, arm, sym, cycle, day, "unknown_settlement", spec["settlement"])
    snap = provider.cycle_chain(
        cache_path,
        sym,
        cycle.isoformat(),
        root=spec["root"],
        max_quote_age_seconds=float(params["max_quote_age_seconds"]),
    )
    stats = snap.get("quote_stats") or {}
    db.record_snapshot(
        conn,
        trade_date=day.isoformat(),
        symbol=sym,
        kind="entry",
        status="ok" if snap["ok"] else snap["reason"],
        quotes_fresh=stats.get("fresh"),
        quotes_stale=stats.get("rejected"),
        spot=snap.get("spot"),
    )
    if not snap["ok"]:
        return _refuse(conn, arm, sym, cycle, day, snap["reason"])
    plan = engine.plan_entry(
        chain=snap["chain"],
        years=clock.years_to(day, cycle),
        fee_symbol=spec["fee_symbol"],
        quantity=int(params["quantity"]),
        costs=config,
    )
    if not plan["ok"]:
        return _refuse(conn, arm, sym, cycle, day, plan["reason"], {"forward": plan.get("forward")})
    ids = snap["symbols"][(plan["strike"], "P")]
    now = clock.now_iso()
    pid = f"{arm}:{sym}:{cycle.isoformat()}"
    db.open_new_position(
        conn,
        position_row={
            "position_id": pid,
            "arm": arm,
            "symbol": sym,
            "root": spec["root"],
            "settlement_style": spec["settlement"],
            "expiration": cycle.isoformat(),
            "entry_session": day.isoformat(),
            "entry_time": now,
            "streamer_symbol": ids["streamer_symbol"],
            "occ_symbol": ids["occ_symbol"],
            "strike": plan["strike"],
            "quantity": plan["quantity"],
            "entry_spot": snap.get("spot"),
            "entry_forward": round(plan["forward"], 4),
            "entry_years": plan["years"],
            "entry_bid": plan["bid"],
            "entry_ask": plan["ask"],
            "entry_mid": plan["mid"],
            "entry_iv": round(plan["iv"], 6),
            "entry_delta": round(plan["delta"], 6),
            "neighbours_json": json.dumps(
                {
                    k: (
                        None
                        if v is None
                        else {"strike": v["strike"], "delta": round(v["delta"], 4), "mid": v["mid"]}
                    )
                    for k, v in plan["neighbours"].items()
                }
            ),
            "entry_value": plan["entry_value"],
            "entry_fees": plan["entry_fees"],
            "entry_slippage": plan["entry_slippage"],
            "notional": plan["notional"],
            "status": "open",
        },
        entry_row={
            "arm": arm,
            "symbol": sym,
            "expiration": cycle.isoformat(),
            "entry_session": day.isoformat(),
            "outcome": "entered",
            "refusal": None,
            "position_id": pid,
            "decided_at": now,
        },
    )
    db.record_decision(
        conn,
        trade_date=day.isoformat(),
        arm=arm,
        symbol=sym,
        mode="entry",
        reason="entered",
        accepted=True,
        detail=json.dumps({"strike": plan["strike"], "delta": round(plan["delta"], 4), "mid": plan["mid"]}),
    )
    _log(
        f"{arm} {sym}: sold {plan['quantity']} {cycle.isoformat()} {plan['strike']:g}P at {plan['mid']:.2f} "
        f"(delta {plan['delta']:.3f}, iv {plan['iv']:.3f}, fwd {plan['forward']:.2f})"
    )
    return "entered"


def run_entries(config: dict, conn, *, cycle: date, day: date, cache_path: str, final: bool) -> dict:
    out = {}
    for arm in enabled_arms(config):
        for sym in stream_request.names(config):
            row = db.entry(conn, arm, sym, cycle.isoformat())
            if row and row["outcome"] != "pending":
                continue
            result = enter_one(config, conn, arm, sym, cycle=cycle, day=day, cache_path=cache_path)
            if result == "pending" and final:
                result = _miss(conn, arm, sym, cycle)
            out[f"{arm}:{sym}"] = result
    return out


def _miss(conn, arm: str, sym: str, cycle: date) -> str:
    row = db.entry(conn, arm, sym, cycle.isoformat()) or {}
    reason = row.get("refusal") or "no_tick_in_window"
    db.save_entry(
        conn,
        {
            "arm": arm,
            "symbol": sym,
            "expiration": cycle.isoformat(),
            "outcome": "missed",
            "refusal": reason,
            "decided_at": clock.now_iso(),
        },
    )
    _log(f"{arm} {sym} {cycle.isoformat()}: the window closed unentered ({reason})")
    return "missed"


def record_misses(config: dict, conn, *, cycle: date, day: date, cache_path: str) -> dict:
    """After the window: every name still pending is missed. It never trades here: a fill after the
    window would be a different rule from the one item 57 judged (guard mutant
    `weeklyput-no-fill-after-window`; `config`, `day` and `cache_path` are its seam)."""
    out = {}
    for row in db.entries(conn):
        if row["expiration"] == cycle.isoformat() and row["outcome"] == "pending":
            out[f"{row['arm']}:{row['symbol']}"] = _miss(conn, row["arm"], row["symbol"], cycle)
    return out


# --------------------------------------------------------------------------- the tick
def run_once(
    config: dict, conn, *, cache_path: str, when: datetime | None = None, force: bool = False
) -> dict:
    _beat()  # liveness is "the loop is turning over", never "it did work"
    when = when or clock.now_et()
    today = when.date()
    settled = run_settlements(config, conn, cache_path=cache_path, when=when)
    if not force and not _cal.is_trading_day(today):
        return {"ok": True, "skipped": "not_a_trading_day", "settled": settled}
    now_min = clock.minute_of_day(when)
    close = clock.close_min(today)
    if not force and not (RTH_OPEN_MIN <= now_min < close):
        return {"ok": True, "skipped": "outside_rth", "now_min": now_min, "settled": settled}

    cycle = clock.cycle_entering(today)
    start, end = clock.decision_window(today, defaults(config))
    in_window = force or start <= now_min <= end
    marks = run_marks(config, conn, cache_path=cache_path, when=when, force=in_window)
    phase, outcome = "mark", {}
    if cycle is not None:
        _ensure_pending(conn, config, cycle, today)
        if in_window:
            phase = "entry"
            outcome = run_entries(
                config,
                conn,
                cycle=cycle,
                day=today,
                cache_path=cache_path,
                final=not force and now_min >= end,
            )
        elif now_min > end:
            phase = "after_window"
            outcome = record_misses(config, conn, cycle=cycle, day=today, cache_path=cache_path)
        else:
            phase = "before_window"
    actions = sum(1 for v in outcome.values() if v == "entered") + settled
    db.record_iteration(
        conn,
        ran_at=time.time(),
        session_date=today.isoformat(),
        phase=phase,
        status="ok",
        open_positions=db.open_position_count(conn),
        marks_written=marks,
        actions_taken=actions,
        note=json.dumps(outcome) if outcome else None,
    )
    return {
        "ok": True,
        "phase": phase,
        "cycle": cycle.isoformat() if cycle else None,
        "entries": outcome,
        "marks": marks,
        "settled": settled,
    }


def run_status(config: dict, conn, *, cache_path: str) -> dict:
    """The watchdog's health object: files only, no broker, no network."""
    when = clock.now_et()
    today = when.date()
    start, end = clock.decision_window(today, defaults(config))
    cycle = clock.next_cycle(today)
    overdue = [
        p["position_id"] for p in db.open_positions(conn) if date.fromisoformat(p["expiration"]) < today
    ]
    entering = clock.cycle_entering(today)
    return {
        "ok": True,
        "date": today.isoformat(),
        "decision_window": f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d} ET",
        "entry_today": entering.isoformat() if entering else None,
        "next_cycle": cycle.isoformat() if cycle else None,
        "next_entry_session": clock.entry_session(cycle).isoformat() if cycle else None,
        "arms": enabled_arms(config),
        "names": list(stream_request.names(config)),
        "open_positions": [
            {k: p[k] for k in ("position_id", "strike", "entry_mid", "entry_delta", "expiration")}
            for p in db.open_positions(conn)
        ],
        "today_entries": [e for e in db.entries(conn) if e["entry_session"] == today.isoformat()],
        "awaiting_close": overdue,
        "stream_cache": cache_path,
        "stream_cache_present": os.path.exists(cache_path),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cherrypick-weeklyput paper session driver")
    ap.add_argument("--config")
    ap.add_argument("--db")
    ap.add_argument("--stream-cache")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, metavar="SECONDS")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true", help="act now, outside the window (testing only)")
    ap.add_argument("--settle", action="store_true", help="settle by hand at the official close")
    ap.add_argument("--date")
    ap.add_argument("--symbol")
    ap.add_argument("--price", type=float)
    args = ap.parse_args(argv)

    config = climod.load_config(args.config)
    cache_path = args.stream_cache or provider.stream_cache_path(config)
    db_path = db.resolve_path(args.db)
    conn = db.connect(db_path)
    stream_request.register(config, conn, db_path, cache_path=cache_path)

    if args.status:
        print(json.dumps(run_status(config, conn, cache_path=cache_path), indent=2, default=str))
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
                    run_once(config, conn, cache_path=cache_path, force=args.force), indent=2, default=str
                )
            )
            return 0
        _log(f"loop starting, interval {args.interval}s, cache {cache_path}")
        while True:
            now = clock.now_et()
            if not args.force and clock.minute_of_day(now) >= clock.close_min(now.date()):
                break
            try:
                run_once(config, conn, cache_path=cache_path, force=args.force)
                stream_request.register(config, conn, db_path, cache_path=cache_path)
            except Exception as exc:  # noqa: BLE001
                _log(f"iteration error (continuing): {type(exc).__name__}: {exc}")
            time.sleep(args.interval)
        _log("session closed")
        return 0
    finally:
        _release_loop_lock()


if __name__ == "__main__":
    raise SystemExit(main())
