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
  - SETTLEMENT: at `live.settle_time` (default 16:20) the tick settles live books from the last
    streamed trade, marked `settlement_source='last_trade_provisional'`; a human re-settles
    with the official print via `--settle --price X` (marked 'official'). Next-day settlement
    happens automatically — settlement is checked before the session gate, like paper.
  - SELF-DISARM (dead-man's switch): a live tick at/after `live.disarm_time` (default 17:00),
    or one that finds the arm stamp from a previous day, uninstalls its OWN scheduled task.
    Arming is per-day by design — today's YES can never carry into tomorrow. The orchestrator
    watchdog backstops this by setting the halt flag if the task somehow survives.

Gates checked every live tick (`readiness()`): `live.enabled`, a non-empty `gate0_confirmed`
attestation, one configured arm, a designated account, halt flag absent — plus the daily-loss
breaker on the live ledger. Live concurrency: at most one incomplete position at a time (an
open short vertical always blocks; a completed fly blocks only while its floor is negative and
`live.negative_floor_override` doesn't name it).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "_core")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from cherrypick.core import calendar as _cal  # noqa: E402
from cherrypick.core import home as _home  # noqa: E402

import book as bookmod  # noqa: E402
import clock  # noqa: E402
import db as dbmod  # noqa: E402
import engine  # noqa: E402
import fly  # noqa: E402
import live_orders  # noqa: E402
import paper_loop as _pl  # noqa: E402
import provider  # noqa: E402
from cli import load_config  # noqa: E402

DEFAULT_ARM = "gex"
_TERMINAL_UNFILLED = {"cancelled", "rejected", "expired"}

_TASK_NAME = "cherrypick-flies-live-loop"
_TASK_INTERVAL_MIN = 1
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

DEFAULT_SETTLE = "16:20"
DEFAULT_DISARM = "17:00"
DEFAULT_CUTOFF = "15:30"
DEFAULT_WATCH_SECONDS = 60
DEFAULT_WATCH_POLL_SECONDS = 10
DEFAULT_HEARTBEAT_SECONDS = 150


# --------------------------------------------------------------------------- paths and logging
def halt_flag_path() -> str:
    """The suite-wide live kill switch — the same path the orchestrator's Live Ops card reports
    (`liveops.halt_flag_path()`); presence is the signal. Resolved through `cherrypick.core.home`
    so `$CHERRYPICK_HOME` values with `~`/vars expand identically on both sides."""
    return str(_home.state_dir() / "halt-live.flag")


def _data_dir() -> str:
    return os.path.dirname(dbmod.live_db_path())


def _once_lock_path() -> str:
    return os.path.join(_data_dir(), "live_loop.once.lock")


def _watch_lock_path() -> str:
    return os.path.join(_data_dir(), "live_watch.lock")


def arm_stamp_path() -> str:
    return os.path.join(_data_dir(), "live_armed.json")


_logger = logging.getLogger("flies_live_loop")


def log_file():
    """Resolved on every call, never at import — same test-isolation lesson as paper_loop."""
    from eod import logs_dir

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
    _logger.info(message)


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
    """The arm's engine params, with the live block's own overrides (completion_cutoff) on top."""
    params = engine.merged_params(config, arm)
    live = _live_cfg(config)
    if live.get("completion_cutoff"):
        params["completion_cutoff"] = live["completion_cutoff"]
    return params


# --------------------------------------------------------------------------- gates
def readiness(config: dict, *, halt_present: bool, designated: str | None) -> list[str]:
    """The unmet live gates, checked every tick — empty means the loop may act. Pure."""
    live = _live_cfg(config)
    unmet = []
    if not live.get("enabled"):
        unmet.append("live.enabled is false")
    if not str(live.get("gate0_confirmed") or "").strip():
        unmet.append("live.gate0_confirmed is empty — a human must attest Gate 0 (who/when)")
    arm = live.get("arm", DEFAULT_ARM)
    arms = config.get("arms") or {}
    if arm not in arms:
        unmet.append(f"live.arm {arm!r} is not a configured arm")
    if halt_present:
        unmet.append("halt flag present (state/halt-live.flag) — live entries halted")
    if not designated:
        unmet.append("no designated account — run `cherrypick account --module flies --set <last4>`")
    return unmet


def daily_loss_tripped(conn, day: str, limit_dollars: float | None) -> bool:
    """The daily-loss breaker over the LIVE ledger: settled net for `day` at or below
    -limit halts new entries. Open structures keep their normal hold-to-settlement rules
    (they are defined-risk; improvising exits is rule 5's territory)."""
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
    """True if this open position should prevent a new entry.

    An open short vertical always blocks — it IS the one incomplete spread the pilot allows.
    A completed fly blocks only when its floor is negative (not risk-free) after fees, and
    even then only until a human names this exact position_id in
    `live.negative_floor_override` — so a stale override can never silently cover a different,
    later stuck position."""
    if pos.get("kind") != "fly":
        return True
    if fly.is_risk_free(pos):
        return False
    return pos.get("position_id") != override_position_id


def _blocking_positions(positions: list[dict], override_position_id: str | None) -> list[dict]:
    return [p for p in positions if p.get("status") == "open" and _is_blocking(p, override_position_id)]


# --------------------------------------------------------------------------- fill confirmation
def _confirm_entry_fill(conn, pos: dict, broker, log) -> dict:
    """Poll a pending entry order; record the ACTUAL fill credit once confirmed. Returns the
    (possibly updated) position dict."""
    status = broker.status(pos["entry_order_id"])
    state = str(status.get("status") or "").strip().lower()
    if state == "filled":
        try:
            actual_credit = abs(float(status.get("price")))
        except (TypeError, ValueError):
            actual_credit = pos["net"]  # can't parse a real price — keep the model rather than corrupt it
        conn.execute(
            "UPDATE fly_positions SET net = ?, credit = ?, entry_fill_status = 'filled' WHERE id = ?",
            (actual_credit, actual_credit, pos["id"]),
        )
        conn.commit()
        log(
            f"entry FILLED {pos['position_id']}: modeled {pos['net']:.2f} credit -> "
            f"actual {actual_credit:.2f}"
        )
        return {**pos, "net": actual_credit, "credit": actual_credit, "entry_fill_status": "filled"}
    if state in _TERMINAL_UNFILLED:
        conn.execute(
            "UPDATE fly_positions SET entry_fill_status = ?, status = 'cancelled' WHERE id = ?",
            (state, pos["id"]),
        )
        conn.commit()
        log(f"entry {state.upper()} {pos['position_id']} — never established")
        return {**pos, "entry_fill_status": state, "status": "cancelled"}
    return pos  # still working — stays pending, still blocks a second entry


def _confirm_completion_fill(conn, pos: dict, broker, log) -> dict:
    """Poll a pending completion order; flip kind='fly' with the ACTUAL debit once confirmed."""
    status = broker.status(pos["completion_order_id"])
    state = str(status.get("status") or "").strip().lower()
    if state == "filled":
        try:
            actual_debit = abs(float(status.get("price")))
        except (TypeError, ValueError):
            actual_debit = pos.get("debit") or 0.0
        completion_fee = fly.vertical_open_fee(pos["symbol"], pos.get("quantity", 1))
        new_net = pos["net"] - actual_debit
        new_fees = (pos.get("fees") or 0.0) + completion_fee
        updated = {**pos, "kind": "fly", "net": new_net, "fees": new_fees}
        floor = fly.position_floor(updated)
        risk_free = fly.is_risk_free(updated)
        conn.execute(
            "UPDATE fly_positions SET kind = 'fly', net = ?, debit = ?, fees = ?, floor_dollars = ?, "
            "risk_free = ?, completion_fill_status = 'filled', completed_at = ? WHERE id = ?",
            (new_net, actual_debit, new_fees, floor, int(risk_free), clock.now_iso(), pos["id"]),
        )
        conn.commit()
        log(
            f"completion FILLED {pos['position_id']}: floor ${floor:.2f} after fees "
            f"({'risk-free' if risk_free else 'NOT risk-free'})"
        )
        return {**updated, "floor_dollars": floor, "risk_free": int(risk_free)}
    if state in _TERMINAL_UNFILLED:
        conn.execute(
            "UPDATE fly_positions SET completion_order_id = NULL, completion_fill_status = ? WHERE id = ?",
            (state, pos["id"]),
        )
        conn.commit()
        log(f"completion {state.upper()} {pos['position_id']} — still a short vertical, may retry")
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

    res = broker.place(spec, live=live)
    log(f"completion order ({'LIVE' if live else 'dry-run'}): {json.dumps(res, default=str)[:200]}")
    if res.get("ok") and live and res.get("order_id"):
        conn.execute(
            "UPDATE fly_positions SET completion_order_id = ?, completion_fill_status = 'pending' "
            "WHERE id = ?",
            (str(res["order_id"]), pos["id"]),
        )
        conn.commit()
        return {**pos, "completion_order_id": str(res["order_id"]), "completion_fill_status": "pending"}
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
        log(f"entry {pos['position_id']} cancelled — evaluation moved to {why}")
        return {**pos, "status": "cancelled", "entry_fill_status": "cancelled"}
    # Cancel refused: the likeliest reason is a fill that beat us. Ask, and record if so.
    log(f"entry cancel refused for {pos['position_id']} ({res.get('error')}) — re-polling status")
    return _confirm_entry_fill(conn, pos, broker, log)


# --------------------------------------------------------------------------- orphan sweep
def _orphans_path() -> str:
    return os.path.join(_data_dir(), "live_orphans.json")


def _sweep_orphans(conn, broker, log) -> int:
    """Diff the broker's working orders (truth) against the ledger's order ids (belief).

    The one crash window nothing else covers: a tick dying between a successful place and the
    DB write leaves a real order resting at the broker with no ledger row — invisible to fill
    polling, entry management, and the cutoff cancel alike. Any working order the ledger has
    never heard of is persisted to live_orphans.json (which `--status` reports and the
    watchdog raises CRITICAL on) and logged loudly. Detection only — cancelling an order this
    process can't account for is a human's call, made with the broker UI open."""
    if not hasattr(broker, "working_orders"):
        return 0
    try:
        working = broker.working_orders()
    except Exception as exc:  # noqa: BLE001 — a failed sweep must not break the tick
        log(f"orphan sweep failed ({type(exc).__name__}: {exc}) — will retry next tick")
        return 0
    known = {
        str(r[0])
        for r in conn.execute(
            "SELECT entry_order_id FROM fly_positions WHERE entry_order_id IS NOT NULL "
            "UNION SELECT completion_order_id FROM fly_positions WHERE completion_order_id IS NOT NULL"
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


# --------------------------------------------------------------------------- the tick
def run_once(config: dict, snapshot: dict, conn, broker, *, live: bool, log=print) -> dict:
    """One live iteration for the pinned arm — the full state machine.

    `broker` is the injected submission seam: place(spec, live) -> {ok, order_id?, ...},
    cancel(order_id) -> {ok}, status(order_id) -> {status, price, filled, ...}. With
    live=False every placement is a dry-run preflight (the rung-0 smoke) and no fill polling
    happens (a dry run places no real order)."""
    live_cfg = _live_cfg(config)
    arm = live_cfg.get("arm", DEFAULT_ARM)
    params = _merged_live_params(config, arm)
    day = snapshot["date"]
    summary = {
        "arm": arm,
        "live": live,
        "entered": 0,
        "completed_orders": 0,
        "cancelled": 0,
        "pending_orders": 0,
        "skips": [],
    }

    rows = conn.execute(
        "SELECT * FROM fly_positions WHERE trade_date = ? AND arm = ? AND status = 'open'", (day, arm)
    ).fetchall()
    positions = [dict(r) for r in rows]

    # --- 0. orphan sweep: broker truth vs ledger belief ---
    if live:
        summary["orphaned_orders"] = _sweep_orphans(conn, broker, log)

    # --- 1. fill confirmation (before anything else acts on a position's current state) ---
    if live:
        updated = []
        for pos in positions:
            if pos.get("completion_order_id") and pos.get("completion_fill_status") == "pending":
                pos = _confirm_completion_fill(conn, pos, broker, log)
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
                bookmod._record_best_debit(conn, pos, plan["debit"], clock.now_iso())
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
                else:
                    log(f"cutoff cancel FAILED for {pos['position_id']}: {res.get('error')}")
            continue
        if not live:
            continue  # dry-run writes no rows, so there is nothing real to complete
        if _cutoff_reached(snapshot.get("now_min"), params):
            summary["skips"].append(
                {"position": pos.get("position_id"), "reason": "completion_cutoff_reached"}
            )
            continue
        placed = place_resting_completion(conn, pos, snapshot, params, broker, live=live, log=log)
        if placed is not None and placed.get("completion_order_id"):
            summary["completed_orders"] += 1

    # --- 4. concurrency gate + entry ---
    # Per-day structure cap (live.max_structures_per_day, off when null): counts every
    # ESTABLISHED structure today — settled and risk-free-completed included — so unlike the
    # one-incomplete-at-a-time rule, freeing the slot never re-opens the day's budget. This is
    # the rung-1 throttle: set 1 for the plan doc's strict one-structure-per-day posture.
    day_capped = False
    day_cap = live_cfg.get("max_structures_per_day")
    if live and day_cap:
        established = conn.execute(
            "SELECT COUNT(*) FROM fly_positions WHERE trade_date = ? AND arm = ? AND status != 'cancelled'",
            (day, arm),
        ).fetchone()[0]
        day_capped = established >= day_cap
        if day_capped:
            summary["skips"].append({"entry": f"max_structures_per_day reached ({established}/{day_cap})"})

    blockers = _blocking_positions(positions, live_cfg.get("negative_floor_override"))
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
        else:
            summary["skips"].append(
                {"entry": f"blocked: {blockers[0]['position_id']} is still an incomplete spread"}
            )
    else:
        enter, reason, plan = engine.evaluate_credit_spread_entry(snapshot, params, positions)
        if not enter:
            summary["skips"].append({"entry": reason})
        else:
            spec = live_orders.entry_spec(snapshot, plan)
            res = broker.place(spec, live=live)
            log(f"entry order ({'LIVE' if live else 'dry-run'}): {json.dumps(res, default=str)[:200]}")
            if res.get("ok") and live and res.get("order_id"):
                pid = f"live-{day}-{arm}-{int(plan['center'])}"
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
                        "net": plan["credit"],
                        "credit": plan["credit"],
                        "fees": plan["open_fee"],
                        "status": "open",
                        "entry_window": plan.get("entry_window"),
                        "underlying_at_entry": snapshot.get("underlying_price"),
                        "entry_time": clock.now_iso(),
                        "entry_order_id": str(res["order_id"]),
                        "entry_fill_status": "pending",
                    },
                )
            summary["entered"] += 1

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

    return summary


# --------------------------------------------------------------------------- settlement
def session_already_settled(conn, day: str) -> bool:
    """Book state is the settled marker, same as paper (see paper_loop.session_already_settled
    for why a file must never be)."""
    total, settled = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(status = 'settled'), 0) FROM fly_books WHERE trade_date = ?", (day,)
    ).fetchone()
    return total > 0 and total == settled


def run_settle_live(
    config: dict, conn, *, cache_path: str, when=None, price: float | None = None, force: bool = False
) -> dict:
    """Settle the live arm's books. No `price` = the provisional path (last streamed trade,
    marked 'last_trade_provisional'); an explicit `price` = the official print (marked
    'official', overwrites provisional, refuses to overwrite official without `force`)."""
    when = when or provider.now_et()
    day = when.date().isoformat()
    live_cfg = _live_cfg(config)
    arm = live_cfg.get("arm", DEFAULT_ARM)
    symbol = live_cfg.get("symbol", "XSP")
    book_id = bookmod.book_id_for(day, arm, symbol)

    existing = conn.execute("SELECT status, settlement_source FROM fly_books WHERE book_id = ?", (book_id,))
    row = existing.fetchone()
    already_official = (
        row is not None and row["status"] == "settled" and row["settlement_source"] == "official"
    )
    already_settled = row is not None and row["status"] == "settled"

    if already_official and not force:
        return {"ok": False, "reason": "already settled with the official print (use --force to override)"}
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
        source = "official"
        if already_settled:
            # Re-settle: flip the book's rows back to open so settle_book recomputes at the
            # official print through the exact same tested path as the first settlement.
            conn.execute(
                "UPDATE fly_positions SET status = 'open' WHERE book_id = ? AND status = 'settled'",
                (book_id,),
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
        f"LIVE settled {book_id} at {settlement:.2f} ({source}): P&L {result['pnl']:+.2f}"
        + (
            " — confirm with: python src/live_loop.py --settle --price <official print>"
            if source == "last_trade_provisional"
            else ""
        )
    )

    # The live day's written record — refreshed on the official re-settle too. Best-effort:
    # a report hiccup must never fail the settlement itself.
    report = None
    try:
        import eod as eodmod

        paper_conn = dbmod.connect(dbmod.default_db_path())
        try:
            report = eodmod.write_live_report(conn, paper_conn, day)
            _log(f"wrote {report['live_eod']}")
        finally:
            paper_conn.close()
    except Exception as exc:  # noqa: BLE001
        _log(f"live EOD report failed ({type(exc).__name__}: {exc}) — settlement itself is recorded")

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
    """The burst fill-watcher: cache-first polling of pending orders for up to `seconds`.

    Streamer-before-API: each cycle reads the stream cache (free) and only calls the broker's
    status endpoint when cached quotes show the market touching the working limit, or when
    `heartbeat` has elapsed since that order's last real poll (fills the cache can't see:
    price improvement, dips between cache writes). On confirming an ENTRY fill it immediately
    places the resting completion (atomic claim — the main tick is the fallback placer). It
    never cancels and makes no other decision."""
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
    symbol = live_cfg.get("symbol", "XSP")
    start = clock_fn()
    deadline = start + seconds
    # Watcher start counts as each order's "last poll": the tick that spawned us just talked to
    # the broker, so the first real poll is also cache-gated rather than automatic.
    last_poll: dict[str, float] = {}
    cycles = 0
    confirmed = 0

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
            max_quote_age_seconds=config.get("defaults", {}).get(
                "max_quote_age_seconds", provider.DEFAULT_MAX_QUOTE_AGE_SECONDS
            ),
        )
        naturals_ok = bool(snapshot.get("ok"))

        for pos in pending:
            is_entry = pos.get("entry_fill_status") == "pending"
            order_id = pos["entry_order_id"] if is_entry else pos["completion_order_id"]
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
            due = clock_fn() - last_poll.get(str(order_id), start) >= heartbeat
            if not touched and not due:
                continue
            last_poll[str(order_id)] = clock_fn()
            if is_entry:
                updated = _confirm_entry_fill(conn, pos, broker, log)
                if updated.get("entry_fill_status") == "filled" and not updated.get("completion_order_id"):
                    confirmed += 1
                    if snapshot.get("ok"):
                        place_resting_completion(conn, updated, snapshot, params, broker, live=live, log=log)
            else:
                updated = _confirm_completion_fill(conn, pos, broker, log)
                if updated.get("completion_fill_status") == "filled":
                    confirmed += 1

        if clock_fn() < deadline:
            sleep(poll)

    return {"ok": True, "cycles": cycles, "confirmed": confirmed}


def _spawn_watcher(live: bool) -> None:
    """Fire the detached burst watcher (headless — the suite's CREATE_NO_WINDOW invariant)."""
    args = [_pl._pythonw(), os.path.abspath(__file__), "--watch-fills"]
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
class BrokerAdapter:
    """The real submission seam over core.broker, holding ONE session/account for its lifetime
    (a per-call session build was ~1 OAuth handshake per broker op). All calls run on one
    private event loop so the SDK's async client stays bound to a single loop. On any broker
    exception the session is dropped and rebuilt once on the next call."""

    def __init__(self, config: dict):
        self._config = config
        self._loop = None
        self._session = None
        self._account = None

    def _run(self, coro):
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(coro)

    def _ensure(self):
        if self._session is None:
            from cherrypick.core import broker as _broker

            import credentials as creds

            self._session = creds.get_session()
            self._account = self._run(_broker.resolve_account(self._session, creds.designated_account()))

    def _reset(self):
        self._session = None
        self._account = None

    def place(self, spec: dict, live: bool) -> dict:
        from cherrypick.core import broker as _broker

        import broker_cli

        if live:
            unmet = broker_cli.live_gates(self._config)
            if unmet:
                return {"ok": False, "error": "live submission gated", "unmet_gates": unmet}
        try:
            self._ensure()
            order = _broker.build_order(spec)
            limit = _live_cfg(self._config).get("account_deploy_limit_pct") or None
            result = self._run(
                _broker.place_order(self._account, self._session, order, live=live, deploy_limit_pct=limit)
            )
        except Exception as exc:  # noqa: BLE001 — surfaced to the caller, session rebuilt next call
            self._reset()
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        rid = (
            (result.get("response") or {}).get("order", {})
            if isinstance(result.get("response"), dict)
            else {}
        )
        if isinstance(rid, dict) and rid.get("id") is not None:
            result["order_id"] = rid["id"]
        return result

    def status(self, order_id: str) -> dict:
        from cherrypick.core import broker as _broker

        try:
            self._ensure()
            return self._run(_broker.order_status(self._account, self._session, order_id))
        except Exception as exc:  # noqa: BLE001
            self._reset()
            return {"order_id": order_id, "status": None, "error": f"{type(exc).__name__}: {exc}"}

    def cancel(self, order_id: str) -> dict:
        from cherrypick.core import broker as _broker

        try:
            self._ensure()
            return self._run(_broker.cancel_order(self._account, self._session, order_id))
        except Exception as exc:  # noqa: BLE001
            self._reset()
            return {"ok": False, "order_id": order_id, "error": f"{type(exc).__name__}: {exc}"}

    def working_orders(self) -> list[dict]:
        from cherrypick.core import broker as _broker

        self._ensure()
        return self._run(_broker.working_orders(self._account, self._session))


# --------------------------------------------------------------------------- scheduled task
def task_installed() -> bool:
    if os.name != "nt":
        return False
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", _TASK_NAME], capture_output=True, text=True, creationflags=_NO_WINDOW
    )
    return r.returncode == 0


def _allow_on_battery() -> dict:
    """Clear Task Scheduler's default battery guards (DisallowStartIfOnBatteries /
    StopIfGoingOnBatteries). schtasks can't set these; the orchestrator patches its own tasks
    the same way (`tasks.allow_on_battery`), but the live task is registered HERE, module-side,
    so it must patch itself — a laptop dropping to battery would otherwise silently stop the
    loop with real working orders resting at the broker. Best-effort: a failure is reported
    but never invalidates the registration (the watchdog freshness check is the backstop)."""
    ps = (
        "$ErrorActionPreference='Stop';"
        f"$s=(Get-ScheduledTask -TaskName '{_TASK_NAME}').Settings;"
        "$s.DisallowStartIfOnBatteries=$false;$s.StopIfGoingOnBatteries=$false;"
        f"Set-ScheduledTask -TaskName '{_TASK_NAME}' -Settings $s | Out-Null"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=_NO_WINDOW,
        )
        return {"ok": r.returncode == 0, "detail": (r.stderr.strip()[:200] or "battery guards cleared")}
    except OSError as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def install_task() -> dict:
    """Arm the live loop FOR TODAY: register the every-minute task and stamp the arm date.
    The stamp is what makes arming per-day — a tick that finds a stale stamp disarms itself."""
    if os.name != "nt":
        return {"ok": False, "error": "scheduled-task install is Windows-only"}
    tr = f'"{_pl._pythonw()}" "{os.path.abspath(__file__)}" --once --live'
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
    battery = None
    if ok:
        _write_arm_stamp()
        battery = _allow_on_battery()
        subprocess.run(
            ["schtasks", "/Run", "/TN", _TASK_NAME], capture_output=True, text=True, creationflags=_NO_WINDOW
        )
    return {
        "ok": ok,
        "task": _TASK_NAME,
        "cadence": f"every {_TASK_INTERVAL_MIN} min",
        "armed_for": provider.now_et().date().isoformat(),
        "battery": battery,
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
    try:
        os.unlink(arm_stamp_path())
    except OSError:
        pass
    return {"ok": r.returncode == 0, "task": _TASK_NAME, "detail": (r.stdout or r.stderr).strip()}


def _write_arm_stamp() -> None:
    os.makedirs(_data_dir(), exist_ok=True)
    with open(arm_stamp_path(), "w", encoding="utf-8") as f:
        json.dump({"date": provider.now_et().date().isoformat(), "at": clock.now_iso()}, f)


def arm_stamp_date() -> str | None:
    try:
        with open(arm_stamp_path(), encoding="utf-8") as f:
            return json.load(f).get("date")
    except (OSError, ValueError):
        return None


def should_disarm(config: dict, now_min: int, today: str) -> str | None:
    """The dead-man's switch, pure: a reason string when the live task must disarm itself.
    Past `live.disarm_time` today, or armed on a previous day (machine slept through the
    disarm window), or no arm stamp at all (arming didn't go through this command's path)."""
    stamped = arm_stamp_date()
    if stamped != today:
        return f"arm stamp is {stamped!r}, today is {today} — arming is per-day"
    if now_min >= disarm_min(config):
        return f"past disarm time ({_live_cfg(config).get('disarm_time') or DEFAULT_DISARM})"
    return None


# --------------------------------------------------------------------------- status
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
        "scheduled_task": task_installed(),
        "task_name": _TASK_NAME,
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
        "last_log_write": last_tick,
        "log_file": str(lf),
        # The pilot's core instrument: live vs contemporaneous paper, with the plan doc's abort
        # rule evaluated. Files only (both ledgers are local SQLite); best-effort.
        "live_vs_paper": _live_vs_paper_safe(conn, arm),
    }


def _live_vs_paper_safe(live_conn, arm: str):
    import analytics

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
        max_quote_age_seconds=config.get("defaults", {}).get(
            "max_quote_age_seconds", provider.DEFAULT_MAX_QUOTE_AGE_SECONDS
        ),
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
    ap.add_argument("--settle", action="store_true", help="settle the live book (see --price)")
    ap.add_argument("--price", type=float, help="official settlement print (marks source='official')")
    ap.add_argument("--force", action="store_true", help="allow re-settling an official settlement")
    ap.add_argument(
        "--date",
        help="with --settle: the session to settle (YYYY-MM-DD, default today) — the next-morning "
        "official-print confirm targets YESTERDAY's book",
    )
    ap.add_argument("--install-task", action="store_true", help=f"arm {_TASK_NAME} for TODAY (1/min)")
    ap.add_argument("--uninstall-task", action="store_true", help="disarm the live loop")
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
    cache_path = args.stream_cache or _pl.stream_cache_path(config)
    conn = dbmod.connect(args.db or dbmod.live_db_path())
    live = not args.dry_run
    try:
        if args.status:
            print(json.dumps(run_status(config, conn), indent=2, default=str))
            return 0
        if args.settle:
            when = None
            if args.date:
                when = datetime.fromisoformat(f"{args.date}T12:00:00")
            out = run_settle_live(
                config, conn, cache_path=cache_path, when=when, price=args.price, force=args.force
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

        import credentials as creds

        designated = creds.designated_account()
        unmet = readiness(config, halt_present=os.path.exists(halt_flag_path()), designated=designated)
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

            # Settlement before the session gate — the settle time is after the close.
            if live and now_min >= settle_min(config) and not session_already_settled(conn, day):
                out = run_settle_live(config, conn, cache_path=cache_path, when=when)
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
                print(json.dumps({"ok": False, "error": f"no snapshot: {snapshot.get('reason')}"}))
                return 1

            limit = _live_cfg(config).get("daily_loss_halt_dollars")
            if live and daily_loss_tripped(conn, day, limit):
                _log("daily-loss breaker tripped — no new entries")
                print(json.dumps({"ok": False, "error": "daily-loss breaker tripped — no new entries"}))
                return 1

            summary = run_once(config, snapshot, conn, BrokerAdapter(config), live=live, log=_log)
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
