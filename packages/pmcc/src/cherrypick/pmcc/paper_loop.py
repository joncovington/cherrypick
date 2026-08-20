"""Paper session driver — mark, manage, enter when capital frees up, settle at the bell.

This is the only file in the module that touches the clock or the filesystem-of-record. Everything
it decides is decided by `engine.py`/`management.py`; this layer supplies snapshots and persists
what came back. That split is what makes the strategy testable, and it is also the suite guardrail:
no network, no MCP, no model call anywhere on a decision path.

One `run_once` carries the whole position lifecycle, gated by the clock rather than by the schedule
(the flies rule: the schedule carries no session logic, so it can never disagree with the engine
about when the day starts or ends):

- Any trading day, in session: mirror the daily bars (keltner substrate), mark every open leg every
  tick, then run management on what the gates allow — the tv-exhausted both-legs close, the roll
  book's breach roll, the covered-call hold.
- Past the disposition time: cover shares delivered by an earlier session's assignment, then sell
  the orphan longs of short-settled positions — oldest obligation first, before this tick can enter
  anything new.
- Inside the entry window, per (symbol, book) with no open position and headroom under
  `max_positions`: plan and enter. `control` and `roll` enter from the SAME plan on the same tick
  (the roll experiment's exact pairing); `keltner` enters only when its pullback-and-reversal gate
  passes, which is its whole variable.
- Past the settle time on any day legs expire: settle them off a staleness-gated spot read. A
  missed settlement day is NOT settled late against a later print — the cache keeps no history, so
  that needs `--settle --date --price` with the official print, and the loop says so rather than
  guessing.
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

from cherrypick.pmcc import book as bookmod
from cherrypick.pmcc import cli as climod
from cherrypick.pmcc import clock, db, engine, keltner, management, provider, stream_request

RTH_OPEN_MIN = 9 * 60 + 30
RTH_CLOSE_MIN = 16 * 60
DEFAULT_SETTLE_MIN = 16 * 60 + 20

_logger = logging.getLogger("pmcc_paper_loop")


def stream_cache_path(config: dict) -> str:
    """The suite's canonical shared stream cache, read-only here. Config first, then the managed
    home — portable paths only."""
    configured = (config.get("source") or {}).get("stream_cache_db")
    if configured:
        return os.path.expanduser(os.path.expandvars(configured))
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "marketdata", "stream_cache.db")


def log_file():
    """Resolved on every call, never at import — a module-level constant would capture the real
    home before any test could redirect it (the flies lesson)."""
    return _home.logs_dir("pmcc") / "pmcc_paper.log"


def _log(message: str) -> None:
    _logs.configure(_logger, log_file())
    _logger.info(message)


def in_session(now_min: int) -> bool:
    return RTH_OPEN_MIN <= now_min < RTH_CLOSE_MIN


def settle_time_min(config: dict) -> int:
    return clock.hhmm_to_min((config.get("defaults") or {}).get("settle_time"), DEFAULT_SETTLE_MIN)


def _symbols(config: dict) -> list[str]:
    return [s.strip().upper() for s in (config.get("symbols") or ["TNA", "TQQQ", "UPRO"])]


# --------------------------------------------------------------------------- loop lock + cadence
def _paper_data_dir() -> str:
    return os.path.dirname(os.environ.get("PMCC_DB_PATH") or db.default_db_path())


def _loop_lock_path() -> str:
    return os.path.join(_paper_data_dir(), "paper_loop.lock")


def _pid_alive(pid: int) -> bool:
    """The settled probe chain (psutil → Win32 OpenProcess → os.kill last)."""
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore

        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass
    try:
        import ctypes

        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except (OSError, SystemError):
            return False


def _acquire_loop_lock(stale_seconds: int = 180) -> bool:
    """Single-instance guard shared by `--interval` and `--once`, so the supervised resident loop
    and an off-session/manual `--once` can never iterate the same book concurrently. A held-but-
    ALIVE lock is never stolen regardless of age; the mtime fallback applies only when the holder's
    PID is unreadable."""
    path = _loop_lock_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            with open(path, encoding="utf-8") as fh:
                holder = int(fh.read().strip())
        except (OSError, ValueError):
            holder = None
        if holder is not None and _pid_alive(holder):
            return False
        try:
            if holder is not None or time.time() - os.path.getmtime(path) > stale_seconds:
                os.unlink(path)
                return _acquire_loop_lock(stale_seconds)
        except OSError:
            pass
        return False


def _release_loop_lock() -> None:
    try:
        os.unlink(_loop_lock_path())
    except OSError:
        pass


def _note_cadence_change(conn, interval_seconds: int) -> None:
    """Journal a tick-cadence change as a measurement break: the mark path's resolution decides how
    finely the tv-exhaustion trigger and the exposure telemetry sample, so pre/post-change numbers
    are not comparable. Keyed off a small state file so the row is written exactly once."""
    try:
        state_path = os.path.join(_paper_data_dir(), "tick_cadence.json")
        prev = None
        try:
            with open(state_path, encoding="utf-8") as fh:
                prev = int(json.load(fh).get("seconds"))
        except (OSError, ValueError, TypeError):
            pass
        if prev == interval_seconds:
            return
        day = clock.today_iso()
        if prev is not None:
            db.record_measurement_break(
                conn,
                break_date=day,
                key="tick_cadence",
                old_value=str(prev),
                new_value=str(interval_seconds),
                note="mark-path resolution changed; tv-trigger and exposure telemetry not comparable across this date",
            )
            _log(f"tick cadence changed {prev}s -> {interval_seconds}s — journaled as a measurement break")
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump({"seconds": interval_seconds, "since": day}, fh)
    except Exception as exc:  # noqa: BLE001 — never let telemetry break the loop
        _log(f"cadence-change journaling failed (non-fatal): {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- advised book
def _advice_decision_path() -> str:
    return os.path.join(_paper_data_dir(), "advice_active.json")


def advice_decision(config: dict, today: str) -> dict:
    """Today's advice decision, derived ONCE per session and replayed thereafter (the flies
    read-once rule: advice can never start, stop, or change mid-session across `--once`
    processes).

    The mechanics live in `cherrypick.core.advice.session_decision` — three modules had written this
    read-once-and-replay identically, and it carries a safety property rather than a convenience.
    """
    return _core_advice.session_decision(
        _home.state_dir(),
        "pmcc",
        today,
        config,
        _advice_decision_path(),
        base_key="base_book",
        log=_log,
    )


def session_books(config: dict, today: str) -> tuple[list[str], dict | None]:
    """(the books entry may open today, the admitted advice params or None). The roster only
    matters at ENTRY — marking, management, disposition, and settlement all iterate open positions
    from the ledger whatever their book tag, so a book once opened can never be stranded by a later
    roster change."""
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
    """One iteration. Owns the whole lifecycle's phase logic — there is exactly one thing to
    schedule and one thing that can fail."""
    when = when or clock.now_et()
    now_min = clock.minute_of_day(when)
    today = when.date()
    day = today.isoformat()

    if not force and not _cal.is_trading_day(today):
        return {"ok": True, "skipped": "not_a_trading_day", "date": day}

    # Settlement before the RTH gate (the settle time is after the close). Only ever settles legs
    # expiring TODAY: a leg whose expiration already passed cannot be honestly priced from a cache
    # that keeps no history, so it is flagged for a manual `--settle --date --price` instead.
    overdue = _overdue_legs(conn, day)
    if overdue and now_min % 60 < 2:
        _log(
            f"{len(overdue)} leg(s) past expiration remain open — settle manually with "
            f"--settle --date <expiration> --price <official print>"
        )
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

    # Phase: mirror the daily bars — the keltner substrate accumulates whether or not anything
    # trades today. Telemetry class, never costs the tick.
    keltner.upsert_daily_bars(conn, cache_path, _symbols(config))

    # Phase: disposals — oldest obligation first, before this tick can enter anything new.
    # Delivered shares go first and on EVERY session: a short-call assignment hands them over at
    # Friday's settlement and the account carries them (short) until covered.
    disposition_min = clock.hhmm_to_min(defaults.get("disposition_time"), 9 * 60 + 45)
    if now_min >= disposition_min:
        actions += _dispose_shares(config, conn, cache_path=cache_path, when=when, day=day)
        actions += _dispose_longs(config, conn, cache_path=cache_path, when=when, day=day)

    # Phase: entries, any trading day inside the window, per (symbol, book) with headroom.
    window_start = clock.hhmm_to_min(defaults.get("entry_window_start"), 10 * 60)
    window_end = clock.hhmm_to_min(defaults.get("entry_window_end"), 15 * 60 + 30)
    if window_start <= now_min <= window_end:
        phase = "entry"
        actions += _try_entries(config, conn, cache_path=cache_path, when=when, day=day)

    # Phase: mark everything open, every tick — the exposure telemetry's substrate — then manage.
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
    )
    return {"ok": True, "phase": phase, "open_positions": len(values), "actions": actions}


def _overdue_legs(conn, day: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT l.position_id, l.leg_role, l.expiration FROM pmcc_legs l "
            "JOIN pmcc_positions p ON p.position_id = l.position_id "
            "WHERE l.status = 'open' AND l.expiration < ? AND p.status != 'closed'",
            (day,),
        )
    ]


def _unsettled_today(conn, day: str) -> bool:
    return bool(db.expiring_open_legs(conn, day))


# --------------------------------------------------------------------------- entry
def _entry_guards(config: dict, symbol: str, plan_dates: dict, day: str) -> str | None:
    """The pre-snapshot refusals for one symbol: settlement declaration and the dividend span over
    the short leg's life. Returns the refusal reason or None."""
    style = engine.settlement_style(config, symbol)
    if style is None:
        return "unknown_settlement"
    if style == "physical":
        if not engine.dividend_coverage_ok(config, symbol, plan_dates["short_expiration"]):
            return "dividend_calendar_lapsed"
        hit = engine.ex_date_in_span(config, symbol, day, plan_dates["short_expiration"])
        if hit is not None:
            return "ex_dividend_span"
    return None


def _keltner_state(config: dict, conn, cache_path: str, symbol: str, day: str, spot: float | None) -> dict:
    """The keltner channel + gate verdict for one symbol at the current spot. Measures come back
    whatever the verdict, and are stamped on EVERY book's entries."""
    params = {**keltner.PARAM_DEFAULTS, **engine.merged_params(config, "keltner")}
    bars = keltner.completed_bars(conn, symbol, day)
    chan = keltner.channel(bars, params)
    session = provider.read_session(cache_path, symbol, day) or {}
    if spot is None:
        return {"ok": False, "reason": "no_spot_price", "measures": {}}
    return keltner.entry_ok(
        spot,
        chan,
        prev_close=session.get("prev_day_close"),
        day_low=session.get("day_low"),
        params=params,
    )


def _try_entries(config: dict, conn, *, cache_path: str, when: datetime, day: str) -> int:
    books, advice_params = session_books(config, day)
    defaults = config.get("defaults") or {}
    max_positions = int(defaults.get("max_positions", 3))
    opened_count = 0
    plan_dates = clock.expiration_plan(when.date(), defaults)

    for symbol in _symbols(config):
        wanting = [
            b
            for b in books
            if db.open_position_for(conn, symbol, b) is None
            and db.open_position_count(conn, b) < max_positions
        ]
        if not wanting:
            continue
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
            continue

        for b in list(wanting):
            reason = _entry_guards(config, symbol, plan_dates, day)
            if reason is not None:
                wanting.remove(b)
                db.record_entry_attempt(
                    conn,
                    trade_date=day,
                    symbol=symbol,
                    book=b,
                    outcome=reason,
                )
                db.record_decision(
                    conn,
                    trade_date=day,
                    book=b,
                    symbol=symbol,
                    mode="entry",
                    reason=reason,
                    accepted=False,
                )
        if not wanting:
            continue

        root = ((config.get("occ_roots") or {}).get(symbol)) or symbol
        snapshot = provider.build_entry_snapshot(
            cache_path,
            symbol,
            plan_dates,
            root=root,
            when=when,
            **provider.snapshot_kwargs(config),
            deep_window_pct=defaults.get("deep_window_pct", provider.DEFAULT_DEEP_WINDOW_PCT),
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
                db.record_entry_attempt(
                    conn,
                    trade_date=day,
                    symbol=symbol,
                    book=b,
                    outcome=snapshot["reason"],
                )
                db.record_decision(
                    conn,
                    trade_date=day,
                    book=b,
                    symbol=symbol,
                    mode="entry",
                    reason=snapshot["reason"],
                    accepted=False,
                )
            continue
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

        # The keltner read happens once per symbol per tick, at the snapshot's spot — measures for
        # every book's rows, a gate only for the keltner book.
        kel = _keltner_state(config, conn, cache_path, symbol, day, snapshot["spot"])

        # One plan for the base books (control/roll/keltner share control's entry params — the
        # exact-pairing property); the advised book plans separately when its overlay touches entry.
        base_params = {**management.PARAM_DEFAULTS, **engine.merged_params(config, "control")}
        planned = engine.plan_entry(snapshot, base_params)
        plans: dict[str, dict] = {}
        for b in wanting:
            if b.startswith("advised:") and advice_params:
                adv_params = {**base_params, **advice_params}
                plans[b] = engine.plan_entry(snapshot, adv_params)
            else:
                plans[b] = planned

        for b in wanting:
            result = plans[b]
            if b == "keltner" and not kel.get("ok"):
                db.record_entry_attempt(
                    conn,
                    trade_date=day,
                    symbol=symbol,
                    book=b,
                    outcome=kel["reason"],
                    spot=snapshot["spot"],
                )
                db.record_decision(
                    conn,
                    trade_date=day,
                    book=b,
                    symbol=symbol,
                    mode="entry",
                    reason=kel["reason"],
                    accepted=False,
                )
                continue
            if not result.get("ok"):
                db.record_entry_attempt(
                    conn,
                    trade_date=day,
                    symbol=symbol,
                    book=b,
                    outcome=result["reason"],
                    block_detail=result.get("detail"),
                    spot=snapshot["spot"],
                    target_yield=base_params.get("target_weekly_yield_min", 0.012),
                    best_yield=result.get("best_yield"),
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
            opened = bookmod.enter_position(
                conn,
                plan,
                config,
                b,
                entry_session=day,
                advice_params=advice_params,
                keltner_measures=kel.get("measures"),
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
                target_yield=base_params.get("target_weekly_yield_min", 0.012),
                achieved_yield=plan["weekly_yield_pct"],
                long_strike=plan["long_strike"],
                short_strike=plan["short_strike"],
                net_debit=plan["net_debit"],
                protection_pct=plan["downside_protection_pct"],
            )
            db.record_decision(
                conn,
                trade_date=day,
                book=b,
                symbol=symbol,
                mode="entry",
                reason=(
                    f"entered {plan['long_strike']:g}/{plan['short_strike']:g} "
                    f"debit {plan['net_debit']:.2f} tv {plan['net_tv']:.2f} "
                    f"yield {plan['weekly_yield_pct']:.2%} protection {plan['downside_protection_pct']:.1%}"
                ),
                accepted=True,
            )
            _log(
                f"[{b}] {symbol}: entered long {plan['long_strike']:g} ({plan['long_expiration']}) / "
                f"short {plan['short_strike']:g} ({plan['short_expiration']}) — "
                f"debit {plan['net_debit']:.2f}, net TV {plan['net_tv']:.2f}, "
                f"weekly yield {plan['weekly_yield_pct']:.2%}, protection {plan['downside_protection_pct']:.1%}, "
                f"long by {plan['long_selected_by']}"
            )
    return opened_count


# --------------------------------------------------------------------------- mark + manage
def _mark_positions(config: dict, conn, *, cache_path: str, when: datetime, day: str) -> tuple[int, dict]:
    """Mark every open leg of every open position; returns (rows written, {position_id: state}).
    Refusals are rows too — a stalled feed and a quiet market must never look identical. The short
    leg's rows carry `short_tv` and the `assignment_exposed` flag, the module's measurement of the
    early-assignment region it deliberately does not model."""
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
        short_tv = None
        exposed = False
        for leg in legs:
            quote = (snapshot.get("quotes") or {}).get(leg["streamer_symbol"])
            greeks = (snapshot.get("greeks") or {}).get(leg["streamer_symbol"]) or {}
            is_short = leg["leg_role"] != "long_call"
            leg_tv = None
            leg_exposed = None
            if is_short and quote is not None and spot is not None:
                leg_tv = engine.short_time_value(quote["mid"], spot, leg["strike"])
                leg_exposed = 1 if management.assignment_exposed(leg_tv, params) else 0
                short_tv = leg_tv
                exposed = bool(leg_exposed)
            db.record_mark(
                conn,
                position_id=position["position_id"],
                leg_role=leg["leg_role"],
                marked_at=ts,
                session_date=day,
                bid=quote["bid"] if quote else None,
                ask=quote["ask"] if quote else None,
                mid=quote["mid"] if quote else None,
                delta=greeks.get("delta"),
                iv=greeks.get("iv"),
                vega=greeks.get("vega"),
                spot=spot,
                short_tv=leg_tv,
                assignment_exposed=leg_exposed,
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
            except Exception:  # noqa: BLE001, S110 — the marks table is the durable record
                pass
        values[position["position_id"]] = {
            "position": position,
            "snapshot": snapshot,
            "params": params,
            "short_tv": short_tv,
        }
    return written, values


def _manage_positions(config: dict, conn, values: dict, *, cache_path: str, when: datetime, day: str) -> int:
    """Evaluate every OPEN position against its own effective params, then act through the gate.
    A blocked or refused action is still an event row (`executed=0` with the gate) — the only
    record that it was SEEN before it was allowed."""
    actions = 0
    for pid, state in values.items():
        position = state["position"]
        if position["status"] != "open":
            continue  # short_settled positions belong to the disposition phase
        params = state["params"]
        decision = management.evaluate(
            position,
            params,
            now=when,
            short_tv=state["short_tv"],
            spot=state["snapshot"].get("spot"),
            rolled_today=db.rolled_today(conn, pid, day),
        )
        if not decision.acts:
            continue
        gate = management.execution_gate(state["snapshot"], params, now=when)
        executed = 0
        detail = dict(decision.detail)
        if gate is None:
            if decision.action == "close_all":
                result = bookmod.close_open_legs(
                    conn, position, state["snapshot"], config, reason=decision.reason, session_date=day
                )
                executed = 1 if result.get("ok") else 0
                if not result.get("ok"):
                    gate = result.get("reason")
                else:
                    actions += 1
                    _log(f"[{position['book']}] {pid} closed — {decision.reason}")
            elif decision.action == "roll_short":
                rolled, gate, detail = _roll_position(
                    config, conn, position, state, cache_path=cache_path, when=when, day=day
                )
                executed = 1 if rolled else 0
                actions += 1 if rolled else 0
        db.record_management_event(
            conn,
            position_id=pid,
            occurred_at=time.time(),
            session_date=day,
            action=decision.action,
            reason=decision.reason,
            executed=executed,
            gate=gate,
            detail_json=json.dumps(detail) if detail else None,
        )
    return actions


def _roll_position(
    config: dict, conn, position: dict, state: dict, *, cache_path: str, when: datetime, day: str
) -> tuple[bool, str | None, dict]:
    """Execute one breach roll: pick the landing expiration, snapshot its deep window, plan, book.
    Returns (rolled, gate-or-refusal, detail). A refusal leaves the position holding like a covered
    call — the verdict repeats next tick."""
    params = state["params"]
    target = clock.roll_expiration(when.date(), position["long_expiration"], params)
    if target is None:
        return False, "no_roll_expiration", {}
    symbol = position["symbol"]
    root = ((config.get("occ_roots") or {}).get(symbol)) or symbol
    defaults = config.get("defaults") or {}
    snapshot = provider.build_roll_snapshot(
        cache_path,
        symbol,
        target,
        root=root,
        when=when,
        **provider.snapshot_kwargs(config),
        deep_window_pct=defaults.get("deep_window_pct", provider.DEFAULT_DEEP_WINDOW_PCT),
    )
    if not snapshot.get("ok"):
        return False, snapshot["reason"], {"target": target}
    # The buyback quote comes from the MARK snapshot (the old short may sit on a different
    # expiration than the roll target's chain); inject it so plan_roll prices both sides.
    short_legs = [
        leg for leg in db.open_legs_for(conn, position["position_id"]) if leg["leg_role"] != "long_call"
    ]
    if not short_legs:
        return False, "no_open_short", {}
    old = short_legs[0]
    mark_quote = (state["snapshot"].get("quotes") or {}).get(old["streamer_symbol"])
    if mark_quote is None:
        return False, "missing_leg_quotes", {}
    snapshot["quotes"][old["streamer_symbol"]] = mark_quote
    roll = engine.plan_roll(snapshot, position, old, params)
    if not roll.get("ok"):
        return False, roll["reason"], {"target": target, "best_yield": roll.get("best_yield")}
    result = bookmod.roll_short_leg(conn, position, roll, config, session_date=day)
    if not result.get("ok"):
        return False, result.get("reason"), {}
    detail = {
        "old_strike": result["old_strike"],
        "new_strike": result["new_strike"],
        "old_expiration": result["old_expiration"],
        "new_expiration": result["new_expiration"],
        "net_roll_credit": result["net_roll_credit"],
    }
    _log(
        f"[{position['book']}] {position['position_id']} rolled short "
        f"{result['old_strike']:g} ({result['old_expiration']}) -> "
        f"{result['new_strike']:g} ({result['new_expiration']}), "
        f"net credit {result['net_roll_credit']:+.2f}"
    )
    return True, None, detail


# --------------------------------------------------------------------------- disposition
def _dispose_longs(config: dict, conn, *, cache_path: str, when: datetime, day: str) -> int:
    """Sell the surviving long of every short-settled position — the combined-disposal half that
    the option ledger sees. It runs the session AFTER the short settled (settlement is post-close,
    so a Friday assignment reaches here Monday morning), alongside `_dispose_shares` covering the
    delivered shares — together they are the 'both legs closed at assignment' model."""
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
                conn, position, snapshot, config, reason="long_disposition", session_date=day
            )
            executed = 1 if result.get("ok") else 0
            if not result.get("ok"):
                gate = result.get("reason")
            else:
                actions += 1
                _log(f"[{position['book']}] {position['position_id']} long disposed")
        db.record_management_event(
            conn,
            position_id=position["position_id"],
            occurred_at=time.time(),
            session_date=day,
            action="close_all",
            reason="long_disposition",
            executed=executed,
            gate=gate,
        )
    return actions


def _dispose_shares(config: dict, conn, *, cache_path: str, when: datetime, day: str) -> int:
    """Cover every share position a physically-settled expiry delivered on an EARLIER session.

    Shares handed over by tonight's settlement cannot be covered tonight, so `before_session=day`
    is the rule rather than an optimisation — and the interval it creates, Friday's settlement to
    the next session's cover, is precisely the weekend exposure the strategy's paper result must
    carry. It is left visible in the ledger (`assigned_session` against `disposed_session`) instead
    of being netted away.

    A spot that will not print is a refusal, not a guess: the shares stay open and the next tick
    retries. Nothing here is gated on the option execution gate — that gate reads an option
    snapshot, and these are shares.
    """
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
            f"[{assignment['book']}] {assignment['position_id']}: covered "
            f"{assignment['shares']} {assignment['direction']} {symbol} shares at {spot:.2f} "
            f"(assigned {assignment['assigned_session']} at {assignment['basis']:.2f}, "
            f"share P&L {result['share_pnl']:+.2f}, fee {result['fee']:.2f})"
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
    """Settle every open leg expiring `day` (default: today) at the settlement print.

    The print is the last streamed trade, staleness-gated (`settlement_max_age_seconds`) — refused
    rather than settled stale, so the next tick retries and the day settles itself when the feed
    recovers. `--price` overrides with the official print, which is the only honest path for a
    missed settlement day. Under physical settlement the print also sets the delivered shares'
    basis, so a stale one would misprice the weekend leg as well as the option one.
    """
    when = when or clock.now_et()
    day = day or when.date().isoformat()
    out = []
    for symbol in _symbols(config):
        if not any(leg["position_symbol"] == symbol for leg in db.expiring_open_legs(conn, day)):
            continue
        max_age = (config.get("defaults") or {}).get("settlement_max_age_seconds", 300)
        spot = price if price is not None else provider.read_spot(cache_path, symbol, max_age_seconds=max_age)
        if spot is None:
            _log(
                f"{symbol}: cannot settle {day} — no price within {max_age}s "
                f"(feed stale or down). Re-run with --price once it recovers."
            )
            out.append({"symbol": symbol, "ok": False, "reason": "no_settlement_price"})
            continue
        results = bookmod.settle_expiring_legs(conn, day, spot, config, symbol=symbol)
        for result in results:
            _log(
                f"{symbol} {result['position_id']}: settled {result['settled_legs']} leg(s) at "
                f"{spot:.2f} ({result['itm']} ITM, fee {result['fee']:.2f})"
            )
        out.append({"symbol": symbol, "ok": True, "settled": len(results), "spot": spot})
    return {"ok": any(r.get("ok") for r in out) or not out, "results": out, "date": day}


def run_status(config: dict, conn, *, cache_path: str) -> dict:
    """Health view for the orchestrator's watchdog: file-only, no broker, no network.

    `session_settled` is today-scoped: on a day legs expire it means every such leg has been
    settled or closed; any other day it is trivially true. `positions_today` counts what the
    watchdog would care about going unsettled — positions entered today plus positions holding a
    leg that expires today.
    """
    when = clock.now_et()
    today = when.date().isoformat()
    expiring = db.expiring_open_legs(conn, today)
    entered_today = conn.execute(
        "SELECT COUNT(*) FROM pmcc_positions WHERE entry_session = ?", (today,)
    ).fetchone()[0]
    expiring_positions = len({leg["position_id"] for leg in expiring})
    open_now = db.open_positions(conn)

    probe_legs = []
    for position in open_now[:1]:
        probe_legs = db.open_legs_for(conn, position["position_id"])
        for leg in probe_legs:
            leg["position_symbol"] = position["symbol"]
    if probe_legs:
        probe = provider.build_mark_snapshot(
            cache_path, probe_legs, when=when, **provider.snapshot_kwargs(config)
        )
        data_ok, data_reason = bool(probe.get("ok")), probe.get("reason")
    else:
        data_ok, data_reason = (
            os.path.exists(cache_path),
            None if os.path.exists(cache_path) else "stream_cache_missing",
        )

    keltner_days = {}
    for symbol in _symbols(config):
        bars = keltner.completed_bars(conn, symbol, today)
        keltner_days[symbol] = len([b for b in bars if b.get("day_close") is not None])

    return {
        "ok": True,
        "date": today,
        "in_session": in_session(clock.minute_of_day(when)),
        "session_settled": not expiring,
        "positions_today": entered_today + expiring_positions,
        "open_positions": len(open_now),
        "expiration_plan": clock.expiration_plan(when.date(), config.get("defaults") or {}),
        "keltner_days": keltner_days,
        "stream_cache": cache_path,
        "stream_cache_present": os.path.exists(cache_path),
        "data_ok": data_ok,
        "data_reason": data_reason,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cherrypick-pmcc paper session driver")
    ap.add_argument("--config")
    ap.add_argument("--db")
    ap.add_argument("--stream-cache", help="override the shared stream cache path")
    ap.add_argument("--once", action="store_true", help="run a single iteration")
    ap.add_argument("--interval", type=int, metavar="SECONDS", help="run continuously until the close")
    ap.add_argument("--settle", action="store_true", help="settle legs expiring --date (default today)")
    ap.add_argument("--price", type=float, help="explicit settlement price (see --settle)")
    ap.add_argument("--date", help="the expiration day --settle should settle (YYYY-MM-DD)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore the trading-day and RTH gates")
    args = ap.parse_args(argv)

    config = climod.load_config(args.config)
    cache_path = args.stream_cache or stream_cache_path(config)
    db_path = args.db or os.environ.get("PMCC_DB_PATH") or db.default_db_path()
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
            _log("another paper loop holds the lock — exiting")
            print(json.dumps({"ok": True, "skipped": "another paper loop is already running"}))
            return 0
        try:
            _log(f"loop starting, interval {args.interval}s, cache {cache_path}")
            _note_cadence_change(conn, args.interval)
            drift = db.stale_writer_columns(conn)
            if drift:
                _log(
                    f"WARNING: {len(drift)} ledger column(s) this checkout will never write — "
                    f"{', '.join(drift)}. The running code is older than the database schema. "
                    "Check the branch."
                )
            while args.force or in_session(clock.minute_of_day(clock.now_et())):
                try:
                    run_once(config, conn, cache_path=cache_path, force=args.force)
                except Exception as exc:  # noqa: BLE001 — a transient failure costs one tick, not the session
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
