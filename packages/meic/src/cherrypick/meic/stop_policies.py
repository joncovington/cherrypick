"""Derived stop policies — computed from a fully-marked open position's recorded path (Phase 1e:
put_max_cost/call_max_cost, put_settle_value/call_settle_value, put_touch_time/call_touch_time),
not run as separate entry streams. A fully-marked position's path contains every stop policy's
outcome: `open` (config.risk.json) records that path with no stop of its own
(per_side_stop_management: false), so stop-none / stop-0.75-net / stop-2.0-side / strike-touch are
all read-side computations over `open`'s rows — at 1x position cost, and with EXACT pairing (same
entries, same strikes, same credit) across every policy, since they are the same rows re-scored.

This is only valid because paper has no market impact and positions are independent. See
validate_against_control below for the derivation's own validation: `control` runs the real
0.95x-net-credit stop live, so re-deriving that exact policy from control's own recorded path and
comparing against control's REAL recorded pnl is the check that this module's arithmetic actually
reconstructs the mechanism it claims to, not an approximation of it.

Every policy's "fired" reading is exact for a REAL stop's own basis (a stopped side's put_max_cost
equals its real stop fill price, since _max_cost_updates records the same cost_now the trigger
fired on) but is a MAX-COST PROXY for any OTHER threshold: derive() cannot know whether a tighter
or looser threshold would have fired at a different, unrecorded moment, only whether the recorded
running maximum ever reached it — and if so, prices the fill AT that maximum. This is a documented
approximation (Phase 3 counterfactual review measured ~$2-8/side replay error against it), not a
full tick-by-tick replay, which would need the complete cost path this database does not store.
"""

from __future__ import annotations

# Named policies: (basis, multiple). basis is one of:
#   "net"   -- multiple x net_credit (the whole IC's credit) is the trigger, per side's own max cost
#   "side"  -- multiple x that side's own credit is the trigger
#   "none"  -- never fires; every side is held to settlement
#   "touch" -- fires the first time spot reached (crossed toward ITM) that side's short strike
POLICIES = {
    "control": ("net", 0.95),  # today's canonical MEIC+ rule -- for validate_against_control only
    "stop-none": ("none", None),
    "stop-0.75-net": ("net", 0.75),
    "stop-2.0-side": ("side", 2.0),
    "strike-touch": ("touch", None),
}


_MULT = 100  # dollar_multiplier is 100 on every recorded row (MEIC trades whole contracts only)


def _side_pnl(credit, fired, max_cost, settle_value):
    """This side's derived P&L: fired -> bought back at the recorded max cost (the fill-price
    proxy); held -> settled at the recorded settle value. None ("cannot be derived") when the
    row is missing the field this policy actually needs -- never silently priced at 0."""
    if fired:
        if credit is None or max_cost is None:
            return None
        return round((credit - max_cost) * _MULT, 2)
    if credit is None or settle_value is None:
        return None
    return round((credit - settle_value) * _MULT, 2)


def derive(row: dict, policy_name: str, *, fee_one_side, fee_full_ic) -> dict:
    """Score one ic_trades row (a plain dict — pass dict(sqlite_row) for a sqlite3.Row) under
    `policy_name` from POLICIES. Returns {"derivable", "put_fired", "call_fired", "pnl", "fee"}.
    `derivable` is False (pnl None) when a side that needed to be priced is missing the recorded
    field the policy requires -- e.g. a pre-Phase-1e row with no put_max_cost/put_settle_value.

    fee_one_side(symbol)/fee_full_ic(symbol) are paper.close_fees_one_side/close_fees_full_ic,
    injected rather than imported to keep this module free of the fee-schedule dependency (a
    caller iterating many rows for the same symbol should compute the fee once, not per row).
    """
    basis, multiple = POLICIES[policy_name]
    put_credit, call_credit, net_credit = row.get("put_credit"), row.get("call_credit"), row.get("net_credit")
    put_max, call_max = row.get("put_max_cost"), row.get("call_max_cost")
    put_settle, call_settle = row.get("put_settle_value"), row.get("call_settle_value")

    if basis == "none":
        put_fired = call_fired = False
    elif basis == "touch":
        put_fired = row.get("put_touch_time") is not None
        call_fired = row.get("call_touch_time") is not None
    elif basis == "net":
        thresh = None if net_credit is None else multiple * net_credit
        put_fired = thresh is not None and put_max is not None and put_max >= thresh
        call_fired = thresh is not None and call_max is not None and call_max >= thresh
    elif basis == "side":
        put_thresh = None if put_credit is None else multiple * put_credit
        call_thresh = None if call_credit is None else multiple * call_credit
        put_fired = put_thresh is not None and put_max is not None and put_max >= put_thresh
        call_fired = call_thresh is not None and call_max is not None and call_max >= call_thresh
    else:
        raise ValueError(f"unknown basis {basis!r}")

    put_pnl = _side_pnl(put_credit, put_fired, put_max, put_settle)
    call_pnl = _side_pnl(call_credit, call_fired, call_max, call_settle)
    if put_pnl is None or call_pnl is None:
        return {
            "derivable": False,
            "put_fired": put_fired,
            "call_fired": call_fired,
            "pnl": None,
            "fee": None,
        }

    symbol = row.get("symbol")
    if put_fired and call_fired:
        fee = fee_full_ic(symbol)
    elif put_fired or call_fired:
        fee = fee_one_side(symbol)
    else:
        fee = 0.0  # both sides held to settlement -- expiration is not a transaction, no closing fee

    return {
        "derivable": True,
        "put_fired": put_fired,
        "call_fired": call_fired,
        # GROSS pnl (credit minus fill/settle cost, summed both sides) -- matches ic_trades.pnl's
        # own convention exactly (fees tracked in a separate column, never netted into pnl there
        # either; see _apply_exit_decision's delta_pnl, which never subtracts a fee). Net pnl is
        # pnl - fee, computed by the caller the same way it would for a real row.
        "pnl": round(put_pnl + call_pnl, 2),
        "fee": fee,
    }


def validate_against_control(rows: list[dict], *, fee_one_side, fee_full_ic, tolerance: float = 0.5) -> dict:
    """The derivation's own validation. `control` runs the REAL 0.95x-net-credit stop live, so
    re-deriving that exact policy (POLICIES['control']) from control's own recorded path and
    comparing against its REAL recorded `pnl` checks that derive()'s arithmetic reconstructs the
    mechanism it claims to, not merely a plausible approximation of it.

    Scoped to status in ('stopped', 'expired') — the two mechanisms derive() actually models (a
    real per-side stop, or cash settlement). A force-closed row is exited by a THIRD mechanism
    (an event/EOD close, not a stop and not settlement) this module does not model; including one
    would report a mismatch that is a scope limitation, not a derivation bug, so those rows are
    counted separately and excluded from the pass/fail tolerance check.

    tolerance is a dollar bound per trade (fee-rounding/order-of-operations noise only — a real
    derivation bug shows up as a large, not marginal, mismatch). Returns a summary dict; raises
    nothing, so a caller can decide how to report a failed validation.
    """
    compared, mismatches, skipped_force_closed, skipped_no_recorded_pnl = [], [], 0, 0
    for row in rows:
        if row.get("risk_profile") != "control":
            continue
        status = row.get("status")
        if status == "force_closed":
            skipped_force_closed += 1
            continue
        if status not in ("stopped", "expired"):
            continue
        real_pnl = row.get("pnl")
        if real_pnl is None:
            skipped_no_recorded_pnl += 1
            continue
        derived = derive(row, "control", fee_one_side=fee_one_side, fee_full_ic=fee_full_ic)
        if not derived["derivable"]:
            skipped_no_recorded_pnl += 1
            continue
        delta = round(derived["pnl"] - real_pnl, 2)
        entry = {
            "ic_order_id": row.get("ic_order_id"),
            "real_pnl": real_pnl,
            "derived_pnl": derived["pnl"],
            "delta": delta,
        }
        compared.append(entry)
        if abs(delta) > tolerance:
            mismatches.append(entry)

    return {
        "compared": len(compared),
        "mismatches": mismatches,
        "skipped_force_closed": skipped_force_closed,
        "skipped_no_recorded_pnl": skipped_no_recorded_pnl,
        "ok": not mismatches and len(compared) > 0,
        "max_abs_delta": max((abs(e["delta"]) for e in compared), default=None),
    }
