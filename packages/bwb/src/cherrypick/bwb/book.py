"""Wires engine decisions to the paper ledger: entries, the add-on fire, and cash settlement.

Fee/P&L conventions: `gross_pnl` is mid-priced and cost-free (per-leg P&L x100 x qty); `fees` is
the TOTAL modeled cost (entry + addon entry + settlement); net is always `gross_pnl - fees`.
"""

from __future__ import annotations

import hashlib
import json

from cherrypick.bwb import clock, db, engine


def position_id(symbol: str, book: str, entry_session: str) -> str:
    return f"{symbol}:{book}:{entry_session}"


def structure_signature(expiration: str, body: float, near: float, far: float) -> str:
    """A hash of (expiration, strikes) — the cohort key's second component. The four base books
    always share one signature (same plan, same tick); an advised overlay with different widths
    or body offset gets its own signature and its own trigger-tick rows rather than silently
    borrowing the base cohort's."""
    raw = f"{expiration}|{body:g}|{near:g}|{far:g}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def enter_position(
    conn, plan: dict, config: dict, book: str, *, entry_session: str, advice_params: dict | None
) -> dict | None:
    """Open one book's base BWB from a plan. Idempotent per position_id."""
    pid = position_id(plan["symbol"], book, entry_session)
    if conn.execute("SELECT 1 FROM bwb_positions WHERE position_id = ?", (pid,)).fetchone():
        return None
    quantity = int((config.get("defaults") or {}).get("quantity", 1))
    leg_quotes = [{"bid": leg["bid"], "ask": leg["ask"]} for leg in plan["legs"]]
    cost = engine.entry_cost(plan["symbol"], leg_quotes, quantity, config)
    now = clock.now_iso()
    sig = structure_signature(plan["expiration"], plan["body_strike"], plan["near_strike"], plan["far_strike"])
    db.save_position(
        conn,
        {
            "position_id": pid,
            "symbol": plan["symbol"],
            "book": book,
            "entry_session": entry_session,
            "structure_signature": sig,
            "quantity": quantity,
            "expiration": plan["expiration"],
            "body_strike": plan["body_strike"],
            "near_strike": plan["near_strike"],
            "far_strike": plan["far_strike"],
            "entry_time": now,
            "entry_spot": plan["spot"],
            "entry_atm_strike": plan.get("atm_strike"),
            "entry_expected_move": plan.get("expected_move"),
            "entry_body_mid": plan["body_mid"],
            "entry_near_mid": plan["near_mid"],
            "entry_far_mid": plan["far_mid"],
            "entry_credit": plan["credit"],
            "entry_narrow_width": plan["narrow_width"],
            "entry_wide_width": plan["wide_width"],
            "entry_max_loss": plan["max_loss"],
            "entry_dte": plan["dte"],
            "entry_cost": cost["fee"],
            "entry_slippage": cost["slippage"],
            "advice_params": (
                json.dumps(advice_params) if (advice_params and book.startswith("advised:")) else None
            ),
            "peak_abs_delta": None,
            "below_flip_seen": 0,
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


def update_latches(conn, position: dict, *, peak_abs_delta, below_flip_seen: bool) -> None:
    db.save_position(
        conn,
        {
            "position_id": position["position_id"],
            "peak_abs_delta": peak_abs_delta,
            "below_flip_seen": 1 if below_flip_seen else 0,
        },
    )


def arm(conn, position: dict, *, reason: str) -> None:
    db.save_position(
        conn, {"position_id": position["position_id"], "armed_at": clock.now_iso(), "arm_reason": reason}
    )


def fire_addon(conn, position: dict, addon_plan: dict, config: dict) -> dict:
    """Open the add-on vertical against an already-armed position. One add-on maximum: the caller
    (management.evaluate) already refuses a re-fire on an already-fired position."""
    quantity = int(position.get("quantity") or 1)
    leg_quotes = [{"bid": leg["bid"], "ask": leg["ask"]} for leg in addon_plan["legs"]]
    cost = engine.addon_entry_cost(position["symbol"], leg_quotes, quantity, config)
    now = clock.now_iso()
    for leg in addon_plan["legs"]:
        db.save_leg(
            conn,
            {
                "position_id": position["position_id"],
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
    db.save_position(
        conn,
        {
            "position_id": position["position_id"],
            "addon_fired_at": now,
            "addon_short_strike": addon_plan["short_strike"],
            "addon_long_strike": addon_plan["long_strike"],
            "addon_credit": addon_plan["credit"],
            "addon_cost": cost["fee"],
            "addon_slippage": cost["slippage"],
            "fees": round((position.get("fees") or 0.0) + cost["total"], 2),
        },
    )
    return {"position_id": position["position_id"], "credit": addon_plan["credit"], "cost": cost}


def settle_expiring_legs(conn, day: str, spot: float, config: dict, *, symbol: str | None = None) -> list[dict]:
    """Cash-settle every open leg expiring `day` at the settlement print. SPX is European and cash
    settled: intrinsic only, no assignment machinery, no shares."""
    now = clock.now_iso()
    results = []
    by_position: dict[str, dict] = {}
    for leg in db.expiring_open_legs(conn, day):
        if symbol is not None and leg["position_symbol"] != symbol:
            continue
        intrinsic = engine.settle_intrinsic(leg["strike"], spot, leg.get("option_type") or "put")
        itm = intrinsic > 0
        db.save_leg(
            conn,
            {
                "position_id": leg["position_id"],
                "leg_role": leg["leg_role"],
                "status": "settled",
                "close_kind": "itm" if itm else "expired",
                "closed_at": now,
                "close_value": intrinsic,
            },
        )
        entry = by_position.setdefault(leg["position_id"], {"itm": 0, "legs": 0})
        entry["legs"] += 1
        if itm:
            entry["itm"] += 1

    for pid, info in by_position.items():
        fee = engine.settlement_fee(info["itm"])
        _accumulate_fees(conn, pid, fee)
        prev_itm = conn.execute(
            "SELECT itm_settlements FROM bwb_positions WHERE position_id = ?", (pid,)
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
        results.append({"position_id": pid, "settled_legs": info["legs"], "itm": info["itm"], "fee": fee})
    return results


def _accumulate_fees(conn, pid: str, fee: float) -> None:
    row = conn.execute("SELECT fees FROM bwb_positions WHERE position_id = ?", (pid,)).fetchone()
    db.save_position(conn, {"position_id": pid, "fees": round((row["fees"] or 0.0) + fee, 2)})


def finalize_if_done(conn, pid: str, *, reason: str, session_date: str) -> bool:
    """Once nothing is open, the position closes: gross P&L from every recorded leg close."""
    legs = db.legs_for(conn, pid)
    if any(leg["status"] == "open" for leg in legs):
        return False
    position = conn.execute("SELECT * FROM bwb_positions WHERE position_id = ?", (pid,)).fetchone()
    if position is None or position["status"] == "closed":
        return False
    quantity = int(position["quantity"] or 1)
    per_share = 0.0
    for leg in legs:
        pnl = engine.leg_pnl(leg)
        if pnl is None:
            return False  # an unpriced close — never finalize on a guess
        per_share += pnl
    db.save_position(
        conn,
        {
            "position_id": pid,
            "status": "closed",
            "exit_reason": position["exit_reason"] or reason,
            "closed_at": clock.now_iso(),
            "closed_session": session_date,
            "gross_pnl": round(per_share * 100 * quantity, 2),
        },
    )
    return True
