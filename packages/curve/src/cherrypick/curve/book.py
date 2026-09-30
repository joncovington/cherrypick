"""Wires engine decisions to the paper ledger: entries, traded closes, and settlement.

`control` and `noflip` enter from the SAME plan on the same tick — identical strikes, mids, modeled
costs — so exit-rule-vs-no-exit-rule is exactly paired by construction. `hook` enters on its own
rare tick because its variable IS the entry condition. Read surfaces must not treat the three as a
fully paired grid (and must present `flip_divergence_count` beside the control/noflip pair — see
analytics.py).

Fee/P&L conventions: `gross_pnl` is mid-priced and cost-free (per-leg P&L x100 x qty, plus any
delivered shares' realized move); `fees` is the TOTAL modeled cost (entry+exit+settlement); net is
always `gross_pnl - fees`.
"""

from __future__ import annotations

import json

from cherrypick.core import advice as _core_advice
from cherrypick.core import spreadbook as _spreadbook

from cherrypick.curve import clock, db, engine


def position_id(symbol: str, arm: str, entry_session: str) -> str:
    return f"{symbol}:{arm}:{entry_session}"


def enter_position(
    conn,
    plan: dict,
    config: dict,
    arm: str,
    *,
    entry_session: str,
    advice_params: dict | None,
    regime: dict | None,
    experiment_id: str | dict | None = None,
) -> dict | None:
    """Open one arm's position from a plan. Idempotent per position_id."""
    pid = position_id(plan["symbol"], arm, entry_session)
    if conn.execute("SELECT 1 FROM curve_positions WHERE position_id = ?", (pid,)).fetchone():
        return None
    quantity = int((config.get("defaults") or {}).get("quantity", 1))
    leg_quotes = [{"bid": leg["bid"], "ask": leg["ask"]} for leg in plan["legs"]]
    cost = engine.entry_cost(plan["symbol"], leg_quotes, quantity, config)
    now = clock.now_iso()
    short_leg = next(leg for leg in plan["legs"] if leg["leg_role"] == "short_call")
    regime = regime or {}
    db.save_position(
        conn,
        {
            "position_id": pid,
            "symbol": plan["symbol"],
            "arm": arm,
            "entry_session": entry_session,
            "quantity": quantity,
            "expiration": plan["expiration"],
            "short_strike": plan["short_strike"],
            "long_strike": plan["long_strike"],
            "entry_time": now,
            "entry_spot": plan["spot"],
            "entry_short_mid": plan["short_mid"],
            "entry_long_mid": plan["long_mid"],
            "entry_credit": plan["credit"],
            "entry_width": plan["width"],
            "entry_max_loss": plan["max_loss"],
            "entry_credit_pct_of_width": plan["credit_pct_of_width"],
            "entry_short_delta": short_leg.get("delta"),
            "short_selected_by": plan.get("short_selected_by"),
            "entry_dte": plan["dte"],
            "entry_ratio": regime.get("ratio"),
            "entry_regime": regime.get("regime"),
            "entry_hook": 1 if regime.get("hook") else 0,
            "entry_cost": cost["fee"],
            "entry_slippage": cost["slippage"],
            "advice_params": (
                json.dumps(advice_params) if (advice_params and arm.startswith("advised:")) else None
            ),
            "experiment_id": _core_advice.stamp_for(arm, experiment_id),
            "advice_base": (
                engine.base_book(arm, decision=experiment_id if isinstance(experiment_id, dict) else None)
                if _core_advice.is_advised(arm)
                else None
            ),
            "status": "open",
            "fees": cost["total"],
        },
    )
    for leg in plan["legs"]:
        db.save_leg(
            conn,
            {
                "position_id": pid,
                "leg_role": leg["leg_role"],
                "occ_symbol": leg["occ_symbol"],
                "streamer_symbol": leg["streamer_symbol"],
                "expiration": leg["expiration"],
                "strike": leg["strike"],
                "option_type": leg["option_type"],
                "action": leg["action"],
                "quantity": quantity,
                "entry_bid": leg["bid"],
                "entry_ask": leg["ask"],
                "entry_mid": leg["mid"],
                "entry_iv": leg.get("iv"),
                "entry_delta": leg.get("delta"),
                "status": "open",
            },
        )
    return {"position_id": pid, "arm": arm, "symbol": plan["symbol"], "entry_credit": plan["credit"]}


# The traded close, share disposal, exit-cost accumulation and finalization are the shared
# spread-book writer in `cherrypick.core.spreadbook`: calendars, pmcc and curve carried identical
# copies of all five. Bound to this module's ledger and kept under the old names for every caller.
_book = _spreadbook.SpreadBook(db._store)
close_open_legs = _book.close_open_legs
dispose_assignment = _book.dispose_assignment
finalize_if_done = _book.finalize_if_done
_accumulate_exit_costs = _book.accumulate_exit_costs
_position_quantity = _book.position_quantity


def settle_expiring_legs(
    conn, day: str, spot: float, config: dict, *, symbol: str | None = None
) -> list[dict]:
    """Settle every open leg expiring `day` at the settlement print. VXX is always physical
    settlement — an ITM leg (short or long) also delivers/receives shares, booked at the settlement
    spot (the calendars/pmcc decomposition)."""
    now = clock.now_iso()
    results = []
    by_position: dict[str, dict] = {}
    for leg in db.expiring_open_legs(conn, day):
        if symbol is not None and leg["position_symbol"] != symbol:
            continue
        intrinsic = engine.settle_intrinsic(leg["strike"], spot)
        assigned = intrinsic > 0
        db.save_leg(
            conn,
            {
                "position_id": leg["position_id"],
                "leg_role": leg["leg_role"],
                "status": "settled",
                "close_kind": "assigned" if assigned else "expired",
                "closed_at": now,
                "close_value": intrinsic,
            },
        )
        entry = by_position.setdefault(leg["position_id"], {"itm": 0, "legs": 0, "assigned": 0})
        entry["legs"] += 1
        if intrinsic > 0:
            entry["itm"] += 1
            quantity = _position_quantity(conn, leg["position_id"])
            assignment = engine.assignment_from(leg, spot, quantity)
            if assignment is not None:
                db.save_assignment(
                    conn,
                    {
                        "position_id": leg["position_id"],
                        "leg_role": leg["leg_role"],
                        "symbol": leg["position_symbol"],
                        "assigned_session": day,
                        "assigned_at": now,
                        "status": "open",
                        **assignment,
                    },
                )
                entry["assigned"] += 1

    for pid, info in by_position.items():
        # Only a cash-settled ITM leg pays here. A physical one pays its $5 event at disposal, inside
        # `engine.assignment_fee` -- and VXX is always physical, so charging `itm` here charged every
        # assigned leg twice. calendars and pmcc carried this guard; curve did not.
        fee = engine.settlement_fee(info["itm"] - info["assigned"])
        _accumulate_exit_costs(conn, pid, fee=fee, slippage=0.0, settlement=True)
        prev_itm = conn.execute(
            "SELECT itm_settlements FROM curve_positions WHERE position_id = ?", (pid,)
        ).fetchone()
        db.save_position(
            conn,
            {
                "position_id": pid,
                "settlement_spot": spot,
                "itm_settlements": (prev_itm["itm_settlements"] or 0 if prev_itm else 0) + info["itm"],
            },
        )
        finalize_if_done(conn, pid, reason="expired", session_date=day)
        still_open = conn.execute(
            "SELECT COUNT(*) FROM curve_legs WHERE position_id = ? AND status = 'open'", (pid,)
        ).fetchone()[0]
        if still_open:
            db.save_position(conn, {"position_id": pid, "status": "short_settled"})
        results.append({"position_id": pid, "settled_legs": info["legs"], "itm": info["itm"], "fee": fee})
    return results
