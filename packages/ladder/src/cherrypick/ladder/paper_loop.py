"""Paper session driver: sell each lot that reached its target, then buy the lots the rule allows.

Everything decided is decided by `rules.py`; this layer reads the cache and persists what came back.
No network, no broker, no model call on this path. The supervisor drives `--once` every 60 s on
trading days; one `run_once`, inside the trading hours (`session_start` to `session_end`, ET):

For each enabled arm and each fund, against one usable quote:

1. **Sell.** Each open lot's target is worked out for today (`rules.target`); a bid at or over it
   sells the lot at the target, the resting limit's price. The lot's lowest bid seen is kept.
2. **Buy.** The next lot's level is `rules.buy_level`; an ask at or under it buys a lot at the
   level. `chain` keeps going while the ask is under each next level and slots remain; `once` buys
   at most one lot a session. A lot sold this tick frees its slot now, as a live account's would.

A fund with no usable quote, or no posted reference close, does nothing that tick and says so.
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
from cherrypick.core.clock import ET, hhmm_to_min, minute_of_day, now_et

from cherrypick.ladder import cli as climod
from cherrypick.ladder import db, provider, rules, stream_request

DEFAULTS = {
    "lot_dollars": 1000.0,
    "min_shares": 3,
    "max_open_lots": 3,
    "max_open_lots_total": 6,
    "drop": rules.DROP,
    "gain": rules.GAIN,
    "max_quote_age_seconds": 120,
    "session_start": "08:15",
    "session_end": "20:00",
}

_logger = logging.getLogger("ladder_paper_loop")


def log_file():
    return _home.logs_dir("ladder") / "ladder_paper.log"


def _log(message: str) -> None:
    _logs.configure(_logger, log_file())
    _logger.info(message)


def _loop_lock_path() -> str:
    return os.path.join(os.path.dirname(db.resolve_path()), "paper_loop.lock")


# --------------------------------------------------------------------------- config
def defaults(config: dict) -> dict:
    return {**DEFAULTS, **_cfg.section(config, "defaults", label="ladder", log=_log)}


def arms(config: dict) -> dict[str, dict]:
    """{arm: params} for every enabled arm, each with its entry and target rule checked."""
    out = {}
    for arm, block in _cfg.registry(config, label="ladder", log=_log).items():
        block = block or {}
        if str(arm).startswith("_") or block.get("enabled", True) is False:
            continue
        p = {**defaults(config), **{k: v for k, v in block.items() if not k.startswith("_")}, "arm": arm}
        if p.get("entry") not in rules.ENTRY_RULES or p.get("target") not in rules.TARGET_RULES:
            _log(
                f"arm {arm}: entry {p.get('entry')!r} / target {p.get('target')!r} not a known rule; skipped"
            )
            continue
        out[arm] = p
    return out


def name_params(params: dict, spec: dict) -> dict:
    """An arm's params with the fund's own overrides (lot size, cap) laid over them."""
    return {
        **params,
        **{k: v for k, v in spec.items() if k in ("lot_dollars", "min_shares", "max_open_lots")},
    }


def in_hours(when: datetime, params: dict) -> bool:
    m = minute_of_day(when)
    return (
        hhmm_to_min(params["session_start"], 8 * 60 + 15) <= m < hhmm_to_min(params["session_end"], 20 * 60)
    )


# --------------------------------------------------------------------------- the chain anchor
def chain_anchor(lots: list[dict]) -> float | None:
    """The last lot bought since this arm last held nothing on the fund, while it still holds one;
    else None. Item 26's `chain`: a sold lot does not move the anchor, an empty book resets it.
    At one instant a sale comes first: a tick sells before it buys."""
    events = []
    for lot in lots:
        events.append((lot["buy_time"], 1, "buy", lot["buy_price"]))
        if lot["status"] == "closed" and lot.get("sell_time"):
            events.append((lot["sell_time"], 0, "sell", None))
    held, anchor = 0, None
    for _, _, kind, price in sorted(events, key=lambda e: (e[0], e[1])):
        if kind == "buy":
            held += 1
            anchor = price
        else:
            held -= 1
            if held <= 0:
                held, anchor = 0, None
    return anchor if held > 0 else None


def _all_lots(conn, arm: str, symbol: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM ladder_positions WHERE arm = ? AND symbol = ? ORDER BY buy_time, id", (arm, symbol)
        )
    ]


# --------------------------------------------------------------------------- sell
def run_sells(conn, p: dict, symbol: str, q: dict, *, today: date, stamp: str) -> int:
    sold = 0
    now = stamp
    for lot in db.open_lots(conn, p["arm"], symbol):
        age = rules.age_days(date.fromisoformat(lot["buy_session"]), today)
        tgt = rules.target(
            p["target"], cost=lot["buy_price"], shares=lot["quantity"], age=age, gain=float(p["gain"])
        )
        update = {
            "position_id": lot["position_id"],
            "target_price": tgt,
            "target_stage": rules.target_stage(p["target"], age),
            "last_bid": q["bid"],
            "last_mark_time": now,
        }
        if lot["low_bid"] is None or q["bid"] < lot["low_bid"]:
            update.update(low_bid=q["bid"], low_bid_time=now)
        if q["bid"] >= tgt:
            m = rules.money(lot["quantity"], lot["buy_price"], tgt)
            update.update(
                status="closed",
                sell_price=tgt,
                sell_session=today.isoformat(),
                sell_time=now,
                sell_bid=q["bid"],
                sell_ask=q["ask"],
                exit_reason=f"target_{update['target_stage']}",
                hold_days=age,
                **{k: v for k, v in m.items() if k != "entry_value"},
            )
            db.record_decision(
                conn,
                trade_date=today.isoformat(),
                arm=p["arm"],
                symbol=symbol,
                mode="exit",
                reason=update["exit_reason"],
                accepted=True,
                detail=json.dumps({"lot": lot["position_id"], "price": tgt, "net": m["net_pnl"]}),
            )
            _log(
                f"{p['arm']} {symbol}: sold {lot['quantity']} at {tgt:.2f} (bought {lot['buy_price']:.2f} "
                f"{lot['buy_session']}, day {age}), net {m['net_pnl']:.2f}"
            )
            sold += 1
        db.save_position(conn, update)
    return sold


# --------------------------------------------------------------------------- buy
def _refuse(conn, p: dict, symbol: str, today: date, reason: str, detail=None) -> None:
    db.record_decision(
        conn,
        trade_date=today.isoformat(),
        arm=p["arm"],
        symbol=symbol,
        mode="entry",
        reason=reason,
        accepted=False,
        detail=json.dumps(detail, default=str) if detail is not None else None,
    )


def run_buys(conn, p: dict, symbol: str, q: dict, ref: float | None, *, today: date, stamp: str) -> int:
    session = today.isoformat()
    bought = 0
    while True:
        if p["entry"] == "once" and db.bought_in_session(conn, p["arm"], symbol, session):
            return bought
        lots = _all_lots(conn, p["arm"], symbol)
        anchor = chain_anchor(lots) if p["entry"] == "chain" else None
        level = rules.buy_level(p["entry"], ref_close=ref, last_buy=anchor, drop=float(p["drop"]))
        if level is None:
            _refuse(conn, p, symbol, today, "no_reference_close")
            return bought
        if q["ask"] > level:
            return bought
        held = sum(1 for lot in lots if lot["status"] == "open")
        if held >= int(p["max_open_lots"]):
            _refuse(conn, p, symbol, today, "name_cap_full", {"level": level, "held": held})
            return bought
        if db.open_count(conn, p["arm"]) >= int(p["max_open_lots_total"]):
            _refuse(conn, p, symbol, today, "arm_cap_full", {"level": level})
            return bought
        shares = rules.lot_shares(float(p["lot_dollars"]), level)
        if shares < int(p["min_shares"]):
            _refuse(conn, p, symbol, today, "lot_too_small", {"level": level, "shares": shares})
            return bought
        n = db.bought_in_session(conn, p["arm"], symbol, session) + 1
        pid = f"{p['arm']}:{symbol}:{session}:{n}"
        now = stamp
        tgt = rules.target(p["target"], cost=level, shares=shares, age=0, gain=float(p["gain"]))
        db.open_lot(
            conn,
            {
                "position_id": pid,
                "arm": p["arm"],
                "symbol": symbol,
                "entry_rule": p["entry"],
                "target_rule": p["target"],
                "quantity": shares,
                "ref_close": ref,
                "chained_from": anchor,
                "buy_level": level,
                "buy_price": level,
                "buy_session": session,
                "buy_time": now,
                "buy_bid": q["bid"],
                "buy_ask": q["ask"],
                "status": "open",
                "target_price": tgt,
                "target_stage": "gain",
                "low_bid": q["bid"],
                "low_bid_time": now,
                "last_bid": q["bid"],
                "last_mark_time": now,
                "entry_value": round(-shares * level, 2),
            },
        )
        db.record_decision(
            conn,
            trade_date=session,
            arm=p["arm"],
            symbol=symbol,
            mode="entry",
            reason="bought",
            accepted=True,
            detail=json.dumps({"lot": pid, "level": level, "shares": shares, "ref": ref, "anchor": anchor}),
        )
        base = f"last lot {anchor:.2f}" if anchor is not None else f"prev close {ref:.2f}"
        _log(
            f"{p['arm']} {symbol}: bought {shares} at {level:.2f} ({base}, ask {q['ask']:.2f}), "
            f"target {tgt:.2f}"
        )
        bought += 1


# --------------------------------------------------------------------------- the tick
def run_once(
    config: dict, conn, *, cache_path: str, when: datetime | None = None, force: bool = False
) -> dict:
    _home.beat("ladder")  # liveness is "the loop is turning over", never "it did work"
    when = when or now_et()
    today = when.date()
    base = defaults(config)
    if not force and not _cal.is_trading_day(today):
        return {"ok": True, "skipped": "not_a_trading_day"}
    if not force and not in_hours(when, base):
        return {"ok": True, "skipped": "outside_hours", "now_min": minute_of_day(when)}

    funds = stream_request.names(config)
    armset = arms(config)
    # One instant for the whole tick, the tick's own: every lot it buys or sells carries it, so the
    # chain anchor's event order is the order ticks happened in.
    stamp = when.astimezone(ET).isoformat(timespec="seconds")
    out: dict[str, dict] = {}
    marks = actions = 0
    for symbol, spec in funds.items():
        q = provider.quote(cache_path, symbol, max_quote_age_seconds=float(base["max_quote_age_seconds"]))
        ref = provider.reference_close(cache_path, symbol, today)
        db.record_snapshot(
            conn,
            trade_date=today.isoformat(),
            symbol=symbol,
            kind="tick",
            status="ok" if q else "no_fresh_quote",
            bid=q["bid"] if q else None,
            ask=q["ask"] if q else None,
            ref_close=ref,
        )
        for arm, params in armset.items():
            p = name_params(params, spec)
            if q is None:
                _refuse(conn, p, symbol, today, "no_fresh_quote")
                out[f"{arm}:{symbol}"] = {"skipped": "no_fresh_quote"}
                continue
            marks += db.open_count(conn, arm)
            sold = run_sells(conn, p, symbol, q, today=today, stamp=stamp)
            bought = run_buys(conn, p, symbol, q, ref, today=today, stamp=stamp)
            actions += sold + bought
            out[f"{arm}:{symbol}"] = {"sold": sold, "bought": bought}
    db.record_iteration(
        conn,
        ran_at=time.time(),
        session_date=today.isoformat(),
        phase="session",
        status="ok",
        open_positions=db.open_count(conn),
        marks_written=marks,
        actions_taken=actions,
        note=json.dumps(out) if actions else None,
    )
    return {"ok": True, "phase": "session", "actions": actions, "by_arm": out}


def run_status(config: dict, conn, *, cache_path: str) -> dict:
    """The watchdog's health object: files only, no broker, no network."""
    when = now_et()
    today = when.date()
    base = defaults(config)
    funds = {}
    for symbol in stream_request.names(config):
        q = provider.quote(cache_path, symbol, max_quote_age_seconds=float(base["max_quote_age_seconds"]))
        ref = provider.reference_close(cache_path, symbol, today)
        levels = {}
        for arm, params in arms(config).items():
            lots = _all_lots(conn, arm, symbol)
            anchor = chain_anchor(lots) if params["entry"] == "chain" else None
            levels[arm] = rules.buy_level(
                params["entry"], ref_close=ref, last_buy=anchor, drop=float(params["drop"])
            )
        funds[symbol] = {
            "bid": q["bid"] if q else None,
            "ask": q["ask"] if q else None,
            "ref_close": ref,
            "next_buy": levels,
        }
    return {
        "ok": True,
        "date": today.isoformat(),
        "hours": f"{base['session_start']}-{base['session_end']} ET",
        "in_hours": _cal.is_trading_day(today) and in_hours(when, base),
        "arms": {a: {"entry": p["entry"], "target": p["target"]} for a, p in arms(config).items()},
        "funds": funds,
        "open_lots": [
            {
                k: lot[k]
                for k in (
                    "position_id",
                    "quantity",
                    "buy_price",
                    "buy_session",
                    "target_price",
                    "target_stage",
                    "last_bid",
                )
            }
            for lot in db.open_lots(conn)
        ],
        "stream_cache": cache_path,
        "stream_cache_present": os.path.exists(cache_path),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cherrypick-ladder paper session driver")
    ap.add_argument("--config")
    ap.add_argument("--db")
    ap.add_argument("--stream-cache")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, metavar="SECONDS")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true", help="act outside the hours (testing only)")
    args = ap.parse_args(argv)

    config = climod.load_config(args.config)
    cache_path = args.stream_cache or provider.stream_cache_path(config)
    db_path = db.resolve_path(args.db)
    conn = db.connect(db_path)
    stream_request.register(config, conn, db_path)

    if args.status:
        print(json.dumps(run_status(config, conn, cache_path=cache_path), indent=2, default=str))
        return 0
    if not (args.once or args.interval):
        ap.error("choose one of --once, --interval, --status")
    if not looplock.acquire(_loop_lock_path(), 180, alive=looplock.pid_alive):
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
            now = now_et()
            if not args.force and not in_hours(now, defaults(config)):
                break
            try:
                run_once(config, conn, cache_path=cache_path, force=args.force)
                stream_request.register(config, conn, db_path)
            except Exception as exc:  # noqa: BLE001
                _log(f"iteration error (continuing): {type(exc).__name__}: {exc}")
            time.sleep(args.interval)
        _log("session closed")
        return 0
    finally:
        looplock.release(_loop_lock_path())


__all__ = ["ET", "run_once", "run_status", "chain_anchor", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
