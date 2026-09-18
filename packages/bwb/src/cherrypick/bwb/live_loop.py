"""bwb LIVE session driver -- one configurable arm, per-day armed, the full daily ladder, no closing
orders. SPX cash-settles; the ledger settles on the official print or not at all.

What this loop is, in one paragraph. The paper loop decides everything (`engine`, `triggers`,
`management`) and records fills the instant a credit is met. This loop makes the SAME decisions
over the SAME stream-cache snapshots for ONE book (`live.arm`), but every fill is the broker's
word: an entry is placed as a limit order and its row is born `pending`, the actual credit
overwrites the modeled one on confirmation, and a terminal order leaves a `cancelled` row that
never established anything. The 1-3-2 add-on is the same again, one step stricter (the meic rule):
placing it records only a pending marker, and the legs are written -- through the paper writer,
with the actual credit -- only when the broker confirms the fill. The live ledger is a separate
file (`db.live_db_path`), so nothing here can reach a paper surface.

The tick, in order, every minute the supervisor drives it:

  0. dead-man's switch: today's arm record, before any broker read (`cherrypick.core.live`)
  1. orphan sweep: working orders at the broker this ledger never recorded (detection only)
  2. confirm pending fills: entries, then add-ons
  3. manage resting orders: cutoff/age cancel, strikes-moved cancel, the bounded walk-down
  4. settle, when due, on an official print only
  5. gates, then ONE entry attempt inside the entry window (structures/day, breakers, floor, caps)
  6. the paper tick's own trigger/mark/manage pass, with the fire seam swapped for order placement
  7. a bounded fill-watch over anything placed this tick (`cherrypick.core.execution.watch`)

Nothing here is a strategy change: the structure entered is `engine.plan_entry`'s, unchanged.
What is live-only is sizing and admission -- the cost-derived floor, the margin caps with the
add-on reserve, the breakers, the add-on throttle -- and every one of those refuses rather than
reshapes, so a live row stays comparable with its paper twin.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import time
from datetime import datetime

from cherrypick.core import calendar as _cal
from cherrypick.core import execution as _execution
from cherrypick.core import home as _home
from cherrypick.core import live as _live
from cherrypick.core import logs as _logs
from cherrypick.core import looplock
from cherrypick.core import settlement as _settlement

from cherrypick.bwb import book as bookmod
from cherrypick.bwb import cli as climod
from cherrypick.bwb import clock, db, engine, live_orders, management, provider, stream_request
from cherrypick.bwb import paper_loop as _pl

DEFAULT_ARM = "control"
DEFAULT_SETTLE = "16:20"
DEFAULT_DISARM = "17:00"
DEFAULT_CUTOFF = "11:00"
DEFAULT_CONCESSION = 0.05
DEFAULT_MIN_NET = 15.0
DEFAULT_REPRICE_MIN = 2
DEFAULT_CANCEL_AFTER_MIN = 20
DEFAULT_WATCH_SECONDS = 30
DEFAULT_WATCH_POLL_SECONDS = 10
ARMED_BY = "live-bwb-start"

_logger = logging.getLogger("bwb_live_loop")


# --------------------------------------------------------------------------- config and paths
def _live_cfg(config: dict) -> dict:
    return config.get("live") or {}


def _arm(config: dict) -> str:
    return str(_live_cfg(config).get("arm") or DEFAULT_ARM)


def settle_min(config: dict) -> int:
    return clock.hhmm_to_min(_live_cfg(config).get("settle_time"), _pl.DEFAULT_SETTLE_MIN)


def disarm_min(config: dict) -> int:
    return clock.hhmm_to_min(_live_cfg(config).get("disarm_time"), 17 * 60)


def entry_cutoff_min(config: dict) -> int:
    return clock.hhmm_to_min(_live_cfg(config).get("entry_cutoff"), 11 * 60)


def entry_time_min(config: dict) -> int:
    return clock.hhmm_to_min((config.get("defaults") or {}).get("entry_time"), 10 * 60)


def _live_params(config: dict) -> dict:
    return {**management.PARAM_DEFAULTS, **engine.merged_params(config, _arm(config))}


def log_file():
    return _home.logs_dir("bwb") / "bwb_live.log"


def _log(message: str) -> None:
    _logs.configure(_logger, log_file())
    _logger.info(message)


def _data_dir() -> str:
    return os.path.dirname(db.live_db_path())


def _once_lock_path() -> str:
    return os.path.join(_data_dir(), "live_once.lock")


def _orphans_path() -> str:
    return os.path.join(_data_dir(), "live_orphans.json")


def halt_flag_path() -> str:
    return str(_home.halt_flag_path())


def arm_record_path() -> str:
    return str(_live.arm_record_path("bwb"))


# --------------------------------------------------------------------------- gates
def readiness(config: dict, *, halt_present: bool, designated: str | None) -> list[str]:
    """The unmet live gates, checked every tick -- empty means the loop may act. Pure. bwb's own
    list (the suite keeps these per module on purpose: the gates ARE the module's posture)."""
    live = _live_cfg(config)
    unmet = []
    if not live.get("enabled"):
        unmet.append("live.enabled is false")
    if not str(live.get("gate0_confirmed") or "").strip():
        unmet.append("live.gate0_confirmed is empty -- a human must attest Gate 0 passed (who/when)")
    if _arm(config) not in engine.BOOKS:
        unmet.append(f"live.arm {_arm(config)!r} is not a base book (one of {', '.join(engine.BOOKS)})")
    if halt_present:
        unmet.append("halt flag present (state/halt-live.flag) -- live entries halted")
    if not designated:
        unmet.append("no designated account -- run `cherrypick account --module bwb --set <last4>`")
    return unmet


def daily_loss_tripped(conn, day: str, limit_dollars: float | None) -> bool:
    """The settled-net breaker over the LIVE ledger. On a hold-to-expiry ladder this can only
    move on a settlement day, so it is a weekly-latency breaker; `mark_drawdown_tripped` is the
    one that can fire mid-week."""
    if not limit_dollars:
        return False
    return db.settled_net_for_session(conn, day) <= -abs(float(limit_dollars))


def mark_drawdown_tripped(conn, limit_dollars: float | None) -> tuple[bool, float | None]:
    """The open book's marked loss against `mark_drawdown_halt_dollars`. Blocks the NEXT entry
    only -- never an exit, so nothing entered diverges from paper. A book that cannot be priced
    (None) does not trip and does not clear: the entry gate treats it as its own refusal."""
    if not limit_dollars:
        return False, None
    loss = db.open_marked_loss(conn)
    if loss is None:
        return False, None
    return loss >= abs(float(limit_dollars)), loss


# --------------------------------------------------------------------------- the broker seam
class BrokerAdapter(_execution.Broker):
    """`cherrypick.core.execution.Broker` with bwb's credentials, gates and account injected."""

    def __init__(self, config: dict):
        from cherrypick.bwb import broker_cli, credentials

        self._config = config
        super().__init__(
            get_session=credentials.get_session,
            designated_account=credentials.designated_account,
            live_gates=lambda: broker_cli.live_gates(self._config),
            deploy_limit_pct=_live_cfg(config).get("account_deploy_limit_pct") or None,
        )

    def official_settlement_price(self, symbol: str) -> tuple[float | None, str]:
        """The official index close (`cherrypick.core.settlement`). Fails closed: any error is
        `(None, "fetch_failed")` and the settle call retries next tick."""
        try:
            self._ensure()
            return self.run(_settlement.official_index_close(self._session, symbol))
        except Exception:  # noqa: BLE001 -- fail-closed, see docstring
            self._reset()
            return None, "fetch_failed"


def _fee_estimate(result: dict) -> float | None:
    """The broker's own fee total from a placement result's serialized preflight/response, or
    None when it gave none. Read defensively: the shape is the SDK's, not ours."""
    response = result.get("response") if isinstance(result, dict) else None
    if not isinstance(response, dict):
        return None
    calc = response.get("fee_calculation") or response.get("fee-calculation")
    if not isinstance(calc, dict):
        return None
    total = calc.get("total_fees", calc.get("total-fees"))
    try:
        return abs(float(total)) if total is not None else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- orphans
def _sweep_orphans(conn, broker, log, symbol: str) -> int:
    """Broker truth against ledger belief, first thing every tick: a working order this ledger
    never recorded is the one crash window nothing else covers (a tick dying between a placement
    and its row). Persisted for `--status` and the arm command's stop rule; detection only --
    cancelling an order this process cannot account for is a human's call."""
    try:
        orphans = _execution.orphans(broker, db.known_order_ids(conn))
    except Exception as exc:  # noqa: BLE001 -- a failed sweep must not break the tick
        log(f"orphan sweep failed ({type(exc).__name__}: {exc}) -- will retry next tick")
        return 0
    orphans = [o for o in orphans if o.get("underlying_symbol") in (None, symbol)]
    try:
        os.makedirs(_data_dir(), exist_ok=True)
        with open(_orphans_path(), "w", encoding="utf-8") as f:
            json.dump({"at": clock.now_iso(), "orphans": orphans}, f)
    except OSError:
        pass
    if orphans:
        log(
            f"ORPHANED ORDERS at the broker, unknown to the ledger: {[o.get('order_id') for o in orphans]} "
            "-- a placement was recorded nowhere (or another system is trading this account). "
            "Review in the broker UI before any further arming."
        )
    return len(orphans)


def read_orphans() -> list[dict]:
    try:
        with open(_orphans_path(), encoding="utf-8") as f:
            return json.load(f).get("orphans", [])
    except (OSError, ValueError):
        return []


# --------------------------------------------------------------------------- fill confirmation
def _confirm_entry_fill(conn, pos: dict, broker, log, config: dict, *, day: str) -> dict:
    """Poll a pending entry; record the ACTUAL credit once confirmed, or cancel the row if the
    order died. Returns the (possibly updated) position."""
    state, price = _execution.fill_state(
        broker.status(pos["entry_order_id"]), fallback_price=pos["entry_limit"]
    )
    pid = pos["position_id"]
    if state == "filled":
        mid = pos.get("entry_mid_at_submit")
        fields = {"position_id": pid, "status": "open", "entry_credit": price, "entry_fill_status": "filled"}
        if mid is not None and price is not None:
            # Measured, never modeled, on a live row: what the fill conceded against the mid the
            # limit was asked from (negative is price improvement). Not a fee -- `fees` stays the
            # broker's estimate until reconciliation replaces it with the real charge.
            fields["entry_slippage"] = round(
                (float(mid) - float(price)) * 100.0 * int(pos.get("quantity") or 1), 2
            )
        db.save_position(conn, fields)
        db.record_decision(
            conn,
            trade_date=day,
            book=pos["book"],
            symbol=pos["symbol"],
            mode="entry",
            reason=f"filled {price:.2f} (asked {float(pos['entry_limit']):.2f}, mid {float(mid or 0):.2f})",
            accepted=True,
        )
        log(f"entry FILLED {pid}: asked {pos['entry_limit']}, filled {price}")
        return {**pos, **fields}
    if state in _execution.TERMINAL_UNFILLED:
        db.save_position(conn, {"position_id": pid, "status": "cancelled", "entry_fill_status": state})
        db.record_decision(
            conn,
            trade_date=day,
            book=pos["book"],
            symbol=pos["symbol"],
            mode="entry",
            reason=f"entry_{state}",
            accepted=False,
        )
        log(f"entry {state.upper()} {pid} -- never established")
        return {**pos, "status": "cancelled", "entry_fill_status": state}
    return pos


def _confirm_addon_fill(conn, pos: dict, broker, log, config: dict, *, day: str) -> dict:
    """Poll a pending add-on; on a fill, write the legs through the paper writer with the actual
    credit (the first moment the ledger records the add-on); on a dead order clear the marker --
    the arm stays live and the next tick may fire again."""
    pid = pos["position_id"]
    stashed = {}
    try:
        stashed = json.loads(pos.get("pending_addon_json") or "{}")
    except ValueError:
        stashed = {}
    plan = stashed.get("plan") or {}
    asked = float(stashed.get("asked") or plan.get("credit") or 0.0)
    state, price = _execution.fill_state(broker.status(pos["addon_order_id"]), fallback_price=asked)
    if state == "filled":
        if not plan.get("legs"):
            log(f"CRITICAL: add-on FILLED for {pid} but its plan stash is unreadable -- legs not recorded")
            return pos
        fired = bookmod.fire_addon(conn, pos, {**plan, "credit": price}, config)
        mid = pos.get("addon_mid_at_submit")
        fields = {"position_id": pid, "addon_fill_status": "filled", "pending_addon_json": None}
        if mid is not None and price is not None:
            fields["addon_slippage"] = round(
                (float(mid) - float(price)) * 100.0 * int(pos.get("quantity") or 1), 2
            )
        est = pos.get("addon_fee_estimate")
        if est is not None:
            fields["addon_cost"] = round(float(est), 2)
        db.save_position(conn, fields)
        db.record_decision(
            conn,
            trade_date=day,
            book=pos["book"],
            symbol=pos["symbol"],
            mode="addon",
            reason=f"addon filled {price:.2f} (asked {asked:.2f})",
            accepted=True,
        )
        log(f"add-on FILLED {pid}: asked {asked}, filled {price} ({fired['cost']})")
        return {**pos, **fields, "addon_fired_at": clock.now_iso()}
    if state in _execution.TERMINAL_UNFILLED:
        db.save_position(
            conn,
            {
                "position_id": pid,
                "addon_fill_status": state,
                "addon_order_id": None,
                "pending_addon_json": None,
            },
        )
        db.record_decision(
            conn,
            trade_date=day,
            book=pos["book"],
            symbol=pos["symbol"],
            mode="addon",
            reason=f"addon_{state}",
            accepted=False,
        )
        log(f"add-on {state.upper()} {pid} -- arm stays live, may re-fire")
        return {**pos, "addon_fill_status": state, "addon_order_id": None, "pending_addon_json": None}
    return pos


def _confirm_pending(conn, broker, log, config: dict, *, day: str) -> dict:
    counts = {"entries_filled": 0, "entries_dead": 0, "addons_filled": 0, "addons_dead": 0}
    for pos in db.pending_entries(conn):
        after = _confirm_entry_fill(conn, pos, broker, log, config, day=day)
        if after.get("entry_fill_status") == "filled":
            counts["entries_filled"] += 1
        elif after.get("status") == "cancelled":
            counts["entries_dead"] += 1
    for pos in db.pending_addons(conn):
        after = _confirm_addon_fill(conn, pos, broker, log, config, day=day)
        if after.get("addon_fill_status") == "filled":
            counts["addons_filled"] += 1
        elif after.get("addon_fill_status") in _execution.TERMINAL_UNFILLED:
            counts["addons_dead"] += 1
    return counts


# --------------------------------------------------------------------------- resting orders
def _minutes_since(iso: str | None, now: datetime) -> float | None:
    """Minutes from a stamped tick time to this tick. Stamps are written from the tick's own
    clock (`when`), so a replayed or tested tick measures against itself, not the wall."""
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if (then.tzinfo is None) != (now.tzinfo is None):
        then, now = then.replace(tzinfo=None), now.replace(tzinfo=None)
    return (now - then).total_seconds() / 60.0


def _cancel(broker, order_id: str, log, what: str) -> bool:
    """A refused cancel is usually the fill racing it: leave the marker and re-poll next tick.
    Never place a second order over one that could not be cancelled."""
    out = broker.cancel(order_id) if hasattr(broker, "cancel") else {"ok": False}
    if not out.get("ok"):
        log(f"{what}: cancel of {order_id} refused ({out.get('error')}) -- re-polling next tick")
        return False
    return True


def _replace(broker, order_id: str, spec: dict, price: float, *, live: bool, log, what: str) -> str | None:
    """Cancel/replace at a new limit; the new order id, or None when the broker refused."""
    out = broker.replace(order_id, live_orders.with_price(spec, price), live=live)
    if not out.get("ok"):
        log(f"{what}: replace of {order_id} at {price:.2f} refused ({out.get('error')})")
        return None
    return str(out.get("order_id") or _execution.order_id_of(out) or order_id)


def _entry_spec_for(pos: dict, plan: dict, config: dict) -> dict:
    return live_orders.entry_spec(
        plan,
        int(pos.get("quantity") or 1),
        float(_live_cfg(config).get("entry_concession", DEFAULT_CONCESSION)),
    )


def _manage_pending_entry(
    conn, pos: dict, broker, config: dict, *, cache_path: str, when: datetime, day: str, live: bool, log
) -> str:
    """One resting entry order, one verdict: `cancelled` (cutoff, age, or the strikes moved),
    `repriced` (the bounded walk-down took a step or the market moved a tick), `resting`."""
    live_cfg = _live_cfg(config)
    pid = pos["position_id"]
    now_min = clock.minute_of_day(when)
    age = _minutes_since(pos.get("entry_placed_at"), when)
    cancel_after = live_cfg.get("entry_cancel_after_minutes", DEFAULT_CANCEL_AFTER_MIN)

    def _dead(reason: str) -> str:
        if _cancel(broker, pos["entry_order_id"], log, f"entry {pid}"):
            db.save_position(
                conn, {"position_id": pid, "status": "cancelled", "entry_fill_status": "cancelled"}
            )
            db.record_decision(
                conn,
                trade_date=day,
                book=pos["book"],
                symbol=pos["symbol"],
                mode="entry",
                reason=f"no_fill:{reason}",
                accepted=False,
            )
            log(f"entry {pid} cancelled ({reason}) -- never established")
            return "cancelled"
        return "resting"

    if now_min >= entry_cutoff_min(config):
        return _dead("entry_cutoff")
    if cancel_after and age is not None and age >= float(cancel_after):
        return _dead(f"older_than_{cancel_after}m")

    # Re-plan on THIS tick's cached quotes: the same strikes with a moved price is a reprice; a
    # different structure means the market left this order behind -- cancel, and a fresh attempt
    # follows this tick if the window is still open.
    symbol = _pl._symbol(config)
    root = config.get("occ_root") or symbol
    plan_dates = clock.target_expiration(when.date(), config.get("defaults") or {})
    if plan_dates is None or plan_dates["expiration"] != pos["expiration"]:
        return "resting"
    snapshot = provider.build_entry_snapshot(
        cache_path, symbol, plan_dates, root=root, when=when, **provider.snapshot_kwargs(config)
    )
    if not snapshot.get("ok"):
        return "resting"
    planned = engine.plan_entry(snapshot, _live_params(config))
    if not planned.get("ok"):
        return "resting"
    plan = planned["plan"]
    if (plan["body_strike"], plan["near_strike"], plan["far_strike"]) != (
        pos["body_strike"],
        pos["near_strike"],
        pos["far_strike"],
    ):
        return _dead("strikes_moved")

    last = _minutes_since(pos.get("entry_repriced_at") or pos.get("entry_placed_at"), when)
    reprice_after = float(live_cfg.get("reprice_after_minutes", DEFAULT_REPRICE_MIN))
    steps = int(pos.get("entry_reprice_count") or 0)
    if last is not None and last < reprice_after:
        return "resting"
    new_limit = live_orders.next_limit(
        float(pos["entry_limit"]),
        float(plan["credit"]),
        float(live_cfg.get("entry_concession", DEFAULT_CONCESSION)),
        float(pos.get("entry_live_floor") or 0.0),
        steps_taken=steps + 1,
    )
    if new_limit is None:
        return "resting"
    try:
        spec = _entry_spec_for(pos, plan, config)
    except ValueError:
        return "resting"
    spec["external_identifier"] = pos.get("entry_external_id") or pid
    new_id = _replace(broker, pos["entry_order_id"], spec, new_limit, live=live, log=log, what=f"entry {pid}")
    if new_id is None:
        return "resting"
    db.save_position(
        conn,
        {
            "position_id": pid,
            "entry_order_id": new_id,
            "entry_limit": new_limit,
            "entry_reprice_count": steps + 1,
            "entry_repriced_at": when.isoformat(),
            "entry_mid_at_submit": plan["credit"],
        },
    )
    db.record_decision(
        conn,
        trade_date=day,
        book=pos["book"],
        symbol=pos["symbol"],
        mode="entry",
        reason=f"reprice:{steps + 1}",
        accepted=True,
    )
    log(
        f"entry {pid} repriced {float(pos['entry_limit']):.2f} -> {new_limit:.2f} (step {steps + 1}, order {new_id})"
    )
    return "repriced"


def _manage_pending_addon(
    conn, pos: dict, broker, config: dict, *, cache_path: str, when: datetime, day: str, live: bool, log
) -> str:
    """The resting add-on's verdict, same shape as the entry's. A cancelled add-on leaves the arm
    live: the next tick's fire places a fresh attempt."""
    live_cfg = _live_cfg(config)
    pid = pos["position_id"]
    age = _minutes_since(pos.get("addon_placed_at"), when)
    cancel_after = live_cfg.get("addon_cancel_after_minutes")
    if cancel_after is None:
        cancel_after = live_cfg.get("entry_cancel_after_minutes", DEFAULT_CANCEL_AFTER_MIN)

    def _dead(reason: str) -> str:
        if _cancel(broker, pos["addon_order_id"], log, f"add-on {pid}"):
            db.save_position(
                conn,
                {
                    "position_id": pid,
                    "addon_fill_status": "cancelled",
                    "addon_order_id": None,
                    "pending_addon_json": None,
                },
            )
            db.record_decision(
                conn,
                trade_date=day,
                book=pos["book"],
                symbol=pos["symbol"],
                mode="addon",
                reason=f"addon_no_fill:{reason}",
                accepted=False,
            )
            log(f"add-on {pid} cancelled ({reason}) -- arm stays live")
            return "cancelled"
        return "resting"

    if cancel_after and age is not None and age >= float(cancel_after):
        return _dead(f"older_than_{cancel_after}m")

    params = management.effective_params(pos, config)
    snap = _pl._addon_snapshot(
        cache_path,
        _pl._symbol(config),
        pos,
        when,
        params.get("max_quote_age_seconds", 300),
        root=config.get("occ_root") or _pl._symbol(config),
    )
    if not snap.get("ok"):
        return "resting"
    planned = engine.plan_addon(snap, pos["far_strike"], params)
    if not planned.get("ok"):
        return _dead(planned.get("reason") or "addon_not_credit")
    plan = planned["plan"]
    last = _minutes_since(pos.get("addon_repriced_at") or pos.get("addon_placed_at"), when)
    if last is not None and last < float(live_cfg.get("reprice_after_minutes", DEFAULT_REPRICE_MIN)):
        return "resting"
    steps = int(pos.get("addon_reprice_count") or 0)
    stashed = {}
    try:
        stashed = json.loads(pos.get("pending_addon_json") or "{}")
    except ValueError:
        stashed = {}
    working = float(stashed.get("asked") or plan["credit"])
    new_limit = live_orders.next_limit(
        working,
        float(plan["credit"]),
        float(live_cfg.get("addon_concession", DEFAULT_CONCESSION)),
        float(pos.get("addon_live_floor") or 0.0),
        steps_taken=steps + 1,
    )
    if new_limit is None:
        return "resting"
    try:
        spec = live_orders.addon_spec(
            plan, int(pos.get("quantity") or 1), float(live_cfg.get("addon_concession", DEFAULT_CONCESSION))
        )
    except ValueError:
        return "resting"
    spec["external_identifier"] = pos.get("addon_external_id") or f"{pid}-addon"
    new_id = _replace(
        broker, pos["addon_order_id"], spec, new_limit, live=live, log=log, what=f"add-on {pid}"
    )
    if new_id is None:
        return "resting"
    db.save_position(
        conn,
        {
            "position_id": pid,
            "addon_order_id": new_id,
            "addon_reprice_count": steps + 1,
            "addon_repriced_at": when.isoformat(),
            "addon_mid_at_submit": plan["credit"],
            "pending_addon_json": json.dumps({"plan": plan, "asked": new_limit}),
        },
    )
    log(f"add-on {pid} repriced {working:.2f} -> {new_limit:.2f} (step {steps + 1}, order {new_id})")
    return "repriced"


# --------------------------------------------------------------------------- entry
def _open_with_legs(conn) -> list[tuple[dict, list[dict]]]:
    return [(pos, db.open_legs_for(conn, pos["position_id"])) for pos in db.open_positions(conn)]


def _refuse(conn, *, day: str, book: str, symbol: str, reason: str, log, detail: str | None = None) -> dict:
    db.record_entry_attempt(
        conn, trade_date=day, symbol=symbol, book=book, outcome=reason, block_detail=detail
    )
    db.record_decision(
        conn, trade_date=day, book=book, symbol=symbol, mode="entry", reason=reason, accepted=False
    )
    log(f"[{book}] entry refused: {reason}{' ' + detail if detail else ''}")
    return {"entry": "refused", "reason": reason}


def _try_live_entry(
    config: dict, conn, broker, *, cache_path: str, when: datetime, day: str, live: bool, log
) -> dict:
    """ONE entry attempt for the live arm, gated in the order the money cares about: the day's
    budget, a working order, the breakers, the plan itself, the live floor, the margin caps -- and
    only then the broker."""
    live_cfg = _live_cfg(config)
    arm = _arm(config)
    symbol = _pl._symbol(config)
    root = config.get("occ_root") or symbol
    now_min = clock.minute_of_day(when)
    refuse = lambda reason, detail=None: _refuse(  # noqa: E731
        conn, day=day, book=arm, symbol=symbol, reason=reason, log=log, detail=detail
    )

    if now_min < entry_time_min(config) or now_min >= entry_cutoff_min(config):
        return {"entry": "outside_window"}
    per_day = int(live_cfg.get("max_structures_per_day", 1))
    if db.established_today(conn, arm, day) >= per_day:
        return {"entry": "done", "reason": "max_structures_per_day_reached"}
    if db.pending_entries(conn):
        return {"entry": "pending"}
    if daily_loss_tripped(conn, day, live_cfg.get("daily_loss_halt_dollars")):
        return refuse("daily_loss_halt")
    tripped, marked = mark_drawdown_tripped(conn, live_cfg.get("mark_drawdown_halt_dollars"))
    if tripped:
        return refuse("mark_drawdown_halt", f"open marked loss {marked:.2f}")

    plan_dates = clock.target_expiration(when.date(), config.get("defaults") or {})
    if plan_dates is None:
        return refuse("no_expiration_plan")
    snapshot = provider.build_entry_snapshot(
        cache_path, symbol, plan_dates, root=root, when=when, **provider.snapshot_kwargs(config)
    )
    if not snapshot.get("ok"):
        db.record_snapshot(
            conn,
            trade_date=day,
            symbol=symbol,
            kind="entry",
            status=snapshot["reason"],
            quotes_stale=snapshot.get("rejected"),
        )
        return refuse(snapshot["reason"])
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
    params = _live_params(config)
    planned = engine.plan_entry(snapshot, params)
    if not planned.get("ok"):
        return refuse(planned["reason"], json.dumps(planned.get("detail")) if planned.get("detail") else None)
    plan = planned["plan"]
    quantity = int(params.get("quantity", 1))
    concession = float(live_cfg.get("entry_concession", DEFAULT_CONCESSION))
    min_net = float(live_cfg.get("min_net_credit_dollars", DEFAULT_MIN_NET))

    # The floor from the schedule first (whether to submit at all), then from the broker's own
    # estimate after the preflight (whether the submitted limit still clears it).
    schedule_fee = engine.entry_cost(symbol, [], quantity, config)["fee"]
    floor = live_orders.live_floor(schedule_fee, min_net, quantity)
    try:
        spec = live_orders.entry_spec(plan, quantity, concession, floor=floor)
    except ValueError as exc:
        return refuse(
            "credit_below_live_floor" if "credit_below_live_floor" in str(exc) else "not_a_credit", str(exc)
        )

    reserve = arm != "control"
    exceeded, totals = live_orders.margin_cap_exceeded(
        live_cfg.get("max_open_margin_dollars"),
        live_cfg.get("max_open_margin_per_expiration_dollars"),
        _open_with_legs(conn),
        plan["legs"],
        plan["credit"],
        plan["expiration"],
        quantity,
        params=params,
        reserve_addons=reserve,
    )
    if exceeded:
        return refuse(f"max_open_margin_{totals['cap']}_reached", json.dumps(totals))

    attempt = (
        1
        + conn.execute(
            "SELECT COUNT(*) FROM bwb_positions WHERE book = ? AND entry_session = ?", (arm, day)
        ).fetchone()[0]
    )
    pid = f"{symbol}:{arm}:{day}:{attempt}"
    spec["external_identifier"] = pid
    result = broker.place(spec, live)
    if not result.get("ok"):
        if result.get("uncertain"):
            log(f"CRITICAL: entry {pid} outcome UNKNOWN -- adapter held; {result.get('error')}")
            return refuse("submit_uncertain", str(result.get("error")))
        return refuse("submit_failed", str(result.get("error") or result.get("unmet_gates")))
    estimate = _fee_estimate(result)
    if estimate is not None:
        est_floor = live_orders.live_floor(estimate, min_net, quantity)
        if spec["price"] < est_floor:
            # The preflight priced the fees above the schedule and the limit no longer clears
            # the floor. On a dry run nothing was placed; live, the order IS working -- pull it.
            if not result.get("dry_run") and result.get("order_id"):
                _cancel(broker, str(result["order_id"]), log, f"entry {pid}")
            return refuse(
                "credit_below_live_floor",
                f"limit {spec['price']:.2f}; schedule floor {floor:.2f}, broker-estimate floor {est_floor:.2f}",
            )
    if result.get("dry_run"):
        db.record_decision(
            conn,
            trade_date=day,
            book=arm,
            symbol=symbol,
            mode="entry",
            reason=f"dry_run_preflight ok: {spec['price']:.2f} credit, fee est {estimate}",
            accepted=False,
        )
        log(f"[{arm}] DRY RUN entry {pid}: preflight ok at {spec['price']:.2f} (fee estimate {estimate})")
        return {"entry": "dry_run", "price": spec["price"], "fee_estimate": estimate}
    order_id = result.get("order_id")
    if order_id is None:
        log(f"CRITICAL: live entry {pid} accepted but no order id came back -- orphan sweep will find it")
        return refuse("no_order_id", str(result))
    opened = bookmod.enter_position(
        conn,
        plan,
        config,
        arm,
        entry_session=day,
        advice_params=None,
        position_id_override=pid,
        extra={
            "status": "pending",
            "entry_order_id": str(order_id),
            "entry_external_id": result.get("external_identifier") or pid,
            "entry_fill_status": "pending",
            "entry_limit": spec["price"],
            "entry_placed_at": when.isoformat(),
            "entry_live_floor": floor
            if estimate is None
            else live_orders.live_floor(estimate, min_net, quantity),
            "entry_reprice_count": 0,
            "entry_mid_at_submit": plan["credit"],
            "entry_fee_estimate": estimate,
            "entry_cost": estimate if estimate is not None else schedule_fee,
            "entry_slippage": 0.0,
            "fees": estimate if estimate is not None else schedule_fee,
            "fees_source": "broker_estimate" if estimate is not None else "modeled",
        },
    )
    if opened is None:
        log(f"CRITICAL: live entry {pid} placed (order {order_id}) but the row already existed")
        return {"entry": "placed", "order_id": order_id, "recorded": False}
    db.record_entry_attempt(
        conn,
        trade_date=day,
        symbol=symbol,
        book=arm,
        outcome="placed",
        spot=plan["spot"],
        body_strike=plan["body_strike"],
        near_strike=plan["near_strike"],
        far_strike=plan["far_strike"],
        credit=spec["price"],
    )
    db.record_decision(
        conn,
        trade_date=day,
        book=arm,
        symbol=symbol,
        mode="entry",
        reason=f"placed {plan['near_strike']:g}/{plan['body_strike']:g}x2/{plan['far_strike']:g} at {spec['price']:.2f}",
        accepted=True,
    )
    log(
        f"[{arm}] LIVE entry {pid} placed: {plan['near_strike']:g}/{plan['body_strike']:g}x2/{plan['far_strike']:g} "
        f"({plan['expiration']}) limit {spec['price']:.2f} (mid {plan['credit']:.2f}), order {order_id}"
        f"{' RECOVERED by identifier' if result.get('recovered') else ''}"
    )
    return {"entry": "placed", "order_id": str(order_id), "position_id": pid, "price": spec["price"]}


# --------------------------------------------------------------------------- the add-on seam
def _make_fire(broker, config: dict, *, live: bool, day: str, when: datetime, log, placed: dict):
    """The `fire` hook for `paper_loop._manage_positions`: place the add-on, stash the plan, and
    return False -- nothing is recorded fired until the broker confirms. One add-on order per
    tick across the ladder (a flip reclaim can arm several positions in the same second)."""
    live_cfg = _live_cfg(config)
    state = {"placed_this_tick": 0}

    def fire(conn, position: dict, plan: dict, cfg: dict) -> bool:
        pid = position["position_id"]
        symbol = position["symbol"]
        if position.get("addon_fill_status") == "pending" or position.get("addon_order_id"):
            return False
        if state["placed_this_tick"] >= 1:
            db.record_decision(
                conn,
                trade_date=day,
                book=position["book"],
                symbol=symbol,
                mode="addon",
                reason="addon_throttled:one_per_tick",
                accepted=False,
            )
            return False
        quantity = int(position.get("quantity") or 1)
        concession = float(live_cfg.get("addon_concession", DEFAULT_CONCESSION))
        min_net = float(live_cfg.get("min_net_credit_dollars", DEFAULT_MIN_NET))
        floor = live_orders.live_floor(
            engine.addon_entry_cost(symbol, [], quantity, cfg)["fee"], min_net, quantity
        )
        try:
            spec = live_orders.addon_spec(plan, quantity, concession, floor=floor)
        except ValueError as exc:
            db.record_decision(
                conn,
                trade_date=day,
                book=position["book"],
                symbol=symbol,
                mode="addon",
                reason="addon_below_live_floor"
                if "credit_below_live_floor" in str(exc)
                else "addon_not_credit",
                accepted=False,
            )
            return False
        attempt = int(position.get("addon_attempts") or 0) + 1
        ext = f"{pid}-addon{attempt}"
        spec["external_identifier"] = ext
        result = broker.place(spec, live)
        state["placed_this_tick"] += 1
        if not result.get("ok"):
            db.record_decision(
                conn,
                trade_date=day,
                book=position["book"],
                symbol=symbol,
                mode="addon",
                reason="addon_submit_uncertain" if result.get("uncertain") else "addon_submit_failed",
                accepted=False,
            )
            log(f"add-on {pid} submit failed: {result.get('error') or result.get('unmet_gates')}")
            return False
        estimate = _fee_estimate(result)
        if result.get("dry_run"):
            db.record_decision(
                conn,
                trade_date=day,
                book=position["book"],
                symbol=symbol,
                mode="addon",
                reason=f"dry_run_preflight ok: add-on {spec['price']:.2f} credit",
                accepted=False,
            )
            log(f"DRY RUN add-on {pid}: preflight ok at {spec['price']:.2f} (fee estimate {estimate})")
            return False
        order_id = result.get("order_id")
        if order_id is None:
            log(
                f"CRITICAL: live add-on {pid} accepted but no order id came back -- orphan sweep will find it"
            )
            return False
        db.save_position(
            conn,
            {
                "position_id": pid,
                "addon_order_id": str(order_id),
                "addon_external_id": result.get("external_identifier") or ext,
                "addon_fill_status": "pending",
                "addon_placed_at": when.isoformat(),
                "addon_live_floor": floor,
                "addon_reprice_count": 0,
                "addon_mid_at_submit": plan["credit"],
                "addon_attempts": attempt,
                "addon_fee_estimate": estimate,
                "pending_addon_json": json.dumps({"plan": plan, "asked": spec["price"]}),
            },
        )
        placed[str(order_id)] = {**position, "addon_order_id": str(order_id), "kind": "addon"}
        db.record_decision(
            conn,
            trade_date=day,
            book=position["book"],
            symbol=symbol,
            mode="addon",
            reason=f"addon placed at {spec['price']:.2f} (mid {plan['credit']:.2f})",
            accepted=True,
        )
        log(f"LIVE add-on {pid} placed at {spec['price']:.2f} (mid {plan['credit']:.2f}), order {order_id}")
        return False

    return fire


# --------------------------------------------------------------------------- settlement
def run_settle_live(
    config: dict,
    conn,
    *,
    cache_path: str,
    when: datetime | None = None,
    price: float | None = None,
    day: str | None = None,
    broker=None,
    log=_log,
) -> dict:
    """Settle the live ledger's legs expiring `day` -- on an OFFICIAL print only. A hand-supplied
    `--price` is official by declaration; otherwise the broker chain must answer with a posted
    close (`core.settlement.OFFICIAL_SOURCES`). Anything else is `official_print_unavailable`
    and the next tick tries again. There is no provisional settlement on a live ledger: this
    schema's settlement is destructive, and a live P&L number is never provisional."""
    when = when or clock.now_et()
    day = day or when.date().isoformat()
    symbol = _pl._symbol(config)
    if not any(
        leg["position_symbol"] == symbol and leg.get("position_status") != "pending"
        for leg in db.expiring_open_legs(conn, day)
    ):
        return {"ok": True, "results": [], "date": day}
    if price is not None:
        spot, source = float(price), "official"
    else:
        if broker is None or not hasattr(broker, "official_settlement_price"):
            return {"ok": False, "reason": "official_print_unavailable", "detail": "no broker", "date": day}
        spot, source = broker.official_settlement_price(symbol)
        if spot is None or not _settlement.is_official_source(source):
            log(f"{symbol}: no official settlement print yet for {day} ({source}) -- retrying next tick")
            return {"ok": False, "reason": "official_print_unavailable", "detail": source, "date": day}
    results = bookmod.settle_expiring_legs(conn, day, spot, config, symbol=symbol)
    for result in results:
        db.save_position(conn, {"position_id": result["position_id"], "settlement_source": source})
        log(
            f"{symbol} {result['position_id']}: settled {result['settled_legs']} leg(s) at {spot:.2f} "
            f"[{source}] ({result['itm']} ITM, fee {result['fee']:.2f})"
        )
    return {"ok": True, "results": results, "spot": spot, "source": source, "date": day}


def _unsettled_today(conn, day: str) -> bool:
    return any(leg.get("position_status") != "pending" for leg in db.expiring_open_legs(conn, day))


# --------------------------------------------------------------------------- the tick
def run_once(
    config: dict,
    conn,
    broker,
    *,
    cache_path: str,
    when: datetime | None = None,
    live: bool = False,
    force: bool = False,
    log=_log,
    clock_fn=time.time,
    sleep_fn=time.sleep,
) -> dict:
    """One live tick. `broker` is the injected seam (`BrokerAdapter` in production, a fake in
    tests). `live=False` is the dry-run posture: the preflight runs against the real account and
    nothing is placed or recorded."""
    _beat()
    when = when or clock.now_et()
    now_min = clock.minute_of_day(when)
    today = when.date()
    day = today.isoformat()
    live_cfg = _live_cfg(config)
    symbol = _pl._symbol(config)
    summary: dict = {"ok": True, "date": day, "live": live}

    if not force and not _cal.is_trading_day(today):
        return {**summary, "skipped": "not_a_trading_day"}

    # 0. the dead-man's switch, before any broker read
    if live:
        reason = should_disarm(config, now_min, day)
        if reason:
            log(f"self-disarm: {reason}")
            uninstall_task()
            return {**summary, "disarmed": reason}

    # 1. broker truth against ledger belief
    summary["orphans"] = _sweep_orphans(conn, broker, log, symbol)

    # 2. what filled, what died
    summary["confirm"] = _confirm_pending(conn, broker, log, config, day=day)

    # 3. resting orders
    verdicts = []
    for pos in db.pending_entries(conn):
        verdicts.append(
            _manage_pending_entry(
                conn, pos, broker, config, cache_path=cache_path, when=when, day=day, live=live, log=log
            )
        )
    for pos in db.pending_addons(conn):
        verdicts.append(
            _manage_pending_addon(
                conn, pos, broker, config, cache_path=cache_path, when=when, day=day, live=live, log=log
            )
        )
    summary["resting"] = verdicts

    # 4. settlement, when due
    if now_min >= settle_min(config) and _unsettled_today(conn, day):
        summary["settle"] = run_settle_live(
            config, conn, cache_path=cache_path, when=when, broker=broker, log=log
        )
        return summary

    if not force and not _pl.in_session(now_min):
        return {**summary, "skipped": "outside_rth"}

    # 5. one entry attempt inside the window
    placed: dict[str, dict] = {}
    entry = _try_live_entry(
        config, conn, broker, cache_path=cache_path, when=when, day=day, live=live, log=log
    )
    summary["entry"] = entry
    if entry.get("entry") == "placed" and entry.get("order_id"):
        placed[entry["order_id"]] = {"position_id": entry["position_id"], "kind": "entry"}

    # 6. the paper tick's own pass over the live positions, with the fire seam swapped
    ticks = _pl._record_trigger_ticks(config, conn, cache_path=cache_path, when=when, day=day)
    marked, values = _pl._mark_positions(config, conn, cache_path=cache_path, when=when, day=day)
    actions = _pl._manage_positions(
        config,
        conn,
        values,
        cache_path=cache_path,
        when=when,
        day=day,
        fire=_make_fire(broker, config, live=live, day=day, when=when, log=log, placed=placed),
        log=log,
    )
    summary.update(
        {"trigger_ticks": ticks, "marks": marked, "open_positions": len(values), "actions": actions}
    )

    # 7. a bounded watch over anything placed this tick
    if placed and live:
        watch_s = float(live_cfg.get("fill_watch_seconds", DEFAULT_WATCH_SECONDS))
        poll_s = float(live_cfg.get("fill_watch_poll_seconds", DEFAULT_WATCH_POLL_SECONDS))

        def on_status(order_id, handle, status) -> bool:
            state, _ = _execution.fill_state(status)
            if state == "working":
                return False
            row = conn.execute(
                "SELECT * FROM bwb_positions WHERE position_id = ?", (handle["position_id"],)
            ).fetchone()
            if row is None:
                return True
            row = dict(row)
            if handle["kind"] == "entry":
                _confirm_entry_fill(conn, row, broker, log, config, day=day)
            else:
                _confirm_addon_fill(conn, row, broker, log, config, day=day)
            return True

        summary["watched"] = _execution.watch(
            broker,
            dict(placed),
            deadline=clock_fn() + watch_s,
            poll_seconds=poll_s,
            heartbeat_seconds=poll_s,
            clock=clock_fn,
            sleep=sleep_fn,
            on_status=on_status,
        )

    db.record_iteration(
        conn,
        ran_at=time.time(),
        session_date=day,
        phase="live" if live else "dry_run",
        status="ok",
        open_positions=len(values),
        marks_written=marked,
        actions_taken=actions,
        note=f"trigger_ticks={ticks}; entry={entry.get('entry')}",
    )
    return summary


# --------------------------------------------------------------------------- arm / disarm
def _spawn_first_tick() -> None:
    """One detached `--once --live` right away so arming does not wait a full interval."""
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x08000000 | 0x00000200  # DETACHED | NO_WINDOW | NEW_GROUP
    subprocess.Popen(
        [_pythonw(), "-m", "cherrypick.bwb.live_loop", "--once", "--live"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def _pythonw() -> str:
    import sys

    exe = sys.executable
    if os.name == "nt" and exe.lower().endswith("python.exe"):
        candidate = exe[:-10] + "pythonw.exe"
        if os.path.exists(candidate):
            return candidate
    return exe


def arm_stamp_date() -> str | None:
    return _live.arm_record_date("bwb")


def should_disarm(config: dict, now_min: int, today: str) -> str | None:
    return _live.should_disarm(
        arm_stamp_date(),
        today=today,
        now_min=now_min,
        disarm_min=disarm_min(config),
        disarm_label=_live_cfg(config).get("disarm_time") or DEFAULT_DISARM,
    )


def install_task() -> dict:
    """Arm the live loop FOR TODAY: the arm record, under a live supervisor, and nothing else
    (`cherrypick.core.live.arm`). No scheduled-task fallback: this module was born under the
    supervisor and has no transition window to honour."""
    return _live.arm(
        "bwb",
        date=clock.now_et().date().isoformat(),
        at=clock.now_iso(),
        armed_by=ARMED_BY,
        spawn_first_tick=_spawn_first_tick,
    )


def uninstall_task() -> dict:
    return _live.disarm("bwb")


# --------------------------------------------------------------------------- status
def run_status(config: dict, conn, *, cache_path: str, broker=None) -> dict:
    """One merged JSON object -- files and DB only; the broker is consulted only for `held`."""
    when = clock.now_et()
    today = when.date().isoformat()
    live_cfg = _live_cfg(config)
    open_now = db.open_positions(conn)
    pending = [p["position_id"] for p in db.pending_entries(conn)] + [
        f"{p['position_id']}-addon" for p in db.pending_addons(conn)
    ]
    lf = log_file()
    try:
        last_log = datetime.fromtimestamp(os.path.getmtime(lf)).isoformat(timespec="seconds")
    except OSError:
        last_log = None
    _, marked = mark_drawdown_tripped(conn, live_cfg.get("mark_drawdown_halt_dollars") or 1)
    return {
        "ok": True,
        "date": today,
        "armed_for": arm_stamp_date(),
        "arm": _arm(config),
        "in_session": _pl.in_session(clock.minute_of_day(when)),
        "open_positions": len(open_now),
        "pending_orders": pending,
        "established_today": db.established_today(conn, _arm(config), today),
        "session_settled": not _unsettled_today(conn, today),
        "halt_flag": os.path.exists(halt_flag_path()),
        "breaker_tripped": daily_loss_tripped(conn, today, live_cfg.get("daily_loss_halt_dollars")),
        "open_marked_loss": marked,
        "orphaned_orders": read_orphans(),
        "broker_held": broker.held if broker is not None and hasattr(broker, "held") else None,
        "last_log_write": last_log,
        "log_file": str(lf),
        "live_db": db.live_db_path(),
        "target_expiration": clock.target_expiration(when.date(), config.get("defaults") or {}),
        "stream_cache": cache_path,
        "stream_cache_present": os.path.exists(cache_path),
    }


def _beat() -> None:
    try:
        path = _home.heartbeat_path("bwb-live")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now().astimezone().isoformat(), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cherrypick-bwb LIVE session driver (per-day armed)")
    ap.add_argument("--config")
    ap.add_argument("--stream-cache")
    ap.add_argument("--once", action="store_true", help="one tick (dry-run unless --live)")
    ap.add_argument("--live", action="store_true", help="place real orders (every gate must be met)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--price", type=float, help="the official settlement print, by hand")
    ap.add_argument("--date", help="YYYY-MM-DD")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--install-task", action="store_true", help="arm for today (the arm command's path)")
    ap.add_argument("--uninstall-task", action="store_true", help="disarm now")
    args = ap.parse_args(argv)

    config = climod.load_config(args.config)
    cache_path = args.stream_cache or _pl.stream_cache_path(config)
    db_path = db.live_db_path()  # never the env override: the live ledger is this file or nothing
    conn = db.connect(db_path)
    stream_request.register(config, conn, db_path, cache_path=cache_path, live=True)

    def out(payload) -> int:
        print(json.dumps(payload, indent=2, default=str))
        return 0 if payload.get("ok", True) else 1

    if args.install_task:
        return out(install_task())
    if args.uninstall_task:
        return out(uninstall_task())
    if args.status:
        return out(run_status(config, conn, cache_path=cache_path))
    if args.settle:
        broker = None
        if args.price is None:
            broker = BrokerAdapter(config)
        return out(
            run_settle_live(
                config, conn, cache_path=cache_path, price=args.price, day=args.date, broker=broker
            )
        )
    if args.once:
        if args.live:
            from cherrypick.bwb import credentials

            unmet = readiness(
                config,
                halt_present=os.path.exists(halt_flag_path()),
                designated=credentials.designated_account(),
            )
            if unmet:
                return out({"ok": False, "error": "live gates unmet", "unmet_gates": unmet})
            reason = should_disarm(
                config, clock.minute_of_day(clock.now_et()), clock.now_et().date().isoformat()
            )
            if reason:
                uninstall_task()
                return out({"ok": True, "disarmed": reason})
        if not looplock.acquire(_once_lock_path(), 180, alive=looplock.pid_alive):
            return out({"ok": True, "skipped": "another live tick is running"})
        try:
            broker = BrokerAdapter(config)
            return out(
                run_once(config, conn, broker, cache_path=cache_path, live=args.live, force=args.force)
            )
        finally:
            looplock.release(_once_lock_path())
    ap.error("choose one of --once, --status, --settle, --install-task, --uninstall-task")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
