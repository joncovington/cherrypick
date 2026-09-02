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

from cherrypick.curve import clock, db, engine


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
    regime: dict | None,
) -> dict | None:
    """Open one book's position from a plan. Idempotent per position_id."""
    pid = position_id(plan["symbol"], book, entry_session)
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
            "book": book,
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
    return {"position_id": pid, "book": book, "symbol": plan["symbol"], "entry_credit": plan["credit"]}


def close_open_legs(
    conn, position: dict, mark_snapshot: dict, config: dict, *, reason: str, session_date: str
) -> dict:
    """Close every still-open leg of one position at the mark's mids."""
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
        fee = engine.settlement_fee(info["itm"])
        _accumulate_exit_costs(conn, pid, fee=fee, slippage=0.0)
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


def _accumulate_exit_costs(conn, pid: str, *, fee: float, slippage: float) -> None:
    row = conn.execute(
        "SELECT exit_cost, exit_slippage, fees FROM curve_positions WHERE position_id = ?", (pid,)
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
    row = conn.execute("SELECT quantity FROM curve_positions WHERE position_id = ?", (pid,)).fetchone()
    return int((row["quantity"] if row else 1) or 1)


def finalize_if_done(conn, pid: str, *, reason: str, session_date: str) -> bool:
    """Once nothing is open, the position closes: gross P&L from the recorded per-leg closes plus
    any delivered shares' realized move."""
    legs = db.legs_for(conn, pid)
    if any(leg["status"] == "open" for leg in legs):
        return False
    if db.open_assignment_count(conn, pid):
        return False
    position = conn.execute("SELECT * FROM curve_positions WHERE position_id = ?", (pid,)).fetchone()
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
    """Close one delivered share position at `price` and finalize its position if that was the
    last thing outstanding."""
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
