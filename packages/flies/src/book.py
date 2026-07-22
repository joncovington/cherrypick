"""One session's book: wires the pure engine to the paper database.

A book is one (trade_date, arm, symbol) triple. Each arm keeps its own book so the arms never share
positions, capital, or luck — the reason MEIC moved to per-(profile x symbol) portfolios was exactly
this, that a cumulative book lets one lucky structure paper over a strategy that doesn't work.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import db as dbmod  # noqa: E402
import engine  # noqa: E402
import fly  # noqa: E402


def book_id_for(trade_date: str, arm: str, symbol: str) -> str:
    return f"{trade_date}:{arm}:{symbol}"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _minutes_since(started: str | None, ended: str) -> float | None:
    """Whole-minute gap between two ISO timestamps, or None if either is unparseable."""
    if not started:
        return None
    try:
        delta = datetime.fromisoformat(ended) - datetime.fromisoformat(started)
    except (ValueError, TypeError):
        return None
    return round(delta.total_seconds() / 60.0, 1)


def _record_best_debit(conn, position: dict, debit: float, when: str) -> None:
    """Keep the running minimum completing debit seen for an open spread.

    Recorded on every evaluation, including the ones that refuse. Afterwards this is what separates
    "the market never got there" from "we asked for a couple of cents too much" — a distinction that
    is invisible without it and that points at opposite remedies.
    """
    best = position.get("best_completing_debit")
    if best is not None and debit >= best:
        return
    position["best_completing_debit"] = debit
    dbmod.save_position(conn, {
        "position_id": position["position_id"],
        "best_completing_debit": round(debit, 4),
        "best_debit_at": when,
    })


def _to_position(row: dict) -> dict:
    """Database row -> the plain dict the pure math in fly.py consumes."""
    return {
        "kind": row["kind"],
        "side": row["side"],
        "center": row["center"],
        "wing_width": row["wing_width"],
        "net": row["net"],
        "quantity": row["quantity"] or 1,
        "fees": row["fees"] or 0.0,
        "entry_mode": row["entry_mode"],
        "status": row["status"],
        "position_id": row["position_id"],
        # Carried because the session stats are recomputed from these dicts after settlement, and
        # pin rate is one of the three numbers the whole thesis turns on.
        "pinned": bool(row["pinned"]),
        # Carried so the running-minimum comparison and the latency clock survive a loop restart —
        # both are cumulative over a session, not per-iteration.
        "best_completing_debit": row["best_completing_debit"],
        "entry_time": row["entry_time"],
    }


def process_snapshot(snapshot: dict, config: dict, conn, arm: str) -> dict:
    """Run one iteration of one arm against one snapshot. Returns a summary of what it did.

    Order matters: completions are evaluated BEFORE new entries. A credit spread that can be squared
    into a risk-free fly right now is worth more than a new credit spread, and evaluating entries
    first could consume the position slot that the completion needs.
    """
    params = engine.merged_params(config, arm)
    symbol = snapshot["symbol"]
    trade_date = snapshot["date"]
    book_id = book_id_for(trade_date, arm, symbol)
    actions: list[dict] = []
    now = _now()

    rows = dbmod.book_positions(conn, book_id)
    positions = [_to_position(r) for r in rows]

    def journal(mode, reason, *, accepted=False, center=None, position_id=None, detail=None):
        dbmod.record_decision(conn, trade_date=trade_date, arm=arm, symbol=symbol, mode=mode,
                              reason=reason, accepted=accepted, center=center,
                              position_id=position_id, detail=detail, when=now)

    # What this arm WANTED this iteration, recorded before any gate can veto it. Written even when
    # nothing trades, because arm divergence is measured over intentions, not fills.
    wanted_center, wanted_reason = engine.select_center(snapshot, params)
    dbmod.record_iteration(conn, iteration_ts=now, trade_date=trade_date, symbol=symbol, arm=arm,
                           center=wanted_center, center_reason=wanted_reason,
                           underlying_price=snapshot.get("underlying_price"))

    # --- 1. complete any open credit spread that has become cheap enough to square off
    for pos in [p for p in positions if p["kind"] == "short_vertical" and p["status"] == "open"]:
        done, reason, plan = engine.evaluate_completion(snapshot, pos, params)
        if plan is not None:
            _record_best_debit(conn, pos, plan["debit"], now)
        if not done:
            journal("completion", reason, center=pos["center"], position_id=pos["position_id"],
                    detail=None if plan is None else f"debit {plan['debit']:.2f} vs gate "
                                                     f"{plan['gate_debit']:.2f}")
            actions.append({"action": "completion_skipped", "position_id": pos["position_id"],
                            "reason": reason})
            continue
        pos["kind"] = "fly"
        pos["net"] = plan["net"]
        pos["fees"] = pos["fees"] + plan["completion_fee"]
        latency = _minutes_since(pos.get("entry_time"), now)
        dbmod.save_position(conn, {
            "position_id": pos["position_id"],
            "kind": "fly",
            "net": plan["net"],
            "debit": plan["debit"],
            "fees": pos["fees"],
            "floor_dollars": plan["floor"],
            "risk_free": int(fly.is_risk_free(pos)),
            "completed_at": now,
            "completion_latency_min": latency,
            "spot_at_completion": snapshot.get("underlying_price"),
        })
        journal("completion", "completed", accepted=True, center=pos["center"],
                position_id=pos["position_id"],
                detail=f"debit {plan['debit']:.2f}, floor ${plan['floor']:.2f} after fees")
        actions.append({"action": "completed", "position_id": pos["position_id"],
                        "debit": plan["debit"], "net": plan["net"], "floor": plan["floor"],
                        "latency_min": latency})

    open_positions = [p for p in positions if p["status"] == "open"]

    # --- 2. legged entry: sell a new credit spread
    if "legged" in params.get("entry_modes", ["legged"]):
        enter, reason, plan = engine.evaluate_credit_spread_entry(snapshot, params, open_positions)
        if enter:
            position_id = f"FLY-{arm}-{symbol}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
            pos = {
                "kind": "short_vertical", "side": plan["side"], "center": plan["center"],
                "wing_width": plan["wing_width"], "net": plan["credit"],
                "quantity": plan["quantity"], "fees": plan["open_fee"],
                "entry_mode": "legged", "status": "open", "position_id": position_id,
            }
            positions.append(pos)
            open_positions.append(pos)
            dbmod.save_position(conn, {
                "position_id": position_id, "book_id": book_id, "trade_date": trade_date,
                "arm": arm, "entry_mode": "legged", "symbol": symbol,
                "kind": "short_vertical", "side": plan["side"], "center": plan["center"],
                "wing_width": plan["wing_width"], "quantity": plan["quantity"],
                "net": plan["credit"], "credit": plan["credit"], "fees": plan["open_fee"],
                "entry_time": now, "entry_window": plan["entry_window"],
                "center_reason": plan["center_reason"],
                "completing_direction": plan["completing_direction"],
                "underlying_at_entry": snapshot.get("underlying_price"),
                "risk_free": 0, "status": "open",
            })
            journal("legged", "entered", accepted=True, center=plan["center"],
                    position_id=position_id,
                    detail=f"{plan['side']} spread for {plan['credit']:.2f} credit, needs spot "
                           f"{plan['completing_direction']} to complete")
            actions.append({"action": "credit_spread_opened", "position_id": position_id,
                            "side": plan["side"], "center": plan["center"], "credit": plan["credit"]})
        else:
            journal("legged", reason, center=wanted_center)
            actions.append({"action": "entry_skipped", "mode": "legged", "reason": reason})

    # --- 3. outright entry: buy a cheap fly, funded only by premium already taken in
    if "outright" in params.get("entry_modes", []):
        cash = fly.book_cash(positions)
        # Whether an OPEN credit spread's premium counts as funding is a real choice, not a detail.
        # The reference book did fund flies from a still-open iron condor, so this defaults on to stay
        # faithful to it — but that premium is not yet earned, which is precisely why the book-level
        # floor below reports `unbounded_below` instead of claiming the book is risk-free.
        realized = cash["net_cash"] if params.get("fund_from_open_credit", True) else max(
            sum(fly.position_pnl(p, p["center"]) for p in positions if p["status"] != "open"), 0.0)
        enter, reason, plan = engine.evaluate_outright_entry(snapshot, params, open_positions, realized)
        if enter:
            position_id = f"FLY-{arm}-{symbol}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}-O"
            pos = {
                "kind": "fly", "side": plan["side"], "center": plan["center"],
                "wing_width": plan["wing_width"], "net": -plan["debit"],
                "quantity": plan["quantity"], "fees": plan["open_fee"],
                "entry_mode": "outright", "status": "open", "position_id": position_id,
            }
            positions.append(pos)
            dbmod.save_position(conn, {
                "position_id": position_id, "book_id": book_id, "trade_date": trade_date,
                "arm": arm, "entry_mode": "outright", "symbol": symbol,
                "kind": "fly", "side": plan["side"], "center": plan["center"],
                "wing_width": plan["wing_width"], "quantity": plan["quantity"],
                "net": -plan["debit"], "debit": plan["debit"], "fees": plan["open_fee"],
                "entry_time": now, "entry_window": plan["entry_window"],
                "center_reason": plan["center_reason"],
                "underlying_at_entry": snapshot.get("underlying_price"),
                "floor_dollars": fly.position_floor(pos),
                "risk_free": int(fly.is_risk_free(pos)), "status": "open",
            })
            journal("outright", "entered", accepted=True, center=plan["center"],
                    position_id=position_id,
                    detail=f"fly bought for {plan['debit']:.2f} debit, funded from ${realized:.2f}")
            actions.append({"action": "fly_bought", "position_id": position_id,
                            "center": plan["center"], "debit": plan["debit"]})
        else:
            journal("outright", reason, center=wanted_center)
            actions.append({"action": "entry_skipped", "mode": "outright", "reason": reason})

    summary = _save_book(conn, book_id, trade_date, arm, symbol, positions, params)
    return {"book_id": book_id, "actions": actions, **summary}


def _save_book(conn, book_id, trade_date, arm, symbol, positions, params, settlement_price=None) -> dict:
    cash = fly.book_cash(positions)
    floor = fly.book_floor(positions, step=params.get("book_scan_step", 1.0))
    stats = engine.session_stats(positions)
    band = floor["band"] or (None, None)
    row = {
        "book_id": book_id, "trade_date": trade_date, "arm": arm, "symbol": symbol,
        **cash,
        "worst": floor["worst"], "worst_at": floor["worst_at"],
        "floor_holds": int(floor["floor_holds"]), "band_low": band[0], "band_high": band[1],
        "unbounded_below": int(floor["unbounded_below"]),
        "completion_rate": stats["completion_rate"], "risk_free_rate": stats["risk_free_rate"],
        "pin_rate": stats["pin_rate"],
        "status": "settled" if settlement_price is not None else "open",
    }
    if settlement_price is not None:
        row["settlement_price"] = settlement_price
        row["pnl"] = round(fly.book_pnl(positions, settlement_price), 2)
    dbmod.save_book(conn, row)
    return {"cash": cash, "floor": floor, "stats": stats}


def settle_book(conn, trade_date: str, arm: str, symbol: str, settlement_price: float,
                config: dict) -> dict:
    """Cash-settle every open position in a book at the settlement print and close the book out."""
    params = engine.merged_params(config, arm)
    book_id = book_id_for(trade_date, arm, symbol)
    rows = dbmod.book_positions(conn, book_id)
    positions = [_to_position(r) for r in rows]

    settled = engine.settle([p for p in positions if p["status"] == "open"], settlement_price)
    for p in settled:
        gross = (p["net"] + p["expiry_payoff"]) * fly.CONTRACT_MULTIPLIER * p["quantity"]
        dbmod.save_position(conn, {
            "position_id": p["position_id"], "settlement_price": settlement_price,
            "expiry_payoff": p["expiry_payoff"], "gross_pnl": round(gross, 2), "pnl": p["pnl"],
            "pinned": int(p["pinned"]), "status": "settled", "exit_time": _now(),
        })

    final = [_to_position(r) for r in dbmod.book_positions(conn, book_id)]
    for p in final:
        p["status"] = "settled"
    summary = _save_book(conn, book_id, trade_date, arm, symbol, final, params, settlement_price)
    return {"book_id": book_id, "settled": len(settled),
            "pnl": round(fly.book_pnl(final, settlement_price), 2), **summary}
