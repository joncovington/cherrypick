"""Paper session driver — mark, manage, enter on the entry day, settle at the bell.

This is the only file in the module that touches the clock or the filesystem-of-record. Everything
it decides is decided by `engine.py`/`management.py`; this layer supplies snapshots and persists
what came back. That split is what makes the strategy testable, and it is also the suite guardrail:
no network, no MCP, no model call anywhere on a decision path.

One `run_once` carries the whole week's shape, gated by the clock rather than by the schedule (the
flies rule: the schedule carries no session logic, so it can never disagree with the engine about
when the day starts or ends):

- Any trading day, in session: mark every open leg every tick (the exit study's substrate), then
  run management on what the gates allow.
- The entry day (Monday, or Tuesday after a Monday holiday), inside the entry window: plan and
  open the week's double calendar in every session book.
- The back expiration's morning (Monday): dispose week N−1's surviving longs — before that same
  tick can open week N, so the two never contend.
- Past the settle time on any day legs expire: cash-settle them off a staleness-gated spot read.
  A missed settlement day (the loop was down through Friday evening) is NOT settled late against
  Monday's print — the cache keeps no history, so that needs `--settle --date --price` with the
  official print, and the loop says so rather than guessing.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime

from cherrypick.core import calendar as _cal
from cherrypick.core import home as _home
from cherrypick.core import logs as _logs

from cherrypick.calendars import book as bookmod
from cherrypick.calendars import cli as climod
from cherrypick.calendars import clock, db, engine, management, provider, stream_request

RTH_OPEN_MIN = 9 * 60 + 30
RTH_CLOSE_MIN = 16 * 60
DEFAULT_SETTLE_MIN = 16 * 60 + 20

_logger = logging.getLogger("calendars_paper_loop")


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
    return _home.logs_dir("calendars") / "calendars_paper.log"


def _log(message: str) -> None:
    _logs.configure(_logger, log_file())
    _logger.info(message)


def heartbeat_file():
    """Resolved on every call for the same reason as `log_file` — never captured at import."""
    return _home.heartbeat_path("calendars")


def _beat() -> None:
    """Publish liveness: this loop reached the top of a tick.

    The supervisor restarts a resident job whose liveness signal goes quiet, and until 2026-08-17
    that signal was this module's LOG. Every line this loop writes is event-driven, so a week holding
    no position wrote nothing and looked exactly like a wedged process — the supervisor killed and
    restarted it every two minutes for four days (107 times on 08-17), costing ~28-61% of the
    session's ticks to restart gaps of up to ten minutes.

    So liveness is published rather than inferred, the way the console already does it. This is
    touched at the TOP of the tick, before any branch: it must mean "the loop is turning over" and
    nothing about what the tick then decided to do. Failure to write it is swallowed — a heartbeat
    that costs a tick would be worse than the problem it solves — and a missing file is not judged
    silent by the supervisor, so the degrade is safe in both directions.
    """
    try:
        path = heartbeat_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(clock.now_iso(), encoding="utf-8")
    except OSError:
        pass


def in_session(now_min: int) -> bool:
    return RTH_OPEN_MIN <= now_min < RTH_CLOSE_MIN


def settle_time_min(config: dict) -> int:
    return clock.hhmm_to_min((config.get("defaults") or {}).get("settle_time"), DEFAULT_SETTLE_MIN)


# --------------------------------------------------------------------------- loop lock + cadence
def _paper_data_dir() -> str:
    return os.path.dirname(os.environ.get("CALENDARS_DB_PATH") or db.default_db_path())


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
    precisely the read-side exit derivation can replay a trigger, so pre/post-change derivations
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
                note="mark-path resolution changed; exit derivations not comparable across this date",
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
    processes). Entries only happen on the entry day, so an artifact landing any other day admits
    params that open nothing — `advice: baseline` on a Tuesday is the design, not a failure."""
    acfg = config.get("advice") or {}
    base = acfg.get("base_book", "control")
    path = _advice_decision_path()

    decision = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                decision = json.load(handle)
        except (OSError, ValueError):
            decision = None
        if decision is not None and decision.get("day") != today:
            decision = None
    if decision is not None:
        return decision

    if acfg.get("enabled") and acfg.get("bounds"):
        from cherrypick.core import advice as _core_advice

        result = _core_advice.load(_home.state_dir(), "calendars", today, acfg.get("bounds") or {})
        params = {p["param"]: p["value"] for p in result["proposals"]} or None
        decision = {
            "day": today,
            "base_book": base,
            "params": params,
            "reason": result["reason"],
            "proposals": result["proposals"],
            "rejected": result.get("rejected") or [],
        }
        for proposal in result["proposals"]:
            _log(
                f"advice applied: {proposal['param']}={proposal['value']!r} — {proposal.get('rationale', '')}"
            )
        if not result["proposals"]:
            _log(f"advice: baseline ({result['reason'] or 'no proposals'})")
    else:
        decision = {"day": today, "base_book": base, "params": None, "reason": "advice_disabled"}

    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(decision, handle, indent=2)
    except OSError:
        pass
    return decision


def session_books(config: dict, today: str) -> tuple[list[str], dict | None]:
    """(the books the entry opens this week, the admitted advice params or None). The roster only
    matters at ENTRY — marking, management, disposition, and settlement all iterate open positions
    from the ledger whatever their book tag, so a book once opened can never be stranded by a later
    roster change (the stranding class the flies advised-arm roster helper exists to prevent is
    designed out here rather than handled)."""
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
    """One iteration. Owns the whole week's phase logic — there is exactly one thing to schedule
    and one thing that can fail."""
    _beat()  # before every gate below: liveness is "the loop is turning over", not "it did work"
    when = when or clock.now_et()
    now_min = clock.minute_of_day(when)
    today = when.date()
    day = today.isoformat()

    if not force and not _cal.is_trading_day(today):
        return {"ok": True, "skipped": "not_a_trading_day", "date": day}

    # Settlement before the RTH gate (the settle time is after the close). Only ever settles legs
    # expiring TODAY: a leg whose expiration already passed cannot be honestly priced from a cache
    # that keeps no history, so it is flagged for a manual `--settle --date --price` instead.
    overdue = [leg for leg in _overdue_legs(conn, day)]
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
        if past_settle and now_min % 60 < 2:
            _log(f"{day} idle — no open legs expiring today")
        return {"ok": True, "skipped": "outside_rth", "now_min": now_min}

    defaults = config.get("defaults") or {}
    actions = 0
    marks_written = 0
    phase = "manage"

    # Phase: Monday long disposition — week N−1's surviving longs go first, before this tick can
    # enter week N, so the overlap day never contends.
    disposition_min = clock.hhmm_to_min(defaults.get("mon_disposition_time"), 9 * 60 + 45)
    if now_min >= disposition_min:
        # Delivered shares go first and on EVERY session, not only Mondays: a physically-settled
        # week hands them over at Friday's settlement, and the account carries them until they are
        # sold. Ordering them ahead of the longs keeps the overlap day's sequence
        # shares -> longs -> entry, oldest obligation first.
        actions += _dispose_shares(config, conn, cache_path=cache_path, when=when, day=day)
        actions += _dispose_longs(config, conn, cache_path=cache_path, when=when, day=day)

    # Phase: entry, only on the entry day inside the window.
    plan_dates = clock.week_plan(today)
    window_start = clock.hhmm_to_min(defaults.get("entry_window_start"), 10 * 60)
    window_end = clock.hhmm_to_min(defaults.get("entry_window_end"), 10 * 60 + 15)
    if plan_dates is not None and plan_dates["entry_session"] == day:
        if window_start <= now_min <= window_end:
            phase = "entry"
            actions += _try_entry(config, conn, cache_path=cache_path, when=when, week=plan_dates)
        elif now_min > window_end and not db.positions_for_week(conn, plan_dates["week_of"]):
            db.record_decision(
                conn,
                trade_date=day,
                book="*",
                symbol=(config.get("symbols") or ["SPX"])[0],
                mode="entry",
                reason="week_skipped_entry_window_exhausted",
                accepted=False,
            )

    # Phase: mark everything open, every tick — the exit study's substrate — then manage.
    marked, values = _mark_positions(config, conn, cache_path=cache_path, when=when, day=day)
    marks_written += marked
    actions += _manage_positions(config, conn, values, when=when, day=day)

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
            "SELECT l.position_id, l.leg_role, l.expiration FROM dc_legs l "
            "JOIN dc_positions p ON p.position_id = l.position_id "
            "WHERE l.status = 'open' AND l.expiration < ? AND p.status != 'closed'",
            (day,),
        )
    ]


def _unsettled_today(conn, day: str) -> bool:
    return bool(db.expiring_open_legs(conn, day))


def _try_entry(config: dict, conn, *, cache_path: str, when: datetime, week: dict) -> int:
    day = week["entry_session"]
    symbol = (config.get("symbols") or ["SPX"])[0].strip().upper()
    # Two settlement models are implemented: European cash (shorts settle at intrinsic) and American
    # physical (an ITM short delivers shares, held until the next session's disposal). A symbol the
    # config declares NEITHER for is refused rather than assumed into one — the original guard's
    # point, and the reason it survives the arrival of the second model: bookkeeping that is wrong
    # at its first Friday is wrong quietly.
    style = engine.settlement_style(config, symbol)
    if style is None:
        db.record_entry_attempt(
            conn, trade_date=day, week_of=week["week_of"], symbol=symbol, outcome="unknown_settlement"
        )
        return 0
    # A physically-settled underlying pays dividends, and an ITM short call is really assigned at
    # the close BEFORE the ex-date — which this module deliberately does not model. Ex-div weeks
    # are SKIPPED, from a declared issuer calendar (see engine.py's dividend block for why the
    # dates are data, not a rule). Two refusals, both journaled: a week the calendar cannot answer
    # for, and a week it answers with an ex-date. Cash-settled symbols never reach this — SPX
    # needs no dividends block.
    if style == "physical":
        if not engine.dividend_coverage_ok(config, symbol, week["back_expiration"]):
            db.record_entry_attempt(
                conn, trade_date=day, week_of=week["week_of"], symbol=symbol,
                outcome="dividend_calendar_lapsed",
            )
            return 0
        hit = engine.ex_date_in_span(config, symbol, week["entry_session"], week["back_expiration"])
        if hit is not None:
            db.record_entry_attempt(
                conn, trade_date=day, week_of=week["week_of"], symbol=symbol,
                outcome="ex_dividend_week", block_detail=f"ex-date {hit}",
            )
            return 0
    books, advice_params = session_books(config, day)
    already = {(p["book"], p["side"]) for p in db.positions_for_week(conn, week["week_of"])}
    if all((b, s) in already for b in books for s in ("put", "call")):
        return 0

    root = ((config.get("occ_roots") or {}).get(symbol)) or symbol
    snapshot = provider.build_entry_snapshot(
        cache_path,
        symbol,
        week["front_expiration"],
        week["back_expiration"],
        root=root,
        when=when,
        **provider.snapshot_kwargs(config),
        strike_window_pct=(config.get("defaults") or {}).get(
            "strike_window_pct", provider.DEFAULT_STRIKE_WINDOW_PCT
        ),
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
        db.record_entry_attempt(
            conn, trade_date=day, week_of=week["week_of"], symbol=symbol, outcome=snapshot["reason"]
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

    params = engine.merged_params(config, "control")
    planned = engine.plan_entry(snapshot, params)
    if not planned.get("ok"):
        db.record_entry_attempt(
            conn,
            trade_date=day,
            week_of=week["week_of"],
            symbol=symbol,
            outcome=planned["reason"],
            block_detail=planned.get("detail"),
            spot=snapshot["spot"],
        )
        db.record_decision(
            conn,
            trade_date=day,
            book="*",
            symbol=symbol,
            mode="entry",
            reason=planned["reason"],
            accepted=False,
        )
        return 0

    plan = planned["plan"]
    opened = bookmod.enter_week(conn, plan, config, books, week=week, advice_params=advice_params)
    if opened:
        db.record_entry_attempt(
            conn,
            trade_date=day,
            week_of=week["week_of"],
            symbol=symbol,
            outcome="filled",
            spot=plan["spot"],
            em=plan["em"],
            put_target=plan["sides"]["put"]["target"],
            call_target=plan["sides"]["call"]["target"],
            put_strike=plan["sides"]["put"]["strike"],
            call_strike=plan["sides"]["call"]["strike"],
            put_debit=plan["sides"]["put"]["debit"],
            call_debit=plan["sides"]["call"]["debit"],
        )
        db.record_decision(
            conn,
            trade_date=day,
            book="*",
            symbol=symbol,
            mode="entry",
            reason=f"entered {week['structure']} em {plan['em']:.2f} "
            f"strikes {plan['sides']['put']['strike']:g}/{plan['sides']['call']['strike']:g}",
            accepted=True,
        )
        _log(
            f"{symbol}: entered {week['structure']} across {sorted({o['book'] for o in opened})} — "
            f"em {plan['em']:.2f}, put {plan['sides']['put']['strike']:g} "
            f"({plan['sides']['put']['debit']:.2f}), call {plan['sides']['call']['strike']:g} "
            f"({plan['sides']['call']['debit']:.2f})"
        )
    return len(opened)


def _mark_positions(config: dict, conn, *, cache_path: str, when: datetime, day: str) -> tuple[int, dict]:
    """Mark every open leg of every open position; returns (rows written, {position_id: state}).
    Refusals are rows too — a stalled feed and a quiet market must never look identical."""
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
        leg_marks: dict[str, dict | None] = {}
        for leg in legs:
            quote = (snapshot.get("quotes") or {}).get(leg["streamer_symbol"])
            greeks = (snapshot.get("greeks") or {}).get(leg["streamer_symbol"]) or {}
            leg_marks[leg["leg_role"]] = quote
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
                spot=snapshot.get("spot"),
                quote_age_s=quote["age_seconds"] if quote else None,
                usable=1 if quote else 0,
                refusal=None if quote else (snapshot.get("reason") or "missing_leg_quotes"),
            )
            written += 1
        values[position["position_id"]] = {
            "position": position,
            "snapshot": snapshot,
            "value": engine.combo_value(leg_marks) if position["status"] == "open" else None,
        }
    return written, values


def _manage_positions(config: dict, conn, values: dict, *, when: datetime, day: str) -> int:
    """Evaluate every OPEN position against its own effective params. Combined value/debit pair a
    position with its same-book twin while both are open (computed BEFORE any close this tick, so
    both sides of a pair see the same numbers)."""
    actions = 0
    for pid, state in values.items():
        position = state["position"]
        if position["status"] != "open":
            continue  # short_settled positions belong to the disposition phase
        params = management.effective_params(position, config)
        combined_value, combined_debit = state["value"], position["entry_debit"]
        twin_pid = bookmod.position_id(
            position["week_of"], position["book"], "call" if position["side"] == "put" else "put"
        )
        twin = values.get(twin_pid)
        if twin is not None and twin["position"]["status"] == "open":
            if combined_value is not None and twin["value"] is not None:
                combined_value = round(combined_value + twin["value"], 4)
            else:
                combined_value = None
            combined_debit = round((combined_debit or 0) + (twin["position"]["entry_debit"] or 0), 4)
        decision = management.evaluate(
            position,
            params,
            now=when,
            combined_value=combined_value,
            combined_debit=combined_debit,
            spot=state["snapshot"].get("spot"),
        )
        if not decision.closes:
            continue
        gate = management.execution_gate(state["snapshot"], params, now=when)
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


def _dispose_longs(config: dict, conn, *, cache_path: str, when: datetime, day: str) -> int:
    """Sell the surviving back legs on their own expiration morning (path and advised `mon_open`
    books; also any book whose scheduled close was missed — the honest backstop, with the events
    trail showing how it got here)."""
    actions = 0
    for position in db.open_positions(conn, statuses=("short_settled",)):
        if position["back_expiration"] != day:
            continue
        legs = db.open_legs_for(conn, position["position_id"])
        if not legs:
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
                _log(f"[{position['book']}] {position['position_id']} longs disposed")
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
    """Sell out every share position a physically-settled expiry delivered on an EARLIER session.

    Shares handed over by tonight's settlement cannot be sold tonight, so `before_session=day` is
    the rule rather than an optimisation — and the interval it creates, Friday's settlement to the
    next session's disposal, is precisely the weekend exposure a cash-settled underlying never has.
    It is left visible in the ledger (`assigned_session` against `disposed_session`) instead of
    being netted away.

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
            f"[{assignment['book']}] {assignment['position_id']}: disposed "
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
    missed settlement day.
    """
    when = when or clock.now_et()
    day = day or when.date().isoformat()
    out = []
    for symbol in config.get("symbols") or ["SPX"]:
        symbol = symbol.strip().upper()
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

    `session_settled` is today-scoped: on a day legs expire (the Friday shorts, the Monday longs)
    it means every such leg has been settled or closed; any other day it is trivially true.
    `positions_today` counts what the watchdog would care about going unsettled — positions
    entered today plus positions holding a leg that expires today.
    """
    when = clock.now_et()
    today = when.date().isoformat()
    expiring = db.expiring_open_legs(conn, today)
    entered_today = conn.execute(
        "SELECT COUNT(*) FROM dc_positions WHERE entry_session = ?", (today,)
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

    return {
        "ok": True,
        "date": today,
        "in_session": in_session(clock.minute_of_day(when)),
        "session_settled": not expiring,
        "positions_today": entered_today + expiring_positions,
        "open_positions": len(open_now),
        "week_plan": clock.week_plan(when.date()),
        "stream_cache": cache_path,
        "stream_cache_present": os.path.exists(cache_path),
        "data_ok": data_ok,
        "data_reason": data_reason,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cherrypick-calendars paper session driver")
    ap.add_argument("--config")
    ap.add_argument("--db")
    ap.add_argument("--stream-cache", help="override the shared stream cache path")
    ap.add_argument("--once", action="store_true", help="run a single iteration")
    ap.add_argument("--interval", type=int, metavar="SECONDS", help="run continuously until the close")
    ap.add_argument("--settle", action="store_true", help="cash-settle legs expiring --date (default today)")
    ap.add_argument("--price", type=float, help="explicit settlement price (see --settle)")
    ap.add_argument("--date", help="the expiration day --settle should settle (YYYY-MM-DD)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore the trading-day and RTH gates")
    args = ap.parse_args(argv)

    config = climod.load_config(args.config)
    cache_path = args.stream_cache or stream_cache_path(config)
    db_path = args.db or os.environ.get("CALENDARS_DB_PATH") or db.default_db_path()
    conn = db.connect(db_path)
    stream_request.register(config, conn, db_path)

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
