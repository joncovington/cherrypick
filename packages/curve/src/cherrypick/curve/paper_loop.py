"""Paper session driver — record the regime, mark, manage, enter once per session, settle.

Everything decided is decided by `regime.py`/`engine.py`/`management.py`; this layer supplies
snapshots and persists what came back. No network, no MCP, no model call anywhere on this path.

One `run_once` carries the day's lifecycle:

- Any trading day: read today's VIX/VIX3M reading and write `curve_regime` — whether or not any
  book trades (rule 7). A stale/missing quote writes a row marked unusable, never a guess.
- Past the disposition time: cover shares an earlier settlement delivered, then sell surviving
  wings of short-settled positions.
- At the entry tick (default 10:00 ET, once per session — position cap is one open position per
  book, no laddering): plan and enter. `control`/`noflip` share one plan (the exact pairing); `hook`
  enters only on the two-day-confirmed hook signal.
- Mark every open leg every tick, then manage: profit-take, the regime-flip hard exit
  (control/hook only, and only on a MEASURED crossing — rule 6), `close_dte`.
- Past the settle time on any day legs expire: settle off a staleness-gated print.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime

from cherrypick.core import advice as _core_advice
from cherrypick.core import calendar as _cal
from cherrypick.core import home as _home
from cherrypick.core import logs as _logs
from cherrypick.core import looplock

from cherrypick.curve import book as bookmod
from cherrypick.curve import cli as climod
from cherrypick.curve import clock, db, engine, management, provider, regime, stream_request

RTH_OPEN_MIN = 9 * 60 + 30
RTH_CLOSE_MIN = 16 * 60
DEFAULT_SETTLE_MIN = 16 * 60 + 20

_logger = logging.getLogger("curve_paper_loop")


def stream_cache_path(config: dict) -> str:
    configured = (config.get("source") or {}).get("stream_cache_db")
    if configured:
        return os.path.expanduser(os.path.expandvars(configured))
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "marketdata", "stream_cache.db")


def log_file():
    return _home.logs_dir("curve") / "curve_paper.log"


def _log(message: str) -> None:
    _logs.configure(_logger, log_file())
    _logger.info(message)


def in_session(now_min: int) -> bool:
    return RTH_OPEN_MIN <= now_min < RTH_CLOSE_MIN


def settle_time_min(config: dict) -> int:
    return clock.hhmm_to_min((config.get("defaults") or {}).get("settle_time"), DEFAULT_SETTLE_MIN)


def _symbol(config: dict) -> str:
    return (config.get("symbol") or "VXX").strip().upper()


def _paper_data_dir() -> str:
    return os.path.dirname(os.environ.get("CURVE_DB_PATH") or db.default_db_path())


def _loop_lock_path() -> str:
    return os.path.join(_paper_data_dir(), "paper_loop.lock")


_pid_alive = looplock.pid_alive  # noqa: F401


def _acquire_loop_lock(stale_seconds: int = 180) -> bool:
    return looplock.acquire(_loop_lock_path(), stale_seconds, alive=_pid_alive)


def _release_loop_lock() -> None:
    looplock.release(_loop_lock_path())


# --------------------------------------------------------------------------- the regime tick
def _record_regime(
    config: dict, conn, *, cache_path: str, day: str, now_min: int, force: bool = False
) -> dict:
    """Read today's VIX/VIX3M reading and write `curve_regime` — traded or not (rule 7).

    **A refusal does not settle the day; only a measurement does.** This tick runs every 60s from
    midnight, so the first one of the session always lands hours before the open and always refuses
    (`outside_rth` — an overnight-frozen VIX must never masquerade as a measured reading), and until
    2026-08-26 that refusal was then treated as "already recorded" and blocked every RTH tick
    behind it. The module's declared second product
    was therefore never measured once: three sessions on file, all stamped 00:00 with a null ratio.

    So a stored REFUSAL is retried and overwritten by the first usable reading — `save_regime`
    upserts on `trade_date` — while a stored MEASUREMENT is final, which keeps the day's basis
    stable at the first moment the feed could actually serve one instead of drifting with each tick.

    **The RTH gate is a clock check, not an inference from quote age.** The paragraph above used to
    rest on "there is no fresh quote outside RTH", and that is an assumption about the feed rather
    than a property of the day. It does not hold: `stream_trades.updated_at` is when the row was
    WRITTEN, not when the exchange printed, so a streamer reconnect — which resubscribes everything
    and takes DXLink's snapshot of the last trade — restamps yesterday's close as seconds old. Two
    sessions were measured that way before this gate existed (2026-08-31 at 01:21 ET and 2026-09-02
    at 02:38 ET, each within a minute of a reconnect, each carrying the prior close to the cent),
    and 09-02 traded on it. Outside RTH we now refuse on the clock and never look at a quote at all,
    so the freshness proxy is no longer load-bearing. See issue #10 for the cache-level half.
    """
    existing = db.regime_for(conn, day)
    if existing is not None and existing.get("usable"):
        return existing
    if not force and not in_session(now_min):
        # Still a row, per rule 7 — a day is recorded whether or not it could be measured, and this
        # refusal is retried by every later tick like any other.
        now = clock.now_iso()
        row = {
            "trade_date": day,
            "tick": now,
            "recorded_at": now,
            "usable": 0,
            "refusal": "outside_rth",
            "ratio": None,
            "regime": None,
            "hook": 0,
            "vix": None,
            "vix3m": None,
            "vix_age_s": None,
            "vix3m_age_s": None,
        }
        db.save_regime(conn, row)
        return row
    defaults = config.get("defaults") or {}
    quotes = provider.read_regime_quotes(
        cache_path, max_quote_age_seconds=defaults.get("max_quote_age_seconds", 300)
    )
    prior_ratio = db.prior_ratio_before(conn, day)
    result = regime.reading(quotes.get("vix"), quotes.get("vix3m"), prior_ratio=prior_ratio, params=defaults)
    now = clock.now_iso()
    row = {
        "trade_date": day,
        "tick": now,
        "recorded_at": now,
        "usable": 1 if result.get("ok") else 0,
        "refusal": None if result.get("ok") else result.get("reason"),
        "ratio": result.get("ratio"),
        "regime": result.get("regime"),
        "hook": 1 if result.get("hook") else 0,
        "vix": result.get("vix"),
        "vix3m": result.get("vix3m"),
        "vix_age_s": result.get("vix_age_seconds"),
        "vix3m_age_s": result.get("vix3m_age_seconds"),
    }
    db.save_regime(conn, row)
    if not result.get("ok"):
        _log(f"regime unmeasured today: {result.get('reason')}")
    return row


# --------------------------------------------------------------------------- advised book
def _advice_decision_path() -> str:
    return os.path.join(_paper_data_dir(), "advice_active.json")


def advice_decision(config: dict, today: str) -> dict:
    return _core_advice.session_decision(
        _home.state_dir(), "curve", today, config, _advice_decision_path(), base_key="base_book", log=_log
    )


def session_books(config: dict, today: str) -> tuple[list[str], dict | None]:
    books = [b for b in engine.BOOKS if (config.get("books") or {}).get(b, {}).get("enabled", True)]
    decision = advice_decision(config, today)
    params = decision.get("params")
    if params:
        books.append(f"advised:{decision.get('base_book') or 'control'}")
    return books, params


# --------------------------------------------------------------------------- the tick
def run_once(
    config: dict, conn, *, cache_path: str, when: datetime | None = None, force: bool = False
) -> dict:
    # At the TOP of the tick, before any gate: liveness means "the loop is turning over",
    # never "it did work". Moved here from the resident branch on 2026-08-24 — the supervisor
    # drives this module as repeated `--once` ticks, so a beat that lived only in the
    # `--interval` path never fired at all and this module published no heartbeat. The
    # watchdog then fell back to the DB mtime and the log, both CONDITIONAL writes, and
    # warned "paper data is stale" every time the module was healthy but idle.
    _beat()
    when = when or clock.now_et()
    now_min = clock.minute_of_day(when)
    today = when.date()
    day = today.isoformat()

    if not force and not _cal.is_trading_day(today):
        return {"ok": True, "skipped": "not_a_trading_day", "date": day}

    _record_regime(
        config, conn, cache_path=cache_path, day=day, now_min=now_min, force=force
    )

    overdue = _overdue_legs(conn, day)
    if overdue and now_min % 60 < 2:
        _log(f"{len(overdue)} leg(s) past expiration remain open — settle manually with --settle")
    past_settle = now_min >= settle_time_min(config)
    if past_settle and _unsettled_today(conn, day):
        _log(f"past settle time — settling legs expiring {day}")
        return {
            "ok": True,
            "settled_session": True,
            **run_settle(config, conn, cache_path=cache_path, when=when),
        }

    if not force and not in_session(now_min):
        return {"ok": True, "skipped": "outside_rth", "now_min": now_min}

    defaults = config.get("defaults") or {}
    actions = 0
    marks_written = 0
    phase = "manage"

    disposition_min = clock.hhmm_to_min(defaults.get("disposition_time"), 9 * 60 + 45)
    if now_min >= disposition_min:
        actions += _dispose_shares(config, conn, cache_path=cache_path, when=when, day=day)
        actions += _dispose_wings(config, conn, cache_path=cache_path, when=when, day=day)

    window_start = clock.hhmm_to_min(defaults.get("entry_window_start"), 10 * 60)
    window_end = clock.hhmm_to_min(defaults.get("entry_window_end"), 10 * 60 + 30)
    if window_start <= now_min <= window_end:
        phase = "entry"
        actions += _try_entries(config, conn, cache_path=cache_path, when=when, day=day)

    marked, values = _mark_positions(config, conn, cache_path=cache_path, when=when, day=day)
    marks_written += marked
    actions += _manage_positions(config, conn, values, day=day)

    db.record_iteration(
        conn,
        ran_at=time.time(),
        session_date=day,
        phase=phase,
        status="ok",
        open_positions=len(values),
        marks_written=marks_written,
        actions_taken=actions,
    )
    return {"ok": True, "phase": phase, "open_positions": len(values), "actions": actions}


def _overdue_legs(conn, day: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT l.position_id, l.leg_role, l.expiration FROM curve_legs l "
            "JOIN curve_positions p ON p.position_id = l.position_id "
            "WHERE l.status = 'open' AND l.expiration < ? AND p.status != 'closed'",
            (day,),
        )
    ]


def _unsettled_today(conn, day: str) -> bool:
    return bool(db.expiring_open_legs(conn, day))


# --------------------------------------------------------------------------- entry
def _try_entries(config: dict, conn, *, cache_path: str, when: datetime, day: str) -> int:
    books, advice_params = session_books(config, day)
    defaults = config.get("defaults") or {}
    max_positions = int(defaults.get("max_positions", 1))
    symbol = _symbol(config)
    opened_count = 0

    plan_dates = clock.target_expiration(when.date(), defaults)
    today_regime = db.regime_for(conn, day) or {}

    wanting = [
        b
        for b in books
        if db.open_position_for(conn, symbol, b) is None and db.open_position_count(conn, b) < max_positions
    ]
    if not wanting:
        return 0
    if plan_dates is None:
        for b in wanting:
            db.record_decision(
                conn,
                trade_date=day,
                book=b,
                symbol=symbol,
                mode="entry",
                reason="no_expiration_plan",
                accepted=False,
            )
        return 0

    # Gate control/noflip on contango; hook on the two-day-confirmed hook signal (rule 6: no
    # regime read means no new entry, recorded as a refusal, never a guess).
    gated: list[str] = []
    for b in list(wanting):
        if b == "hook" or (b.startswith("advised:") and b.split(":", 1)[1] == "hook"):
            reason = (
                None
                if today_regime.get("usable") and today_regime.get("hook")
                else ("regime_unmeasured" if not today_regime.get("usable") else "no_hook_signal")
            )
        else:
            reason = (
                None
                if today_regime.get("usable") and today_regime.get("regime") == "contango"
                else ("regime_unmeasured" if not today_regime.get("usable") else "not_contango")
            )
        if reason is not None:
            wanting.remove(b)
            db.record_entry_attempt(conn, trade_date=day, symbol=symbol, book=b, outcome=reason)
            db.record_decision(
                conn, trade_date=day, book=b, symbol=symbol, mode="entry", reason=reason, accepted=False
            )
        else:
            gated.append(b)
    wanting = gated
    if not wanting:
        return 0

    root = config.get("occ_root") or symbol
    snapshot = provider.build_entry_snapshot(
        cache_path, symbol, plan_dates, root=root, when=when, **provider.snapshot_kwargs(config)
    )
    if not snapshot.get("ok"):
        _log(f"{symbol}: entry snapshot refused ({snapshot['reason']})")
        db.record_snapshot(
            conn,
            trade_date=day,
            symbol=symbol,
            kind="entry",
            status=snapshot["reason"],
            quotes_stale=snapshot.get("rejected"),
        )
        for b in wanting:
            db.record_entry_attempt(conn, trade_date=day, symbol=symbol, book=b, outcome=snapshot["reason"])
            db.record_decision(
                conn,
                trade_date=day,
                book=b,
                symbol=symbol,
                mode="entry",
                reason=snapshot["reason"],
                accepted=False,
            )
        return 0
    db.record_snapshot(
        conn,
        trade_date=day,
        symbol=symbol,
        kind="entry",
        status="ok",
        quotes_fresh=snapshot["quote_stats"]["fresh"],
        quotes_stale=snapshot["quote_stats"]["rejected"],
        spot=snapshot["spot"],
    )

    base_params = {**management.PARAM_DEFAULTS, **engine.merged_params(config, "control")}
    planned = engine.plan_entry(snapshot, base_params)
    plans: dict[str, dict] = {}
    for b in wanting:
        base = b.split(":", 1)[1] if b.startswith("advised:") else b
        if b.startswith("advised:") and advice_params:
            plans[b] = engine.plan_entry(snapshot, {**base_params, **advice_params})
        elif base == "hook":
            plans[b] = engine.plan_entry(
                snapshot, {**management.PARAM_DEFAULTS, **engine.merged_params(config, "hook")}
            )
        else:
            plans[b] = planned

    for b in wanting:
        result = plans[b]
        if not result.get("ok"):
            db.record_entry_attempt(
                conn,
                trade_date=day,
                symbol=symbol,
                book=b,
                outcome=result["reason"],
                block_detail=json.dumps(result.get("detail")) if result.get("detail") else None,
                spot=snapshot["spot"],
            )
            db.record_decision(
                conn,
                trade_date=day,
                book=b,
                symbol=symbol,
                mode="entry",
                reason=result["reason"],
                accepted=False,
            )
            continue
        plan = result["plan"]
        if plan.get("short_selected_by") == "delta_computed":
            # Temporary visibility while the feed's own greeks coverage is unproven for VXX's
            # chain: a Black-Scholes fallback is a legitimate computation (see engine.bs_call_delta),
            # not a defect, but every occurrence is worth a human noticing until there is enough
            # history to know how often the feed actually lacks delta here.
            _logger.warning(
                "curve entry (%s/%s): short strike %.2f selected via delta_computed, "
                "feed had no delta for this chain",
                symbol,
                b,
                plan["short_strike"],
            )
        opened = bookmod.enter_position(
            conn, plan, config, b, entry_session=day, advice_params=advice_params, regime=today_regime
        )
        if opened is None:
            continue
        opened_count += 1
        db.record_entry_attempt(
            conn,
            trade_date=day,
            symbol=symbol,
            book=b,
            outcome="filled",
            spot=plan["spot"],
            short_strike=plan["short_strike"],
            long_strike=plan["long_strike"],
            credit=plan["credit"],
            credit_pct_of_width=plan["credit_pct_of_width"],
        )
        db.record_decision(
            conn,
            trade_date=day,
            book=b,
            symbol=symbol,
            mode="entry",
            reason=(
                f"entered {plan['short_strike']:g}/{plan['long_strike']:g} "
                f"credit {plan['credit']:.2f} ({plan['credit_pct_of_width']:.1%} of width)"
            ),
            accepted=True,
        )
        _log(
            f"[{b}] {symbol}: entered {plan['short_strike']:g}/{plan['long_strike']:g} "
            f"({plan['expiration']}) credit {plan['credit']:.2f}"
        )
    return opened_count


# --------------------------------------------------------------------------- mark + manage
def _mark_positions(config: dict, conn, *, cache_path: str, when: datetime, day: str) -> tuple[int, dict]:
    written = 0
    values: dict[str, dict] = {}
    ts = time.time()
    for position in db.open_positions(conn):
        legs = db.open_legs_for(conn, position["position_id"])
        for leg in legs:
            leg["position_symbol"] = position["symbol"]
        snapshot = provider.build_mark_snapshot(
            cache_path, legs, when=when, **provider.snapshot_kwargs(config)
        )
        params = management.effective_params(position, config)
        spot = snapshot.get("spot")
        short_quote = long_quote = None
        for leg in legs:
            quote = (snapshot.get("quotes") or {}).get(leg["streamer_symbol"])
            if leg["leg_role"] == "short_call":
                short_quote = quote
            else:
                long_quote = quote
        close_cost = engine.spread_close_cost(short_quote, long_quote)
        short_tv = None
        exposed = 0
        if short_quote is not None and spot is not None:
            short_tv = round(short_quote["mid"] - max(0.0, spot - position["short_strike"]), 4)
            exposed = 1 if management.assignment_exposed(short_tv, params) else 0
        for leg in legs:
            quote = (snapshot.get("quotes") or {}).get(leg["streamer_symbol"])
            is_short = leg["leg_role"] == "short_call"
            db.record_mark(
                conn,
                position_id=position["position_id"],
                leg_role=leg["leg_role"],
                marked_at=ts,
                session_date=day,
                bid=quote["bid"] if quote else None,
                ask=quote["ask"] if quote else None,
                mid=quote["mid"] if quote else None,
                spot=spot,
                close_cost=close_cost if is_short else None,
                short_tv=short_tv if is_short else None,
                assignment_exposed=exposed if is_short else None,
                quote_age_s=quote["age_seconds"] if quote else None,
                usable=1 if quote else 0,
                refusal=None if quote else (snapshot.get("reason") or "missing_leg_quotes"),
            )
            written += 1
        if exposed:
            try:
                db.save_position(
                    conn,
                    {
                        "position_id": position["position_id"],
                        "exposure_ticks": int(position.get("exposure_ticks") or 0) + 1,
                    },
                )
            except Exception:  # noqa: BLE001, S110
                pass
        values[position["position_id"]] = {
            "position": position,
            "snapshot": snapshot,
            "params": params,
            "close_cost": close_cost,
        }
    return written, values


def _manage_positions(config: dict, conn, values: dict, *, day: str) -> int:
    actions = 0
    today_regime = db.regime_for(conn, day)
    for pid, state in values.items():
        position = state["position"]
        if position["status"] != "open":
            continue
        params = state["params"]
        decision = management.evaluate(
            position, params, now=clock.now_et(), close_cost=state["close_cost"], regime=today_regime
        )
        if not decision.acts:
            continue
        gate = management.execution_gate(state["snapshot"], params, now=clock.now_et())
        executed = 0
        if gate is None:
            result = bookmod.close_open_legs(
                conn, position, state["snapshot"], config, reason=decision.reason, session_date=day
            )
            executed = 1 if result.get("ok") else 0
            if not result.get("ok"):
                gate = result.get("reason")
            else:
                actions += 1
                _log(f"[{position['book']}] {pid} closed — {decision.reason}")
        db.record_management_event(
            conn,
            position_id=pid,
            occurred_at=time.time(),
            session_date=day,
            action=decision.action,
            reason=decision.reason,
            executed=executed,
            gate=gate,
            detail_json=json.dumps(decision.detail) if decision.detail else None,
        )
    return actions


# --------------------------------------------------------------------------- disposition
def _dispose_wings(config: dict, conn, *, cache_path: str, when: datetime, day: str) -> int:
    """Sell the surviving wing of every short-settled position — the disposal half the option
    ledger sees, the session AFTER the ITM leg settled."""
    actions = 0
    for position in db.open_positions(conn, statuses=("short_settled",)):
        legs = db.open_legs_for(conn, position["position_id"])
        if not legs:
            bookmod.finalize_if_done(conn, position["position_id"], reason="expired", session_date=day)
            continue
        for leg in legs:
            leg["position_symbol"] = position["symbol"]
        snapshot = provider.build_mark_snapshot(
            cache_path, legs, when=when, **provider.snapshot_kwargs(config)
        )
        params = management.effective_params(position, config)
        gate = management.execution_gate(snapshot, params, now=when)
        executed = 0
        if gate is None:
            result = bookmod.close_open_legs(
                conn, position, snapshot, config, reason="wing_disposition", session_date=day
            )
            executed = 1 if result.get("ok") else 0
            if not result.get("ok"):
                gate = result.get("reason")
            else:
                actions += 1
                _log(f"[{position['book']}] {position['position_id']} wing disposed")
        db.record_management_event(
            conn,
            position_id=position["position_id"],
            occurred_at=time.time(),
            session_date=day,
            action="close_all",
            reason="wing_disposition",
            executed=executed,
            gate=gate,
        )
    return actions


def _dispose_shares(config: dict, conn, *, cache_path: str, when: datetime, day: str) -> int:
    open_shares = db.open_assignments(conn, before_session=day)
    if not open_shares:
        return 0
    max_age = (config.get("defaults") or {}).get("max_quote_age_seconds", 300)
    actions = 0
    spots: dict[str, float | None] = {}
    for assignment in open_shares:
        symbol = assignment["symbol"]
        if symbol not in spots:
            spots[symbol] = provider.read_spot(cache_path, symbol, max_age_seconds=max_age)
        spot = spots[symbol]
        if spot is None:
            db.record_management_event(
                conn,
                position_id=assignment["position_id"],
                occurred_at=time.time(),
                session_date=day,
                action="dispose_shares",
                reason="share_disposition",
                executed=0,
                gate="no_spot",
            )
            continue
        result = bookmod.dispose_assignment(conn, assignment, spot, session_date=day)
        actions += 1
        _log(
            f"[{assignment['book']}] {assignment['position_id']}: covered {assignment['shares']} "
            f"{assignment['direction']} {symbol} shares at {spot:.2f}, share P&L {result['share_pnl']:+.2f}"
        )
        db.record_management_event(
            conn,
            position_id=assignment["position_id"],
            occurred_at=time.time(),
            session_date=day,
            action="dispose_shares",
            reason="share_disposition",
            executed=1,
        )
    return actions


# --------------------------------------------------------------------------- settle / status
def run_settle(
    config: dict,
    conn,
    *,
    cache_path: str,
    when: datetime | None = None,
    price: float | None = None,
    day: str | None = None,
) -> dict:
    when = when or clock.now_et()
    day = day or when.date().isoformat()
    symbol = _symbol(config)
    if not any(leg["position_symbol"] == symbol for leg in db.expiring_open_legs(conn, day)):
        return {"ok": True, "results": [], "date": day}
    max_age = (config.get("defaults") or {}).get("settlement_max_age_seconds", 300)
    spot = price if price is not None else provider.read_spot(cache_path, symbol, max_age_seconds=max_age)
    if spot is None:
        _log(
            f"{symbol}: cannot settle {day} — no price within {max_age}s. Re-run with --price once it recovers."
        )
        return {
            "ok": False,
            "results": [{"symbol": symbol, "ok": False, "reason": "no_settlement_price"}],
            "date": day,
        }
    results = bookmod.settle_expiring_legs(conn, day, spot, config, symbol=symbol)
    for result in results:
        _log(
            f"{symbol} {result['position_id']}: settled {result['settled_legs']} leg(s) at {spot:.2f} "
            f"({result['itm']} ITM, fee {result['fee']:.2f})"
        )
    return {
        "ok": True,
        "results": [{"symbol": symbol, "ok": True, "settled": len(results), "spot": spot}],
        "date": day,
    }


def run_status(config: dict, conn, *, cache_path: str) -> dict:
    when = clock.now_et()
    today = when.date().isoformat()
    expiring = db.expiring_open_legs(conn, today)
    entered_today = conn.execute(
        "SELECT COUNT(*) FROM curve_positions WHERE entry_session = ?", (today,)
    ).fetchone()[0]
    expiring_positions = len({leg["position_id"] for leg in expiring})
    open_now = db.open_positions(conn)
    return {
        "ok": True,
        "date": today,
        "in_session": in_session(clock.minute_of_day(when)),
        "session_settled": not expiring,
        "positions_today": entered_today + expiring_positions,
        "open_positions": len(open_now),
        "target_expiration": clock.target_expiration(when.date(), config.get("defaults") or {}),
        "today_regime": db.regime_for(conn, today),
        "stream_cache": cache_path,
        "stream_cache_present": os.path.exists(cache_path),
    }


def _beat() -> None:
    try:
        path = _home.heartbeat_path("curve")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now().astimezone().isoformat(), encoding="utf-8")
    except OSError:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cherrypick-curve paper session driver")
    ap.add_argument("--config")
    ap.add_argument("--db")
    ap.add_argument("--stream-cache")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, metavar="SECONDS")
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--price", type=float)
    ap.add_argument("--date", help="YYYY-MM-DD")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    config = climod.load_config(args.config)
    cache_path = args.stream_cache or stream_cache_path(config)
    db_path = args.db or os.environ.get("CURVE_DB_PATH") or db.default_db_path()
    conn = db.connect(db_path)
    stream_request.register(config, conn, db_path, cache_path=cache_path)

    if args.status:
        print(json.dumps(run_status(config, conn, cache_path=cache_path), indent=2, default=str))
        return 0
    if args.settle:
        print(
            json.dumps(
                run_settle(config, conn, cache_path=cache_path, price=args.price, day=args.date),
                indent=2,
                default=str,
            )
        )
        return 0
    if args.interval:
        if not _acquire_loop_lock():
            print(json.dumps({"ok": True, "skipped": "another paper loop is already running"}))
            return 0
        try:
            _log(f"loop starting, interval {args.interval}s, cache {cache_path}")
            drift = db.stale_writer_columns(conn)
            if drift:
                _log(
                    f"WARNING: {len(drift)} ledger column(s) this checkout will never write — {', '.join(drift)}."
                )
            while args.force or in_session(clock.minute_of_day(clock.now_et())):
                try:
                    run_once(config, conn, cache_path=cache_path, force=args.force)
                except Exception as exc:  # noqa: BLE001
                    _log(f"iteration error (continuing): {type(exc).__name__}: {exc}")
                time.sleep(args.interval)
            _log("session closed")
            return 0
        finally:
            _release_loop_lock()
    if args.once:
        if not _acquire_loop_lock():
            print(json.dumps({"ok": True, "skipped": "another paper loop is already running"}))
            return 0
        try:
            print(
                json.dumps(
                    run_once(config, conn, cache_path=cache_path, force=args.force), indent=2, default=str
                )
            )
            return 0
        finally:
            _release_loop_lock()

    ap.error("choose one of --once, --interval, --settle, --status")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
