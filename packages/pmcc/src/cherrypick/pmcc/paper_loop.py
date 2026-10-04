"""Paper session driver — mark, manage, enter when capital frees up, settle at the bell.

This is the only file in the module that touches the clock or the filesystem-of-record. Everything
it decides is decided by `engine.py`/`management.py`; this layer supplies snapshots and persists
what came back. That split is what makes the strategy testable, and it is also the suite guardrail:
no network, no MCP, no model call anywhere on a decision path.

One `run_once` carries the whole position lifecycle, gated by the clock rather than by the schedule
(the flies rule: the schedule carries no session logic, so it can never disagree with the engine
about when the day starts or ends):

- Any trading day, in session: mark every open leg every tick, then run management on what the
  gates allow — the default hold-to-short-expiration exit, or (advised-only) the early
  tv-exhaustion close.
- Past the disposition time: cover shares delivered by an earlier session's assignment, then sell
  the orphan longs of short-settled positions — oldest obligation first, before this tick can enter
  anything new.
- Inside the entry window, for each configured symbol (TQQQ, XSP) with no open position and
  headroom under `max_positions`: plan and enter `control` (and one `advised:<experiment>` per advisor experiment when the advisor
  has admitted params for today).
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
from datetime import date, datetime

from cherrypick.core import advice as _core_advice
from cherrypick.core import calendar as _cal
from cherrypick.core import config as _cfg
from cherrypick.core import home as _home
from cherrypick.core import logs as _logs
from cherrypick.core import looplock

from cherrypick.pmcc import analytics, clock, db, engine, management, provider, stream_request
from cherrypick.pmcc import book as bookmod
from cherrypick.pmcc import cli as climod

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
    home = str(_home.home())
    return os.path.join(home, "data", "marketdata", "stream_cache.db")


def log_file():
    """Resolved on every call, never at import — a module-level constant would capture the real
    home before any test could redirect it (the flies lesson)."""
    return _home.logs_dir("pmcc") / "pmcc_paper.log"


def _log(message: str) -> None:
    _logs.configure(_logger, log_file())
    _logger.info(message)


def in_session(now_min: int, day: date | None = None) -> bool:
    """Regular trading hours, closing at the session's own bell when `day` is given (13:00 on an
    early close), else at 16:00."""
    close = clock.session_close_min(day) if day is not None else RTH_CLOSE_MIN
    return RTH_OPEN_MIN <= now_min < close


def settle_time_min(config: dict, day: date | None = None) -> int:
    """When the settlement pass runs: the configured time (16:20), carried to the session's own
    close on an early close -- 13:20 on 2026-11-27, when a 16:20 pass would read a print three hours
    stale and refuse it."""
    configured = clock.hhmm_to_min((config.get("defaults") or {}).get("settle_time"), DEFAULT_SETTLE_MIN)
    if day is None:
        return configured
    return clock.session_close_min(day) + (configured - RTH_CLOSE_MIN)


def _symbols(config: dict) -> list[str]:
    return [s.strip().upper() for s in (config.get("symbols") or ["TQQQ"])]


# --------------------------------------------------------------------------- loop lock + cadence
def _paper_data_dir() -> str:
    return os.path.dirname(os.environ.get("PMCC_DB_PATH") or db.default_db_path())


def _loop_lock_path() -> str:
    return os.path.join(_paper_data_dir(), "paper_loop.lock")


_pid_alive = looplock.pid_alive  # noqa: F401  (re-exported: tests monkeypatch this name)


def _acquire_loop_lock(stale_seconds: int = 180) -> bool:
    """Single-instance guard shared by `--interval` and `--once`, so the supervised resident loop
    and an off-session/manual `--once` can never iterate the same arm concurrently.

    `cherrypick.core.looplock` holds the semantics (a live holder is never stolen, whatever its
    age); `_pid_alive` is passed explicitly so a test monkeypatching that name still steers it."""
    return looplock.acquire(_loop_lock_path(), stale_seconds, alive=_pid_alive)


def _release_loop_lock() -> None:
    looplock.release(_loop_lock_path())


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


# --------------------------------------------------------------------------- advised arm
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


def session_books(config: dict, today: str) -> tuple[list[str], dict[str, dict]]:
    """(the arms entry may open today, `{advised tag: its experiment entry}`). The roster only
    matters at ENTRY — marking, management, disposition, and settlement all iterate open positions
    from the ledger whatever their arm tag, so an arm once opened can never be stranded by a later
    roster change. `control`, plus `shield` and `shield_hold` from 2026-10-05 where the config
    enables them (an undeclared arm other than control stays off), plus control's advised twins.

    One advised arm PER EXPERIMENT the day's decision admitted (2026-09-17): each entry names its
    own tag (`advised:<experiment name>`), the base it shadows and the params it overlays; a
    decision recorded before that date still yields its single `advised:<base>`. The map is empty
    on a baseline day. Every tag here is on the roster the stream request subscribes for -- the
    2026-08-27 lesson (`stream_window.entry_possible`) holds for every twin, not just the first."""
    declared = _cfg.registry(config, label="pmcc", log=_log)
    arms = [
        b for b in engine.ARMS if (declared.get(b) or {}).get("enabled", engine.DEFAULT_ENABLED.get(b, False))
    ]
    advised = advised_entries(advice_decision(config, today))
    arms.extend(tag for tag in advised if tag not in arms)
    return arms, advised


def advised_entries(decision: dict | None) -> dict[str, dict]:
    """`{tag: experiment entry}` for every experiment the decision opens a arm for, in artifact
    order -- keyed by tag because planning, freezing and stamping all look the arm up by it."""
    return {e["tag"]: e for e in _core_advice.advised_books(decision) if e.get("tag")}


# --------------------------------------------------------------------------- the boundary
# The first session of the shield boundary (docs/shield-study.md). Entries from the deploy on are
# stamped `analytics.CURRENT_ERA`; if the deploy slips past this date, move it with the deploy.
SHIELD_FROM = "2026-10-05"
_PRE_SHIELD = {"arms": "control", "symbols": "TQQQ, XSP", "max_positions": "3"}


def _note_shield_boundary(conn, config: dict) -> None:
    """Journal the shield boundary once a machine's config enables a held-long arm: the era, the
    arm roster, the symbols and the held-long arms' first-entry pacing, each a `measurement_breaks`
    row dated `SHIELD_FROM`. The new values are read from the config as it stands, so each row says
    what this machine runs. Idempotent (one row per date and key) and best-effort: telemetry, never
    a reason to skip a tick."""
    try:
        declared = _cfg.registry(config, label="pmcc")
        on = [
            b
            for b in engine.ARMS
            if (declared.get(b) or {}).get("enabled", engine.DEFAULT_ENABLED.get(b, False))
        ]
        held = {b: engine.merged_params(config, b) for b in on}
        held = {b: p for b, p in held.items() if management.is_held_long(p)}
        if not held:
            return
        defaults = config.get("defaults") or {}
        rows = [
            (
                "era",
                "redesign",
                analytics.CURRENT_ERA,
                "headline and excursions scope to the new era; earlier rows stay in the ledger, History "
                "and any era='ALL' read, and TQQQ's open positions finish under the old one",
            ),
            (
                "arms",
                _PRE_SHIELD["arms"],
                ", ".join(on),
                "held-long arms beside control: a ~1-year 0.90-0.95-delta long held while a weekly "
                "0.70-delta short rolls against it; shield rolls early, shield_hold holds to Friday",
            ),
            (
                "symbols",
                f"{_PRE_SHIELD['symbols']}; max_positions {_PRE_SHIELD['max_positions']}",
                f"{', '.join(config.get('symbols') or [])}; max_positions {defaults.get('max_positions')}",
                "each symbol is its own population; TQQQ takes no new entries and runs off",
            ),
            (
                "pacing",
                None,
                json.dumps({b: p.get("entry_symbols_per_session") for b, p in held.items()}, sort_keys=True),
                "held-long first entries staggered: at most this many symbols enter a session",
            ),
        ]
        for key, old, new, note in rows:
            db.record_measurement_break(
                conn, break_date=SHIELD_FROM, key=key, old_value=old, new_value=new, note=note
            )
    except Exception as exc:  # noqa: BLE001 -- never let telemetry break the loop
        _log(f"shield boundary journaling failed (non-fatal): {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- the tick
def run_once(
    config: dict, conn, *, cache_path: str, when: datetime | None = None, force: bool = False
) -> dict:
    """One iteration. Owns the whole lifecycle's phase logic — there is exactly one thing to
    schedule and one thing that can fail."""
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
    _note_shield_boundary(conn, config)

    # Settlement before the RTH gate (the settle time is after the close). Only ever settles legs
    # expiring TODAY: a leg whose expiration already passed cannot be honestly priced from a cache
    # that keeps no history, so it is flagged for a manual `--settle --date --price` instead.
    overdue = _overdue_legs(conn, day)
    if overdue and now_min % 60 < 2:
        _log(
            f"{len(overdue)} leg(s) past expiration remain open — settle manually with "
            f"--settle --date <expiration> --price <official print>"
        )
    past_settle = now_min >= settle_time_min(config, today)
    if past_settle and _unsettled_today(conn, day):
        _log(f"past settle time — settling legs expiring {day}")
        return {
            "ok": True,
            "settled_session": True,
            **run_settle(config, conn, cache_path=cache_path, when=when),
        }

    if not force and not in_session(now_min, today):
        return {"ok": True, "skipped": "outside_rth", "now_min": now_min}
    # The session's advice decision is read and RECORDED on every in-session tick, not only on
    # the entry path (2026-09-17, the calendars finding applied suite-wide): read-once, so the first
    # tick derives it and every later one replays the file. A session with nothing to enter still
    # tells the advisor the artifact reached the loop -- "governed a day with nothing to govern" is
    # a different fact from "never read", and the enactment check exists to tell them apart.
    if not force:
        advice_decision(config, day)
    defaults = config.get("defaults") or {}
    actions = 0
    marks_written = 0
    phase = "manage"

    # Phase: disposals — oldest obligation first, before this tick can enter anything new.
    # Delivered shares go first and on EVERY session: a short-call assignment hands them over at
    # Friday's settlement and the account carries them (short) until covered.
    disposition_min = clock.hhmm_to_min(defaults.get("disposition_time"), 9 * 60 + 45)
    if now_min >= disposition_min:
        actions += _dispose_shares(config, conn, cache_path=cache_path, when=when, day=day)
        actions += _dispose_longs(config, conn, cache_path=cache_path, when=when, day=day)

    # Phase: entries, any trading day inside the window, per (symbol, arm) with headroom.
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
def _short_guard(config: dict, symbol: str, day: str, short_expiration: str) -> str | None:
    """The refusals for selling one short: settlement declaration and the dividend span over the
    short's life. Every new short runs it -- an entry's, and since 2026-10-04 a held-long roll's or
    sale's, each of which sells a short spanning its own week."""
    style = engine.settlement_style(config, symbol)
    if style is None:
        return "unknown_settlement"
    if style == "physical":
        if not engine.dividend_coverage_ok(config, symbol, short_expiration):
            return "dividend_calendar_lapsed"
        hit = engine.ex_date_in_span(config, symbol, day, short_expiration)
        if hit is not None:
            return "ex_dividend_span"
    return None


def _entry_guards(config: dict, symbol: str, plan_dates: dict, day: str) -> str | None:
    """The pre-snapshot refusals for one symbol's entry: `_short_guard` over the plan's short."""
    return _short_guard(config, symbol, day, plan_dates["short_expiration"])


def arm_params(config: dict, arm: str, advised: dict | None = None, decision: dict | None = None) -> dict:
    """The params one arm enters under: its own merged config, or for an advised twin its base's
    with the experiment's overlay. Each arm plans from its OWN params -- until 2026-10-04 every
    non-advised arm reused control's plan, which a second base arm would have traded silently."""
    entry = (advised or {}).get(arm)
    if entry:
        base = engine.base_book(arm, config=config, decision=decision)
        return {
            **management.PARAM_DEFAULTS,
            **engine.merged_params(config, base),
            **(entry.get("params") or {}),
        }
    return {**management.PARAM_DEFAULTS, **engine.merged_params(config, arm)}


def _held_long_entries_today(conn, config: dict, day: str) -> set[str]:
    """Symbols a held-long arm entered on `day` -- the pacing counter for staggered first entries."""
    out: set[str] = set()
    for row in conn.execute("SELECT * FROM pmcc_positions WHERE entry_session = ?", (day,)):
        if management.is_held_long(management.effective_params(dict(row), config)):
            out.add(row["symbol"])
    return out


def _refuse_entry(
    conn, day: str, symbol: str, arm: str, reason: str, *, attempt: bool = True, **extra
) -> None:
    if attempt:
        db.record_entry_attempt(conn, trade_date=day, symbol=symbol, arm=arm, outcome=reason, **extra)
    db.record_decision(
        conn, trade_date=day, arm=arm, symbol=symbol, mode="entry", reason=reason, accepted=False
    )


def _try_entries(config: dict, conn, *, cache_path: str, when: datetime, day: str) -> int:
    arms, advised = session_books(config, day)
    # The decision itself is what each advised row is stamped from: `stamp_for(arm, decision)`
    # resolves the id per tag, so two experiments on one session carry two ids.
    decision = advice_decision(config, day) if advised else None
    defaults = config.get("defaults") or {}
    max_positions = int(defaults.get("max_positions", 3))
    opened_count = 0
    params_by_arm = {b: arm_params(config, b, advised, decision) for b in arms}
    paced = _held_long_entries_today(conn, config, day)

    for symbol in _symbols(config):
        free = [b for b in arms if db.open_position_for(conn, symbol, b) is None]
        wanting = [b for b in free if db.open_position_count(conn, b) < max_positions]
        for b in free:
            if b not in wanting:
                # A free slot on this symbol but the arm is at its cap across symbols: its own reason,
                # so a full arm never reads as a full slot.
                _refuse_entry(conn, day, symbol, b, "arm_cap", attempt=False)
        if not free:
            # Every arm already holds this symbol, so there is nothing to attempt. Recorded rather
            # than skipped: a session with no attempt rows at all reads identically to a loop that
            # never evaluated entry, and "all slots full" is the benign half of that pair. Collapsed
            # per (day, arm, symbol, reason), so a whole session costs one counted row.
            for b in arms:
                _refuse_entry(conn, day, symbol, b, "slot_held", attempt=False)
            continue

        # Each arm's expiration plan: control's weekly pair, or a held-long arm's weekly short and
        # listed ~1-year long. Arms whose plans, and deep windows, agree share one snapshot -- shield
        # and shield_hold always do, which is what makes them a pair.
        groups: dict[tuple, list[str]] = {}
        plan_dates_by_key: dict[tuple, dict] = {}
        listed: list[str] | None = None
        for b in wanting:
            params = params_by_arm[b]
            if management.is_held_long(params):
                limit = params.get("entry_symbols_per_session")
                if limit is not None and symbol not in paced and len(paced) >= int(limit):
                    _refuse_entry(conn, day, symbol, b, "entry_pacing")
                    continue
                if listed is None:
                    listed = provider.listed_expirations(cache_path, symbol)
                dates = clock.held_long_plan(when.date(), listed, params)
                if dates is None:
                    _refuse_entry(conn, day, symbol, b, "no_leap_listed" if listed else "no_listing")
                    continue
            else:
                dates = clock.expiration_plan(when.date(), params)
                if dates is None:
                    _refuse_entry(conn, day, symbol, b, "no_expiration_plan", attempt=False)
                    continue
            reason = _entry_guards(config, symbol, dates, day)
            if reason is not None:
                _refuse_entry(conn, day, symbol, b, reason)
                continue
            pct = provider.deep_window_pct_for(config, symbol, params)
            key = (dates["short_expiration"], dates["long_expiration"], pct)
            groups.setdefault(key, []).append(b)
            plan_dates_by_key[key] = dates

        root = ((config.get("occ_roots") or {}).get(symbol)) or symbol
        for key, group in groups.items():
            snapshot = provider.build_entry_snapshot(
                cache_path,
                symbol,
                plan_dates_by_key[key],
                root=root,
                when=when,
                **provider.snapshot_kwargs(config),
                deep_window_pct=key[2],
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
                for b in group:
                    _refuse_entry(conn, day, symbol, b, snapshot["reason"])
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
            plans = {b: engine.plan_entry(snapshot, params_by_arm[b]) for b in group}
            opened_count += _fill_entries(
                conn, config, symbol, group, plans, snapshot, advised, decision, day, paced, params_by_arm
            )
    return opened_count


def _fill_entries(
    conn, config, symbol, group, plans, snapshot, advised, decision, day, paced, params_by_arm
) -> int:
    opened_count = 0
    wanting = group
    for b in wanting:
        result = plans[b]
        if not result.get("ok"):
            db.record_entry_attempt(
                conn,
                trade_date=day,
                symbol=symbol,
                arm=b,
                outcome=result["reason"],
                block_detail=result.get("detail"),
                spot=snapshot["spot"],
            )
            db.record_decision(
                conn,
                trade_date=day,
                arm=b,
                symbol=symbol,
                mode="entry",
                reason=result["reason"],
                accepted=False,
            )
            continue
        plan = result["plan"]
        if plan.get("long_selected_by") == "extrinsic":
            # Temporary visibility while the feed's own greeks coverage is unproven for this
            # deep-ITM window: extrinsic-only selection is a legitimate degrade (the delta
            # floor simply can't be checked), not a defect, but every occurrence is worth a
            # human noticing until there is enough history to know how often the feed lacks
            # deep-strike deltas here.
            _logger.warning(
                "pmcc entry (%s/%s): long strike %.2f selected via extrinsic-only fallback, "
                "feed had no delta for this deep-ITM candidate",
                symbol,
                b,
                plan["long_strike"],
            )
        opened = bookmod.enter_position(
            conn,
            plan,
            config,
            b,
            entry_session=day,
            advice_params=(advised.get(b) or {}).get("params"),
            experiment_id=decision,
        )
        if opened is None:
            continue
        opened_count += 1
        if management.is_held_long(params_by_arm[b]):
            paced.add(symbol)
        db.record_entry_attempt(
            conn,
            trade_date=day,
            symbol=symbol,
            arm=b,
            outcome="filled",
            spot=plan["spot"],
            long_strike=plan["long_strike"],
            short_strike=plan["short_strike"],
            net_debit=plan["net_debit"],
            protection_pct=plan["downside_protection_pct"],
        )
        db.record_decision(
            conn,
            trade_date=day,
            arm=b,
            symbol=symbol,
            mode="entry",
            reason=(
                f"entered {plan['long_strike']:g}/{plan['short_strike']:g} "
                f"debit {plan['net_debit']:.2f} tv {plan['net_tv']:.2f} "
                f"protection {plan['downside_protection_pct']:.1%}"
            ),
            accepted=True,
        )
        _log(
            f"[{b}] {symbol}: entered long {plan['long_strike']:g} ({plan['long_expiration']}) / "
            f"short {plan['short_strike']:g} ({plan['short_expiration']}) — "
            f"debit {plan['net_debit']:.2f}, net TV {plan['net_tv']:.2f}, "
            f"protection {plan['downside_protection_pct']:.1%}, "
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
        style = engine.settlement_style(config, position["symbol"])
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
                leg_exposed = (
                    1 if management.assignment_exposed(leg_tv, params, settlement_style=style) else 0
                )
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
        if management.is_held_long(params):
            actions += _manage_held_long(config, conn, state, cache_path=cache_path, when=when, day=day)
            continue
        decision = management.evaluate(
            position,
            params,
            now=when,
            short_tv=state["short_tv"],
            spot=state["snapshot"].get("spot"),
        )
        if not decision.acts:
            continue
        gate = management.execution_gate(state["snapshot"], params, now=when)
        executed = 0
        detail = dict(decision.detail)
        if gate is None and decision.action == "close_all":
            result = bookmod.close_open_legs(
                conn, position, state["snapshot"], config, reason=decision.reason, session_date=day
            )
            executed = 1 if result.get("ok") else 0
            if not result.get("ok"):
                gate = result.get("reason")
            else:
                actions += 1
                _log(f"[{position['arm']}] {pid} closed — {decision.reason}")
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


def _manage_refusal(conn, position: dict, day: str, reason: str) -> None:
    """A held-long verdict that could not be carried out this tick. Collapsed per (day, arm, symbol,
    reason) -- a sale refused for a whole session is one counted row, not one per tick."""
    db.record_decision(
        conn,
        trade_date=day,
        arm=position["arm"],
        symbol=position["symbol"],
        mode="manage",
        reason=reason,
        accepted=False,
    )


def _manage_held_long(config: dict, conn, state: dict, *, cache_path: str, when: datetime, day: str) -> int:
    """One held-long position's tick: the verdict (`management.evaluate_held_long`) and, through the
    short-only gate, the ticket that carries it out -- a roll, a sale, a buyback, or the full close."""
    position, params, snapshot = state["position"], state["params"], state["snapshot"]
    pid = position["position_id"]
    legs = db.legs_for(conn, pid)
    long_leg = next((leg for leg in legs if leg["leg_role"] == "long_call" and leg["status"] == "open"), None)
    short_leg = next(
        (leg for leg in legs if leg["leg_role"] != "long_call" and leg["status"] == "open"), None
    )
    if long_leg is None:
        return 0
    spot = snapshot.get("spot")
    quotes = snapshot.get("quotes") or {}
    marks = {
        leg["leg_role"]: quotes[leg["streamer_symbol"]]["mid"]
        for leg in legs
        if leg["status"] == "open" and quotes.get(leg["streamer_symbol"]) is not None
    }
    pnl = engine.pnl_to_date(position, legs, marks, db.assignments_for(conn, pid), spot)
    long_dte = (date.fromisoformat(long_leg["expiration"]) - when.date()).days
    short = None
    old_quote = None
    if short_leg is not None:
        old_quote = quotes.get(short_leg["streamer_symbol"])
        entry_spot = (
            short_leg.get("entry_spot")
            if short_leg.get("entry_spot") is not None
            else position.get("entry_spot")
        )
        entry_tv = None
        if entry_spot is not None and short_leg.get("entry_mid") is not None:
            entry_tv = short_leg["entry_mid"] - max(0.0, entry_spot - short_leg["strike"])
        short = {
            "strike": short_leg["strike"],
            "expiration": short_leg["expiration"],
            "tv": state.get("short_tv"),
            "entry_tv": entry_tv,
        }
    verdict = management.evaluate_held_long(
        position,
        params,
        now=when,
        short=short,
        spot=spot,
        pnl=pnl,
        long_dte=long_dte,
        rolled_today=db.rolled_today(conn, pid, day),
        session_close_min=clock.session_close_min(when.date()),
    )
    if not verdict.acts:
        return 0

    if verdict.action == "close_all":
        gate = management.execution_gate(snapshot, params, now=when)
        executed = 0
        if gate is None:
            result = bookmod.close_open_legs(
                conn, position, snapshot, config, reason=verdict.reason, session_date=day
            )
            executed = 1 if result.get("ok") else 0
            gate = None if result.get("ok") else result.get("reason")
        db.record_management_event(
            conn,
            position_id=pid,
            occurred_at=time.time(),
            session_date=day,
            action="close_all",
            reason=verdict.reason,
            executed=executed,
            gate=gate,
            detail_json=json.dumps(verdict.detail) if verdict.detail else None,
        )
        if executed:
            _log(f"[{position['arm']}] {pid} closed -- {verdict.reason}")
        return executed

    if verdict.action == "close_short":
        gate = management.short_execution_gate([old_quote], params, now=when)
        if gate is not None:
            _manage_refusal(conn, position, day, f"{verdict.reason}:{gate}")
            return 0
        bookmod.close_short_leg(
            conn, position, short_leg, old_quote, config, reason=verdict.reason, session_date=day, spot=spot
        )
        _log(f"[{position['arm']}] {pid} short {short_leg['strike']:g} bought back -- {verdict.reason}")
        return 1

    # roll_short / sell_short: both sell next week's short.
    if verdict.action == "sell_short":
        window_start = clock.hhmm_to_min(params.get("entry_window_start"), 10 * 60)
        window_end = clock.hhmm_to_min(params.get("entry_window_end"), 15 * 60 + 30)
        if not window_start <= clock.minute_of_day(when) <= window_end:
            return 0
        if db.open_assignment_count(conn, pid):
            _manage_refusal(conn, position, day, "sell_short:shares_open")
            return 0
    target = clock.short_expiration(when.date(), params, cap=long_leg["expiration"])
    if target is None:
        _manage_refusal(conn, position, day, f"{verdict.reason}:no_roll_expiration")
        return 0
    guard = _short_guard(config, position["symbol"], day, target["short_expiration"])
    if guard is not None:
        # The new short is refused. An EXPIRING short is still bought back -- it cannot be carried
        # through its own expiry under the IRA policy -- and the position runs without a short until
        # a later tick clears the date. Any other roll keeps the short it has.
        if verdict.action == "roll_short" and verdict.reason == "expiry" and old_quote is not None:
            gate = management.short_execution_gate([old_quote], params, now=when)
            if gate is None:
                bookmod.close_short_leg(
                    conn,
                    position,
                    short_leg,
                    old_quote,
                    config,
                    reason=f"expiry:{guard}",
                    session_date=day,
                    spot=spot,
                )
                return 1
        _manage_refusal(conn, position, day, f"{verdict.reason}:{guard}")
        return 0
    root = ((config.get("occ_roots") or {}).get(position["symbol"])) or position["symbol"]
    roll_snap = provider.build_roll_snapshot(
        cache_path,
        position["symbol"],
        target["short_expiration"],
        root=root,
        short_dte=target["short_dte"],
        when=when,
        **provider.snapshot_kwargs(config),
    )
    if not roll_snap.get("ok"):
        _manage_refusal(conn, position, day, f"{verdict.reason}:{roll_snap['reason']}")
        return 0
    buyback = old_quote if verdict.action == "roll_short" else None
    plan = engine.plan_short(roll_snap, params, long_strike=long_leg["strike"], buyback=buyback)
    if not plan.get("ok"):
        _manage_refusal(conn, position, day, f"{verdict.reason}:{plan['reason']}")
        return 0
    new_quote = {"bid": plan["leg"]["bid"], "ask": plan["leg"]["ask"], "mid": plan["leg"]["mid"]}
    gate_quotes = [old_quote, new_quote] if verdict.action == "roll_short" else [new_quote]
    gate = management.short_execution_gate(gate_quotes, params, now=when)
    if gate is not None:
        _manage_refusal(conn, position, day, f"{verdict.reason}:{gate}")
        return 0
    if verdict.action == "roll_short":
        out = bookmod.roll_short_leg(
            conn,
            position,
            short_leg,
            old_quote,
            plan,
            config,
            reason=verdict.reason,
            session_date=day,
            spot=spot,
        )
        _log(
            f"[{position['arm']}] {pid} rolled {out['old_strike']:g} -> {out['new_strike']:g} "
            f"({out['new_expiration']}), net {plan['net_credit']:+.2f} -- {verdict.reason}"
        )
    else:
        bookmod.sell_short_leg(
            conn, position, plan, config, reason=verdict.reason, session_date=day, spot=spot
        )
        _log(f"[{position['arm']}] {pid} sold short {plan['strike']:g} ({plan['short_expiration']})")
    return 1


# --------------------------------------------------------------------------- disposition
def _dispose_longs(config: dict, conn, *, cache_path: str, when: datetime, day: str) -> int:
    """Sell the surviving long of every short-settled position — the combined-disposal half that
    the option ledger sees. It runs the session AFTER the short settled (settlement is post-close,
    so a Friday assignment reaches here Monday morning), alongside `_dispose_shares` covering the
    delivered shares — together they are the 'both legs closed at assignment' model."""
    actions = 0
    for position in db.open_positions(conn, statuses=("short_settled",)):
        if management.is_held_long(management.effective_params(position, config)):
            continue  # a held-long long outlives its shorts; it is never disposed with one
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
                _log(f"[{position['arm']}] {position['position_id']} long disposed")
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
            f"[{assignment['arm']}] {assignment['position_id']}: covered "
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
    symbol: str | None = None,
) -> dict:
    """Settle every open leg expiring `day` (default: today) at the settlement print.

    The print is the last streamed trade, staleness-gated (`settlement_max_age_seconds`) — refused
    rather than settled stale, so the next tick retries and the day settles itself when the feed
    recovers. `--price` overrides with the official print, which is the only honest path for a
    missed settlement day. Under physical settlement the print also sets the delivered shares'
    basis, so a stale one would misprice the weekend leg as well as the option one.

    The work list is the LEDGER's: every symbol holding a leg that expires `day`, not the config's
    current `symbols`. Until 2026-10-04 it was the config's, so retiring a symbol from `symbols`
    while it still held legs would have left them unsettled for good.

    An explicit `price` is ONE underlying's print, so it needs `symbol` whenever more than one
    symbol expires that day. Applied across symbols it would settle XSP at a TQQQ print.
    """
    when = when or clock.now_et()
    day = day or when.date().isoformat()
    expiring_symbols = sorted({leg["position_symbol"] for leg in db.expiring_open_legs(conn, day)})
    if symbol is not None:
        expiring_symbols = [s for s in expiring_symbols if s == symbol.upper()]
    if price is not None and len(expiring_symbols) > 1:
        _log(f"--price {price} names no symbol, but {', '.join(expiring_symbols)} all expire {day}")
        return {
            "ok": False,
            "reason": "price_needs_symbol",
            "symbols": expiring_symbols,
            "results": [],
            "date": day,
        }
    out = []
    for symbol in expiring_symbols:
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

    return {
        "ok": True,
        "date": today,
        "in_session": in_session(clock.minute_of_day(when)),
        "session_settled": not expiring,
        "positions_today": entered_today + expiring_positions,
        "open_positions": len(open_now),
        "expiration_plan": clock.expiration_plan(when.date(), config.get("defaults") or {}),
        "stream_cache": cache_path,
        "stream_cache_present": os.path.exists(cache_path),
        "data_ok": data_ok,
        "data_reason": data_reason,
    }


def _beat() -> None:
    """Publish liveness: this loop reached the top of a tick.

    Same contract as the calendars loop, learned from the same incident (107 restarts on
    2026-08-17): every line this loop logs is event-driven, and the ledger only moves when a mark
    or an entry lands, so a quiet-but-healthy tick writes nothing anyone can see — on 2026-08-21
    the watchdog's freshness check WARNed mid-session over a 17-minute gap between mark writes.
    Touched at the TOP of the tick, before any branch; failure is swallowed (a heartbeat that
    costs a tick would be worse than the problem it solves)."""
    try:
        path = _home.heartbeat_path("pmcc")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now().astimezone().isoformat(), encoding="utf-8")
    except OSError:
        pass


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
    ap.add_argument("--symbol", help="the one underlying --settle --price applies to")
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
                run_settle(
                    config, conn, cache_path=cache_path, price=args.price, day=args.date, symbol=args.symbol
                ),
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
