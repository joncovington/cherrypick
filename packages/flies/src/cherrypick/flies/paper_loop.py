"""Paper session driver — fetch a snapshot, run every arm, settle at the bell.

This is the only file in the module that touches the clock or the filesystem-of-record. Everything it
decides is decided by `engine.py`; this layer just supplies snapshots and persists what came back.
That split is what makes the strategy testable, and it is also the suite guardrail: no network, no MCP,
and no model call anywhere on a decision path.

Typical use is one scheduled `--once` per interval during RTH plus a `--settle` after the close, which
is how the orchestrator drives it (by subprocess, never by import). `--interval` runs the same loop
in-process for a manual session.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time

from cherrypick.core import advice as _core_advice
from cherrypick.core import calendar as _cal  # noqa: E402
from cherrypick.core import home as _home
from cherrypick.core import logs as _logs
from cherrypick.core import looplock

from cherrypick.flies import book as bookmod  # noqa: E402
from cherrypick.flies import cli as climod  # noqa: E402
from cherrypick.flies import db as dbmod  # noqa: E402
from cherrypick.flies import eod as eodmod  # noqa: E402
from cherrypick.flies import (
    provider,  # noqa: E402
    stream_request,  # noqa: E402
    stream_window,  # noqa: E402
)

# Regular trading hours, ET, as minutes of day. The engine's own entry windows sit inside this; the
# session gate exists so an out-of-hours run is a clean no-op rather than an iteration against a
# frozen cache full of yesterday's quotes.
RTH_OPEN_MIN = 9 * 60 + 30
RTH_CLOSE_MIN = 16 * 60

# Settlement runs from the SAME recurring task rather than a second daily one. Two tasks can drift
# apart — one fires, the other is disabled or missed, and the books sit unsettled with nobody to
# notice. MEIC uses this same self-trigger for its EOD report.
DEFAULT_SETTLE_MIN = 16 * 60 + 20

_TASK_NAME = "cherrypick-flies-paper-loop"
# Cadence history — load-bearing for THIS strategy in a way it is not for MEIC: the completing
# spread of a legged fly can cheapen transiently, so a slower poll measures a lower completion rate
# — the module's headline number — for reasons that have nothing to do with the market. Any discrete
# poll underestimates what a resting limit order would catch live, so the measured completion rate
# is a floor on that count and a ceiling on live fill quality — which also means CHANGING the
# cadence changes what the number measures; every change is a journaled measurement break (see
# `_note_cadence_change`) and pre/post rates are never pooled. 2 min -> 1 min on 2026-07-29 (XSP
# move: ~1/10 the premiums against the same fee stack, so inter-tick dips mattered more); 1 min was
# the OS scheduler's floor. Since the 2026-08-09 supervisor cutover the in-session driver is the
# resident `--interval` loop at the orchestrator-configured cadence (15s) — supervised
# (restart-on-death, restart-on-silence), which is the reliability model that going sub-minute
# always required — while off-session ticks stay `--once` spawns so settlement, retries, and the
# idle heartbeat keep their shape. _TASK_INTERVAL_MIN survives only for the legacy standalone
# schtasks path (`--install-task`) and the off-session heartbeat rate-limit below.
_TASK_INTERVAL_MIN = 1
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def stream_cache_path(config: dict) -> str:
    """The suite's canonical shared stream cache, which this module reads read-only. Config first, then
    the managed home, and `~` expands — portable paths only, no machine-specific absolutes anywhere in
    the suite. The default (`data/marketdata/stream_cache.db`) is producer-agnostic: whichever streamer
    is the active producer (MEIC's, or the standalone `packages/streamer` daemon) writes that one file."""
    configured = (config.get("source") or {}).get("stream_cache_db")
    if configured:
        return os.path.expanduser(os.path.expandvars(configured))
    home = os.environ.get("CHERRYPICK_HOME") or os.path.join(os.path.expanduser("~"), ".cherrypick")
    return os.path.join(home, "data", "marketdata", "stream_cache.db")


_logger = logging.getLogger("flies_paper_loop")


def log_file():
    """Resolved on every call, never at import.

    A module-level constant here read `CHERRYPICK_HOME` once, when the interpreter first imported
    this file — which is before any test can redirect it. So every test in the suite appended to the
    real `~/.cherrypick/logs/flies/flies_paper.log` no matter how carefully it isolated everything
    else, and a fixture session's fills sat interleaved with a live one's in the file an operator
    reads to reconstruct the day.
    """
    return eodmod.logs_dir() / "flies_paper.log"


def _setup_logging() -> None:
    """Log to a rotating file as well as stdout.

    The scheduled task runs under pythonw.exe with no console, so anything printed to stdout is
    discarded. Without a file the first live session would leave no trace of why it did or didn't
    trade.

    This file used to carry a second, unstated job: the supervisor measured the resident loop's
    silence against its mtime, so how much this logged decided whether the process was allowed to
    live. It no longer does — liveness is `_beat()`'s heartbeat file — so this log is free to be
    exactly as talkative as a human reading it needs, and no more.

    The file handler is rebuilt if the resolved path has moved since it was attached, so a redirected
    home takes effect even though the logger itself is process-global state.
    """
    # The suite's one line format (cherrypick.core.logs). This used to write
    # "[%(asctime)s] %(message)s" with a *naive* stamp and no level -- one of the three shapes the
    # dashboard's log card had to reverse-engineer, and part of why it mis-ordered and then dropped
    # whole sources. The shared writer emits an offset, so a line is an unambiguous instant, and
    # carries the level so a reader is not guessing severity from prose.
    #
    # Two behaviours are inherited rather than re-implemented: the handler is rebuilt when the
    # resolved path moves (this package's own idea, so a redirected home takes effect mid-process),
    # and the console handler is attached only for a real TTY (meic's, because the scheduled task
    # runs under pythonw.exe where writing to an invalid stdout can take the daemon down -- this
    # package attached it unconditionally).
    _logs.configure(_logger, log_file())


def _log(message: str) -> None:
    _setup_logging()
    _logger.info(message)


def heartbeat_file():
    """Resolved on every call, never at import — same reason as `log_file` above."""
    from cherrypick.core import home as _core_home

    return _core_home.heartbeat_path("flies")


def _beat() -> None:
    """Publish liveness: this loop reached the top of a tick.

    Until 2026-08-17 the supervisor measured this module's silence against its LOG, which worked here
    only by luck — `run_once` happens to log a line per symbol per tick, so the file was never quiet
    in session. Calendars, whose lines are all event-driven, was killed and restarted every two
    minutes for four days on the same mechanism. Luck is not a supervision contract, and a change that
    merely made this loop quieter would have inherited that bug silently.

    Touched at the TOP of the tick, before any branch: it means "the loop is turning over" and nothing
    about what the tick then decided. Failures are swallowed — a heartbeat that costs a tick would be
    worse than the problem it solves.
    """
    try:
        path = heartbeat_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(provider.now_et().isoformat(timespec="seconds"), encoding="utf-8")
    except OSError:
        pass


def in_session(now_min: int) -> bool:
    return RTH_OPEN_MIN <= now_min < RTH_CLOSE_MIN


# --------------------------------------------------------------------------- loop lock + cadence
def _paper_data_dir() -> str:
    return os.path.dirname(os.environ.get("FLIES_DB_PATH") or dbmod.default_db_path())


def _loop_lock_path() -> str:
    return os.path.join(_paper_data_dir(), "paper_loop.lock")


_pid_alive = looplock.pid_alive  # noqa: F401  (re-exported: tests monkeypatch this name)


def _acquire_loop_lock(stale_seconds: int = 180) -> bool:
    """Single-instance guard shared by `--interval` and `--once`, so the supervised resident loop
    and an off-session/manual `--once` can never iterate the same book concurrently.

    `cherrypick.core.looplock` holds the semantics (a live holder is never stolen, whatever its
    age); `_pid_alive` is passed explicitly so a test monkeypatching that name still steers it."""
    return looplock.acquire(_loop_lock_path(), stale_seconds, alive=_pid_alive)


def _release_loop_lock() -> None:
    looplock.release(_loop_lock_path())


def _cadence_state_path() -> str:
    return os.path.join(_paper_data_dir(), "tick_cadence.json")


def _note_cadence_change(conn, interval_seconds: int) -> None:
    """Journal a tick-cadence change as an explicit measurement break.

    The completion rate is cadence-dependent (a faster poll catches transient completing-debit dips
    a slower one misses — see the cadence-history note at the top), so pre/post-change rates are
    not comparable and must never be pooled. The break is recorded where analysis actually looks:
    one decision-journal row (visible on the dashboard's Decision Journal card), keyed off a small
    state file so the row is written exactly once per change. Best-effort — telemetry, never a
    reason to skip a tick. First-ever run baselines against 60s, the cadence of the entire
    pre-supervisor ledger.
    """
    try:
        prev = 60
        state_path = _cadence_state_path()
        try:
            with open(state_path, encoding="utf-8") as fh:
                prev = int(json.load(fh).get("seconds", 60))
        except (OSError, ValueError):
            pass
        if prev == interval_seconds:
            return
        day = provider.now_et().date().isoformat()
        reason = (
            f"tick_interval {prev}s->{interval_seconds}s (supervisor cutover); "
            "completion rate not comparable across this date"
        )
        detail = json.dumps({"old_seconds": prev, "new_seconds": interval_seconds})
        dbmod.record_decision(
            conn,
            trade_date=day,
            arm="*",
            symbol="*",
            mode="cadence",
            reason=reason,
            accepted=False,
            detail=detail,
        )
        # Also to `measurement_breaks`, where every cross-module read surface looks. The decision
        # journal is this module's own narrative and a reader has to know to grep it for mode
        # 'cadence'; the suite review asks each ledger a uniform question, and a break recorded only
        # in prose or only in a module-shaped table is invisible to it.
        dbmod.record_measurement_break(
            conn, break_date=day, scope="*", kind="cadence", reason=reason, detail=detail
        )
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump({"seconds": interval_seconds, "since": day}, fh)
        _log(f"tick cadence changed {prev}s -> {interval_seconds}s — journaled as a measurement break")
    except Exception as exc:  # noqa: BLE001 — never let telemetry break the loop
        _log(f"cadence-change journaling failed (non-fatal): {type(exc).__name__}: {exc}")


def _entry_cadence_state_path() -> str:
    return os.path.join(_paper_data_dir(), "entry_cadence.json")


def _note_entry_cadence_change(conn, config: dict) -> None:
    """Journal a change to the ENTRY cadence as an explicit measurement break.

    Distinct from `_note_cadence_change`, which tracks how often the loop POLLS. This one tracks how
    often an arm may ENTER, and the two break different numbers: tick cadence moves the completion
    rate (a faster poll catches transient completing-debit dips), entry cadence moves how many
    structures a session accumulates at all — so it changes what a per-session net even means, and
    pooling across it compares books built under different opportunity counts.

    Same mechanism as its sibling: one decision-journal row per change, keyed off a small state file
    so it is written exactly once. First-ever run baselines against 0 — the entire pre-2026-08-11
    ledger had no entry cadence, pacing came only from entry windows.

    Best-effort. Telemetry is never a reason to skip a tick.
    """
    try:
        seconds = int((config.get("defaults") or {}).get("min_seconds_between_entries", 0) or 0)
        prev = 0
        state_path = _entry_cadence_state_path()
        try:
            with open(state_path, encoding="utf-8") as fh:
                prev = int(json.load(fh).get("seconds", 0))
        except (OSError, ValueError):
            pass
        if prev == seconds:
            return
        day = provider.now_et().date().isoformat()
        dbmod.record_decision(
            conn,
            trade_date=day,
            arm="*",
            symbol="*",
            mode="cadence",
            reason=(
                f"entry_cadence {prev}s->{seconds}s (per-arm portfolios); "
                "entries per session not comparable across this date"
            ),
            accepted=False,
            detail=json.dumps({"old_seconds": prev, "new_seconds": seconds, "kind": "entry"}),
        )
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump({"seconds": seconds, "since": day}, fh)
        _log(f"entry cadence changed {prev}s -> {seconds}s — journaled as a measurement break")
    except Exception as exc:  # noqa: BLE001 — never let telemetry break the loop
        _log(f"entry-cadence journaling failed (non-fatal): {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- advised arms
# The loop side of the agentic layer. An out-of-band advisor proposes parameter values; this
# re-validates them with the SAME core code the producer used (`cherrypick.core.advice`) against
# this module's own `advice.bounds` manifest, and runs the admitted set as a SYNTHETIC ARM beside
# the un-advised base. Absent, stale, or invalid advice means baseline — no synthetic arm at all.
#
# An advised arm is a NEW BOOK, not a change to an existing one. That convention is what keeps the
# comparison honest: `control` never changes meaning mid-experiment, so its history stays poolable,
# and `fly_books`/`fly_positions` key on the arm STRING, so `advised:control` needs no `engine.ARMS`
# entry and attribution comes free from the stored tag.
#
# There is deliberately no management twin (MEIC has one). Flies has no exits — a position is held
# to settlement — so an advised book that stops receiving advice has nothing left to decide. It just
# needs to settle, which `session_arms` guarantees by including any advised arm that still has rows
# for the day being processed, whatever today's advice says.


def _advice_decision_path() -> str:
    return os.path.join(_paper_data_dir(), "advice_active.json")


def advice_decision(config: dict, today: str) -> dict:
    """Today's advice decision, derived ONCE per session and replayed thereafter.

    Read-once matters across `--once` processes: the first tick of the day records what it decided,
    and every later tick replays that record, so advice can never start, stop, or change mid-session
    however late an artifact lands or however the config is flipped intraday.

    The mechanics live in `cherrypick.core.advice.session_decision` — three modules had written this
    read-once-and-replay identically, and it carries a safety property rather than a convenience.
    """
    return _core_advice.session_decision(
        _home.state_dir(),
        "flies",
        today,
        config,
        _advice_decision_path(),
        base_key="base_arm",
        log=_log,
    )


def _advised_arms_with_books(conn, trade_date: str) -> list[str]:
    """Advised arms that already hold positions for this day — they must settle whatever today's
    advice says, or a book opened this morning would be stranded by an afternoon config change."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT arm FROM fly_positions WHERE trade_date = ? AND arm LIKE 'advised:%'",
            (trade_date,),
        ).fetchall()
    except Exception:  # noqa: BLE001 — a missing table is a module that has not traded yet
        return []
    return [r[0] for r in rows]


def session_arms(config: dict, conn, trade_date: str) -> list[str]:
    """The arms this session runs: the enabled roster, plus any advised arm.

    ONE helper, called by both the tick and settlement, because those two call sites reading
    different rosters is precisely how an advised book gets entered and never settled. It mutates
    `config["arms"]` so `engine.merged_params` resolves the synthetic arm the ordinary way — the
    alternative is a second params path that only the advised book takes, which is a second thing
    that can differ from the control for reasons the experiment did not intend.
    """
    arms = climod.enabled_arms(config)
    decision = advice_decision(config, trade_date)
    base = decision.get("base_arm") or "control"
    overlay = decision.get("params")

    advised: list[str] = []
    if overlay:
        tag = f"advised:{base}"
        config.setdefault("arms", {})[tag] = {**(config.get("arms", {}).get(base) or {}), **overlay}
        advised.append(tag)

    for tag in _advised_arms_with_books(conn, trade_date):
        if tag in advised:
            continue
        # A book from an earlier decision today. Its positions carry their own recorded economics;
        # the base arm's params are the honest reconstruction of everything else about it.
        tag_base = tag.split(":", 1)[1]
        config.setdefault("arms", {}).setdefault(tag, dict(config.get("arms", {}).get(tag_base) or {}))
        advised.append(tag)

    return arms + advised


def settle_time_min(config: dict) -> int:
    at = config.get("defaults", {}).get("settle_time")
    if not at:
        return DEFAULT_SETTLE_MIN
    hours, minutes = at.split(":")
    return int(hours) * 60 + int(minutes)


def session_already_settled(conn, day: str) -> bool:
    """Have this day's books been closed out? The marker is book state, so a task firing every
    minute after the close settles once and then no-ops.

    This was the existence of `paper-eod-<day>.md`, which made the marker something any process able
    to create a file could set. On 2026-07-20 the test suite did exactly that against the real
    managed home while the session was live: the loop read its own day as finished at the settle
    time, skipped settlement in silence, and left eleven positions open under a report describing a
    fixture. A marker for "settlement happened" must be writable only by settlement.

    A day with no books at all is NOT settled — that is the no-trade session, which still has to run
    settlement once so the day gets its roll-up and its report.
    """
    total, settled = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(status = 'settled'), 0) FROM fly_books WHERE trade_date = ?", (day,)
    ).fetchone()
    return total > 0 and total == settled


def run_once(config: dict, conn, *, cache_path: str, when=None, force: bool = False) -> dict:
    """One iteration across every configured symbol and every enabled arm.

    Also owns end-of-day settlement. The recurring task calls only this, so there is exactly one
    thing to schedule and one thing that can fail.
    """
    _beat()  # before every gate below: liveness is "the loop is turning over", not "it did work"
    when = when or provider.now_et()
    now_min = provider.minute_of_day(when)
    day = when.date().isoformat()

    # Nothing happens on a non-trading day — not even settlement.
    #
    # The off-session tick fires every minute forever, so without this the Saturday-evening tick would find
    # the clock past the settle time and no report written for that date, "settle" a session that
    # never happened, and emit paper-eod-<saturday>.md. The suite digest discovers those files by
    # filename alone, so it would ingest weekends and holidays as real sessions.
    if not force and not _cal.is_trading_day(when.date()):
        return {"ok": True, "skipped": "not_a_trading_day", "date": day}

    # Settlement next, and deliberately BEFORE the RTH gate — the settle time is after the close, so
    # an RTH-gated check would never reach it.
    past_settle = now_min >= settle_time_min(config)
    settled = session_already_settled(conn, day)
    if past_settle and not settled:
        _log(f"past settle time ({now_min // 60:02d}:{now_min % 60:02d}) — settling {day}")
        return {
            "ok": True,
            "settled_session": True,
            **run_settle(config, conn, cache_path=cache_path, when=when),
        }

    if not force and not in_session(now_min):
        # A settled day is silent for the rest of the evening, and silence is what a dead loop looks
        # like too — the whole reason 2026-07-20 read as "the loop stopped at 13:58" is that the
        # skip logged nothing. So leave a heartbeat, rate-limited to the first tick of each hour:
        # the off-session --once tick fires ~1/min around the clock (the in-session resident loop
        # never reaches this branch) and a line per tick would bury the session it surrounds.
        if past_settle and settled and now_min % 60 < _TASK_INTERVAL_MIN:
            _log(f"{day} settled — idle until the next session")
        return {"ok": True, "skipped": "outside_rth", "now_min": now_min, "session_settled": settled}

    arms = session_arms(config, conn, day)
    results = []
    for symbol in config.get("symbols", ["SPX"]):
        snapshot = provider.build_snapshot(
            cache_path,
            symbol,
            when=when,
            **provider.snapshot_kwargs(config),
        )
        if not snapshot.get("ok"):
            # Not an error. A streamer still warming up, or a symbol with no fresh quotes, is an
            # ordinary condition — log it so a barren session is explicable afterwards. Record the
            # refusal to fly_snapshots too: the arm loop below never runs on a refusal, so without
            # this the tick leaves no trace and a feed outage looks identical to a quiet market.
            _log(f"{symbol}: no snapshot ({snapshot['reason']})")
            dbmod.record_snapshot(
                conn,
                trade_date=day,
                symbol=symbol,
                status=snapshot["reason"],
                quotes_rejected=snapshot.get("rejected"),
            )
            results.append({"symbol": symbol, "ok": False, "reason": snapshot["reason"]})
            continue

        stats = snapshot["quote_stats"]
        _log(
            f"{symbol}: spot {snapshot['underlying_price']:.2f} dte {snapshot['dte']} "
            f"quotes {stats['fresh']} fresh / {stats['rejected']} rejected"
        )
        dbmod.record_snapshot(
            conn,
            trade_date=day,
            symbol=symbol,
            status="ok",
            quotes_fresh=stats["fresh"],
            quotes_rejected=stats["rejected"],
            underlying_price=snapshot["underlying_price"],
        )
        for arm in arms:
            outcome = bookmod.process_snapshot(snapshot, config, conn, arm)
            for action in outcome["actions"]:
                if action["action"] not in ("entry_skipped", "completion_skipped"):
                    _log(f"  [{arm}] {action}")
            results.append({"symbol": symbol, "arm": arm, "ok": True, **outcome})
    return {"ok": True, "iterations": len(results), "results": results}


def run_settle(config: dict, conn, *, cache_path: str, when=None, price: float | None = None) -> dict:
    """Settle every book for the session at the settlement price.

    Caveat worth knowing when reading the results: `price` defaults to the last streamed trade, which
    approximates but is not identical to the official settlement print. For 0DTE SPX that difference is
    usually small, and it is systematic rather than random — but a position centred within a point of
    spot can settle on the wrong side of its centre because of it. Pass `--price` with the official
    print for a book that matters.
    """
    when = when or provider.now_et()
    trade_date = when.date().isoformat()
    out = []
    for symbol in config.get("symbols", ["SPX"]):
        # A stale settlement print is worse than a late one: it decides every position's P&L at
        # once and cannot be undone. Refuse rather than settle the session against an old number —
        # the operator can re-run with --price once the feed recovers, or with the official print.
        settle_max_age = config.get("defaults", {}).get("settlement_max_age_seconds", 300)
        settlement = (
            price
            if price is not None
            else provider.read_spot(cache_path, symbol, max_age_seconds=settle_max_age)
        )
        if settlement is None:
            _log(
                f"{symbol}: cannot settle — no price within {settle_max_age}s "
                f"(feed stale or down). Re-run with --price once it recovers."
            )
            out.append({"symbol": symbol, "ok": False, "reason": "no_settlement_price"})
            continue
        source = "explicit" if price is not None else "last_trade"
        # The same roster the tick entered on, including any advised arm — settling a narrower list
        # than the one that traded would strand an advised book open with no way to close it.
        for arm in session_arms(config, conn, trade_date):
            result = bookmod.settle_book(
                conn, trade_date, arm, symbol, settlement, config, settlement_source=source
            )
            _log(
                f"{symbol} [{arm}] settled at {settlement:.2f} ({source}): "
                f"P&L {result['pnl']:+.2f}, stats {result['stats']}"
            )
            out.append({"symbol": symbol, "arm": arm, "ok": True, "settlement_source": source, **result})

    # Only write the reports if something actually settled.
    #
    # A report describing a session that never closed is worse than no report: it is indistinguishable
    # from a real one. Refusing leaves the books open, which is now exactly what `session_already_settled`
    # reads, so the next tick tries again and the day settles by itself as soon as the feed recovers.
    settled_any = any(r.get("ok") for r in out)
    if not settled_any:
        _log("nothing settled — not writing EOD reports; will retry on the next tick")
        return {"ok": False, "settled": 0, "results": out, "reason": "no_settlement_price"}

    # The per-module EOD reports were retired 2026-08-13: packages/review builds one fact set
    # across every module and renders from that, so a module writing its own prose was a second,
    # unreconciled account of the same session. Settlement itself is unchanged and is what the
    # review reads.
    return {"ok": True, "settled": len(out), "results": out}


# --------------------------------------------------------------------------- scheduled task
def _pythonw() -> str:
    """pythonw.exe where available, so the every-minute run is genuinely headless — a console
    window flashing up 200 times a session would make the machine unusable."""
    candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return candidate if os.path.exists(candidate) else sys.executable


def task_installed() -> bool:
    if os.name != "nt":
        return False
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", _TASK_NAME], capture_output=True, text=True, creationflags=_NO_WINDOW
    )
    return r.returncode == 0


def install_task() -> dict:
    """Register the recurring loop.

    One task, running `--once` every couple of minutes. `--once` is internally gated — out of hours
    it is a clean no-op, after the close it settles once — so the schedule carries no session logic
    of its own and cannot disagree with the engine about when the day starts or ends.
    """
    if os.name != "nt":
        return {
            "ok": False,
            "error": "scheduled-task install is Windows-only; elsewhere run "
            "`python src/paper_loop.py --interval 120` or use cron",
        }
    # `-m` rather than an absolute script path: the registered task no longer bakes in this
    # file's location. Requires cherrypick-flies installed in this interpreter (scripts/dev-install).
    tr = f'"{_pythonw()}" -m cherrypick.flies.paper_loop --once'
    r = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/TN",
            _TASK_NAME,
            "/TR",
            tr,
            "/SC",
            "MINUTE",
            "/MO",
            str(_TASK_INTERVAL_MIN),
            "/F",
            "/IT",
        ],
        capture_output=True,
        text=True,
        creationflags=_NO_WINDOW,
    )
    ok = r.returncode == 0
    if ok:  # fire once now so the first tick isn't up to two minutes away
        subprocess.run(
            ["schtasks", "/Run", "/TN", _TASK_NAME], capture_output=True, text=True, creationflags=_NO_WINDOW
        )
    return {
        "ok": ok,
        "task": _TASK_NAME,
        "cadence": f"every {_TASK_INTERVAL_MIN} min",
        "detail": (r.stdout or r.stderr).strip(),
    }


def uninstall_task() -> dict:
    if os.name != "nt":
        return {"ok": False, "error": "Windows-only"}
    subprocess.run(
        ["schtasks", "/End", "/TN", _TASK_NAME], capture_output=True, text=True, creationflags=_NO_WINDOW
    )
    r = subprocess.run(
        ["schtasks", "/Delete", "/TN", _TASK_NAME, "/F"],
        capture_output=True,
        text=True,
        creationflags=_NO_WINDOW,
    )
    return {"ok": r.returncode == 0, "task": _TASK_NAME, "detail": (r.stdout or r.stderr).strip()}


def run_status(config: dict, conn, *, cache_path: str) -> dict:
    """Health view for the orchestrator: is the upstream cache there, and what has this module done
    today? Deliberately file-only — no broker, no network — so it stays safe on a watchdog path."""
    when = provider.now_et()
    today = when.date().isoformat()
    books = [
        dict(r) for r in conn.execute("SELECT * FROM fly_books WHERE trade_date = ?", (today,)).fetchall()
    ]
    positions = conn.execute("SELECT COUNT(*) FROM fly_positions WHERE trade_date = ?", (today,)).fetchone()[
        0
    ]

    # Can we actually SEE the market right now? This module has no streamer of its own, so when the
    # upstream one stalls we go blind and can do nothing about it but say so.
    #
    # It has to be reported explicitly because every other health signal stays green through an
    # outage: the task keeps firing, the loop keeps running, and the log keeps growing — with a
    # `no_fresh_quotes` line every two minutes. A freshness check that watches file mtimes reads
    # that as perfect health. Observed 2026-07-20: 53 such lines during an 8-minute streamer stall,
    # with nothing anywhere reporting a problem.
    probe = provider.build_snapshot(
        cache_path,
        (config.get("symbols") or ["SPX"])[0],
        when=when,
        **provider.snapshot_kwargs(config),
    )
    data_ok = bool(probe.get("ok"))
    return {
        "ok": True,
        "date": today,
        "in_session": in_session(provider.minute_of_day(when)),
        # The orchestrator's watchdog reads this to tell "the loop is registered and quiet" from
        # "nothing is scheduled at all" — which look identical in an empty paper DB.
        "scheduled_task": task_installed(),
        "task_name": _TASK_NAME,
        "session_settled": session_already_settled(conn, today),
        "stream_cache": cache_path,
        "stream_cache_present": os.path.exists(cache_path),
        # `data_ok` false during RTH means the module is running but blind — the orchestrator surfaces
        # this rather than trusting log freshness.
        "data_ok": data_ok,
        "data_reason": None if data_ok else probe.get("reason"),
        "quote_stats": probe.get("quote_stats"),
        "books": books,
        "positions_today": positions,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cherrypick-flies paper session driver")
    ap.add_argument("--config")
    ap.add_argument("--db")
    ap.add_argument("--stream-cache", help="override MEIC's stream cache path")
    ap.add_argument("--once", action="store_true", help="run a single iteration")
    ap.add_argument("--interval", type=int, metavar="SECONDS", help="run continuously until the close")
    ap.add_argument("--settle", action="store_true", help="cash-settle today's books")
    ap.add_argument("--price", type=float, help="explicit settlement price (see --settle)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument(
        "--install-task",
        action="store_true",
        help=f"register the recurring {_TASK_NAME} task (every {_TASK_INTERVAL_MIN} min; Windows)",
    )
    ap.add_argument("--uninstall-task", action="store_true")
    ap.add_argument("--date", help="a session day (YYYY-MM-DD); default today")
    ap.add_argument("--force", action="store_true", help="ignore the RTH session gate")
    args = ap.parse_args(argv)

    # Task registration touches no config and no database, so handle it before either is opened —
    # `--install-task` must work on a machine that has not been configured yet.
    if args.install_task:
        print(json.dumps(install_task(), indent=2))
        return 0
    if args.uninstall_task:
        print(json.dumps(uninstall_task(), indent=2))
        return 0

    config = climod.load_config(args.config)
    cache_path = args.stream_cache or stream_cache_path(config)
    conn = dbmod.connect(args.db)
    # Tell the streamer which underlyings we need kept fresh in the shared cache (best-effort), and
    # whether any of them need a wider-than-default ATM window after repeated missing_leg_quotes
    # refusals (stream_window.py; state lives in this DB, so paper's escalation is independent of
    # live's own).
    try:
        base_width = int(config.get("stream_window", {}).get("base_width", 60))
        symbols = config.get("symbols") or ["SPX"]
        today = provider.now_et().date().isoformat()
        hints = stream_window.hints_for_symbols(conn, symbols, today, base_width=base_width)
    except Exception:  # noqa: BLE001 — window escalation is advisory, never fatal to the loop
        hints = None
    stream_request.register(config, window_hints=hints)

    if args.status:
        print(json.dumps(run_status(config, conn, cache_path=cache_path), indent=2, default=str))
        return 0
    if args.settle:
        print(
            json.dumps(
                run_settle(config, conn, cache_path=cache_path, price=args.price), indent=2, default=str
            )
        )
        return 0
    if args.interval:
        # One loop at a time — the supervised resident session and any off-session/manual --once
        # share this lock, so two processes can never iterate the same book concurrently.
        if not _acquire_loop_lock():
            _log("another paper loop holds the lock — exiting")
            print(json.dumps({"ok": True, "skipped": "another paper loop is already running"}))
            return 0
        try:
            _log(f"loop starting, interval {args.interval}s, cache {cache_path}")
            _note_cadence_change(conn, args.interval)
            _note_entry_cadence_change(conn, config)
            # Stale-checkout guard (2026-08-05). The loop imports from the working tree, so a session
            # run from an older branch writes NULL to any regime column that branch predates --
            # silently, all day, with no backfill path afterwards. Logged rather than enforced: a
            # stale checkout cannot fix itself, and refusing to trade would turn a telemetry gap
            # into an outage.
            drift = dbmod.stale_writer_columns(conn)
            if drift:
                _log(
                    f"WARNING: {len(drift)} ledger column(s) this checkout will never write — "
                    f"{', '.join(drift)}. The running code is older than the database schema; "
                    "this session's rows will be missing that telemetry. Check the branch."
                )
            while args.force or in_session(provider.minute_of_day(provider.now_et())):
                # A transient failure costs one tick, not the session — the supervisor restarts a
                # dead resident child, but dying on every quote hiccup would turn each into a
                # restart+backoff cycle when the next tick would have been fine.
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
