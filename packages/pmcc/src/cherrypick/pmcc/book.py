"""Wires engine decisions to the paper ledger: entries, traded closes, and settlement.

Single arm (`control`) since the 2026-08-23 redesign — no more roll, no more keltner entry gate,
no more multi-arm fill pairing — plus its `advised:<experiment>` synthetic twins (one per advisor
experiment since 2026-09-17; a single `advised:control` before).
Since 2026-08-23 the arm runs two symbols (TQQQ, physical; XSP, cash) rather than one; the
settlement-style branch below is what keeps their bookkeeping correctly diverging.

Fee/P&L conventions (the ledger reader depends on these):
- `gross_pnl` is mid-priced and cost-free: the sum of per-leg P&L (`engine.leg_pnl`) x100 x qty,
  plus any delivered shares' realized move (already in dollars).
- `fees` is the TOTAL modeled cost — entry fee + entry slippage + every exit/roll fee + exit
  slippage + settlement fees — so net is always `gross_pnl - fees`, one subtraction.
- `entry_cost`/`exit_cost` hold the fee halves and `entry_slippage`/`exit_slippage` the slippage
  halves separately, so cost composition stays analyzable without unpicking a single number.
"""

from __future__ import annotations

import json

from cherrypick.core import advice as _core_advice
from cherrypick.core import spreadbook as _spreadbook

from cherrypick.pmcc import analytics, clock, db, engine


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
    keltner_measures: dict | None = None,
    experiment_id: str | dict | None = None,
) -> dict | None:
    """Open one arm's position from a plan. Idempotent per position_id: a arm that already holds
    the day's position is skipped, so a tick retry cannot double-enter.

    `experiment_id` is the session decision (the row's stamp resolved by its tag through
    `cherrypick.core.advice.stamp_for`, so each advised arm carries its own experiment) or, for a
    caller that already resolved it, the id itself."""
    pid = position_id(plan["symbol"], arm, entry_session)
    if conn.execute("SELECT 1 FROM pmcc_positions WHERE position_id = ?", (pid,)).fetchone():
        return None
    quantity = int((config.get("defaults") or {}).get("quantity", 1))
    leg_quotes = [{"bid": leg["bid"], "ask": leg["ask"]} for leg in plan["legs"]]
    cost = engine.entry_cost(plan["symbol"], leg_quotes, quantity, config)
    now = clock.now_iso()
    measures = keltner_measures or {}
    long_leg = next(leg for leg in plan["legs"] if leg["leg_role"] == "long_call")
    short_leg = next(leg for leg in plan["legs"] if leg["leg_role"] != "long_call")
    db.save_position(
        conn,
        {
            "position_id": pid,
            "symbol": plan["symbol"],
            "arm": arm,
            "entry_session": entry_session,
            "quantity": quantity,
            "long_expiration": plan["long_expiration"],
            "long_strike": plan["long_strike"],
            "short_expiration": plan["short_expiration"],
            "short_strike": plan["short_strike"],
            "entry_time": now,
            "entry_spot": plan["spot"],
            "long_entry_mid": plan["long_mid"],
            "short_entry_mid": plan["short_mid"],
            "net_debit": plan["net_debit"],
            "entry_cost": cost["fee"],
            "entry_slippage": cost["slippage"],
            "entry_short_dte": plan["short_dte"],
            "entry_long_dte": plan["long_dte"],
            "entry_total_premium": plan["total_premium"],
            "entry_short_intrinsic": plan["short_intrinsic"],
            "entry_short_tv": plan["short_tv"],
            "entry_net_tv": plan["net_tv"],
            "entry_long_extrinsic": plan["long_extrinsic"],
            "entry_profit_pct": plan["profit_pct"],
            "entry_weekly_yield_pct": plan["weekly_yield_pct"],
            "entry_downside_protection_pct": plan["downside_protection_pct"],
            "entry_breakeven": plan["breakeven"],
            "entry_buffer_to_breakeven_pct": plan["buffer_to_breakeven_pct"],
            "entry_long_delta": long_leg.get("delta"),
            "entry_short_delta": short_leg.get("delta"),
            "entry_long_iv": long_leg.get("iv"),
            "entry_short_iv": short_leg.get("iv"),
            "long_selected_by": plan["long_selected_by"],
            "keltner_mid": measures.get("keltner_mid"),
            "keltner_atr": measures.get("keltner_atr"),
            "keltner_days": measures.get("keltner_days"),
            "keltner_distance_atr": measures.get("keltner_distance_atr"),
            "keltner_bounce_atr": measures.get("keltner_bounce_atr"),
            "keltner_prev_close_gap": measures.get("keltner_prev_close_gap"),
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
            "era": analytics.CURRENT_ERA,
        },
    )
    shares = engine.leg_costs(
        plan["symbol"],
        [{**leg, "selling": leg["action"] == "Sell to Open"} for leg in plan["legs"]],
        quantity,
        config,
        cost,
        opening=True,
    )
    for leg, share in zip(plan["legs"], shares, strict=True):
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
                "opened_at": now,
                "opened_session": entry_session,
                "entry_spot": plan["spot"],
                "entry_cost": share["fee"],
                "entry_slippage": share["slippage"],
            },
        )
    return {"position_id": pid, "arm": arm, "symbol": plan["symbol"], "net_debit": plan["net_debit"]}


# The traded close, share disposal, exit-cost accumulation and finalization are the shared
# spread-book writer in `cherrypick.core.spreadbook`: calendars, pmcc and curve carried identical
# copies of all five. Bound to this module's ledger and kept under the old names for every caller.
_book = _spreadbook.SpreadBook(db._store)
dispose_assignment = _book.dispose_assignment
finalize_if_done = _book.finalize_if_done
_accumulate_exit_costs = _book.accumulate_exit_costs
_position_quantity = _book.position_quantity


def close_open_legs(
    conn, position: dict, mark_snapshot: dict, config: dict, *, reason: str, session_date: str
) -> dict:
    """The shared traded close (`cherrypick.core.spreadbook`), then each closed leg's own share of
    the close ticket, its closing spot and why it closed. The stamp lives here rather than in the
    shared writer, which calendars and curve run byte-identically; the position-level totals it
    writes are untouched."""
    legs = db.open_legs_for(conn, position["position_id"])
    result = _book.close_open_legs(
        conn, position, mark_snapshot, config, reason=reason, session_date=session_date
    )
    if result.get("ok") and legs:
        quotes = mark_snapshot.get("quotes") or {}
        priced = [
            {**quotes[leg["streamer_symbol"]], "selling": leg["action"] == "Buy to Open"} for leg in legs
        ]
        shares = engine.leg_costs(
            position["symbol"],
            priced,
            int(position.get("quantity") or 1),
            config,
            result["cost"],
            opening=False,
        )
        for leg, share in zip(legs, shares, strict=True):
            db.save_leg(
                conn,
                {
                    "position_id": leg["position_id"],
                    "leg_role": leg["leg_role"],
                    "close_spot": mark_snapshot.get("spot"),
                    "close_cost": share["fee"],
                    "close_slippage": share["slippage"],
                    "close_reason": reason,
                },
            )
    return result


def settle_expiring_legs(
    conn, day: str, spot: float, config: dict, *, symbol: str | None = None
) -> list[dict]:
    """Settle every open leg expiring `day` at the settlement print — scoped to one underlying when
    `symbol` is given, because the print is per-symbol. A settled short leaves the position
    `short_settled` (the long survives to the next session's combined disposal); a long leg still
    open at its own expiry settles the same way and finalizes once nothing is outstanding (the
    backstop for a loop that was down through its disposition window).

    Under the PHYSICAL settlement style an ITM leg also delivers shares. The option leg still arms
    at intrinsic — that is its value at expiry under either style — and the delivered shares become
    a `pmcc_assignments` row carrying the settlement spot as their basis: for this module's assigned
    short call, SHORT 100 shares per contract, covered the next session. The $5 event charge moves
    with it: it is levied at disposal (`engine.assignment_fee`, which folds in the equity
    pass-throughs) rather than here, so one assignment is never charged twice.
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
        assigned = physical and intrinsic > 0
        db.save_leg(
            conn,
            {
                "position_id": leg["position_id"],
                "leg_role": leg["leg_role"],
                "status": "settled",
                "close_kind": "assigned" if assigned else ("expired" if intrinsic <= 0 else "cash_settled"),
                "closed_at": now,
                "close_value": intrinsic,
                # Settled, not traded: no fee and no slippage on the leg itself. Its settlement or
                # assignment charge is the position's `settlement_fees`, a separate column.
                "close_spot": spot,
                "close_cost": 0.0,
                "close_slippage": 0.0,
                "close_reason": "settlement",
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
            "SELECT itm_settlements FROM pmcc_positions WHERE position_id = ?", (pid,)
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
            "SELECT COUNT(*) FROM pmcc_legs WHERE position_id = ? AND status = 'open'", (pid,)
        ).fetchone()[0]
        if still_open:
            db.save_position(conn, {"position_id": pid, "status": "short_settled"})
        results.append({"position_id": pid, "settled_legs": info["legs"], "itm": info["itm"], "fee": fee})
    return results
