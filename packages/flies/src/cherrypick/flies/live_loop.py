#!/usr/bin/env python3
"""Flies LIVE loop — the paper loop's sibling, running the full autonomous trade cycle.

Architecture (docs/live-trading-plan.md + the 2026-07-30 loop plan): a **1-minute self-healing
scheduled main tick** (`--once --live`, task `cherrypick-flies-live-loop` — the same reliability
model as the paper loop; a resident trading daemon already proved fragile on Windows in MEIC)
plus **state-triggered burst watchers**: when a tick places an order or sees one unconfirmed, it
spawns a detached `--watch-fills` subprocess that polls fills every ~10s for up to ~60s, then
exits — the next tick takes over.

The streamer comes before API calls (suite rule): all pricing reads the shared stream cache,
and cached quotes GATE broker calls — a resting entry is only cancelled/replaced when the
cached evaluation actually moved, and the watcher only polls order status when cached quotes
show the market touching the working limit (plus a slow heartbeat poll as the safety net; the
broker remains the only source of truth for fills).

Order lifecycle:
  - ENTRY: Day limit at the modeled credit. Re-evaluated each tick from cache; cancelled and
    re-placed only when the center or credit moved (>= one tick). A cancel failure re-polls
    status first — "already filled" is the expected race.
  - COMPLETION: placed ONCE, immediately after the entry fill confirms (by the watcher, or by
    the next tick as fallback — an atomic DB claim makes exactly one placer win), resting at
    the max safe debit `min(credit - fee_buffer, floor bound)` so it can never fill at a price
    either gate would refuse. The resting limit IS the gate; it catches every transient dip a
    poll would miss. Cancelled at `completion_cutoff` (default 15:30).
    (A PRE-CLOSE EXIT step used to sit here, closing ITM positions in the final minutes when that
    was cheaper than the assignment fee. Removed 2026-08-01: it lost ~$34/position in paper and
    never once fired in live. Every position now holds to settlement — see CLAUDE.md rule 5.)
  - SETTLEMENT: at `live.settle_time` (default 16:20) each tick tries to auto-fetch the OFFICIAL
    print (tastytrade -> Yahoo -> Barchart, see `broker_cli.official_settlement_price`) and settle
    directly, tagged with WHICH source answered (see broker_cli.official_settlement_price -- only a
    posted close counts as official); if every source comes up empty it falls back to
    the last streamed trade, marked `'last_trade_provisional'`. A still-provisional session keeps
    retrying the official fetch on every subsequent tick until it upgrades or the loop
    self-disarms — a human can still force it with `--settle --price X` (marked 'official') if the
    auto-fetch never lands. Next-day settlement happens automatically — settlement is checked
    before the session gate, like paper.
  - SELF-DISARM (dead-man's switch): a live tick at/after `live.disarm_time` (default 17:00),
    or one that finds the arm stamp from a previous day, uninstalls its OWN scheduled task.
    Arming is per-day by design — today's YES can never carry into tomorrow. The orchestrator
    watchdog backstops this by setting the halt flag if the task somehow survives.

Gates checked every live tick (`readiness()`): `live.enabled`, a non-empty `gate0_confirmed`
attestation, one configured arm and a designated account -- unmet, the tick does nothing. The halt
flag and the daily-loss breaker are ENTRY blockers (`entry_blockers()`, 2026-10-08): they stop new
structures only, while fills are confirmed, completions placed and cut off, and the book settled. Live concurrency (2026-09-25): no count limit by default -- the
buying-power cap below is the sizing gate, and an uncompleted vertical or a negative-floor fly
counts against it at its worst case. `live.max_incomplete_spreads` restores a count limit (1 was
the pilot's rule until 2026-09-25), with `live.negative_floor_override` still naming a stuck
negative-floor fly it may step past. Buying power is capped locally by
`live.max_open_margin_dollars` (2026-09-17): the worst-case dollar exposure of every open live
position plus the proposed spread's own must fit under it, computed from the ledger and the
plan alone -- no balance read, so a transient broker failure can never block a legitimate
entry the way the fail-closed account governor did (which is why `account_deploy_limit_pct`
stays null for the pilot).

Journaled every tick, live or dry-run (added 2026-07-30 — until then live wrote none of this, so
the live dashboard's Session Timeline and Decision Journal cards read empty even on a session
with a real fill): `fly_snapshots` (feed quality), `fly_iterations` (what the pinned arm wanted,
before any gate), `fly_decisions` (why an entry/completion was accepted or refused, via the same
`record_decision` run-collapsing paper's book.py uses — a gate that refuses every tick is one row
with a growing `occurrences`, not hundreds of identical ones).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from cherrypick.core import calendar as _cal  # noqa: E402
from cherrypick.core import config as _cfg  # noqa: E402
from cherrypick.core import execution as _execution
from cherrypick.core import home as _home  # noqa: E402
from cherrypick.core import live as _live  # noqa: E402
from cherrypick.core import redact as _redact  # noqa: E402
from cherrypick.core import settlement as _settlement  # noqa: E402

from cherrypick.flies import (
    alerts_db,  # noqa: E402
    clock,  # noqa: E402
    close_tags,  # noqa: E402
    engine,  # noqa: E402
    fill_facts,  # noqa: E402
    fill_model,  # noqa: E402
    fly,  # noqa: E402
    intraday_advice,  # noqa: E402
    intraday_eval,  # noqa: E402
    live_orders,  # noqa: E402
    provider,  # noqa: E402
    stream_request,  # noqa: E402
    stream_window,  # noqa: E402
)
from cherrypick.flies import book as bookmod  # noqa: E402
from cherrypick.flies import db as dbmod  # noqa: E402
from cherrypick.flies import paper_loop as _pl  # noqa: E402
from cherrypick.flies.cli import load_config  # noqa: E402

DEFAULT_ARM = "gex"
_TERMINAL_UNFILLED = _execution.TERMINAL_UNFILLED  # one reading of a dead order, suite-wide

_TASK_INTERVAL_MIN = 1
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

DEFAULT_SETTLE = "16:20"
DEFAULT_DISARM = "17:00"
# The live-only end-of-quarter refusal (core.calendar.is_quarterly_expiry), core's one spelling.
QUARTER_END_REASON = _live.QUARTER_END_REASON


def _quarter_end(day: str) -> bool:
    """Is `day` the last trading day of a quarter? A seam so the test suite, whose live fixtures are
    dated the real today, does not refuse every entry on a real quarter end (tests/conftest.py)."""
    return _cal.is_quarterly_expiry(_cal.date.fromisoformat(str(day)))


DEFAULT_CUTOFF = "15:30"
DEFAULT_WATCH_SECONDS = 60
DEFAULT_WATCH_POLL_SECONDS = 10
DEFAULT_HEARTBEAT_SECONDS = 150


# --------------------------------------------------------------------------- paths and logging
def halt_flag_path() -> str:
    """The suite-wide live kill switch; presence is the signal. One resolver for every live loop
    and the orchestrator (`cherrypick.core.home.halt_flag_path`), kept here as a string for the
    callers that already read it that way."""
    return str(_home.halt_flag_path())


def _data_dir() -> str:
    return os.path.dirname(dbmod.live_db_path())


def _once_lock_path() -> str:
    return os.path.join(_data_dir(), "live_loop.once.lock")


def _watch_lock_path() -> str:
    return os.path.join(_data_dir(), "live_watch.lock")


def arm_stamp_path() -> str:
    """The live ARM RECORD — 'armed for today' as a file, in the SHARED state dir so the module
    (self-disarm), the supervisor (job enablement), and the watchdog (dead-man's backstop) all read
    the one file. Under the supervisor, this record IS the armed
    signal: present-and-dated-today enables the `flies-live` job; deleting it disarms within one
    supervisor pass. Only this module's human-confirmed arm command ever writes it. The
    convention is `cherrypick.core.live` (2026-09-18), so a second armed module cannot drift."""
    return str(_live.arm_record_path("flies"))


def _legacy_arm_stamp_path() -> str:
    """Pre-supervisor location (`data/flies/live_armed.json`) — read as a migration fallback for
    one transition window, never written anymore."""
    return os.path.join(_data_dir(), "live_armed.json")


_logger = logging.getLogger("flies_live_loop")


def log_file():
    """Resolved on every call, never at import — same test-isolation lesson as paper_loop."""
    from cherrypick.flies.eod import logs_dir

    return logs_dir() / "flies_live.log"


def _setup_logging() -> None:
    target = log_file()
    attached = next((h for h in _logger.handlers if isinstance(h, RotatingFileHandler)), None)
    if attached is not None:
        if os.path.abspath(attached.baseFilename) == os.path.abspath(str(target)):
            return
        _logger.removeHandler(attached)
        attached.close()
    target.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    handler = RotatingFileHandler(target, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(fmt)
    _logger.addHandler(handler)
    if not any(type(h) is logging.StreamHandler for h in _logger.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        _logger.addHandler(stream)
    _logger.setLevel(logging.INFO)


def _log(message: str) -> None:
    _setup_logging()
    # Masked at the sink (2026-09-25): order-placement lines log the broker's response whole, and it
    # carries the full account number, which the suite guardrail says a log never holds.
    _logger.info(_redact.redact_accounts(message))


# --------------------------------------------------------------------------- locks
def _acquire_lock(path: str, stale_seconds: int = 180) -> bool:
    """O_EXCL lock file with staleness steal — MEIC's --once overlap guard, generalized."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:  # steal a stale lock (a prior process that died mid-run)
            if time.time() - os.path.getmtime(path) > stale_seconds:
                os.unlink(path)
                return _acquire_lock(path, stale_seconds)
        except OSError:
            pass
        return False


def _release_lock(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# --------------------------------------------------------------------------- config helpers
def _live_cfg(config: dict) -> dict:
    return config.get("live") or {}


def _time_min(hhmm: str) -> int:
    return engine.time_to_minutes(hhmm)


def settle_min(config: dict) -> int:
    return _time_min(_live_cfg(config).get("settle_time") or DEFAULT_SETTLE)


def disarm_min(config: dict) -> int:
    return _time_min(_live_cfg(config).get("disarm_time") or DEFAULT_DISARM)


def _merged_live_params(config: dict, arm: str) -> dict:
    """The arm's engine params, with the live block's own overrides on top: `completion_cutoff`,
    and `no_entry_before` (2026-09-17) -- the live pilot's own start of day.

    The start is a LIVE override rather than a change to the arm because the two loops differ in
    exactly the way that makes the first entry matter. Paper control is unbounded, so a 10:00
    entry costs nothing beyond its own outcome; live holds one incomplete position at a time, so
    a 10:00 entry -- the day's weakest slot in the era, 36 entries netting +$61 with a median 81
    minutes to complete -- sits on the slot through 10:15 and 10:30, the two slots that made the
    money (34 entries, 88% completion, +$2,730). Replaying control's era entries under the live
    rule: start 10:00 +$1,749 on 42 structures, 10:30 +$2,283 on 29 with half the losing days.
    Paper keeps 10:00 as the comparison baseline.

    **10:15 since 2026-09-29.** The argument above was made for the one-incomplete rule, removed
    2026-09-25; under the margin cap that replaced it, the weak 10:00-10:14 slot still crowds the
    good ones -- it just does so through the cap. `scripts/flies_live_start_replay.py` over the
    same 25 era sessions: 10:15 +$4,758 against 10:30's +$2,735 (19 of 21 differing days better,
    sign p 0.0002) and 10:00's +$3,614. Journaled as a live `entry_rules` break. The engine's
    `before_open_gate` reads this key,
    so the override is refused ahead of the arm's own windows like any other blackout floor; it
    can only ever move the start LATER than the arm's (the later of the two wins).
    """
    params = engine.merged_params(config, arm)
    live = _live_cfg(config)
    if live.get("completion_cutoff"):
        params["completion_cutoff"] = live["completion_cutoff"]
    start = live.get("no_entry_before")
    if start:
        arm_start = params.get("no_entry_before")
        params["no_entry_before"] = max(str(start), str(arm_start)) if arm_start else str(start)
    return params


# --------------------------------------------------------------------------- gates
def readiness(config: dict, *, designated: str | None) -> list[str]:
    """The unmet ACCOUNT gates, checked every tick -- empty means the loop may act on the account
    at all. Pure. The halt flag and the daily-loss breaker are not here: they stop NEW ENTRIES only
    (`entry_blockers`), so fills are still confirmed, completions placed and cut off, and the book
    settled while either is in force (2026-10-08 -- before that the halt returned ahead of all of it,
    so a halt mid-session left resting orders unwatched and the day unsettled)."""
    live = _live_cfg(config)
    unmet = []
    if not live.get("enabled"):
        unmet.append("live.enabled is false")
    if not str(live.get("gate0_confirmed") or "").strip():
        unmet.append("live.gate0_confirmed is empty — a human must attest Gate 0 (who/when)")
    arm = live.get("arm", DEFAULT_ARM)
    arms = _cfg.registry(config, label="flies")
    if arm not in arms:
        unmet.append(f"live.arm {arm!r} is not a configured arm")
    if not designated:
        unmet.append("no designated account — run `cherrypick account --module flies --set <last4>`")
    return unmet


def entry_blockers(config: dict, conn, day: str, *, halt_present: bool) -> list[str]:
    """Why no NEW live entry may be placed this tick; empty means entries may proceed. Everything
    that manages what is already open -- fill confirmation, completions, cutoff cancels, settlement
    -- runs regardless."""
    blockers = []
    if halt_present:
        blockers.append("halt_flag_present")
    if daily_loss_tripped(conn, day, _live_cfg(config).get("daily_loss_halt_dollars")):
        blockers.append("daily_loss_breaker")
    return blockers


def daily_loss_tripped(conn, day: str, limit_dollars: float | None) -> bool:
    """The daily-loss breaker over the LIVE ledger: settled net for `day` at or below
    -limit halts new entries. Open structures keep their normal hold-to-settlement rules,
    without exception — never a P&L-driven stop or adjustment."""
    if not limit_dollars:
        return False
    row = conn.execute(
        "SELECT COALESCE(SUM(pnl), 0) FROM fly_positions WHERE trade_date = ? AND status = 'settled'", (day,)
    ).fetchone()
    return float(row[0] or 0.0) <= -abs(limit_dollars)


def _cutoff_reached(now_min: int | None, params: dict) -> bool:
    cutoff = params.get("completion_cutoff", DEFAULT_CUTOFF)
    return now_min is not None and now_min >= _time_min(cutoff)


def _is_blocking(pos: dict, override_position_id: str | None) -> bool:
    """True if this open position counts as incomplete for `live.max_incomplete_spreads`.

    An open short vertical always counts. A completed fly counts only when its floor is negative
    (not risk-free) after fees, and even then only until a human names this exact position_id in
    `live.negative_floor_override` — so a stale override can never silently cover a different,
    later stuck position."""
    if pos.get("kind") != "fly":
        return True
    if fly.is_risk_free(pos):
        return False
    return pos.get("position_id") != override_position_id


def _blocking_positions(positions: list[dict], override_position_id: str | None) -> list[dict]:
    return [p for p in positions if p.get("status") == "open" and _is_blocking(p, override_position_id)]


def open_margin_dollars(positions: list[dict]) -> float:
    """Worst-case dollar exposure of every OPEN position, summed -- the buying power the live
    ledger says is committed right now, without asking the broker.

    Each position contributes `max(0, -position_floor)`: for an uncompleted short vertical that
    is width less credit, plus fees and the assignment reserve; a completed risk-free fly
    contributes nothing (its floor is already non-negative, it cannot lose). This is the same
    per-position worst case the console's "max possible loss" tile sums, applied to open rows."""
    return float(sum(max(0.0, -fly.position_floor(p)) for p in positions if p.get("status") == "open"))


def proposed_margin_dollars(plan: dict) -> float:
    """The buying power a planned short vertical would commit: width less the credit it collects,
    per contract. Fees and the assignment reserve are deliberately left out here -- the cap is
    a sizing buffer, and the ledger side above already carries them once the row exists."""
    return float(
        max(0.0, (plan["wing_width"] - plan["credit"]) * fly.CONTRACT_MULTIPLIER * plan.get("quantity", 1))
    )


def margin_cap_exceeded(cap: float | None, positions: list[dict], plan: dict) -> tuple[bool, float]:
    """Would opening `plan` push the ledger's open exposure past `live.max_open_margin_dollars`?
    Returns (exceeded, would_be_total). Off when the cap is null or zero. Pure."""
    if not cap:
        return False, 0.0
    total = open_margin_dollars(positions) + proposed_margin_dollars(plan)
    return total > float(cap), total


# --------------------------------------------------------------------------- fill realism telemetry
def _telemetry(log, fn, *args, **kwargs):
    """Run one `fill_facts` recorder call, swallowing any failure into the log. Fill-realism rows
    are measurement, written beside trading decisions that have already been made: losing one is a
    gap in the data, never a reason for a fill confirmation or an order placement to fail."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- telemetry must never break the loop
        log(f"fill telemetry {getattr(fn, '__name__', fn)} failed ({type(exc).__name__}: {exc})")
        return None


def _completion_limit(conn, pos: dict, params: dict) -> float:
    """The working completion order's limit: as recorded at placement, else the bound it was priced
    from, floored to the tick the way `resting_completion_spec` submits it."""
    order = dbmod.live_order(conn, pos["completion_order_id"]) if pos.get("completion_order_id") else None
    if order is not None and order.get("limit_price") is not None:
        return order["limit_price"]
    return live_orders.tick_floor(
        live_orders.max_safe_completion_debit(
            pos, params.get("min_floor_dollars", 0.0), params.get("fee_buffer", 0.10)
        )
    )


def _observe_working_orders(
    conn, snapshot: dict, positions: list[dict], params: dict, *, source: str, log
) -> int:
    """One `fly_order_path` row per working live order off this snapshot. Called before fill
    confirmation, so the look that notices a fill is itself on the path."""
    seen = 0
    for pos in positions:
        if pos.get("status") != "open":
            continue
        if pos.get("entry_fill_status") == "pending" and pos.get("entry_order_id"):
            leg, order_id, limit = fill_model.ENTRY, pos["entry_order_id"], pos.get("net")
        elif pos.get("completion_fill_status") == "pending" and pos.get("completion_order_id"):
            leg, order_id = fill_model.COMPLETION, pos["completion_order_id"]
            limit = _telemetry(log, _completion_limit, conn, pos, params)
        else:
            continue
        if _telemetry(
            log,
            fill_facts.observe,
            conn,
            snapshot,
            pos,
            leg=leg,
            order_id=order_id,
            limit_price=limit,
            source=source,
        ):
            seen += 1
    return seen


# --------------------------------------------------------------------------- fill confirmation
def measured_entry_slippage(
    mid: float | None, actual_credit: float | None, quantity: int | None
) -> float | None:
    """What a live entry fill conceded against the mid it was asked from, in dollars: (mid − actual
    credit) x100 x qty -- negative is price improvement. MEASURED, never modelled, and not a fee:
    like the model's concession on a paper row it is already inside the fill price. None when the
    submission mid was not recorded (rows placed before 2026-09-25), never a guessed zero."""
    if mid is None or actual_credit is None:
        return None
    return round((float(mid) - float(actual_credit)) * fly.CONTRACT_MULTIPLIER * int(quantity or 1), 2)


def _confirm_entry_fill(conn, pos: dict, broker, log) -> dict:
    """Poll a pending entry order; record the ACTUAL fill credit once confirmed. Returns the
    (possibly updated) position dict."""
    status = broker.status(pos["entry_order_id"])
    state, price = _execution.fill_state(status, fallback_price=pos["net"])
    if state == "filled":
        actual_credit = price  # the model only when the broker reported a fill without a parseable price
        slippage = measured_entry_slippage(
            pos.get("entry_mid_at_submit"), actual_credit, pos.get("quantity", 1)
        )
        conn.execute(
            "UPDATE fly_positions SET net = ?, credit = ?, entry_fill_status = 'filled', "
            "slippage_dollars = ? WHERE id = ?",
            (actual_credit, actual_credit, slippage, pos["id"]),
        )
        conn.commit()
        _telemetry(
            log,
            fill_facts.filled,
            conn,
            pos,
            leg=fill_model.ENTRY,
            order_id=pos["entry_order_id"],
            status=status,
            limit_price=pos["net"],
        )
        log(
            f"entry FILLED {pos['position_id']}: modeled {pos['net']:.2f} credit -> "
            f"actual {actual_credit:.2f}"
        )
        return {
            **pos,
            "net": actual_credit,
            "credit": actual_credit,
            "entry_fill_status": "filled",
            "slippage_dollars": slippage,
        }
    if state in _TERMINAL_UNFILLED:
        conn.execute(
            "UPDATE fly_positions SET entry_fill_status = ?, status = 'cancelled' WHERE id = ?",
            (state, pos["id"]),
        )
        conn.commit()
        _telemetry(
            log,
            fill_facts.resolved,
            conn,
            pos,
            leg=fill_model.ENTRY,
            order_id=pos["entry_order_id"],
            outcome=state,
            limit_price=pos["net"],
        )
        log(f"entry {state.upper()} {pos['position_id']} — never established")
        return {**pos, "entry_fill_status": state, "status": "cancelled"}
    return pos  # still working — stays pending, still blocks a second entry


def _confirm_completion_fill(conn, pos: dict, broker, log, spot: float | None = None) -> dict:
    """Poll a pending completion order; flip kind='fly' with the ACTUAL debit once confirmed.

    `spot` (the tick's own cached underlying price — never a fresh broker call, per streamer-
    before-API) records `completion_latency_min`/`spot_at_completion` the same way paper's
    book.py always has. Regression (2026-07-30): live never recorded either, so every live
    Performance card's Completion panel (median latency, latency range, median spot move) read
    blank for a real session with real completions."""
    status = broker.status(pos["completion_order_id"])
    state, price = _execution.fill_state(status, fallback_price=pos.get("debit") or 0.0)
    if state == "filled":
        actual_debit = price
        completion_fee = fly.vertical_open_fee(pos["symbol"], pos.get("quantity", 1))
        new_net = pos["net"] - actual_debit
        new_fees = (pos.get("fees") or 0.0) + completion_fee
        now = clock.now_iso()
        latency = bookmod._minutes_since(pos.get("entry_time"), now)
        updated = {**pos, "kind": "fly", "net": new_net, "fees": new_fees}
        floor = fly.position_floor(updated)
        risk_free = fly.is_risk_free(updated)
        conn.execute(
            "UPDATE fly_positions SET kind = 'fly', net = ?, debit = ?, fees = ?, floor_dollars = ?, "
            "risk_free = ?, completion_fill_status = 'filled', completed_at = ?, "
            "completion_latency_min = ?, spot_at_completion = ? WHERE id = ?",
            (new_net, actual_debit, new_fees, floor, int(risk_free), now, latency, spot, pos["id"]),
        )
        conn.commit()
        log(
            f"completion FILLED {pos['position_id']}: floor ${floor:.2f} after fees "
            f"({'risk-free' if risk_free else 'NOT risk-free'})"
        )
        # "The moment worth waking up for" (trade_notifier.py's own words for this event) — journaled
        # here, once, rather than at each of this function's two callers (run_once's own fill-
        # confirmation pass and the burst watcher), so neither path can silently skip it.
        dbmod.record_decision(
            conn,
            trade_date=pos["trade_date"],
            arm=pos["arm"],
            symbol=pos["symbol"],
            mode="completion",
            reason="completed",
            accepted=True,
            center=pos["center"],
            position_id=pos["position_id"],
            detail=f"debit {actual_debit:.2f}, floor ${floor:.2f} "
            f"({'risk-free' if risk_free else 'NOT risk-free'})",
            when=now,
        )
        _telemetry(
            log,
            fill_facts.filled,
            conn,
            pos,
            leg=fill_model.COMPLETION,
            order_id=pos["completion_order_id"],
            status=status,
            limit_price=actual_debit,
        )
        return {
            **updated,
            "floor_dollars": floor,
            "risk_free": int(risk_free),
            "completion_latency_min": latency,
            "spot_at_completion": spot,
        }
    if state in _TERMINAL_UNFILLED:
        conn.execute(
            "UPDATE fly_positions SET completion_order_id = NULL, completion_fill_status = ? WHERE id = ?",
            (state, pos["id"]),
        )
        conn.commit()
        _telemetry(
            log,
            fill_facts.resolved,
            conn,
            pos,
            leg=fill_model.COMPLETION,
            order_id=pos["completion_order_id"],
            outcome=state,
        )
        log(f"completion {state.upper()} {pos['position_id']} — still a short vertical, may retry")
        dbmod.record_decision(
            conn,
            trade_date=pos["trade_date"],
            arm=pos["arm"],
            symbol=pos["symbol"],
            mode="completion",
            reason=f"completion_{state}",
            center=pos["center"],
            position_id=pos["position_id"],
            when=clock.now_iso(),
        )
        return {**pos, "completion_order_id": None, "completion_fill_status": state}
    return pos  # still working


# --------------------------------------------------------------------------- completion placement
def place_resting_completion(conn, pos: dict, snapshot: dict, params: dict, broker, *, live: bool, log):
    """Place the resting completion order for a confirmed-filled short vertical.

    Guarded by an atomic DB claim so the fill watcher and the main tick can both try and
    exactly one wins; on a placement failure the claim is released so the next tick retries.
    Returns the updated position dict, or None if this caller lost the claim."""
    cur = conn.execute(
        "UPDATE fly_positions SET completion_order_id = 'PLACING' "
        "WHERE id = ? AND completion_order_id IS NULL",
        (pos["id"],),
    )
    conn.commit()
    if cur.rowcount == 0:
        return None  # someone else is placing (or placed) it

    try:
        spec = live_orders.resting_completion_spec(snapshot, pos, params)
    except ValueError as exc:
        conn.execute("UPDATE fly_positions SET completion_order_id = NULL WHERE id = ?", (pos["id"],))
        conn.commit()
        log(f"completion for {pos['position_id']} not placeable yet: {exc}")
        return pos

    # The completion's identity is the position's own, suffixed, for the same reason the entry's is
    # its position id: recovered by it in the seam, matchable by it in the orphan sweep.
    spec["external_identifier"] = f"{pos['position_id']}-completion"
    res = broker.place(spec, live=live)
    log(f"completion order ({'LIVE' if live else 'dry-run'}): {json.dumps(res, default=str)[:200]}")
    if res.get("ok") and live and res.get("order_id"):
        # The far strike is stamped at PLACEMENT, not fill: the order rests at the broker for the
        # session, and the leg it names needs to stay quoted the whole time it is working -- a
        # fill is confirmed by polling the order, never by pricing the leg, so a late stamp would
        # buy nothing and lose the mark path for a resting order (see db.py's column comment).
        completing = bookmod.leg_symbol_columns(
            snapshot, pos["side"], completing=live_orders.completing_long_strike(pos)
        ).get("completing_leg_symbol")
        conn.execute(
            "UPDATE fly_positions SET completion_order_id = ?, completion_fill_status = 'pending', "
            "completing_leg_symbol = COALESCE(?, completing_leg_symbol) WHERE id = ?",
            (str(res["order_id"]), completing, pos["id"]),
        )
        conn.commit()
        _telemetry(
            log,
            fill_facts.placed,
            conn,
            pos,
            leg=fill_model.COMPLETION,
            order_id=res["order_id"],
            limit_price=spec["price"],
            snapshot=snapshot,
        )
        return {
            **pos,
            "completion_order_id": str(res["order_id"]),
            "completion_fill_status": "pending",
            "completing_leg_symbol": completing or pos.get("completing_leg_symbol"),
        }
    # dry-run, or placement refused: release the claim so a later tick can retry
    conn.execute("UPDATE fly_positions SET completion_order_id = NULL WHERE id = ?", (pos["id"],))
    conn.commit()
    return pos


# --------------------------------------------------------------------------- entry management
def _manage_pending_entry(conn, pos: dict, snapshot: dict, params: dict, others: list, broker, log) -> dict:
    """Streamer-first management of a resting, unconfirmed entry order.

    Re-runs the entry evaluation against cached quotes (free). Unchanged center + credit
    within one tick -> leave the resting order alone, zero broker calls. Moved, or refused
    outright -> cancel; a fresh placement happens in this tick's entry stage. A failed cancel
    re-polls status — "already filled" is the expected race and gets recorded."""
    enter, reason, plan = engine.evaluate_credit_spread_entry(snapshot, params, others)
    if enter and plan["center"] == pos["center"] and abs(plan["credit"] - pos["net"]) < live_orders.TICK:
        return pos  # evaluation unchanged — the resting order stands, no broker call

    why = f"center {plan['center']}" if enter else f"refused ({reason})"
    res = broker.cancel(pos["entry_order_id"])
    if res.get("ok"):
        conn.execute(
            "UPDATE fly_positions SET status = 'cancelled', entry_fill_status = 'cancelled' WHERE id = ?",
            (pos["id"],),
        )
        conn.commit()
        # 'replaced' when the loop re-prices to a new order, 'cancelled' when it walks away: an
        # entry fill rule is fitted on both, and they are different non-fills.
        _telemetry(
            log,
            fill_facts.resolved,
            conn,
            pos,
            leg=fill_model.ENTRY,
            order_id=pos["entry_order_id"],
            outcome="replaced" if enter else "cancelled",
            limit_price=pos["net"],
        )
        log(f"entry {pos['position_id']} cancelled — evaluation moved to {why}")
        return {**pos, "status": "cancelled", "entry_fill_status": "cancelled"}
    # Cancel refused: the likeliest reason is a fill that beat us. Ask, and record if so.
    log(f"entry cancel refused for {pos['position_id']} ({res.get('error')}) — re-polling status")
    return _confirm_entry_fill(conn, pos, broker, log)


# --------------------------------------------------------------------------- orphan sweep
def _orphans_path() -> str:
    return os.path.join(_data_dir(), "live_orphans.json")


def _held_path() -> str:
    """The broker seam's persisted hold: a submit whose outcome is unknown, or an order found at
    the broker that no caller recorded. Refuses every live submit until resolved or acknowledged."""
    return os.path.join(_data_dir(), "live_held.json")


def _sweep_orphans(conn, broker, log, symbol: str) -> int | None:
    """Diff the broker's working orders (truth) against the ledger's order ids (belief),
    scoped to `symbol` — the one this arm actually trades.

    The one crash window nothing else covers: a tick dying between a successful place and the
    DB write leaves a real order resting at the broker with no ledger row — invisible to fill
    polling, entry management, and the cutoff cancel alike. Any working order the ledger has
    never heard of is persisted to live_orphans.json (which `--status` reports and the
    watchdog raises CRITICAL on) and logged loudly. Detection only — cancelling an order this
    process can't account for is a human's call, made with the broker UI open.

    Scoped to `symbol` because this account is not exclusively this loop's: a still-resting
    order this process didn't place but that shares the account (manual trading in another
    symbol, another module) is real and none of this process's business — the sweep exists to
    catch OUR crashed placements, not to audit the whole account. `working_orders()` already
    drops filled/cancelled/rejected orders (see cherrypick.core.broker.working_orders); the
    symbol filter is the second, independent reason a non-flies order shouldn't page anyone."""
    if not hasattr(broker, "working_orders"):
        return 0
    try:
        working = broker.working_orders()
    except Exception as exc:  # noqa: BLE001 — a failed sweep must not break the tick
        log(f"orphan sweep failed ({type(exc).__name__}: {exc}) — will retry next tick")
        return None  # unknown -- and unknown blocks new entries (run_once step 4)
    working = [o for o in working if o.get("underlying_symbol") == symbol]
    known = {
        str(r[0])
        for r in conn.execute(
            "SELECT entry_order_id FROM fly_positions WHERE entry_order_id IS NOT NULL "
            "UNION SELECT completion_order_id FROM fly_positions WHERE completion_order_id IS NOT NULL "
            "UNION SELECT close_order_id FROM fly_positions WHERE close_order_id IS NOT NULL"
        ).fetchall()
    }
    orphans = [o for o in working if str(o.get("order_id")) not in known]
    try:
        os.makedirs(_data_dir(), exist_ok=True)
        with open(_orphans_path(), "w", encoding="utf-8") as f:
            json.dump({"at": clock.now_iso(), "orphans": orphans}, f)
    except OSError:
        pass
    if orphans:
        log(
            f"ORPHANED ORDERS at the broker, unknown to the ledger: "
            f"{[o.get('order_id') for o in orphans]} — a placement was recorded nowhere "
            "(or another system is trading this account). Review in the broker UI before "
            "any further arming."
        )
    return len(orphans)


def read_orphans() -> list[dict]:
    try:
        with open(_orphans_path(), encoding="utf-8") as f:
            return json.load(f).get("orphans", [])
    except (OSError, ValueError):
        return []


def run_once(
    config: dict, snapshot: dict, conn, broker, *, live: bool, log=print, entry_block: list[str] | None = None
) -> dict:
    """One live iteration for the pinned arm — the full state machine.

    `broker` is the injected submission seam: place(spec, live) -> {ok, order_id?, ...},
    cancel(order_id) -> {ok}, status(order_id) -> {status, price, filled, ...}. With
    live=False every placement is a dry-run preflight (the rung-0 smoke) and no fill polling
    happens (a dry run places no real order)."""
    live_cfg = _live_cfg(config)
    arm = live_cfg.get("arm", DEFAULT_ARM)
    params = _merged_live_params(config, arm)
    day = snapshot["date"]
    symbol = snapshot["symbol"]
    # The intraday agent, in the mode today's arm record names (docs/intraday-agent-plan.md): off,
    # shadow (read and stamped, never acted on) or gates (a fresh decision sets the trend gate).
    # Anything going wrong here is the agent being off for the tick, never a failed tick.
    try:
        params, agent_ctx = intraday_advice.live_tick(
            params, intraday_advice.agent_config(config), read_arm_record(), day, time.time()
        )
    except Exception as exc:  # noqa: BLE001
        log(f"intraday agent context unavailable (agent off this tick): {type(exc).__name__}: {exc}")
        agent_ctx = {"mode": "off", "decision": None}
    summary = {
        "arm": arm,
        "live": live,
        "entered": 0,
        "completed_orders": 0,
        "cancelled": 0,
        "pending_orders": 0,
        "skips": [],
        "agent_mode": agent_ctx["mode"],
    }

    def journal(mode, reason, *, accepted=False, center=None, position_id=None, detail=None):
        """The same decision journal paper's book.py writes (fly_decisions) — live never called
        this at all until 2026-07-30's first live fill left the live dashboard's Session Timeline
        and Decision Journal cards permanently empty ("no data" on a real position). Mirrors
        book.py's `journal` closure so both ledgers read the same way on the dashboard."""
        dbmod.record_decision(
            conn,
            trade_date=day,
            arm=arm,
            symbol=symbol,
            mode=mode,
            reason=reason,
            accepted=accepted,
            center=center,
            position_id=position_id,
            detail=detail,
            when=clock.now_iso(),
        )

    # Feed-quality + "what this arm wanted" journals — every tick, before any gate, on the SAME
    # cadence paper_loop.py/book.py already write them on (paper_snapshot's refusal path is
    # recorded by the caller, main(), since run_once is never invoked without an ok snapshot).
    stats = snapshot.get("quote_stats") or {}
    dbmod.record_snapshot(
        conn,
        trade_date=day,
        symbol=symbol,
        status="ok",
        quotes_fresh=stats.get("fresh"),
        quotes_rejected=stats.get("rejected"),
        underlying_price=snapshot.get("underlying_price"),
    )
    wanted_center, wanted_reason = engine.select_center(snapshot, params)
    dbmod.record_iteration(
        conn,
        iteration_ts=clock.now_iso(),
        trade_date=day,
        symbol=symbol,
        arm=arm,
        center=wanted_center,
        center_reason=wanted_reason,
        underlying_price=snapshot.get("underlying_price"),
    )

    rows = conn.execute(
        "SELECT * FROM fly_positions WHERE trade_date = ? AND arm = ? AND status = 'open'", (day, arm)
    ).fetchall()
    positions = [dict(r) for r in rows]
    # Minute-of-day of each fill, the way paper's book._to_position stamps it (2026-09-17). The
    # engine's cadence clock and the miss-stop gate both read `entry_time_min`, and a raw ledger
    # row does not carry it -- so until now the live tick handed the engine rows with no fill
    # minute and both gates silently never fired live. None for an unreadable stamp, never 0.
    for p in positions:
        p["entry_time_min"] = bookmod._minute_of_day(p.get("entry_time"))

    # --- 0. orphan sweep: broker truth vs ledger belief ---
    if live:
        summary["orphaned_orders"] = _sweep_orphans(conn, broker, log, symbol)

    # --- 1. fill confirmation (before anything else acts on a position's current state) ---
    if live:
        # What each working order's market looks like on this tick, before any confirmation: the
        # look that notices a fill belongs on the path too (fill_facts.py; telemetry only).
        _observe_working_orders(conn, snapshot, positions, params, source="tick", log=log)
        updated = []
        for pos in positions:
            if pos.get("completion_order_id") and pos.get("completion_fill_status") == "pending":
                pos = _confirm_completion_fill(conn, pos, broker, log, snapshot.get("underlying_price"))
            elif pos.get("entry_order_id") and pos.get("entry_fill_status") not in (
                "filled",
                *_TERMINAL_UNFILLED,
            ):
                pos = _confirm_entry_fill(conn, pos, broker, log)
            if pos.get("status") == "open":
                updated.append(pos)
        positions = updated

    # --- 2. entry-order management (cache-gated cancel/replace of a resting entry) ---
    if live:
        managed = []
        for pos in positions:
            if pos.get("kind") == "short_vertical" and pos.get("entry_fill_status") == "pending":
                others = [p for p in positions if p is not pos]
                pos = _manage_pending_entry(conn, pos, snapshot, params, others, broker, log)
            if pos.get("status") == "open":
                managed.append(pos)
        positions = managed

    # --- 3. completion: place resting orders for confirmed spreads; cutoff-cancel working ones ---
    for pos in positions:
        if pos.get("kind") != "short_vertical":
            continue
        if live and pos.get("entry_fill_status") != "filled":
            continue  # unconfirmed entry — nothing to complete until we know we hold it
        if pos.get("completion_order_id"):
            # Telemetry: keep the counterfactual best-debit record honest while the order rests.
            _, _, plan = engine.evaluate_completion(snapshot, pos, params)
            if plan is not None:
                bookmod._record_best_debit(conn, pos, plan["market_debit"], clock.now_iso())
            if _cutoff_reached(snapshot.get("now_min"), params):
                res = broker.cancel(pos["completion_order_id"])
                if res.get("ok"):
                    conn.execute(
                        "UPDATE fly_positions SET completion_order_id = NULL, "
                        "completion_fill_status = NULL WHERE id = ?",
                        (pos["id"],),
                    )
                    conn.commit()
                    summary["cancelled"] += 1
                    _telemetry(
                        log,
                        fill_facts.resolved,
                        conn,
                        pos,
                        leg=fill_model.COMPLETION,
                        order_id=pos["completion_order_id"],
                        outcome="cutoff_cancelled",
                    )
                    journal(
                        "completion",
                        "cutoff_cancelled",
                        accepted=True,
                        center=pos["center"],
                        position_id=pos["position_id"],
                    )
                else:
                    log(f"cutoff cancel FAILED for {pos['position_id']}: {res.get('error')}")
                    journal(
                        "completion",
                        "cutoff_cancel_failed",
                        center=pos["center"],
                        position_id=pos["position_id"],
                        detail=res.get("error"),
                    )
            continue
        if not live:
            continue  # dry-run writes no rows, so there is nothing real to complete
        if _cutoff_reached(snapshot.get("now_min"), params):
            summary["skips"].append(
                {"position": pos.get("position_id"), "reason": "completion_cutoff_reached"}
            )
            journal(
                "completion",
                "completion_cutoff_reached",
                center=pos["center"],
                position_id=pos["position_id"],
            )
            continue
        placed = place_resting_completion(conn, pos, snapshot, params, broker, live=live, log=log)
        if placed is not None and placed.get("completion_order_id"):
            summary["completed_orders"] += 1
            journal(
                "completion",
                "placed",
                accepted=True,
                center=pos["center"],
                position_id=pos["position_id"],
                detail=f"resting order {placed.get('completion_order_id')}",
            )

    # --- 4. concurrency gate + entry ---
    # Per-day structure cap (live.max_structures_per_day, off when null): counts every
    # ESTABLISHED structure today — settled and risk-free-completed included — so unlike the
    # one-incomplete-at-a-time rule, freeing the slot never re-opens the day's budget. This is
    # the rung-1 throttle: set 1 for the plan doc's strict one-structure-per-day posture.
    day_capped = False
    # No new live entry on the last trading day of a quarter (2026-09-30), all day. Live-only by
    # construction: paper enters through book.py, never here, so the paper books keep measuring the
    # day. Everything above this step -- fills, resting orders, completions -- still runs, and
    # settlement is its own path. Applied on the dry run too, so the rehearsal says what live does.
    if _quarter_end(day):
        day_capped = True
        summary["skips"].append({"entry": "quarter-end session: no new live entries"})
        journal("entry", QUARTER_END_REASON, center=wanted_center)
    # The halt flag and the daily-loss breaker (`entry_blockers`): no new entry, and nothing else
    # held back -- every step above this one has already run.
    # Orders at the broker the ledger never recorded -- or a sweep that could not look -- stop new
    # entries (2026-10-08): detection alone let the same tick place another order beside them.
    orphan_count = summary.get("orphaned_orders", 0)
    if live and orphan_count != 0:
        entry_block = [
            *(entry_block or []),
            "orphan_sweep_failed" if orphan_count is None else "orphaned_orders",
        ]
    if entry_block and not day_capped:
        day_capped = True
        summary["skips"].append({"entry": f"entries blocked: {', '.join(entry_block)}"})
        journal("entry", "entries_blocked", center=wanted_center, detail=", ".join(entry_block))
    day_cap = live_cfg.get("max_structures_per_day")
    if live and day_cap and not day_capped:
        established = conn.execute(
            "SELECT COUNT(*) FROM fly_positions WHERE trade_date = ? AND arm = ? AND status != 'cancelled'",
            (day, arm),
        ).fetchone()[0]
        day_capped = established >= day_cap
        if day_capped:
            summary["skips"].append({"entry": f"max_structures_per_day reached ({established}/{day_cap})"})
            journal(
                "entry",
                "max_structures_per_day_reached",
                center=wanted_center,
                detail=f"{established}/{day_cap}",
            )

    # Count limit on incomplete positions (live.max_incomplete_spreads). Off by default since
    # 2026-09-25: the pilot now sizes by the buying-power cap alone, which counts an uncompleted
    # vertical and a negative-floor fly at their worst case. The pilot ran with 1 until then --
    # "one incomplete position at a time" -- and setting 1 restores exactly that. A replay of
    # control's paper era (08-21..09-24): the rule netted +$2,130 with a -$883 max drawdown and a
    # -$298 worst session; the $1,000 cap alone +$3,119, -$1,256 and -$818.
    limit = live_cfg.get("max_incomplete_spreads")
    incomplete = _blocking_positions(positions, live_cfg.get("negative_floor_override"))
    blockers = incomplete if limit and len(incomplete) >= int(limit) else []
    if day_capped:
        pass  # the day's structure budget is spent — no entry evaluation at all
    elif blockers:
        negative = [p for p in blockers if p.get("kind") == "fly"]
        if negative:
            p = negative[0]
            summary["skips"].append(
                {
                    "entry": f"blocked: completed fly {p['position_id']} has a negative floor "
                    f"(${fly.position_floor(p):.2f}) — set live.negative_floor_override to "
                    f"{p['position_id']!r} to permit a new entry"
                }
            )
            journal(
                "entry",
                "negative_floor_blocks",
                center=p["center"],
                position_id=p["position_id"],
                detail=f"floor ${fly.position_floor(p):.2f}",
            )
        else:
            summary["skips"].append(
                {"entry": f"blocked: {blockers[0]['position_id']} is still an incomplete spread"}
            )
            journal(
                "entry",
                "incomplete_spread_blocks",
                center=blockers[0]["center"],
                position_id=blockers[0]["position_id"],
            )
    else:
        enter, reason, plan = engine.evaluate_credit_spread_entry(snapshot, params, positions)
        # Buying-power cap (live.max_open_margin_dollars, off when null): the ledger's open
        # worst-case exposure plus this spread's own must fit under it. Checked AFTER the engine
        # accepts, because the credit -- and so the margin -- is only known from the plan. Local
        # and deterministic on purpose: the account governor (account_deploy_limit_pct) is
        # fail-closed on a balance read, and a transient broker failure blocking a legitimate
        # entry is the reason it was switched off for the pilot; this cap reads nothing remote.
        margin_cap = live_cfg.get("max_open_margin_dollars")
        if enter and margin_cap:
            exceeded, would_be = margin_cap_exceeded(margin_cap, positions, plan)
            if exceeded:
                enter = False
                reason = "max_open_margin_reached"
                summary["skips"].append(
                    {
                        "entry": f"max_open_margin_reached (${would_be:.0f} would exceed ${float(margin_cap):.0f})"
                    }
                )
                journal(
                    "entry",
                    reason,
                    center=plan["center"],
                    detail=f"open+proposed ${would_be:.2f} > cap ${float(margin_cap):.2f}",
                )
                plan = None
        if not enter:
            if reason != "max_open_margin_reached":
                summary["skips"].append({"entry": reason})
                # plan is None on refusal, so there's no plan["center"] -- wanted_center (this arm's
                # target strike, computed above for the iteration journal) is the one useful thing to
                # carry, same choice book.py's own refusal-path journal call makes.
                journal("entry", reason, center=wanted_center)
        else:
            spec = live_orders.entry_spec(snapshot, plan)
            entry_price = plan["credit"]
            # The mid the order is asked from, for the fill's measured slippage: the fresh quotes'
            # when re-priced below, else the plan's own (its credit plus what the model conceded).
            entry_mid = round(plan["credit"] + plan.get("slippage", 0.0), 4)
            # FRESH-QUOTE CHECK (live-only, entry-only — the one narrow exception to "cached
            # quotes gate broker calls"): the cached snapshot's credit can diverge from what the
            # broker's real-time execution-quality check considers marketable (a low-liquidity
            # 1-wide 0DTE vertical is the case that surfaced this — a "Spread Checker" rejection
            # on 2026-07-30 with the preflight dry-run showing no warning at all). Immediately
            # before submitting, and only when live, re-price off one fresh REST quote and refuse
            # to submit rather than risk another rejection on stale data.
            if live:
                symbols = [leg["symbol"] for leg in spec["legs"]]
                fresh = broker.fresh_quotes(symbols)
                new_price, info = live_orders.entry_fresh_reprice(spec, fresh, params.get("slippage_frac"))
                tolerance = live_cfg.get("fresh_quote_tolerance_dollars", 0.05)
                if new_price is None:
                    summary["skips"].append(
                        {
                            "entry": f"skipped: fresh quote unavailable "
                            f"({info['reason']}: {info.get('missing')})"
                        }
                    )
                    journal(
                        "entry",
                        "fresh_quote_unavailable",
                        center=plan["center"],
                        detail=f"{info['reason']}: {info.get('missing')}",
                    )
                    spec = None
                elif plan["credit"] - info["fresh_credit"] > tolerance:
                    summary["skips"].append(
                        {
                            "entry": f"skipped: fresh quote diverged (cached {plan['credit']:.2f}, "
                            f"fresh {info['fresh_credit']:.2f}, tolerance {tolerance})"
                        }
                    )
                    journal(
                        "entry",
                        "fresh_quote_diverged",
                        center=plan["center"],
                        detail=f"cached {plan['credit']:.2f}, fresh {info['fresh_credit']:.2f}",
                    )
                    spec = None
                else:
                    spec["price"] = new_price
                    entry_price = new_price
                    entry_mid = info.get("fresh_mid", entry_mid)
            if spec is not None:
                # Per-attempt unique (paper's book.py convention: microsecond timestamp), not
                # day+arm+center alone — a retry at the SAME centre after an earlier rejection
                # used to collide on the same position_id, and the UPSERT silently overwrote
                # the rejected attempt's row, erasing it from the ledger entirely (surfaced
                # 2026-07-30: the orphan sweep kept re-flagging an order the ledger used to
                # know about, because the row that recorded it no longer existed).
                #
                # Minted BEFORE the submit and sent as the order's `external_identifier`
                # (2026-09-17): the broker's copy of the order then carries the ledger's own key,
                # so an uncertain outcome is recovered by it in the seam rather than re-placed,
                # and an orphan the sweep finds can be matched to the row that meant to record it.
                pid = f"live-{arm}-{int(plan['center'])}-{clock.now_et().strftime('%Y%m%d%H%M%S%f')}"
                spec["external_identifier"] = pid
                res = broker.place(spec, live=live)
                log(f"entry order ({'LIVE' if live else 'dry-run'}): {json.dumps(res, default=str)[:200]}")
                if res.get("ok") and live and res.get("order_id"):
                    # Worst-case dollar outcome as of THIS OPEN credit spread — full defined risk
                    # (-W), net of trading fees AND the worst-case exercise-assignment fee (both
                    # legs ITM), so the dashboard's Floor column has a real number for an
                    # uncompleted position instead of sitting blank until it completes.
                    floor = fly.position_floor(
                        {
                            "kind": "short_vertical",
                            "side": plan["side"],
                            "center": plan["center"],
                            "wing_width": plan["wing_width"],
                            "quantity": plan["quantity"],
                            "net": entry_price,
                            "fees": plan["open_fee"],
                        }
                    )
                    dbmod.save_position(
                        conn,
                        {
                            "position_id": pid,
                            "book_id": bookmod.book_id_for(day, arm, snapshot["symbol"]),
                            "trade_date": day,
                            "arm": arm,
                            "entry_mode": "legged",
                            "symbol": snapshot["symbol"],
                            "kind": "short_vertical",
                            "side": plan["side"],
                            "center": plan["center"],
                            "wing_width": plan["wing_width"],
                            "quantity": plan["quantity"],
                            # `entry_price` is the fresh-repriced value when live (what was
                            # actually submitted), else the cached `plan["credit"]` unchanged.
                            "net": entry_price,
                            "credit": entry_price,
                            "entry_mid_at_submit": entry_mid,
                            "fees": plan["open_fee"],
                            "floor_dollars": floor,
                            "risk_free": 0,
                            "status": "open",
                            "entry_window": plan.get("entry_window"),
                            # center_reason and the regime tags were paper-only until 2026-08-01,
                            # so live centre selection was unauditable -- every live row had
                            # center_reason NULL, and the `gex` arm (the live default) gave no
                            # record of whether it centred on gamma or degraded to ATM. The plan
                            # has always carried it; only the live writer dropped it.
                            "center_reason": plan.get("center_reason"),
                            "entry_center_delta": plan.get("center_delta"),
                            "completing_direction": plan.get("completing_direction"),
                            "underlying_at_entry": snapshot.get("underlying_price"),
                            **bookmod.regime_columns("entry", snapshot, params, center=plan.get("center")),
                            # The two contracts just ordered, as streamer symbols, so the live
                            # request file's leg query can keep them quoted once spot leaves the
                            # ATM window (see stream_request.py). Same source as the OCC symbols on
                            # the order: the snapshot quote at each strike carries both forms.
                            **bookmod.leg_symbol_columns(
                                snapshot,
                                plan["side"],
                                **bookmod.entry_leg_strikes(
                                    "short_vertical", plan["side"], plan["center"], plan["wing_width"]
                                ),
                            ),
                            "entry_time": clock.now_iso(),
                            "entry_order_id": str(res["order_id"]),
                            "entry_fill_status": "pending",
                            **_agent_stamp(agent_ctx, snapshot, params, plan["side"], log),
                        },
                    )
                    _telemetry(
                        log,
                        fill_facts.placed,
                        conn,
                        {
                            "trade_date": day,
                            "position_id": pid,
                            "side": plan["side"],
                            "center": plan["center"],
                            "wing_width": plan["wing_width"],
                            "quantity": plan["quantity"],
                        },
                        leg=fill_model.ENTRY,
                        order_id=res["order_id"],
                        limit_price=entry_price,
                        snapshot=snapshot,
                        mid_at_submit=entry_mid,
                    )
                    journal(
                        "entry",
                        "entered",
                        accepted=True,
                        center=plan["center"],
                        position_id=pid,
                        detail=f"credit {entry_price:.2f}, order {res['order_id']}",
                    )
                else:
                    if not live:
                        decision_reason = "dry_run"
                    elif not res.get("ok"):
                        decision_reason = "placement_failed"
                    else:
                        decision_reason = "order_id_missing"  # placed but unrecordable — investigate
                    journal("entry", decision_reason, center=plan["center"], detail=res.get("error"))
                # A live order that never reached the ledger was not entered: counting it spawned a
                # fill watcher with nothing to watch (2026-10-08). A dry run still counts its would-be entry.
                if not live or (res.get("ok") and res.get("order_id")):
                    summary["entered"] += 1

    # --- 4b. the agent's named closes, TAGGED on the live rows at live natural (close_tags.py) ---
    # Never an order: no live closing path exists (intraday_advice.LIVE_CLOSES_BUILT). The tags are
    # how the shadow's closes are scored against real live quotes.
    decision = agent_ctx.get("decision")
    if live and decision and decision.get("close_stranded"):
        try:
            tagged = close_tags.tag(
                conn,
                snapshot,
                arm,
                params,
                source=close_tags.AGENT,
                agent_ids=decision["close_stranded"],
                now=clock.now_iso(),
            )
            for t in tagged:
                log(f"agent close tagged (live {agent_ctx['mode']}, not executed): {t}")
        except Exception as exc:  # noqa: BLE001
            log(f"agent close tagging failed (non-fatal): {type(exc).__name__}: {exc}")

    # --- 5. live book roll-up (so the dashboard/analytics/settled-marker see the live day) ---
    if live:
        final_rows = conn.execute(
            "SELECT * FROM fly_positions WHERE trade_date = ? AND arm = ? AND status != 'cancelled'",
            (day, arm),
        ).fetchall()
        if final_rows:
            book_positions = [bookmod._to_position(dict(r)) for r in final_rows]
            symbol = live_cfg.get("symbol") or (final_rows[0]["symbol"] if final_rows else "XSP")
            bookmod._save_book(
                conn, bookmod.book_id_for(day, arm, symbol), day, arm, symbol, book_positions, params
            )
        pending = conn.execute(
            "SELECT COUNT(*) FROM fly_positions WHERE trade_date = ? AND arm = ? AND status = 'open' "
            "AND (entry_fill_status = 'pending' OR completion_fill_status = 'pending')",
            (day, arm),
        ).fetchone()[0]
        summary["pending_orders"] = int(pending)
        summary["marks"] = _record_marks(conn, snapshot, day, [dict(r) for r in final_rows], params)

    return summary


def _agent_stamp(agent_ctx: dict, snapshot: dict, params: dict, side: str, log) -> dict:
    """`intraday_advice.entry_stamp`, never allowed to cost the entry's row."""
    try:
        return intraday_advice.entry_stamp(agent_ctx, snapshot, params, side)
    except Exception as exc:  # noqa: BLE001
        log(f"agent entry stamp failed (non-fatal): {type(exc).__name__}: {exc}")
        return {}


def _record_marks(conn, snapshot: dict, day: str, rows: list[dict], params: dict) -> int:
    """Mark every OPEN live position at mid off this tick's cached quotes (2026-09-17). Pure
    telemetry, after every decision has been made; a position with an unquoted leg gets no row
    (a mid with a hole is not a mark). `open_margin` is the same open worst-case figure the
    buying-power cap reads, so the page and the gate can never disagree about it."""
    if not snapshot.get("ok"):
        return 0
    open_rows = [r for r in rows if r.get("status") == "open"]
    if not open_rows:
        return 0
    margin = open_margin_dollars(open_rows)
    spot = snapshot.get("underlying_price")
    written = 0
    for pos in open_rows:
        value = fly.structure_mid(pos, lambda side, strike: engine.quote(snapshot, side, strike))
        if value is None:
            continue
        resting = None
        if pos.get("completion_fill_status") == "pending" and pos.get("kind") == "short_vertical":
            resting = live_orders.max_safe_completion_debit(
                pos, params.get("min_floor_dollars", 0.0), params.get("fee_buffer", 0.10)
            )
        dbmod.record_live_mark(
            conn,
            trade_date=day,
            position_id=pos["position_id"],
            kind=pos["kind"],
            structure_mid=round(value, 4),
            mark_pnl=round(fly.mark_pnl(pos, lambda side, strike: engine.quote(snapshot, side, strike)), 2),
            spot=spot,
            open_margin=round(margin, 2),
            resting_limit=resting,
            iteration_ts=snapshot.get("iteration_ts"),
        )
        written += 1
    return written


# --------------------------------------------------------------------------- settlement
def session_already_settled(conn, day: str) -> bool:
    """Book state is the settled marker, same as paper (see paper_loop.session_already_settled
    for why a file must never be)."""
    total, settled = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(status = 'settled'), 0) FROM fly_books WHERE trade_date = ?", (day,)
    ).fetchone()
    return total > 0 and total == settled


# Settlement-source values that represent a genuinely POSTED closing print (see
# `broker_cli.official_settlement_price`). Anything else -- an intraday last/mark tick, or the
# stream cache's last trade -- is provisional and must keep the retry alive.
_OFFICIAL_SOURCES = _settlement.OFFICIAL_SOURCES  # one rule for what a cash-settled ledger may settle on


def _is_official_source(source: str | None) -> bool:
    return source in _OFFICIAL_SOURCES


def session_officially_settled(conn, day: str) -> bool:
    """Stricter than `session_already_settled`: true only once EVERY book has the OFFICIAL print,
    not just a provisional one. Gates the tick's own settlement call — a merely provisional
    session must keep being retried (auto-fetching the official price, see `run_settle_live`) on
    every subsequent tick until it upgrades or the loop self-disarms, rather than being treated as
    done the moment any settlement — even a stale last-trade guess — lands."""
    marks = ",".join("?" * len(_OFFICIAL_SOURCES))
    total, official = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(status = 'settled' AND settlement_source IN ({marks})), 0) "
        f"FROM fly_books WHERE trade_date = ?",
        (*sorted(_OFFICIAL_SOURCES), day),
    ).fetchone()
    return total > 0 and total == official


def _relabel_reconciled_settlement(conn, book_id, held, reconciled, settlement, source) -> dict:
    """Re-settle a book the broker has already confirmed: correct the recorded print, keep the money.

    After `fee_reconcile` a row's net, fees, gross and P&L are the broker's own cash, not the model's,
    and the settlement price no longer decides any of them. Recomputing them at a new print would
    overwrite real cash with the model, so only the price and its source change. A book reconciled
    only in part is refused: its unconfirmed rows would need the model and its confirmed ones must
    not get it, and no single settlement does both.
    """
    if len(reconciled) != len(held):
        return {
            "ok": False,
            "book_id": book_id,
            "reason": (
                f"{len(reconciled)} of {len(held)} positions are broker-reconciled; re-settle refused "
                "until fee_reconcile confirms the rest"
            ),
        }
    conn.execute(
        "UPDATE fly_positions SET settlement_price = ?, settlement_source = ? WHERE book_id = ? AND status = 'settled'",
        (settlement, source, book_id),
    )
    conn.execute(
        "UPDATE fly_books SET settlement_price = ?, settlement_source = ? WHERE book_id = ?",
        (settlement, source, book_id),
    )
    conn.commit()
    book = conn.execute("SELECT pnl FROM fly_books WHERE book_id = ?", (book_id,)).fetchone()
    _log(f"LIVE re-labelled {book_id} at {settlement:.2f} ({source}); broker-reconciled money unchanged")
    return {
        "ok": True,
        "book_id": book_id,
        "settlement": settlement,
        "source": source,
        "money": "broker_reconciled_unchanged",
        "pnl": book["pnl"] if book is not None else None,
    }


def run_settle_live(
    config: dict,
    conn,
    *,
    cache_path: str,
    when=None,
    price: float | None = None,
    force: bool = False,
    broker=None,
) -> dict:
    """Settle the live arm's books. No `price` = try the official print first (`broker`, when
    given, auto-fetches it — tastytrade -> Yahoo -> Barchart, see
    `broker_cli.official_settlement_price`); if that comes up empty, falls back to the provisional
    path (last streamed trade, marked 'last_trade_provisional'). An explicit `price` always wins
    and marks 'official' directly (overwrites provisional, refuses to overwrite an existing
    official settlement without `force`).

    The auto-fetch is retried on every call, not just the first: a still-provisional book (source
    != 'official') keeps trying on each subsequent tick until a source answers or the loop
    self-disarms — the real settlement print isn't guaranteed to exist the instant the market
    closes."""
    when = when or provider.now_et()
    day = when.date().isoformat()
    live_cfg = _live_cfg(config)
    arm = live_cfg.get("arm", DEFAULT_ARM)
    symbol = live_cfg.get("symbol", "XSP")
    book_id = bookmod.book_id_for(day, arm, symbol)

    existing = conn.execute("SELECT status, settlement_source FROM fly_books WHERE book_id = ?", (book_id,))
    row = existing.fetchone()
    already_official = (
        row is not None and row["status"] == "settled" and _is_official_source(row["settlement_source"])
    )
    already_settled = row is not None and row["status"] == "settled"

    if already_official and not force:
        return {"ok": False, "reason": "already settled with the official print (use --force to override)"}

    auto_source = None
    if price is None and broker is not None and not already_official:
        auto_price, auto_reason = broker.official_settlement_price(symbol)
        if auto_price is not None:
            price, auto_source = auto_price, auto_reason
        elif not already_settled:
            _log(f"live settle: official price auto-fetch unavailable ({auto_reason}) — using last trade")

    if already_settled and price is None:
        return {"ok": True, "skipped": "already_settled", "book_id": book_id}

    if price is None:
        settle_max_age = config.get("defaults", {}).get("settlement_max_age_seconds", 300)
        settlement = provider.read_spot(cache_path, symbol, max_age_seconds=settle_max_age)
        if settlement is None:
            _log(
                f"live settle: no {symbol} price within {settle_max_age}s — will retry next tick "
                f"(or run --settle --price <official>)"
            )
            return {"ok": False, "reason": "no_settlement_price"}
        source = "last_trade_provisional"
    else:
        settlement = price
        # Only a POSTED CLOSING value earns 'official'. An intraday last/mark tick is marked
        # provisional so `session_officially_settled` stays False and the loop keeps retrying
        # until a real close posts (or the day disarms and a human confirms with --price).
        # Before 2026-07-31 every auto-fetch was stamped 'official' regardless of which field
        # answered, which both overstated the number and stopped the retry.
        # Keep WHICH source answered rather than flattening to a bare "official" -- the provenance
        # stays recoverable from the ledger. A human-supplied --price (auto_source None) is
        # official by definition. `_OFFICIAL_SOURCES` decides which of these still count as
        # official for `session_officially_settled`'s retry gate.
        source = auto_source if auto_source is not None else "official"
        if already_settled:
            held = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM fly_positions WHERE book_id = ? AND status = 'settled'", (book_id,)
                ).fetchall()
            ]
            reconciled = [r for r in held if r["broker_reconciliation_status"] == "reconciled"]
            if reconciled:
                return _relabel_reconciled_settlement(conn, book_id, held, reconciled, settlement, source)
            # Re-settle: flip the book's rows back to open so settle_book recomputes at the
            # official print through the exact same tested path as the first settlement. The first
            # settlement folded its expiry fee into `fees`, and settling again adds one, so it comes
            # out first -- left in, every re-settle charged each ITM strike twice. Rows settled
            # before `settlement_fees` was recorded get theirs recomputed at the price they settled at.
            for r in held:
                prior = r["settlement_fees"]
                if prior is None:
                    prior = fly.expire_fee(fly.itm_legs_at_settlement(r, r["settlement_price"]))
                conn.execute(
                    "UPDATE fly_positions SET status = 'open', fees = ? WHERE position_id = ?",
                    (round((r["fees"] or 0.0) - prior, 2), r["position_id"]),
                )
            conn.execute("UPDATE fly_books SET status = 'open' WHERE book_id = ?", (book_id,))
            conn.commit()

    result = bookmod.settle_book(conn, day, arm, symbol, settlement, config)
    conn.execute(
        "UPDATE fly_positions SET settlement_source = ? WHERE book_id = ? AND status = 'settled'",
        (source, book_id),
    )
    conn.execute("UPDATE fly_books SET settlement_source = ? WHERE book_id = ?", (source, book_id))
    conn.commit()
    _log(
        f"LIVE settled {book_id} at {settlement:.2f} "
        f"({source}{f', auto-fetched via {auto_source}' if auto_source else ''}): "
        f"P&L {result['pnl']:+.2f} "
        f"({result['itm_legs']} ITM leg(s), ${result['assignment_fees']:.2f} assignment fees)"
        + (
            " — confirm with: python src/live_loop.py --settle --price <official print>"
            if source == "last_trade_provisional"
            else ""
        )
    )

    # The live day's written record — refreshed on the official re-settle too. Best-effort:
    # a report hiccup must never fail the settlement itself.
    report = None
    return {
        "ok": True,
        "book_id": book_id,
        "settlement": settlement,
        "source": source,
        "report": report,
        **result,
    }


# --------------------------------------------------------------------------- fill watcher
def _natural_prices(snapshot: dict, pos: dict) -> dict:
    """Cache-side view of whether a working order's limit is touchable right now.

    Entry (sell center / buy wing): natural credit = bid(center) - ask(long wing).
    Completion (buy far / sell center): natural debit = ask(far) - bid(center).
    """
    side, center, width = pos["side"], pos["center"], pos["wing_width"]
    out = {}
    center_q = engine.quote(snapshot, side, center)
    entry_long = center - width if side == engine.PUT else center + width
    entry_long_q = engine.quote(snapshot, side, entry_long)
    if center_q and entry_long_q:
        out["entry_natural_credit"] = (center_q.get("bid") or 0.0) - (entry_long_q.get("ask") or 0.0)
    far = live_orders.completing_long_strike(pos)
    far_q = engine.quote(snapshot, side, far)
    if center_q and far_q:
        out["completion_natural_debit"] = (far_q.get("ask") or 0.0) - (center_q.get("bid") or 0.0)
    return out


def run_watch(
    config: dict,
    conn,
    broker,
    *,
    cache_path: str,
    live: bool,
    seconds: int | None = None,
    poll: int | None = None,
    heartbeat: int | None = None,
    log=None,
    sleep=time.sleep,
    clock_fn=time.monotonic,
) -> dict:
    """The burst fill-watcher: for up to `seconds`, learns about fills either by PUSH (tastytrade's
    account-alert websocket, `use_order_alert_stream`) or by cache-first polling of pending orders.

    Streamer-before-API, extended 2026-07-31 with a second kind of stream: the shared market-data
    cache still gates the fallback poll exactly as before (only calls `.status()` when cached
    quotes show the market touching the working limit, or when `heartbeat` has elapsed since that
    order's last real poll). When `use_order_alert_stream` is on, each cycle ALSO blocks up to
    `poll` seconds on `broker.wait_for_order_alerts` — tastytrade's own account-alert websocket,
    not the market-data cache — for a push notification on any pending order; a hit there is
    treated exactly like a cache-touch (still routed through the ordinary `_confirm_*_fill` ->
    `.status()` call, not a separate code path, so the DB-write logic never forks in two). Fails
    closed: any streamer error is invisible here (`BrokerAdapter.wait_for_order_alerts` already
    swallows it to `[]`), so a bad websocket just falls back to being byte-for-byte the pre-2026-07-31
    cache-gated polling loop. On confirming an ENTRY fill it immediately places the resting
    completion (atomic claim — the main tick is the fallback placer). It never cancels and makes
    no other decision."""
    log = log or _log
    live_cfg = _live_cfg(config)
    arm = live_cfg.get("arm", DEFAULT_ARM)
    params = _merged_live_params(config, arm)
    seconds = seconds if seconds is not None else live_cfg.get("fill_watch_seconds", DEFAULT_WATCH_SECONDS)
    poll = poll if poll is not None else live_cfg.get("fill_watch_poll_seconds", DEFAULT_WATCH_POLL_SECONDS)
    heartbeat = (
        heartbeat
        if heartbeat is not None
        else live_cfg.get("fill_heartbeat_seconds", DEFAULT_HEARTBEAT_SECONDS)
    )
    use_alert_stream = live_cfg.get("use_order_alert_stream", False)
    use_alert_daemon = live_cfg.get("use_order_alert_daemon", False)
    symbol = live_cfg.get("symbol", "XSP")
    start = clock_fn()
    deadline = start + seconds
    # Watcher start counts as each order's "last poll": the tick that spawned us just talked to
    # the broker, so the first real poll is also cache-gated rather than automatic.
    last_poll: dict[str, float] = {}
    cycles = 0
    confirmed = 0

    # Daemon mode: read the WAL inbox a separately-running alert daemon appends to, rather than
    # opening our own websocket per cycle. `alerts_checkpoint` starts at None so a freshly spawned
    # watcher sees everything on file for its pending orders (it has no prior checkpoint of its
    # own), then advances past what it has already acted on. A failure to open the inbox is not
    # fatal -- it just leaves the watcher on its ordinary cache-gated polling.
    alerts_conn = None
    alerts_checkpoint: str | None = None
    if use_alert_daemon:
        try:
            alerts_conn = alerts_db.connect()
        except Exception as exc:  # noqa: BLE001
            log(f"alert inbox unavailable ({type(exc).__name__}: {exc}) -- falling back to polling")

    while clock_fn() < deadline:
        day = provider.now_et().date().isoformat()
        rows = conn.execute(
            "SELECT * FROM fly_positions WHERE trade_date = ? AND arm = ? AND status = 'open' "
            "AND (entry_fill_status = 'pending' OR completion_fill_status = 'pending')",
            (day, arm),
        ).fetchall()
        pending = [dict(r) for r in rows]
        if not pending:
            break
        cycles += 1

        snapshot = provider.build_snapshot(
            cache_path,
            symbol,
            **provider.snapshot_kwargs(config),
        )
        naturals_ok = bool(snapshot.get("ok"))
        if live:
            # The watcher looks every ~`poll` seconds while an order works -- six times the main
            # tick's resolution -- so its looks are most of each order's path (fill_facts.py).
            _observe_working_orders(conn, snapshot, pending, params, source="watch", log=log)

        # Each position's relevant order this cycle (entry > completion, same priority the
        # per-position loop below uses) -- computed once so the alert-wait call and the
        # per-position confirm loop agree on exactly which order_id each position means.
        order_id_for_pos = {}
        for pos in pending:
            if pos.get("entry_fill_status") == "pending":
                order_id_for_pos[pos["position_id"]] = pos["entry_order_id"]
            else:
                order_id_for_pos[pos["position_id"]] = pos["completion_order_id"]

        alerted_ids: set = set()
        alert_order_ids: set = set()
        remaining = deadline - clock_fn()
        pending_order_ids = {str(oid) for oid in order_id_for_pos.values() if oid}
        if use_alert_daemon and alerts_conn is not None and pending_order_ids:
            # Daemon mode: the websocket is already open in a separate process, appending to the
            # WAL inbox. Read it (a local query, no network) instead of opening our own socket.
            try:
                inbox = alerts_db.alerts_since(alerts_conn, pending_order_ids, alerts_checkpoint)
            except Exception as exc:  # noqa: BLE001 -- an unreadable inbox is never fatal here
                log(f"alert inbox read failed ({type(exc).__name__}: {exc}) -- falling back to polling")
                inbox = []
            alerted_ids = {str(a["order_id"]) for a in inbox}
            if inbox:
                alerts_checkpoint = max(a["received_at"] for a in inbox)
        elif use_alert_stream and remaining > 0:
            wait_for = max(0.0, min(poll, remaining))
            alert_order_ids = pending_order_ids
            alerts = broker.wait_for_order_alerts(alert_order_ids, wait_for) if alert_order_ids else []
            alerted_ids = {str(a["order_id"]) for a in alerts}

        for pos in pending:
            is_entry = pos.get("entry_fill_status") == "pending"
            order_id = order_id_for_pos[pos["position_id"]]
            touched = True  # no cache view -> fall through to the heartbeat-limited poll
            if naturals_ok:
                nat = _natural_prices(snapshot, pos)
                if is_entry and "entry_natural_credit" in nat:
                    touched = nat["entry_natural_credit"] >= pos["net"] - live_orders.TICK
                elif not is_entry and "completion_natural_debit" in nat:
                    # The resting completion sits at the max safe bound; recompute it for the gate.
                    bound = live_orders.max_safe_completion_debit(
                        pos, params.get("min_floor_dollars", 0.0), params.get("fee_buffer", 0.10)
                    )
                    touched = nat["completion_natural_debit"] <= bound + live_orders.TICK
            # A push alert is treated exactly like a cache touch -- still confirmed through the
            # ordinary .status() call below, never a second, divergent write path.
            touched = touched or str(order_id) in alerted_ids
            due = clock_fn() - last_poll.get(str(order_id), start) >= heartbeat
            if not touched and not due:
                continue
            last_poll[str(order_id)] = clock_fn()
            if is_entry:
                updated = _confirm_entry_fill(conn, pos, broker, log)
                if updated.get("entry_fill_status") == "filled" and not updated.get("completion_order_id"):
                    confirmed += 1
                    if snapshot.get("ok"):
                        placed = place_resting_completion(
                            conn, updated, snapshot, params, broker, live=live, log=log
                        )
                        # Regression (2026-07-30): this is the PRIMARY completion-placement path in
                        # practice — the watcher polls every ~poll seconds vs. the main tick's 1
                        # minute, so it usually wins the atomic claim before run_once's own fallback
                        # placer ever gets a turn. Instrumenting only run_once's copy of this call
                        # left the Decision Journal showing zero completion rows despite real
                        # completions having happened.
                        if placed is not None and placed.get("completion_order_id"):
                            dbmod.record_decision(
                                conn,
                                trade_date=day,
                                arm=arm,
                                symbol=symbol,
                                mode="completion",
                                reason="placed",
                                accepted=True,
                                center=updated.get("center"),
                                position_id=updated.get("position_id"),
                                detail=f"resting order {placed.get('completion_order_id')}",
                                when=clock.now_iso(),
                            )
            else:
                updated = _confirm_completion_fill(conn, pos, broker, log, snapshot.get("underlying_price"))
                if updated.get("completion_fill_status") == "filled":
                    confirmed += 1

        # The alert-wait call above already blocked for up to `poll` seconds this cycle when it
        # ran -- sleeping again would double-wait every cycle for no benefit. Daemon mode's inbox
        # read is instant, though, so it still needs the sleep to pace the loop.
        if not (use_alert_stream and alert_order_ids) and clock_fn() < deadline:
            sleep(poll)

    if alerts_conn is not None:
        alerts_conn.close()
    return {"ok": True, "cycles": cycles, "confirmed": confirmed}


def _spawn_watcher(live: bool) -> None:
    """Fire the detached burst watcher (headless — the suite's CREATE_NO_WINDOW invariant)."""
    args = [_pl._pythonw(), "-m", "cherrypick.flies.live_loop", "--watch-fills"]
    if live:
        args.append("--live")
    flags = _NO_WINDOW | (0x00000008 if os.name == "nt" else 0)  # DETACHED_PROCESS on Windows
    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
        start_new_session=(os.name != "nt"),
    )


# --------------------------------------------------------------------------- broker seam
def _get_session():
    from cherrypick.flies import credentials as creds

    return creds.get_session()


def _designated_account():
    from cherrypick.flies import credentials as creds

    return creds.designated_account()


class BrokerAdapter(_execution.Broker):
    """This module's live seam: `cherrypick.core.execution.Broker` -- one session and account on
    one process-wide loop, the session dropped and rebuilt on any broker error, fail-closed
    result shapes -- with flies' own pieces injected: its keyring session, its designated
    account, `broker_cli.live_gates` (re-checked on every live submit), its serializer and the
    live block's deploy cap. Plus the two fetches only this module makes: a one-shot REST
    re-quote before a live entry, and the official settlement print.

    The adapter itself, with its three incident-driven behaviours (the process-wide loop from
    2026-08-04, the atomic `_ensure`, the loud `working_orders`), moved to core on 2026-09-17 so
    the next module to go live inherits them rather than re-learns them; the tests moved with
    it (`packages/core/tests/test_execution.py`). What is left here is only what is flies'."""

    def __init__(self, config: dict):
        self._config = config
        super().__init__(
            get_session=_get_session,
            designated_account=_designated_account,
            live_gates=self._gates,
            deploy_limit_pct=_live_cfg(config).get("account_deploy_limit_pct") or None,
            # An uncertain submit's hold outlives this tick's process (2026-10-08).
            hold_path=_held_path(),
        )

    def _gates(self) -> list[str]:
        from cherrypick.flies import broker_cli

        return broker_cli.live_gates(self._config)

    _run = _execution.Broker.run  # the pre-2026-09-17 name, for any caller that still uses it

    def fresh_quotes(self, symbols: list[str]) -> dict:
        """A one-shot REST bid/ask snapshot for `symbols`, used only to reprice a live entry
        immediately before submission (see run_once). Fails closed: any error returns `{}`, which
        the caller treats identically to 'nothing came back' — never a reason to fall back to a
        stale cached price for a live order."""
        from cherrypick.flies import broker_cli

        try:
            self._ensure()
            return self.run(broker_cli.fresh_option_quotes(self._session, symbols))
        except Exception:  # noqa: BLE001 — fail-closed, see docstring
            self._reset()
            return {}

    def official_settlement_price(self, symbol: str) -> tuple[float | None, str]:
        """Best-effort auto-fetch of the official settlement print (tastytrade -> Yahoo ->
        Barchart, see `broker_cli.official_settlement_price`). Fails closed: any error returns
        `(None, "fetch_failed")`, which `run_settle_live` treats the same as every source coming
        up empty — falling back to the existing last-trade-provisional path, never a guess."""
        from cherrypick.flies import broker_cli

        try:
            self._ensure()
            return self.run(broker_cli.official_settlement_price(self._session, symbol))
        except Exception:  # noqa: BLE001 — fail-closed, see docstring
            self._reset()
            return None, "fetch_failed"


# --------------------------------------------------------------------------- scheduled task
def _supervisor_heartbeat_fresh(max_age_seconds: int = 90) -> bool:
    """Is the orchestrator's supervisor daemon driving this box? (`cherrypick.core.live`.) Fresh
    heartbeat → arming is a record write; stale/absent → arming is refused."""
    return _live.supervisor_heartbeat_fresh(max_age_seconds)


def _spawn_first_tick() -> None:
    """Fire one detached `--once --live` immediately so arming doesn't wait up to a full interval
    for the first tick."""
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x08000000 | 0x00000200  # DETACHED | NO_WINDOW | NEW_GROUP
    try:
        subprocess.Popen(
            [_pl._pythonw(), "-m", "cherrypick.flies.live_loop", "--once", "--live"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            start_new_session=(os.name != "nt"),  # POSIX: out of the arming caller's group
        )
    except OSError:
        pass  # the next scheduled tick covers it


def install_task() -> dict:
    """Arm the live loop FOR TODAY: the arm record, under a live supervisor, and nothing else
    (`cherrypick.core.live.arm`; the supervisor derives the `flies-live` job from it within one pass
    and one tick fires at once). No supervisor, no arming -- the Windows-only scheduled-task fallback
    was removed on 2026-10-08, after every box had run under the supervisor for two months; it was a
    second, less-watched way to drive a live loop."""
    out = _live.arm(
        "flies",
        date=provider.now_et().date().isoformat(),
        at=clock.now_iso(),
        armed_by="live-flies-start",
        spawn_first_tick=_spawn_first_tick,
        heartbeat_fresh=_supervisor_heartbeat_fresh,
    )
    if out.get("ok"):
        out["cadence"] = "every 60s (supervisor job flies-live)"
    return out


def uninstall_task() -> dict:
    """Disarm: delete the arm record, which disables the supervisor's `flies-live` job within one
    pass. A legacy scheduled task still registered on an old box is the orchestrator's to remove
    (`run.py install` deletes every legacy task; `doctor` reports one)."""
    removed = _live.disarm("flies", legacy_paths=[_legacy_arm_stamp_path()])["arm_record_removed"]
    return {
        "ok": removed,
        "arm_record_removed": removed,
        "detail": "disarmed (arm record removed)" if removed else "nothing was armed",
    }


def _write_arm_stamp() -> None:
    _live.write_arm_record(
        "flies", date=provider.now_et().date().isoformat(), at=clock.now_iso(), armed_by="live-flies-start"
    )


def expected_legs(config: dict, conn) -> dict:
    """The contracts this ledger says the broker holds right now, for the orchestrator's
    positions-vs-ledger check (`cherrypick.core.livepositions`, 2026-10-08). Files and DB only.

    Counted: open rows whose entry FILL is confirmed, expanded by `fly.position_legs` (a pending
    completion leaves `kind` a vertical, so its unfilled leg is not counted). Not counted: a pending
    entry (nothing filled yet), cancelled and settled rows, and the paper-only hedge leg. `pending`
    says how many orders are still working, so a reader knows the book may be mid-change."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM fly_positions WHERE status = 'open'").fetchall()]
    legs, pending = [], 0
    for row in rows:
        if row.get("entry_fill_status") == "pending":
            pending += 1
            continue
        if row.get("completion_fill_status") == "pending":
            pending += 1
        qty = int(row.get("quantity") or 1)
        for expiry, right, strike, sign in fly.position_legs(row):
            legs.append(
                {
                    "underlying": row["symbol"],
                    "expiry": expiry,
                    "right": right,
                    "strike": strike,
                    "qty": sign * qty,
                }
            )
    symbol = _live_cfg(config).get("symbol")
    underlyings = sorted({r["symbol"] for r in rows} | ({symbol} if symbol else set()))
    return {
        "ok": True,
        "module": "flies",
        "underlyings": underlyings,
        "armed_today": arm_stamp_date() == provider.now_et().date().isoformat(),
        "legs": legs,
        "pending": pending,
    }


def arm_stamp_date() -> str | None:
    return _live.arm_record_date("flies", legacy_paths=[_legacy_arm_stamp_path()])


def read_arm_record() -> dict | None:
    try:
        with open(arm_stamp_path(), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def set_agent_mode(config: dict, mode: str, *, today: str | None = None) -> dict:
    """Write the day's intraday agent selection onto TODAY's arm record (/live-flies-start, after the
    YES). Refuses -- writing nothing -- unless the record is for today and `mode` is one the
    evidence offers (`intraday_eval.offered_live_modes`: the qualification file, capped by config
    and by what the live loop can do). "off" is always accepted. The record's other keys are kept,
    and the record still self-disarms with everything on it, so tomorrow needs a fresh choice."""
    today = today or provider.now_et().date().isoformat()
    record = read_arm_record()
    if record is None or record.get("date") != today:
        return {"ok": False, "error": f"no arm record for {today}: arm first (the YES), then choose"}
    offered = intraday_eval.offered_live_modes(config, intraday_eval.read_qualification())
    if mode not in offered:
        return {"ok": False, "error": f"{mode!r} is not offered today", "offered": offered}
    acfg = intraday_advice.agent_config(config)
    record["intraday_agent"] = {"mode": mode, "model": acfg["model"], "confirmed_at": clock.now_iso()}
    path = Path(arm_stamp_path())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record), encoding="utf-8")
    os.replace(tmp, path)
    return {"ok": True, "date": today, "intraday_agent": record["intraday_agent"], "offered": offered}


def agent_status(config: dict) -> dict:
    """The agent's block for `--status`: the config, today's mode in force, and what may be offered
    and why -- the readout the selection is made from."""
    acfg = intraday_advice.agent_config(config)
    q = intraday_eval.read_qualification()
    today = provider.now_et().date().isoformat()
    return {
        "enabled": acfg["enabled"],
        "model": acfg["model"],
        "live_mode_max": acfg["live_mode_max"],
        "live_closes_built": intraday_advice.LIVE_CLOSES_BUILT,
        "mode_today": intraday_advice.live_mode_today(acfg, read_arm_record(), today),
        "offered_modes": intraday_eval.offered_live_modes(config, q),
        "qualification_generated_at": (q or {}).get("generated_at"),
        "criteria": [
            {"id": c.get("id"), "mode": c.get("mode"), "value": c.get("value"), "pass": c.get("pass")}
            for c in (q or {}).get("criteria", [])
        ],
    }


def should_disarm(config: dict, now_min: int, today: str) -> str | None:
    """The dead-man's switch, pure (`cherrypick.core.live.should_disarm`): a reason string when
    the live task must disarm itself -- past `live.disarm_time` today, or the arm record is not
    today's (a previous day's arm, or no arm through this command's path at all)."""
    return _live.should_disarm(
        arm_stamp_date(),
        today=today,
        now_min=now_min,
        disarm_min=disarm_min(config),
        disarm_label=_live_cfg(config).get("disarm_time") or DEFAULT_DISARM,
    )


# --------------------------------------------------------------------------- status
def settle_overdue(config: dict, conn, *, cache_path: str, today: str, close_fn=None) -> dict:
    """Settle every past session the live ledger still holds open, at THAT session's official close
    (`core.settlement.dated_index_close`, source `yahoo_daily`) -- never a provisional price. The
    catch-up for a machine that was down between the close and the disarm (2026-10-08; the owner
    chose official-print-only). A session with a pending entry is left for a person: whether that
    order filled is the broker's word, not this function's. No close found: left, still alerting."""
    close_fn = close_fn or _settlement.dated_index_close
    symbol = _live_cfg(config).get("symbol", "XSP")
    done, left = [], []
    for item in overdue_settlement(conn, today):
        session = item["session"]
        if item["pending_entries"]:
            left.append({"session": session, "reason": "pending_entries"})
            continue
        price = close_fn(symbol, session)
        if price is None:
            left.append({"session": session, "reason": "no_official_close"})
            continue
        when = datetime.fromisoformat(f"{session}T16:30:00")
        out = run_settle_live(
            config, conn, cache_path=cache_path, when=when, broker=_settlement.DatedClose(price)
        )
        (done if out.get("ok") else left).append({"session": session, "price": price, **out})
    return {"ok": True, "settled": done, "left": left}


def overdue_settlement(conn, today: str) -> list[dict]:
    """Past sessions the live ledger still holds open, oldest first: `{session, positions,
    pending_entries}`. These are 0DTE, so every one has expired. Settlement happens only inside an
    armed day's own ticks, and the next morning's stale arm record disarms the loop at once -- so a
    machine down between the close and the disarm left the book open forever, silently (2026-10-08)."""
    rows = conn.execute(
        "SELECT trade_date, COUNT(*) AS n, SUM(entry_fill_status = 'pending') AS pend FROM fly_positions "
        "WHERE status = 'open' AND trade_date < ? GROUP BY trade_date ORDER BY trade_date",
        (today,),
    ).fetchall()
    return [
        {"session": r["trade_date"], "positions": r["n"], "pending_entries": int(r["pend"] or 0)}
        for r in rows
    ]


def run_status(config: dict, conn) -> dict:
    """One merged JSON object (the streamer convention) — files and DB only, no broker."""
    when = provider.now_et()
    today = when.date().isoformat()
    live_cfg = _live_cfg(config)
    arm = live_cfg.get("arm", DEFAULT_ARM)
    open_rows = conn.execute(
        "SELECT position_id, kind, entry_fill_status, completion_fill_status FROM fly_positions "
        "WHERE trade_date = ? AND arm = ? AND status = 'open'",
        (today, arm),
    ).fetchall()
    pending = [
        r["position_id"]
        for r in open_rows
        if r["entry_fill_status"] == "pending" or r["completion_fill_status"] == "pending"
    ]
    lf = log_file()
    try:
        last_tick = datetime.fromtimestamp(os.path.getmtime(lf)).isoformat(timespec="seconds")
    except OSError:
        last_tick = None
    return {
        "ok": True,
        "date": today,
        "in_session": _pl.in_session(provider.minute_of_day(when)),
        "armed_for": arm_stamp_date(),
        "arm": arm,
        "symbol": live_cfg.get("symbol"),
        "open_positions": len(open_rows),
        "pending_orders": len(pending),
        "pending_position_ids": pending,
        "session_settled": session_already_settled(conn, today),
        "halt_flag": os.path.exists(halt_flag_path()),
        "breaker_tripped": daily_loss_tripped(conn, today, live_cfg.get("daily_loss_halt_dollars")),
        # From the last tick's broker-truth sweep (files only here — status never talks to the broker).
        "orphaned_orders": len(read_orphans()),
        # The seam's persisted hold (a submit of unknown outcome, or an unrecorded order found).
        "broker_held": _execution.read_hold(_held_path()),
        # Broker contact across ticks: `failing_since` set while every call fails (2026-10-08).
        "broker_health": _execution.read_broker_health(
            os.path.join(os.path.dirname(_held_path()), _execution.HEALTH_FILENAME)
        ),
        "overdue_settlement": overdue_settlement(conn, today),
        "last_log_write": last_tick,
        "log_file": str(lf),
        # The order-alert daemon's own view of itself (PID probe + its heartbeat file). Only
        # meaningful when live.use_order_alert_daemon is on; a stale heartbeat_at on a `running`
        # process is the tell for a silently-dead websocket -- liveness alone can't see that.
        "alert_daemon": _alert_daemon_status_safe() if live_cfg.get("use_order_alert_daemon") else None,
        # The pilot's core instrument: live vs contemporaneous paper, with the plan doc's abort
        # rule evaluated. Files only (both ledgers are local SQLite); best-effort.
        "live_vs_paper": _live_vs_paper_safe(conn, arm),
        "intraday_agent": _agent_status_safe(config),
    }


def _agent_status_safe(config: dict):
    try:
        return agent_status(config)
    except Exception as exc:  # noqa: BLE001 -- status is a read-only diagnostic
        return {"error": f"{type(exc).__name__}: {exc}"}


def _alert_daemon_status_safe():
    """Best-effort: `--status` must never fail because the optional daemon module or its files
    aren't there (it's an accelerator, and status is a read-only diagnostic)."""
    try:
        from cherrypick.flies import alert_daemon

        return alert_daemon.status()
    except Exception as exc:  # noqa: BLE001
        return {"running": None, "error": f"{type(exc).__name__}: {exc}"}


def _live_vs_paper_safe(live_conn, arm: str):
    from cherrypick.flies import analytics

    try:
        paper_conn = dbmod.connect(dbmod.default_db_path())
        try:
            return analytics.live_vs_paper(live_conn, paper_conn, arm)
        finally:
            paper_conn.close()
    except Exception as exc:  # noqa: BLE001 — a broken comparison must not break --status
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- main
def _build_snapshot(config: dict, cache_path: str):
    symbol = _live_cfg(config).get("symbol", "XSP")
    return provider.build_snapshot(
        cache_path,
        symbol,
        **provider.snapshot_kwargs(config),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="one tick of the live state machine")
    ap.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="preflight orders, place nothing (DEFAULT — this is the rung-0 smoke)",
    )
    ap.add_argument(
        "--live",
        dest="dry_run",
        action="store_false",
        help="place real orders. Requires every readiness gate AND the breaker clear.",
    )
    ap.add_argument("--watch-fills", action="store_true", help="burst fill-watcher (spawned by ticks)")
    ap.add_argument("--status", action="store_true", help="one JSON health object, files/DB only")
    ap.add_argument(
        "--expected-legs",
        action="store_true",
        help="the filled open legs the ledger holds (JSON; files/DB only)",
    )
    ap.add_argument(
        "--settle-overdue",
        action="store_true",
        help="settle past sessions still open, at each one's official close",
    )
    ap.add_argument("--settle", action="store_true", help="settle the live book (see --price)")
    ap.add_argument("--price", type=float, help="official settlement print (marks source='official')")
    ap.add_argument("--force", action="store_true", help="allow re-settling an official settlement")
    ap.add_argument(
        "--date",
        help="with --settle: the session to settle (YYYY-MM-DD, default today) — the next-morning "
        "official-print confirm targets YESTERDAY's book",
    )
    ap.add_argument(
        "--install-task", action="store_true", help="arm the live loop for TODAY (supervisor job)"
    )
    ap.add_argument("--uninstall-task", action="store_true", help="disarm the live loop")
    ap.add_argument(
        "--agent-mode",
        choices=intraday_advice.LIVE_MODES,
        help="today's intraday agent selection, written onto today's arm record (after arming)",
    )
    ap.add_argument("--config")
    ap.add_argument("--db")
    ap.add_argument("--stream-cache")
    args = ap.parse_args()

    if args.install_task:
        print(json.dumps(install_task(), indent=2))
        return 0
    if args.uninstall_task:
        print(json.dumps(uninstall_task(), indent=2))
        return 0

    config = load_config(args.config)
    if args.agent_mode:
        out = set_agent_mode(config, args.agent_mode)
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1
    cache_path = args.stream_cache or _pl.stream_cache_path(config)
    db_path = args.db or dbmod.live_db_path()
    conn = dbmod.connect(db_path)
    live = not args.dry_run
    try:
        if args.status:
            print(json.dumps(run_status(config, conn), indent=2, default=str))
            return 0
        if args.expected_legs:
            print(json.dumps(expected_legs(config, conn), default=str))
            return 0
        if args.settle_overdue:
            today = provider.now_et().date().isoformat()
            print(json.dumps(settle_overdue(config, conn, cache_path=cache_path, today=today), default=str))
            return 0
        if args.settle:
            when = None
            if args.date:
                when = datetime.fromisoformat(f"{args.date}T12:00:00")
            out = run_settle_live(
                config,
                conn,
                cache_path=cache_path,
                when=when,
                price=args.price,
                force=args.force,
                broker=BrokerAdapter(config),
            )
            print(json.dumps(out, indent=2, default=str))
            return 0 if out.get("ok") else 1

        if args.watch_fills:
            if not _acquire_lock(_watch_lock_path(), stale_seconds=120):
                print(json.dumps({"ok": True, "skipped": "watcher already running"}))
                return 0
            try:
                out = run_watch(config, conn, BrokerAdapter(config), cache_path=cache_path, live=live)
            finally:
                _release_lock(_watch_lock_path())
            print(json.dumps(out, default=str))
            return 0

        if not args.once:
            print(json.dumps({"ok": False, "error": "choose --once, --watch-fills, --status or --settle"}))
            return 2

        from cherrypick.flies import credentials as creds

        designated = creds.designated_account()
        unmet = readiness(config, designated=designated)
        if live and unmet:
            _log(f"live gates unmet: {unmet}")
            print(json.dumps({"ok": False, "error": "live gates unmet", "unmet": unmet}))
            return 1
        if unmet:
            print(f"note: dry-run with unmet live gates: {unmet}")

        if not _acquire_lock(_once_lock_path()):
            print(json.dumps({"ok": True, "skipped": "another --once is already running"}))
            return 0
        try:
            when = provider.now_et()
            now_min = provider.minute_of_day(when)
            day = when.date().isoformat()

            # Dead-man's switch first: a live task past its window disarms before anything else.
            if live:
                reason = should_disarm(config, now_min, day)
                if reason:
                    out = uninstall_task()
                    _log(f"live loop DISARMED ({reason}) — re-arm with /live-flies-start")
                    print(json.dumps({"ok": True, "disarmed": reason, **out}))
                    return 0

            if not _cal.is_trading_day(when.date()):
                print(json.dumps({"ok": True, "skipped": "not_a_trading_day", "date": day}))
                return 0

            # Fee reconciliation used to ride here, on every live tick. It moved out to its own
            # scheduled job (`<module>-fee-reconcile`, jobspec.py) on 2026-09-22, because a tick is
            # the wrong place for it: this block only ran under `--live`, the dead-man's switch
            # above returns before reaching it, and the supervisor disables the live job the moment
            # the loop disarms — so a session traded on Tuesday was never reconciled unless someone
            # armed live trading again. Confirming real cash against the ledger must not depend on
            # whether we chose to trade today. `python -m cherrypick.flies.fee_reconcile` is the
            # same work, runnable by hand.

            # Tell the streamer which underlying we need kept fresh (best-effort), whether it
            # needs a wider-than-default ATM window after repeated missing_leg_quotes refusals
            # (stream_window.py), and -- since 2026-09-17 -- which option legs this ledger's open
            # positions hold, so they stay quoted after spot leaves the window (the request file
            # carries a leg query the streamer re-runs against live_trades.db every poll).
            # live_loop previously registered no request of its own at all, free-riding entirely
            # on paper_loop's or another module's registration of the same symbol. This DB
            # (live_trades.db) has its own escalation state, independent of paper's.
            if live:
                symbol = _live_cfg(config).get("symbol", "XSP")
                try:
                    sw = config.get("stream_window") or {}
                    hints = stream_window.hints_for_symbols(
                        conn,
                        [symbol],
                        day,
                        base_width=int(sw.get("base_width", 60)),
                        request_base_width=bool(sw.get("request_base_width")),
                    )
                except Exception:  # noqa: BLE001 — window escalation is advisory, never fatal
                    hints = None
                stream_request.register({"symbols": [symbol]}, window_hints=hints, live=True, db_path=db_path)

            # Settlement before the session gate — the settle time is after the close. Gated on
            # OFFICIAL, not just settled: a provisional settlement keeps retrying the auto
            # official-price fetch on every subsequent tick until it upgrades or the loop
            # self-disarms, rather than being treated as done after its first (guessed) pass.
            if live and now_min >= settle_min(config) and not session_officially_settled(conn, day):
                out = run_settle_live(
                    config, conn, cache_path=cache_path, when=when, broker=BrokerAdapter(config)
                )
                print(json.dumps({"ok": True, "settled_session": True, **out}, default=str))
                return 0

            if not _pl.in_session(now_min):
                if now_min % 60 < _TASK_INTERVAL_MIN:
                    _log(f"outside RTH ({now_min // 60:02d}:{now_min % 60:02d}) — idle")
                print(json.dumps({"ok": True, "skipped": "outside_rth", "now_min": now_min}))
                return 0

            snapshot = _build_snapshot(config, cache_path)
            if not snapshot.get("ok"):
                _log(f"no snapshot: {snapshot.get('reason')}")
                # run_once is never called on a refused snapshot, so it never gets a chance to
                # journal this — without it, a feed outage and a quiet market are indistinguishable
                # on the dashboard (same gap paper_loop.py's own comment describes for fly_snapshots).
                symbol = _live_cfg(config).get("symbol", "XSP")
                dbmod.record_snapshot(
                    conn,
                    trade_date=day,
                    symbol=symbol,
                    status=snapshot.get("reason", "unknown"),
                    quotes_rejected=snapshot.get("rejected"),
                )
                print(json.dumps({"ok": False, "error": f"no snapshot: {snapshot.get('reason')}"}))
                return 1

            # The halt flag and the daily-loss breaker stop NEW entries only: the tick still
            # confirms fills, places and cuts off completions, and the book still settles.
            blockers = entry_blockers(config, conn, day, halt_present=os.path.exists(halt_flag_path()))
            if blockers:
                _log(f"new entries blocked ({', '.join(blockers)}) -- managing what is open")

            summary = run_once(
                config, snapshot, conn, BrokerAdapter(config), live=live, log=_log, entry_block=blockers
            )
            if live and (summary.get("pending_orders") or summary.get("entered")):
                _spawn_watcher(live)
                summary["watcher_spawned"] = True
        finally:
            _release_lock(_once_lock_path())
        print(
            json.dumps(
                {"ok": True, "at": datetime.now().isoformat(timespec="seconds"), **summary}, default=str
            )
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
