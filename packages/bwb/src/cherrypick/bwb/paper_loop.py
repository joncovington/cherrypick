"""Paper session driver — enter the daily ladder, record trigger ticks, manage/fire add-ons, settle.

Everything decided is decided by `engine.py`/`triggers.py`/`management.py`; this layer supplies
snapshots and persists what came back. No network, no MCP, no model call anywhere on this path.

One `run_once` carries the day's lifecycle:

- At the entry tick (default 10:00 ET, once per session): plan and enter ONE new BWB per enabled
  book — the daily ladder, so positions accumulate across sessions rather than capping at one.
- Every in-session tick (60s): for each open COHORT (entry_session x structure_signature), read the
  near wing's delta + spot + gamma_flip ONCE and record one `bwb_trigger_ticks` row — the shared
  telemetry every base book's positions in that cohort read. Then, per position: update latches,
  evaluate the book's own trigger, arm/fire the add-on, mark every leg.
- Past the settle time on any day legs expire: settle at cash intrinsic off a staleness-gated print.
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

from cherrypick.bwb import book as bookmod
from cherrypick.bwb import cli as climod
from cherrypick.bwb import clock, db, engine, management, provider, stream_request

RTH_OPEN_MIN = 9 * 60 + 30
RTH_CLOSE_MIN = 16 * 60
DEFAULT_SETTLE_MIN = 16 * 60 + 20

_logger = logging.getLogger("bwb_paper_loop")


def stream_cache_path(config: dict) -> str:
    configured = (config.get("source") or {}).get("stream_cache_db")
    if configured:
        return os.path.expanduser(os.path.expandvars(configured))
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "marketdata", "stream_cache.db")


def log_file():
    return _home.logs_dir("bwb") / "bwb_paper.log"


def _log(message: str) -> None:
    _logs.configure(_logger, log_file())
    _logger.info(message)


def in_session(now_min: int) -> bool:
    return RTH_OPEN_MIN <= now_min < RTH_CLOSE_MIN


def settle_time_min(config: dict) -> int:
    return clock.hhmm_to_min((config.get("defaults") or {}).get("settle_time"), DEFAULT_SETTLE_MIN)


def _symbol(config: dict) -> str:
    return (config.get("symbol") or "SPX").strip().upper()


def _paper_data_dir() -> str:
    return os.path.dirname(os.environ.get("BWB_DB_PATH") or db.default_db_path())


def _loop_lock_path() -> str:
    return os.path.join(_paper_data_dir(), "paper_loop.lock")


_pid_alive = looplock.pid_alive  # noqa: F401


def _acquire_loop_lock(stale_seconds: int = 180) -> bool:
    return looplock.acquire(_loop_lock_path(), stale_seconds, alive=_pid_alive)


def _release_loop_lock() -> None:
    looplock.release(_loop_lock_path())


# --------------------------------------------------------------------------- advised book
def _advice_decision_path() -> str:
    return os.path.join(_paper_data_dir(), "advice_active.json")


def advice_decision(config: dict, today: str) -> dict:
    return _core_advice.session_decision(
        _home.state_dir(), "bwb", today, config, _advice_decision_path(), base_key="base_book", log=_log
    )


def session_books(config: dict, today: str) -> tuple[list[str], dict | None]:
    books = [b for b in engine.BOOKS if (config.get("books") or {}).get(b, {}).get("enabled", True)]
    # The wall book is OPT-IN, the reverse of the base four: it trades a different structure
    # (call-side, body at the GEX call wall) rather than a different add-on timing, so absence
    # from `engine.BOOKS` is what keeps "the four books enter the identical BWB" true. It never
    # arms (`triggers.evaluate` returns fired=False for an unknown book) and its trigger-tick
    # cohort records the call-side candidates for a future replay, the way the put books' own
    # triggers were earned.
    if (config.get("books") or {}).get("wall", {}).get("enabled"):
        books.append("wall")
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

    entry_time = clock.hhmm_to_min(defaults.get("entry_time"), 10 * 60)
    if now_min >= entry_time:
        phase = "entry"
        actions += _try_entries(config, conn, cache_path=cache_path, when=when, day=day)

    tick_written = _record_trigger_ticks(config, conn, cache_path=cache_path, when=when, day=day)
    marked, values = _mark_positions(config, conn, cache_path=cache_path, when=when, day=day)
    marks_written += marked
    actions += _manage_positions(config, conn, values, cache_path=cache_path, when=when, day=day)

    db.record_iteration(
        conn,
        ran_at=time.time(),
        session_date=day,
        phase=phase,
        status="ok",
        open_positions=len(values),
        marks_written=marks_written,
        actions_taken=actions,
        note=f"trigger_ticks={tick_written}",
    )
    return {"ok": True, "phase": phase, "open_positions": len(values), "actions": actions}


def _overdue_legs(conn, day: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT l.position_id, l.leg_role, l.expiration FROM bwb_legs l "
            "JOIN bwb_positions p ON p.position_id = l.position_id "
            "WHERE l.status = 'open' AND l.expiration < ? AND p.status != 'closed'",
            (day,),
        )
    ]


def _unsettled_today(conn, day: str) -> bool:
    return bool(db.expiring_open_legs(conn, day))


# --------------------------------------------------------------------------- entry (daily ladder)
def _try_entries(config: dict, conn, *, cache_path: str, when: datetime, day: str) -> int:
    books, advice_params = session_books(config, day)
    defaults = config.get("defaults") or {}
    symbol = _symbol(config)
    opened_count = 0

    plan_dates = clock.target_expiration(when.date(), defaults)

    wanting = [b for b in books if db.open_position_for(conn, symbol, b, day) is None]
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
    wall_reading: dict | None = None
    for b in wanting:
        if b == "wall":
            # The wall comes off the SAME reading the flip trigger uses — one compute, one basis —
            # read once here rather than per-book. A session with no reading is a refusal the
            # attempt rows record; the wall book never borrows the EM placement as a fallback.
            if wall_reading is None:
                wall_reading = provider.gamma_flip_reading(
                    cache_path,
                    symbol,
                    snapshot["expiration"],
                    root,
                    max_age_seconds=defaults.get("max_quote_age_seconds", 300),
                )
            wall = wall_reading.get("call_wall") if wall_reading.get("ok") else None
            plans[b] = engine.plan_wall_entry(
                snapshot, {**base_params, **engine.merged_params(config, "wall")}, wall
            )
        elif b.startswith("advised:") and advice_params:
            plans[b] = engine.plan_entry(snapshot, {**base_params, **advice_params})
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
        opened = bookmod.enter_position(conn, plan, config, b, entry_session=day, advice_params=advice_params)
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
            body_strike=plan["body_strike"],
            near_strike=plan["near_strike"],
            far_strike=plan["far_strike"],
            credit=plan["credit"],
        )
        db.record_decision(
            conn,
            trade_date=day,
            book=b,
            symbol=symbol,
            mode="entry",
            reason=f"entered {plan['near_strike']:g}/{plan['body_strike']:g}x2/{plan['far_strike']:g} credit {plan['credit']:.2f}",
            accepted=True,
        )
        _log(
            f"[{b}] {symbol}: entered BWB {plan['near_strike']:g}/{plan['body_strike']:g}x2/{plan['far_strike']:g} "
            f"({plan['expiration']}) credit {plan['credit']:.2f}"
        )
    return opened_count


def _legs_with_symbol(conn, position: dict) -> list[dict]:
    """A position's open legs, each carrying `position_symbol`.

    `bwb_legs` has no symbol column — the underlying lives on the position — and
    `provider.build_mark_snapshot` resolves spot from `legs[0]["position_symbol"]`. The marks path
    set that and the trigger-tick path did not, so every tick row recorded a NULL spot and therefore
    `measured = 0`, for four sessions, while `bwb_marks` beside it was perfectly healthy. One helper
    both callers use, so the two cannot drift apart again.
    """
    legs = db.open_legs_for(conn, position["position_id"])
    for leg in legs:
        leg["position_symbol"] = position["symbol"]
    return legs


# --------------------------------------------------------------------------- trigger ticks (the second product)
def _record_trigger_ticks(config: dict, conn, *, cache_path: str, when: datetime, day: str) -> int:
    """One row per open COHORT (entry_session x structure_signature) — shared across every base
    book's positions in that cohort."""
    defaults = config.get("defaults") or {}
    symbol = _symbol(config)
    root = config.get("occ_root") or symbol
    max_age = defaults.get("max_quote_age_seconds", 300)
    ts = time.time()
    written = 0

    cohorts: dict[tuple[str, str], dict] = {}
    for position in db.open_positions(conn):
        key = (position["entry_session"], position["structure_signature"])
        cohorts.setdefault(key, position)  # any position in the cohort carries the shared strikes

    for (entry_session, sig), position in cohorts.items():
        legs = _legs_with_symbol(conn, position)
        near_leg = next((leg for leg in legs if leg["leg_role"] == "near_long"), None)
        mark = provider.build_mark_snapshot(cache_path, legs, when=when, max_quote_age_seconds=max_age)
        near_abs_delta = None
        if near_leg is not None:
            g = _read_greek(cache_path, near_leg["streamer_symbol"], max_age_seconds=max_age * 6)
            if g is not None and g.get("delta") is not None:
                near_abs_delta = abs(g["delta"])
        flip = provider.gamma_flip_reading(
            cache_path, symbol, position["expiration"], root, max_age_seconds=max_age
        )
        # Recorded as two facts as well as their AND: `measured` is what the replay gates on, but a
        # tick with spot and no flip is a different outage from a tick with neither, and one flag
        # cannot say which. Both were broken at once in the 2026-08-24..27 rows, and the single
        # flag showed that as one undifferentiated wall of refusals.
        spot_measured = mark.get("spot") is not None
        flip_measured = bool(flip.get("ok"))
        measured = spot_measured and flip_measured
        row = {
            "entry_session": entry_session,
            "structure_signature": sig,
            "symbol": symbol,
            "ticked_at": ts,
            "session_date": day,
            "near_abs_delta": near_abs_delta,
            "peak_abs_delta": position.get("peak_abs_delta"),
            "spot": mark.get("spot"),
            "gamma_flip": flip.get("gamma_flip") if flip.get("ok") else None,
            "gamma_flip_basis": provider.GAMMA_FLIP_BASIS if flip.get("ok") else None,
            "below_flip_seen": 1 if position.get("below_flip_seen") else 0,
            "addon_short_bid": None,
            "addon_short_ask": None,
            "addon_long_bid": None,
            "addon_long_ask": None,
            "measured": 1 if measured else 0,
            "spot_measured": 1 if spot_measured else 0,
            "flip_measured": 1 if flip_measured else 0,
            # Name the half that actually failed. Previously the flip reason won whenever both were
            # absent, so a NULL spot hid behind a GEX message for four sessions.
            "refusal": None
            if measured
            else "; ".join(
                filter(
                    None,
                    [
                        None if spot_measured else f"spot: {mark.get('reason') or 'no_spot_price'}",
                        None if flip_measured else f"flip: {flip.get('reason') or 'no_flip'}",
                    ],
                )
            ),
        }
        db.record_trigger_tick(conn, row)
        written += 1
    return written


# --------------------------------------------------------------------------- mark + manage
def _mark_positions(config: dict, conn, *, cache_path: str, when: datetime, day: str) -> tuple[int, dict]:
    written = 0
    values: dict[str, dict] = {}
    ts = time.time()
    defaults = config.get("defaults") or {}
    max_age = defaults.get("max_quote_age_seconds", 300)
    root = config.get("occ_root") or _symbol(config)
    symbol = _symbol(config)

    for position in db.open_positions(conn):
        legs = _legs_with_symbol(conn, position)
        snapshot = provider.build_mark_snapshot(cache_path, legs, when=when, max_quote_age_seconds=max_age)
        params = management.effective_params(position, config)
        quotes = snapshot.get("quotes") or {}

        # The near wing's delta — pulled once per position from a lightweight greeks read, since
        # `build_mark_snapshot` only carries quotes.
        near_leg = next((leg for leg in legs if leg["leg_role"] == "near_long"), None)
        near_abs_delta = None
        if near_leg is not None:
            g = _read_greek(cache_path, near_leg["streamer_symbol"], max_age_seconds=max_age * 6)
            if g is not None and g.get("delta") is not None:
                near_abs_delta = abs(g["delta"])

        flip = provider.gamma_flip_reading(
            cache_path, symbol, position["expiration"], root, max_age_seconds=max_age
        )

        tick = {
            "abs_delta": near_abs_delta,
            "spot": snapshot.get("spot"),
            "gamma_flip": flip.get("gamma_flip") if flip.get("ok") else None,
        }
        trigger_state = {
            "peak_abs_delta": position.get("peak_abs_delta"),
            "below_flip_seen": bool(position.get("below_flip_seen")),
        }

        close_cost = engine.close_cost(
            [
                {"action": leg["action"], "mid": (quotes.get(leg["streamer_symbol"]) or {}).get("mid")}
                for leg in legs
            ]
        )

        addon_credit = None
        addon_block = None
        if position.get("armed_at") and not position.get("addon_fired_at"):
            addon_snap = _addon_snapshot(cache_path, symbol, position, when, max_age, root=root)
            if addon_snap.get("ok"):
                addon_plan = engine.plan_addon(addon_snap, position["far_strike"], params)
                if addon_plan.get("ok"):
                    addon_credit = addon_plan.get("plan", {}).get("credit")
                else:
                    addon_block = addon_plan.get("reason")
            else:
                # WHY the add-on could not be priced, kept rather than discarded. An armed position
                # that cannot price produces a `hold`, and holds are not recorded — so "waiting for
                # a credit" and "cannot read the chain at all" looked identical. The second was
                # true for every armed tick until 2026-08-27 (`not_root_listed`, the OCC-root bug
                # above) and left no trace anywhere in the ledger.
                addon_block = addon_snap.get("reason")

        for leg in legs:
            quote = quotes.get(leg["streamer_symbol"])
            db.record_mark(
                conn,
                position_id=position["position_id"],
                leg_role=leg["leg_role"],
                marked_at=ts,
                session_date=day,
                bid=quote["bid"] if quote else None,
                ask=quote["ask"] if quote else None,
                mid=quote["mid"] if quote else None,
                spot=snapshot.get("spot"),
                close_cost=close_cost,
                quote_age_s=quote["age_seconds"] if quote else None,
                usable=1 if quote else 0,
                refusal=None if quote else (snapshot.get("reason") or "missing_leg_quotes"),
            )
            written += 1

        values[position["position_id"]] = {
            "position": position,
            "snapshot": snapshot,
            "params": params,
            "close_cost": close_cost,
            "trigger_state": trigger_state,
            "tick": tick,
            "addon_credit": addon_credit,
            "addon_block": addon_block,
        }
    return written, values


def _read_greek(cache_path: str, streamer_symbol: str, *, max_age_seconds: float) -> dict | None:
    from pathlib import Path

    from cherrypick.core.db import connect_ro

    path = Path(cache_path)
    if not path.exists():
        return None
    conn = connect_ro(path)
    try:
        g = provider._greeks(conn, [streamer_symbol], now_ts=time.time(), max_age_seconds=max_age_seconds)
        return g.get(streamer_symbol)
    finally:
        conn.close()


def _addon_snapshot(
    cache_path: str, symbol: str, position: dict, when: datetime, max_age: float, *, root: str | None = None
) -> dict:
    """A lightweight entry-shaped snapshot (chain+quotes+greeks) for re-pricing the add-on against
    the position's own expiration/root, reusing `build_entry_snapshot`'s chain read.

    **`root` is the OCC root and is NOT the symbol.** This resolved `root = symbol` until
    2026-08-27, so every add-on lookup asked the cache for `SPX`-rooted contracts while SPX's
    weeklies are listed as `SPXW` — `not_root_listed`, on every tick, for every armed position. The
    flip book armed all four of its positions the moment the gamma flip became measurable and then
    sat unable to price a single one. Every other snapshot in this module already resolves
    `config.get("occ_root") or symbol`; this was the one that did not.
    """
    plan = {"expiration": position["expiration"], "dte": 0}
    snap = provider.build_entry_snapshot(
        cache_path, symbol, plan, root=root or symbol, when=when, max_quote_age_seconds=max_age
    )
    return snap


def _manage_positions(config: dict, conn, values: dict, *, cache_path: str, when: datetime, day: str) -> int:
    actions = 0
    for pid, state in values.items():
        position = state["position"]
        if position["status"] != "open":
            continue
        params = state["params"]
        decision, latches = management.evaluate(
            position,
            params,
            trigger_state=state["trigger_state"],
            tick=state["tick"],
            addon_credit=state["addon_credit"],
        )
        bookmod.update_latches(
            conn,
            position,
            peak_abs_delta=latches["peak_abs_delta"],
            below_flip_seen=latches["below_flip_seen"],
        )
        if not decision.acts:
            # An armed position that cannot price its add-on is a REFUSAL, not silence. Collapsed
            # per (date, book, reason), so a whole session of it costs one counted row.
            block = state.get("addon_block")
            if block and position.get("armed_at") and not position.get("addon_fired_at"):
                db.record_decision(
                    conn,
                    trade_date=day,
                    book=position["book"],
                    symbol=position["symbol"],
                    mode="addon",
                    reason=f"addon_blocked:{block}",
                    accepted=0,
                )
            continue
        gate = management.execution_gate(state["snapshot"], params, now=clock.now_et())
        executed = 0
        if decision.action == "arm":
            bookmod.arm(conn, position, reason=decision.reason)
            executed = 1
            actions += 1
            _log(f"[{position['book']}] {pid} armed — {decision.reason}")
        elif decision.action == "fire_addon" and gate is None:
            snap = _addon_snapshot(
                cache_path,
                _symbol(config),
                position,
                when,
                params.get("max_quote_age_seconds", 300),
                root=config.get("occ_root") or _symbol(config),
            )
            if snap.get("ok"):
                addon_plan = engine.plan_addon(snap, position["far_strike"], params)
                if addon_plan.get("ok"):
                    bookmod.fire_addon(conn, position, addon_plan["plan"], config)
                    executed = 1
                    actions += 1
                    _log(
                        f"[{position['book']}] {pid} add-on fired — credit {addon_plan['plan']['credit']:.2f}"
                    )
                else:
                    gate = addon_plan.get("reason")
            else:
                gate = snap.get("reason")
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
        "SELECT COUNT(*) FROM bwb_positions WHERE entry_session = ?", (today,)
    ).fetchone()[0]
    open_now = db.open_positions(conn)
    return {
        "ok": True,
        "date": today,
        "in_session": in_session(clock.minute_of_day(when)),
        "session_settled": not expiring,
        "positions_today": entered_today,
        "open_positions": len(open_now),
        "target_expiration": clock.target_expiration(when.date(), config.get("defaults") or {}),
        "stream_cache": cache_path,
        "stream_cache_present": os.path.exists(cache_path),
    }


def _beat() -> None:
    try:
        path = _home.heartbeat_path("bwb")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now().astimezone().isoformat(), encoding="utf-8")
    except OSError:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cherrypick-bwb paper session driver")
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
    db_path = args.db or os.environ.get("BWB_DB_PATH") or db.default_db_path()
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
