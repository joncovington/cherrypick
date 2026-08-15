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

# The stop_trigger_ratio sweep (2026-08-15, advisor creative proposal #12). Same "net" basis the
# real per-side stop uses -- a side's cost against the WHOLE IC's net credit -- so a grid point is
# the deployed mechanism at a different number, not a different mechanism. Brackets the deployed
# 0.95 in both directions, matching the advice bounds meic already declares for this parameter.
GRID_RATIOS = (0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20, 1.25)


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


def derive(row: dict, policy_name: str | tuple, *, fee_one_side, fee_full_ic) -> dict:
    """Score one ic_trades row (a plain dict — pass dict(sqlite_row) for a sqlite3.Row) under
    `policy_name` from POLICIES, or under a raw ``(basis, multiple)`` spec (what the grid passes,
    so a swept ratio needs no entry in POLICIES). Returns
    {"derivable", "put_fired", "call_fired", "pnl", "fee"}. `derivable` is False (pnl None) when a
    side that needed to be priced is missing the recorded field the policy requires -- e.g. a
    pre-Phase-1e row with no put_max_cost/put_settle_value.

    fee_one_side(symbol)/fee_full_ic(symbol) are paper.close_fees_one_side/close_fees_full_ic,
    injected rather than imported to keep this module free of the fee-schedule dependency (a
    caller iterating many rows for the same symbol should compute the fee once, not per row).
    """
    basis, multiple = POLICIES[policy_name] if isinstance(policy_name, str) else policy_name
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


def censored_above(row: dict) -> float | None:
    """The net-credit ratio above which THIS row's recorded path says nothing, or None if the row
    can answer any ratio.

    The trap the grid would otherwise walk into. `*_max_cost` is a running maximum recorded *while
    the side is open*, so a side that actually stopped stopped being observed at that moment: its
    max_cost is the stop fill and the path beyond it was never seen. Scoring a LOOSER ratio against
    it would silently answer "that threshold never fired" when the truth is "we cut the recording
    off before it could." A side held to settlement has a complete path and censors nothing.

    So this returns max(recorded max_cost) / net_credit for a row whose status is 'stopped' — every
    ratio at or below that is answerable, everything above it is not — and None otherwise. Note the
    permissive `open` arm runs with per_side_stop_management off, which is exactly why it is the
    arm the sweep is scored over: none of its rows censor anything.
    """
    if row.get("status") != "stopped":
        return None
    net_credit = row.get("net_credit")
    if not net_credit:
        return None
    costs = [c for c in (row.get("put_max_cost"), row.get("call_max_cost")) if c is not None]
    return round(max(costs) / net_credit, 6) if costs else None


def capital_at_risk(row: dict) -> float | None:
    """An IC's defined max loss: (wing_width - net_credit) x 100 x quantity. Exact from the row --
    both inputs are recorded -- so the grid's result is reportable on max risk rather than only in
    dollars, which is what makes it comparable with any other arm's return_on_capital."""
    width, credit = row.get("wing_width"), row.get("net_credit")
    if width is None or credit is None:
        return None
    return round((width - credit) * _MULT * (row.get("quantity") or 1), 2)


def shadow_settle(row: dict, *, fee_one_side, fee_full_ic) -> dict:
    """The per-fill shadow ledger: what this fill would have been worth held to expiry with no stop
    at all, beside what it really did, plus the excursion the stop threshold is actually compared
    against.

    The counterfactual is not an estimate — `*_settle_value` is recorded for stopped sides too, so
    a stopped fill's held-to-expiry value is a stored number rather than a reconstruction.

    `mae_over_credit` is max_cost over net_credit, NOT the spot-based `*_mae_spot` columns. The
    proposal that asked for this called it "the empirical value stop_trigger_ratio is compared
    against", and that value is a COST ratio: the stop fires when a side's cost-to-close reaches
    ratio x net_credit. Spot excursion is a different quantity that no threshold here reads.

    `mfe_over_credit` is always None, and deliberately: favourable excursion is not recorded
    anywhere (only the adverse running maximum is), and the stream cache keeps no quote history to
    reconstruct it from. Rendering it as 0.0 would be the "misleadingly precise zero" this suite
    already has a rule about. It needs its own instrumentation change on the write path.
    """
    none_out = derive(row, "stop-none", fee_one_side=fee_one_side, fee_full_ic=fee_full_ic)
    net_credit = row.get("net_credit")
    puts, calls = row.get("put_max_cost"), row.get("call_max_cost")
    worst = max([c for c in (puts, calls) if c is not None], default=None)
    real_pnl, real_fee = row.get("pnl"), row.get("fees")

    shadow_net = None if none_out["pnl"] is None else round(none_out["pnl"] - none_out["fee"], 2)
    real_net = None if real_pnl is None else round(real_pnl - (real_fee or 0.0), 2)
    return {
        "ic_order_id": row.get("ic_order_id"),
        "trade_date": row.get("trade_date"),
        "risk_profile": row.get("risk_profile"),
        "symbol": row.get("symbol"),
        "stop_fired": row.get("status") == "stopped",
        "credit_at_entry": net_credit,
        "capital_at_risk": capital_at_risk(row),
        "realized_net": real_net,
        "shadow_settle_net": shadow_net,
        # Positive means the stop COST money: holding would have paid more than stopping did.
        "stop_cost": None if (shadow_net is None or real_net is None) else round(shadow_net - real_net, 2),
        "mae_over_credit": (
            None if (worst is None or not net_credit) else round(worst / net_credit, 4)
        ),
        "mfe_over_credit": None,  # not recorded; see the docstring
        "censored_above": censored_above(row),
    }


def score_grid(
    row: dict, *, fee_one_side, fee_full_ic, ratios: tuple = GRID_RATIOS
) -> dict[float, dict]:
    """Score one row at every ratio in `ratios` on the net basis — the whole stop curve for one
    fill, from one recorded path, at zero risk and zero extra position cost.

    A ratio this row cannot answer comes back `derivable: False` with `censored: True` rather than
    a number (see `censored_above`). That distinction is the point: "this threshold would not have
    fired" and "we stopped watching before it could" are opposite conclusions.
    """
    limit = censored_above(row)
    out: dict[float, dict] = {}
    for ratio in ratios:
        if limit is not None and ratio > limit:
            out[ratio] = {"derivable": False, "censored": True, "pnl": None, "fee": None,
                          "put_fired": None, "call_fired": None}
            continue
        scored = derive(row, ("net", ratio), fee_one_side=fee_one_side, fee_full_ic=fee_full_ic)
        out[ratio] = {**scored, "censored": False}
    return out


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
