"""Wires engine decisions to the paper ledger: entries, traded closes, rolls, and settlement.

One plan can open several books' positions on the same tick (`control` and `roll` enter identically
by construction — that shared fill is the roll-vs-hold experiment's exact pairing), while `keltner`
enters on its own tick when its gate passes; its variable is entry timing, so it deliberately does
NOT share fills. Read surfaces must not treat the three as a fully paired grid.

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

from cherrypick.pmcc import clock, db, engine


def position_id(symbol: str, book: str, entry_session: str) -> str:
    return f"{symbol}:{book}:{entry_session}"


def enter_position(
    conn,
    plan: dict,
    config: dict,
    book: str,
    *,
    entry_session: str,
    advice_params: dict | None,
    keltner_measures: dict | None = None,
) -> dict | None:
    """Open one book's position from a plan. Idempotent per position_id: a book that already holds
    the day's position is skipped, so a tick retry cannot double-enter."""
    pid = position_id(plan["symbol"], book, entry_session)
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
            "book": book,
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
                json.dumps(advice_params) if (advice_params and book.startswith("advised:")) else None
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
    return {"position_id": pid, "book": book, "symbol": plan["symbol"], "net_debit": plan["net_debit"]}


def close_open_legs(
    conn, position: dict, mark_snapshot: dict, config: dict, *, reason: str, session_date: str
) -> dict:
    """Close every still-open leg of one position at the mark's mids — the traded close (the
    tv-exhausted both-legs exit, a roll-exhausted close, or the orphan-long disposition; whichever,
    the arithmetic is one path so no two exits can price differently).

    The caller has already run the execution gate; this refuses only on an unpriceable leg, which
    should not happen after a gated `ok` snapshot but is checked anyway — closing one leg of a pair
    on a guess is worse than holding both a tick longer.
    """
    legs = db.open_legs_for(conn, position["position_id"])
    quotes = mark_snapshot.get("quotes") or {}
    for leg in legs:
        if quotes.get(leg["streamer_symbol"]) is None:
            return {"ok": False, "reason": "missing_leg_quotes", "position_id": position["position_id"]}

    now = clock.now_iso()
    quantity = int(position.get("quantity") or 1)
    leg_quotes = []
    sell_legs = 0
    for leg in legs:
        quote = quotes[leg["streamer_symbol"]]
        leg_quotes.append({"bid": quote["bid"], "ask": quote["ask"]})
        # Closing inverts the opening action: an opened-long leg is SOLD to close.
        if leg["action"] == "Buy to Open":
            sell_legs += 1
        db.save_leg(
            conn,
            {
                "position_id": leg["position_id"],
                "leg_role": leg["leg_role"],
                "status": "closed",
                "close_kind": "traded",
                "closed_at": now,
                "close_bid": quote["bid"],
                "close_ask": quote["ask"],
                "close_value": quote["mid"],
            },
        )
    cost = engine.close_cost(position["symbol"], leg_quotes, quantity, config, sell_legs=sell_legs)
    _accumulate_exit_costs(conn, position["position_id"], fee=cost["fee"], slippage=cost["slippage"])
    finalize_if_done(conn, position["position_id"], reason=reason, session_date=session_date)
    return {"ok": True, "position_id": position["position_id"], "legs_closed": len(legs), "cost": cost}


def roll_short_leg(conn, position: dict, roll: dict, config: dict, *, session_date: str) -> dict:
    """Execute one roll: buy the current short back at its mark, open the next `short_call_<n>` at
    the roll plan's mid — one 2-leg transaction, one fee stack. The position row's
    `short_strike`/`short_expiration` move with the live short, and `roll_count` increments; the
    retired leg keeps its own entry/close record (`close_kind='rolled'`), so the whole chain of
    shorts stays auditable leg by leg."""
    short_legs = [
        leg for leg in db.open_legs_for(conn, position["position_id"]) if leg["leg_role"] != "long_call"
    ]
    if not short_legs:
        return {"ok": False, "reason": "no_open_short"}
    old = short_legs[0]
    now = clock.now_iso()
    quantity = int(position.get("quantity") or 1)
    buyback = roll["buyback"]
    new_leg = roll["new_leg"]
    db.save_leg(
        conn,
        {
            "position_id": position["position_id"],
            "leg_role": old["leg_role"],
            "status": "closed",
            "close_kind": "rolled",
            "closed_at": now,
            "close_bid": buyback["bid"],
            "close_ask": buyback["ask"],
            "close_value": buyback["mid"],
        },
    )
    role = db.next_short_role(conn, position["position_id"])
    db.save_leg(
        conn,
        {
            "position_id": position["position_id"],
            "leg_role": role,
            "occ_symbol": new_leg["occ_symbol"],
            "streamer_symbol": new_leg["streamer_symbol"],
            "expiration": new_leg["expiration"],
            "strike": new_leg["strike"],
            "option_type": "call",
            "action": "Sell to Open",
            "quantity": quantity,
            "entry_bid": new_leg["bid"],
            "entry_ask": new_leg["ask"],
            "entry_mid": new_leg["mid"],
            "entry_iv": new_leg.get("iv"),
            "entry_delta": new_leg.get("delta"),
            "status": "open",
        },
    )
    leg_quotes = [
        {"bid": buyback["bid"], "ask": buyback["ask"]},
        {"bid": new_leg["bid"], "ask": new_leg["ask"]},
    ]
    cost = engine.close_cost(position["symbol"], leg_quotes, quantity, config, sell_legs=1)
    _accumulate_exit_costs(conn, position["position_id"], fee=cost["fee"], slippage=cost["slippage"])
    db.save_position(
        conn,
        {
            "position_id": position["position_id"],
            "short_strike": new_leg["strike"],
            "short_expiration": new_leg["expiration"],
            "roll_count": int(position.get("roll_count") or 0) + 1,
        },
    )
    return {
        "ok": True,
        "position_id": position["position_id"],
        "old_strike": old["strike"],
        "new_strike": new_leg["strike"],
        "old_expiration": old["expiration"],
        "new_expiration": new_leg["expiration"],
        "net_roll_credit": roll["net_roll_credit"],
        "new_role": role,
        "cost": cost,
    }


def settle_expiring_legs(
    conn, day: str, spot: float, config: dict, *, symbol: str | None = None
) -> list[dict]:
    """Settle every open leg expiring `day` at the settlement print — scoped to one underlying when
    `symbol` is given, because the print is per-symbol. A settled short leaves the position
    `short_settled` (the long survives to the next session's combined disposal); a long leg still
    open at its own expiry settles the same way and finalizes once nothing is outstanding (the
    backstop for a loop that was down through its disposition window).

    Under the PHYSICAL settlement style an ITM leg also delivers shares. The option leg still books
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
        _accumulate_exit_costs(conn, pid, fee=fee, slippage=0.0)
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


def _accumulate_exit_costs(conn, pid: str, *, fee: float, slippage: float) -> None:
    row = conn.execute(
        "SELECT exit_cost, exit_slippage, fees FROM pmcc_positions WHERE position_id = ?", (pid,)
    ).fetchone()
    db.save_position(
        conn,
        {
            "position_id": pid,
            "exit_cost": round((row["exit_cost"] or 0.0) + fee, 2),
            "exit_slippage": round((row["exit_slippage"] or 0.0) + slippage, 2),
            "fees": round((row["fees"] or 0.0) + fee + slippage, 2),
        },
    )


def _position_quantity(conn, pid: str) -> int:
    row = conn.execute("SELECT quantity FROM pmcc_positions WHERE position_id = ?", (pid,)).fetchone()
    return int((row["quantity"] if row else 1) or 1)


def finalize_if_done(conn, pid: str, *, reason: str, session_date: str) -> bool:
    """Once nothing is open, the position closes: gross P&L from the recorded per-leg closes plus
    any delivered shares' realized move, the exit reason from whichever path finished it.
    `closed_session` is what the ledger reader reports as the session — the day the LAST leg (or
    share position) closed, which is the day the result became a fact.

    An undisposed share position holds the close open exactly as an open option leg does. Closing a
    position while its shares are still outstanding would book a result the account has not yet
    realized — and here that gap can span a weekend."""
    legs = db.legs_for(conn, pid)
    if any(leg["status"] == "open" for leg in legs):
        return False
    if db.open_assignment_count(conn, pid):
        return False
    position = conn.execute("SELECT * FROM pmcc_positions WHERE position_id = ?", (pid,)).fetchone()
    if position is None or position["status"] == "closed":
        return False
    quantity = int(position["quantity"] or 1)
    per_share = 0.0
    exit_value = 0.0
    for leg in legs:
        pnl = engine.leg_pnl(leg)
        if pnl is None:
            return False  # an unpriced close — never finalize on a guess
        per_share += pnl
        exit_value += leg["close_value"] * (1 if leg["action"] == "Buy to Open" else -1)
    # Already in dollars (shares x move), so it is added AFTER the per-share legs are scaled.
    shares_pnl = sum(a["share_pnl"] or 0.0 for a in db.assignments_for(conn, pid))
    db.save_position(
        conn,
        {
            "position_id": pid,
            "status": "closed",
            "exit_reason": position["exit_reason"] or reason,
            "closed_at": clock.now_iso(),
            "closed_session": session_date,
            "exit_value": round(exit_value, 4),
            "gross_pnl": round(per_share * 100 * quantity + shares_pnl, 2),
        },
    )
    return True


def dispose_assignment(conn, assignment: dict, price: float, *, session_date: str) -> dict:
    """Close one delivered share position at `price` (a buy-to-cover for this module's short-call
    assignments) and finalize its position if that was the last thing outstanding. The fee lands
    here rather than at settlement because only now is the disposal price known, and the equity
    pass-throughs are computed on it."""
    pnl = engine.share_pnl(assignment["direction"], assignment["shares"], assignment["basis"], price)
    fee = engine.assignment_fee(assignment, price)
    db.save_assignment(
        conn,
        {
            "position_id": assignment["position_id"],
            "leg_role": assignment["leg_role"],
            "status": "disposed",
            "disposed_session": session_date,
            "disposed_at": clock.now_iso(),
            "disposal_price": round(float(price), 4),
            "share_pnl": pnl,
            "fees": fee,
        },
    )
    _accumulate_exit_costs(conn, assignment["position_id"], fee=fee, slippage=0.0)
    finalize_if_done(conn, assignment["position_id"], reason="shares_disposed", session_date=session_date)
    return {"position_id": assignment["position_id"], "share_pnl": pnl, "fee": fee, "price": price}
