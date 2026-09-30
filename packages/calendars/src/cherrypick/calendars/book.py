"""Wires engine decisions to the paper ledger: entries, traded closes, and cash settlement.

One plan, N arms. Every arm's positions for a week are written from the SAME plan — identical
strikes, identical entry mids, identical modeled costs — which is what makes the whole experiment
exactly paired by construction: any later divergence between arms is exit policy and nothing else.

Fee/P&L conventions (the ledger reader depends on these):
- `gross_pnl` is mid-priced and cost-free: the sum of per-leg P&L (`engine.leg_pnl`) x100 x qty.
- `fees` is the TOTAL modeled cost — entry fee + entry slippage + exit fees + exit slippage +
  settlement fees — so net is always `gross_pnl - fees`, one subtraction, no double counting.
- `entry_cost`/`exit_cost` hold the fee halves and `entry_slippage`/`exit_slippage` the slippage
  halves separately, so cost composition stays analyzable without unpicking a single number.
"""

from __future__ import annotations

import json

from cherrypick.core import advice as _core_advice
from cherrypick.core import spreadbook as _spreadbook

from cherrypick.calendars import clock, db, engine


def position_id(week_of: str, arm: str, side: str) -> str:
    return f"{week_of}:{arm}:{side}"


def enter_week(
    conn,
    plan: dict,
    config: dict,
    arms: list[str],
    *,
    week: dict,
    advice_params: dict | None,
    experiment_id: str | dict | None = None,
    advised: dict[str, dict] | None = None,
) -> list[dict]:
    """Open the week's put and call calendars in every session arm. Idempotent per (arm, side):
    a arm that already holds the position is skipped, so a tick retry cannot double-enter.

    `advised` is `{tag: params}` -- each advised arm's own overlay, frozen on its rows (one arm per
    experiment since 2026-09-17); `advice_params` is the older single overlay, applied to every
    advised arm in `arms` when `advised` is not given. `experiment_id` is the session decision
    (each arm's stamp resolved by its tag through `cherrypick.core.advice.stamp_for`) or, for a
    caller that already resolved it, the id itself."""
    opened = []
    quantity = int((config.get("defaults") or {}).get("quantity", 1))
    symbol = plan["symbol"]
    now = clock.now_iso()
    for arm in arms:
        overlay = advised.get(arm) if advised is not None else advice_params
        params_json = json.dumps(overlay) if (overlay and _core_advice.is_advised(arm)) else None
        for side, side_plan in plan["sides"].items():
            pid = position_id(week["week_of"], arm, side)
            if conn.execute("SELECT 1 FROM dc_positions WHERE position_id = ?", (pid,)).fetchone():
                continue
            leg_quotes = [{"bid": leg["bid"], "ask": leg["ask"]} for leg in side_plan["legs"]]
            cost = engine.entry_cost(symbol, leg_quotes, quantity, config)
            db.save_position(
                conn,
                {
                    "position_id": pid,
                    "week_of": week["week_of"],
                    "entry_session": week["entry_session"],
                    "arm": arm,
                    "side": side,
                    "symbol": symbol,
                    "structure": week["structure"],
                    "front_expiration": week["front_expiration"],
                    "back_expiration": week["back_expiration"],
                    "strike": side_plan["strike"],
                    "quantity": quantity,
                    "entry_time": now,
                    "entry_debit": side_plan["debit"],
                    "entry_cost": cost["fee"],
                    "entry_slippage": cost["slippage"],
                    "entry_spot": plan["spot"],
                    "entry_em": plan["em"],
                    "entry_em_pct": plan["em_pct"],
                    "entry_front_atm_call_mid": plan["front_atm_call_mid"],
                    "entry_front_atm_put_mid": plan["front_atm_put_mid"],
                    "entry_front_iv": plan["front_iv"],
                    "entry_back_iv": plan["back_iv"],
                    "entry_term_structure": plan["term_structure"],
                    "entry_context": json.dumps({"target": side_plan["target"]}),
                    "advice_params": params_json,
                    "experiment_id": _core_advice.stamp_for(arm, experiment_id),
                    "advice_base": (
                        engine.base_book(
                            arm, decision=experiment_id if isinstance(experiment_id, dict) else None
                        )
                        if _core_advice.is_advised(arm)
                        else None
                    ),
                    "status": "open",
                    "fees": cost["total"],
                },
            )
            for leg in side_plan["legs"]:
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
            opened.append({"position_id": pid, "arm": arm, "side": side, "debit": side_plan["debit"]})
    return opened


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
    """Settle every open leg expiring `day` at the settlement print — scoped to one underlying when
    `symbol` is given, because the print is per-symbol. Front legs leave the position
    `short_settled` (the long survives the weekend); a back leg still open at its own expiry
    settles the same way and finalizes the position (`longs_expired` — the disposition was missed
    or refused all day, and intrinsic at the bell is the honest outcome).

    Under a PHYSICAL settlement style an ITM leg also delivers shares. The option leg still arms
    at intrinsic — that is its value at expiry under either style — and the delivered shares become
    a `dc_assignments` row carrying the settlement spot as their basis, so the share leg contributes
    exactly the disposal-vs-settlement move and nothing that intrinsic already counted. The $5
    event charge moves with it: it is levied at disposal (`engine.assignment_fee`, which folds in
    the equity pass-throughs) rather than here, so one assignment is never charged twice.
    """
    now = clock.now_iso()
    results = []
    by_position: dict[str, dict] = {}
    for leg in db.expiring_open_legs(conn, day):
        if symbol is not None and leg["position_symbol"] != symbol:
            continue
        style = engine.settlement_style(config, leg["position_symbol"]) or "cash"
        intrinsic = engine.settle_intrinsic(leg["strike"], leg["option_type"], spot)
        physical = style == "physical"
        db.save_leg(
            conn,
            {
                "position_id": leg["position_id"],
                "leg_role": leg["leg_role"],
                "status": "settled",
                "close_kind": "assigned" if (physical and intrinsic > 0) else "cash_settled",
                "closed_at": now,
                "close_value": intrinsic,
            },
        )
        entry = by_position.setdefault(leg["position_id"], {"itm": 0, "legs": 0, "assigned": 0})
        entry["legs"] += 1
        if intrinsic > 0:
            entry["itm"] += 1
            if physical:
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
        # Only the CASH-settled ITM legs pay here; a physical one pays at disposal.
        fee = engine.settlement_fee(info["itm"] - info["assigned"])
        _accumulate_exit_costs(conn, pid, fee=fee, slippage=0.0, settlement=True)
        prev_itm = conn.execute(
            "SELECT itm_settlements FROM dc_positions WHERE position_id = ?", (pid,)
        ).fetchone()
        db.save_position(
            conn,
            {
                "position_id": pid,
                "settlement_spot": spot,
                "itm_settlements": (prev_itm["itm_settlements"] or 0 if prev_itm else 0) + info["itm"],
            },
        )
        finalize_if_done(conn, pid, reason="longs_expired", session_date=day)
        still_open = conn.execute(
            "SELECT COUNT(*) FROM dc_legs WHERE position_id = ? AND status = 'open'", (pid,)
        ).fetchone()[0]
        if still_open:
            db.save_position(conn, {"position_id": pid, "status": "short_settled"})
        results.append({"position_id": pid, "settled_legs": info["legs"], "itm": info["itm"], "fee": fee})
    return results
