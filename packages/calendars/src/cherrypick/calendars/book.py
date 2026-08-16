"""Wires engine decisions to the paper ledger: entries, traded closes, and cash settlement.

One plan, N books. Every book's positions for a week are written from the SAME plan — identical
strikes, identical entry mids, identical modeled costs — which is what makes the whole experiment
exactly paired by construction: any later divergence between books is exit policy and nothing else.

Fee/P&L conventions (the ledger reader depends on these):
- `gross_pnl` is mid-priced and cost-free: the sum of per-leg P&L (`engine.leg_pnl`) x100 x qty.
- `fees` is the TOTAL modeled cost — entry fee + entry slippage + exit fees + exit slippage +
  settlement fees — so net is always `gross_pnl - fees`, one subtraction, no double counting.
- `entry_cost`/`exit_cost` hold the fee halves and `entry_slippage`/`exit_slippage` the slippage
  halves separately, so cost composition stays analyzable without unpicking a single number.
"""

from __future__ import annotations

import json

from cherrypick.calendars import clock, db, engine


def position_id(week_of: str, book: str, side: str) -> str:
    return f"{week_of}:{book}:{side}"


def enter_week(
    conn, plan: dict, config: dict, books: list[str], *, week: dict, advice_params: dict | None
) -> list[dict]:
    """Open the week's put and call calendars in every session book. Idempotent per (book, side):
    a book that already holds the position is skipped, so a tick retry cannot double-enter."""
    opened = []
    quantity = int((config.get("defaults") or {}).get("quantity", 1))
    symbol = plan["symbol"]
    now = clock.now_iso()
    for book in books:
        params_json = json.dumps(advice_params) if (advice_params and book.startswith("advised:")) else None
        for side, side_plan in plan["sides"].items():
            pid = position_id(week["week_of"], book, side)
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
                    "book": book,
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
            opened.append({"position_id": pid, "book": book, "side": side, "debit": side_plan["debit"]})
    return opened


def close_open_legs(
    conn, position: dict, mark_snapshot: dict, config: dict, *, reason: str, session_date: str
) -> dict:
    """Close every still-open leg of one position at the mark's mids — the traded close (Friday's
    scheduled exit, an advised trigger, or the Monday long disposition; whichever, the arithmetic
    is one path so no two exits can price differently).

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


def settle_expiring_legs(
    conn, day: str, spot: float, config: dict, *, symbol: str | None = None
) -> list[dict]:
    """Settle every open leg expiring `day` at the settlement print — scoped to one underlying when
    `symbol` is given, because the print is per-symbol. Front legs leave the position
    `short_settled` (the long survives the weekend); a back leg still open at its own expiry
    settles the same way and finalizes the position (`longs_expired` — the disposition was missed
    or refused all day, and intrinsic at the bell is the honest outcome).

    Under a PHYSICAL settlement style an ITM leg also delivers shares. The option leg still books
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
        _accumulate_exit_costs(conn, pid, fee=fee, slippage=0.0)
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


def _accumulate_exit_costs(conn, pid: str, *, fee: float, slippage: float) -> None:
    row = conn.execute(
        "SELECT exit_cost, exit_slippage, fees FROM dc_positions WHERE position_id = ?", (pid,)
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
    row = conn.execute("SELECT quantity FROM dc_positions WHERE position_id = ?", (pid,)).fetchone()
    return int((row["quantity"] if row else 1) or 1)


def finalize_if_done(conn, pid: str, *, reason: str, session_date: str) -> bool:
    """Once nothing is open, the position closes: gross P&L from the recorded per-leg closes plus
    any delivered shares' realized move, the exit reason from whichever path finished it.
    `closed_session` is what the ledger reader reports as the session — the day the LAST leg closed,
    which is the day the result became a fact.

    An undisposed share position holds the close open exactly as an open option leg does. Closing a
    week while its shares are still outstanding would book a result the account has not yet realized
    — and on a physically-settled underlying that gap spans a weekend."""
    legs = db.legs_for(conn, pid)
    if any(leg["status"] == "open" for leg in legs):
        return False
    if db.open_assignment_count(conn, pid):
        return False
    position = conn.execute("SELECT * FROM dc_positions WHERE position_id = ?", (pid,)).fetchone()
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
    """Close one delivered share position at `price` and finalize its week if that was the last
    thing outstanding. The fee lands here rather than at settlement because only now is the disposal
    price known, and the equity pass-throughs are computed on it."""
    pnl = engine.share_pnl(
        assignment["direction"], assignment["shares"], assignment["basis"], price
    )
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
    finalize_if_done(
        conn, assignment["position_id"], reason="shares_disposed", session_date=session_date
    )
    return {"position_id": assignment["position_id"], "share_pnl": pnl, "fee": fee, "price": price}
