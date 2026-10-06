"""Paper session driver: credit distributions, read the regime, take each arm's one decision.

Everything decided is decided by `regime.py`/`engine.py`; this layer reads inputs and persists what
came back. No network, no MCP, no model call anywhere on this path.

One `run_once` per tick (the supervisor drives `--once` every 60s through the session):

- Every in-session tick: credit any distribution the technicals store now shows for a stint that
  held the fund over its ex-date.
- Inside the decision window (`clock.decision_window`, ten minutes before the close): read
  VIX/VIX3M and write `contango_regime`; for each arm not yet decided today, apply its switch rule
  and, when the rule says to change holdings, sell one fund and buy the other at this tick's
  quotes. A tick that cannot act (unmeasured regime, stale or wide quote) leaves the arm undecided
  for the next tick.
- After the window: an arm still undecided is recorded `missed`, holding what it held. The window is
  never extended -- a fill after it would be a different rule from the one the replay measured.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime

from cherrypick.core import calendar as _cal
from cherrypick.core import config as _cfg
from cherrypick.core import home as _home
from cherrypick.core import logs as _logs
from cherrypick.core import looplock

from cherrypick.contango import cli as climod
from cherrypick.contango import clock, db, engine, provider, regime, stream_request

RTH_OPEN_MIN = 9 * 60 + 30
DONE = ("hold", "switch", "missed")

_logger = logging.getLogger("contango_paper_loop")


def log_file():
    return _home.logs_dir("contango") / "contango_paper.log"


def _log(message: str) -> None:
    _logs.configure(_logger, log_file())
    _logger.info(message)


def _paper_data_dir() -> str:
    return os.path.dirname(os.environ.get("CONTANGO_DB_PATH") or db.default_db_path())


def _loop_lock_path() -> str:
    return os.path.join(_paper_data_dir(), "paper_loop.lock")


def _acquire_loop_lock(stale_seconds: int = 180) -> bool:
    return looplock.acquire(_loop_lock_path(), stale_seconds, alive=looplock.pid_alive)


def _release_loop_lock() -> None:
    looplock.release(_loop_lock_path())


def _beat() -> None:
    try:
        path = _home.heartbeat_path("contango")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now().astimezone().isoformat(), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- arms
def merged_params(config: dict, arm: str) -> dict:
    params = {**(config.get("defaults") or {}), **(_cfg.registry(config, label="contango").get(arm) or {})}
    params["arm"] = arm
    return params


def enabled_arms(config: dict) -> list[str]:
    """Every arm the config declares and does not disable, in declared order. Each is validated
    here, so an inverted threshold pair stops the loop at the first tick, not at the first switch."""
    declared = _cfg.registry(config, label="contango", log=_log)
    arms = [a for a, block in declared.items() if (block or {}).get("enabled", True)]
    for arm in arms:
        regime.thresholds(merged_params(config, arm))
    return arms


def _symbols(params: dict) -> tuple[str, str]:
    return str(params.get("risk_symbol", "SVXY")).upper(), str(params.get("cash_symbol", "SHV")).upper()


# --------------------------------------------------------------------------- distributions
def credit_distributions(config: dict, conn, *, technicals_path: str, day: str) -> int:
    held = db.positions(conn)
    if not held:
        return 0
    symbols = sorted({p["symbol"] for p in held})
    since = min(p["entry_session"] for p in held)
    rows = provider.read_dividends(technicals_path, symbols, since=since, through=day)
    if rows is None:
        db.record_decision(
            conn,
            trade_date=day,
            arm="*",
            symbol=",".join(symbols),
            mode="distributions",
            reason="technicals_store_unreadable",
            accepted=False,
            detail=technicals_path,
        )
        return 0
    credits = engine.dividend_credits(held, rows, db.credited_pairs(conn))
    for c in credits:
        pos = next(p for p in db.positions(conn, c["arm"]) if p["position_id"] == c["position_id"])
        acct = db.account(conn, c["arm"])
        update = {
            "position_id": pos["position_id"],
            "distributions": round(pos["distributions"] + c["amount"], 2),
        }
        if pos["status"] == "closed":
            # Landed after the stint closed (the store's rows trail the ex-date): the closed row's
            # money is restated so it still adds up.
            update["gross_pnl"] = round((pos["gross_pnl"] or 0.0) + c["amount"], 2)
            update["net_pnl"] = round((pos["net_pnl"] or 0.0) + c["amount"], 2)
        db.apply_distribution(
            conn,
            credit={**c, "credited_session": day},
            position=update,
            cash=acct["cash"] + c["amount"],
        )
        _log(
            f"{c['arm']}: {c['symbol']} distribution ex {c['ex_date']} ${c['amount']:.2f} on {c['shares']} sh"
        )
    return len(credits)


# --------------------------------------------------------------------------- the regime
def record_regime(config: dict, conn, *, cache_path: str, day: str) -> dict:
    """Today's reading. A stored MEASUREMENT is final (the day's basis does not drift with each
    tick); a stored refusal is retried and overwritten by the first usable read."""
    existing = db.regime_for(conn, day)
    if existing is not None and existing.get("usable"):
        return existing
    prints = provider.read_regime_prints(cache_path)
    result = regime.reading(prints.get("vix"), prints.get("vix3m"), config.get("defaults") or {})
    row = {
        "trade_date": day,
        "tick": clock.now_iso(),
        "ratio": result.get("ratio"),
        "vix": result.get("vix"),
        "vix3m": result.get("vix3m"),
        "vix_age_s": result.get("vix_age_seconds"),
        "vix3m_age_s": result.get("vix3m_age_seconds"),
        "usable": 1 if result.get("ok") else 0,
        "refusal": None if result.get("ok") else result.get("reason"),
    }
    db.save_regime(conn, row)
    return row


# --------------------------------------------------------------------------- the decision
def _session_row(
    day: str,
    arm: str,
    *,
    reading: dict,
    before: str | None,
    after: str | None,
    action: str,
    refusal: str | None,
    holding: dict | None,
    cash: float,
    quotes: dict,
) -> dict:
    mark = quotes.get(holding["symbol"])["mid"] if holding and quotes.get(holding["symbol"]) else None
    return {
        "trade_date": day,
        "arm": arm,
        "decided_at": clock.now_iso(),
        "ratio": reading.get("ratio") if reading.get("usable") else None,
        "state_before": before,
        "state_after": after,
        "action": action,
        "refusal": refusal,
        "holding_symbol": holding["symbol"] if holding else None,
        "shares": holding["shares"] if holding else None,
        "mark": mark,
        "cash": round(cash, 2),
        "nav": engine.nav(cash, holding, mark),
    }


def decide_arm(config: dict, conn, arm: str, *, day: str, reading: dict, quotes: dict, final: bool) -> str:
    """One arm's decision on one tick: 'hold', 'switch', 'pending' (retry next tick) or 'missed'."""
    params = merged_params(config, arm)
    risk_symbol, cash_symbol = _symbols(params)
    acct = db.open_account(conn, arm, float(params.get("starting_capital", 10_000)), day)
    pos = db.open_position(conn, arm)
    current = pos["role"] if pos else None
    holding = {"symbol": pos["symbol"], "shares": pos["shares"]} if pos else None

    def refuse(reason: str) -> str:
        db.record_decision(
            conn, trade_date=day, arm=arm, symbol=risk_symbol, mode="decision", reason=reason, accepted=False
        )
        if not final:
            return "pending"
        db.save_session(
            conn,
            _session_row(
                day,
                arm,
                reading=reading,
                before=current,
                after=current,
                action="missed",
                refusal=reason,
                holding=holding,
                cash=acct["cash"],
                quotes=quotes,
            ),
        )
        _log(f"{arm}: decision window closed unacted ({reason}); still holding {current or 'nothing'}")
        return "missed"

    if not reading.get("usable"):
        return refuse(f"regime_{reading.get('refusal') or 'unmeasured'}")
    target = regime.target_state(reading["ratio"], current, params)
    target_symbol = risk_symbol if target == regime.RISK else cash_symbol
    if pos is not None and pos["symbol"] == target_symbol:
        db.save_session(
            conn,
            _session_row(
                day,
                arm,
                reading=reading,
                before=current,
                after=target,
                action="hold",
                refusal=None,
                holding=holding,
                cash=acct["cash"],
                quotes=quotes,
            ),
        )
        return "hold"

    plan = engine.plan_switch(
        cash=acct["cash"], holding=holding, target_symbol=target_symbol, quotes=quotes, params=params
    )
    if not plan["ok"]:
        return refuse(plan["reason"])
    now = clock.now_iso()
    closed = None
    if pos is not None:
        sell = plan["sell"]
        closed = {
            "position_id": pos["position_id"],
            "status": "closed",
            "exit_session": day,
            "exit_time": now,
            "exit_bid": sell["bid"],
            "exit_ask": sell["ask"],
            "exit_mid": sell["mid"],
            "exit_value": sell["value"],
            "exit_fees": sell["fees"],
            "exit_slippage": sell["slippage"],
            "exit_ratio": reading["ratio"],
            "exit_reason": f"switch_to_{target}",
            **engine.stint_result(pos, sell),
        }
    buy = plan["buy"]
    opened = {
        "position_id": f"{arm}:{target_symbol}:{day}",
        "arm": arm,
        "symbol": target_symbol,
        "role": target,
        "shares": buy["shares"],
        "entry_session": day,
        "entry_time": now,
        "entry_bid": buy["bid"],
        "entry_ask": buy["ask"],
        "entry_mid": buy["mid"],
        "entry_value": -buy["value"],
        "entry_fees": buy["fees"],
        "entry_slippage": buy["slippage"],
        "entry_ratio": reading["ratio"],
        "status": "open",
        "distributions": 0.0,
    }
    db.apply_switch(conn, arm=arm, closed=closed, opened=opened, cash=plan["cash_after"])
    db.record_decision(
        conn,
        trade_date=day,
        arm=arm,
        symbol=target_symbol,
        mode="decision",
        reason=f"switch_to_{target}",
        accepted=True,
        detail=json.dumps({"ratio": reading["ratio"], "from": current}),
    )
    db.save_session(
        conn,
        _session_row(
            day,
            arm,
            reading=reading,
            before=current,
            after=target,
            action="switch",
            refusal=None,
            holding={"symbol": target_symbol, "shares": buy["shares"]},
            cash=plan["cash_after"],
            quotes=quotes,
        ),
    )
    sold = f"sold {pos['shares']} {pos['symbol']} @ {plan['sell']['mid']:.2f}, " if pos else ""
    bought = f"bought {buy['shares']} {target_symbol} @ {buy['mid']:.2f}"
    _log(f"{arm}: ratio {reading['ratio']:.4f} -> {target}: {sold}{bought}")
    return "switch"


def run_decisions(config: dict, conn, *, cache_path: str, day: str, final: bool) -> dict:
    arms = _undecided(config, conn, day)
    if not arms:
        return {}
    reading = record_regime(config, conn, cache_path=cache_path, day=day)
    defaults = config.get("defaults") or {}
    symbols = sorted({s for a in arms for s in _symbols(merged_params(config, a))})
    quotes = provider.read_quotes(
        cache_path,
        symbols,
        max_quote_age_seconds=defaults.get("max_quote_age_seconds", provider.DEFAULT_MAX_QUOTE_AGE_SECONDS),
    )
    for sym in symbols:
        q = quotes.get(sym)
        db.record_snapshot(
            conn,
            trade_date=day,
            symbol=sym,
            kind="quote",
            status="ok" if q else "stale",
            quotes_fresh=1 if q else 0,
            quotes_stale=0 if q else 1,
            spot=q["mid"] if q else None,
        )
    return {
        a: decide_arm(config, conn, a, day=day, reading=reading, quotes=quotes, final=final) for a in arms
    }


def _undecided(config: dict, conn, day: str) -> list[str]:
    return [a for a in enabled_arms(config) if (db.session_for(conn, day, a) or {}).get("action") not in DONE]


def record_misses(config: dict, conn, *, cache_path: str, day: str) -> dict:
    """After the window: every arm still undecided is `missed`, holding what it held, with the last
    refusal its window recorded (or `no_tick_in_window` when the loop never ticked inside it). It
    never trades here -- a fill after the window would be a different rule."""
    out = {}
    for arm in _undecided(config, conn, day):
        params = merged_params(config, arm)
        acct = db.open_account(conn, arm, float(params.get("starting_capital", 10_000)), day)
        pos = db.open_position(conn, arm)
        holding = {"symbol": pos["symbol"], "shares": pos["shares"]} if pos else None
        last = conn.execute(
            "SELECT reason FROM contango_decisions WHERE trade_date = ? AND arm = ? AND mode = 'decision' "
            "ORDER BY id DESC LIMIT 1",
            (day, arm),
        ).fetchone()
        reason = last["reason"] if last else "no_tick_in_window"
        quotes = (
            provider.read_quotes(cache_path, [holding["symbol"]], max_quote_age_seconds=900)
            if holding
            else {}
        )
        db.save_session(
            conn,
            _session_row(
                day,
                arm,
                reading=db.regime_for(conn, day) or {},
                before=pos["role"] if pos else None,
                after=pos["role"] if pos else None,
                action="missed",
                refusal=reason,
                holding=holding,
                cash=acct["cash"],
                quotes=quotes,
            ),
        )
        _log(f"{arm}: decision window closed unacted ({reason})")
        out[arm] = "missed"
    return out


# --------------------------------------------------------------------------- the tick
def run_once(
    config: dict,
    conn,
    *,
    cache_path: str,
    technicals_path: str,
    when: datetime | None = None,
    force: bool = False,
) -> dict:
    _beat()  # liveness is "the loop is turning over", never "it did work"
    when = when or clock.now_et()
    today = when.date()
    day = today.isoformat()
    if not force and not _cal.is_trading_day(today):
        return {"ok": True, "skipped": "not_a_trading_day", "date": day}
    now_min = clock.minute_of_day(when)
    close = clock.close_min(today)
    if not force and not (RTH_OPEN_MIN <= now_min < close):
        return {"ok": True, "skipped": "outside_rth", "now_min": now_min}

    for arm in enabled_arms(config):
        db.open_account(conn, arm, float(merged_params(config, arm).get("starting_capital", 10_000)), day)
    credited = credit_distributions(config, conn, technicals_path=technicals_path, day=day)
    start, end = clock.decision_window(today, config.get("defaults") or {})
    phase, outcome = "wait", {}
    if force or start <= now_min <= end:
        phase = "decision"
        outcome = run_decisions(config, conn, cache_path=cache_path, day=day, final=force or now_min >= end)
    elif now_min > end:
        phase = "after_window"
        outcome = record_misses(config, conn, cache_path=cache_path, day=day)
    actions = sum(1 for v in outcome.values() if v == "switch") + credited
    db.record_iteration(
        conn,
        ran_at=time.time(),
        session_date=day,
        phase=phase,
        status="ok",
        open_positions=db.open_position_count(conn),
        marks_written=0,
        actions_taken=actions,
        note=json.dumps(outcome) if outcome else None,
    )
    return {"ok": True, "phase": phase, "decisions": outcome, "distributions": credited}


def run_status(config: dict, conn, *, cache_path: str) -> dict:
    when = clock.now_et()
    today = when.date()
    start, end = clock.decision_window(today, config.get("defaults") or {})
    arms = {}
    for arm in enabled_arms(config):
        pos = db.open_position(conn, arm)
        arms[arm] = {
            "account": db.account(conn, arm),
            "holding": {k: pos[k] for k in ("symbol", "role", "shares", "entry_session", "entry_mid")}
            if pos
            else None,
            "today": db.session_for(conn, today.isoformat(), arm),
        }
    return {
        "ok": True,
        "date": today.isoformat(),
        "decision_window": f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d} ET",
        "today_regime": db.regime_for(conn, today.isoformat()),
        "arms": arms,
        "stream_cache": cache_path,
        "stream_cache_present": os.path.exists(cache_path),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cherrypick-contango paper session driver")
    ap.add_argument("--config")
    ap.add_argument("--db")
    ap.add_argument("--stream-cache")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, metavar="SECONDS")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true", help="act now, outside the window (testing only)")
    args = ap.parse_args(argv)

    config = climod.load_config(args.config)
    cache_path = args.stream_cache or provider.stream_cache_path(config)
    technicals_path = provider.technicals_db_path(config)
    db_path = args.db or os.environ.get("CONTANGO_DB_PATH") or db.default_db_path()
    conn = db.connect(db_path)
    stream_request.register(config)

    if args.status:
        print(json.dumps(run_status(config, conn, cache_path=cache_path), indent=2, default=str))
        return 0
    if not (args.once or args.interval):
        ap.error("choose one of --once, --interval, --status")
    if not _acquire_loop_lock():
        print(json.dumps({"ok": True, "skipped": "another paper loop is already running"}))
        return 0
    try:
        if args.once:
            result = run_once(
                config, conn, cache_path=cache_path, technicals_path=technicals_path, force=args.force
            )
            print(json.dumps(result, indent=2, default=str))
            return 0
        _log(f"loop starting, interval {args.interval}s, cache {cache_path}")
        while True:
            now = clock.now_et()
            if not args.force and clock.minute_of_day(now) >= clock.close_min(now.date()):
                break
            try:
                run_once(
                    config, conn, cache_path=cache_path, technicals_path=technicals_path, force=args.force
                )
            except Exception as exc:  # noqa: BLE001
                _log(f"iteration error (continuing): {type(exc).__name__}: {exc}")
            time.sleep(args.interval)
        _log("session closed")
        return 0
    finally:
        _release_loop_lock()


if __name__ == "__main__":
    raise SystemExit(main())
