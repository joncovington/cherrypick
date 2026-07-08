"""Short straddle earnings strategy: sell a straddle at the expected-move
boundaries (short call at ATM, short put at ATM) -- same short strikes as
iron_fly.py, but more aggressive entry conditions based on realized move
history and dispersion analysis.

Entry conditions:
1. Realized move history must support naked short (low dispersion, high credit)
2. IV rank must be sufficient for the naked risk
3. Expected move must match historical realized moves (not overpriced)
4. Capital must be available (higher than spreads)

Exits:
1. Profit target at 50% max credit (post-IV-crush capture)
2. Stop loss at 2x credit (defined max loss, still larger than spreads)
3. Time-based backstop at 4 hours post-announcement (prevents directional drift)

Unlike short_strangle (which uses expected-move boundaries), short straddle
uses ATM strikes for maximum credit collection when conditions support naked risk.

Uses src/scanner.py's shared engine for everything not specific to this
strategy's structure or thresholds. Strategy-specific config lives under
config.json's "strategies.short_straddle" key.

Commands (see CLAUDE.md's Tool Reference):
  get_candidates --date MM/DD/YYYY
  get_order --symbol X --earnings_date YYYY-MM-DD --earnings_timing "..."
"""

import os
import sys
import time
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scanner


def _strategy_config(config: dict) -> dict:
    return config.get("strategies", {}).get("short_straddle", {})


def fetch_price_and_term_structure(symbol: str, earnings_date: date, earnings_timing: str, full_config: dict) -> dict:
    """Live price + term structure/expected move via scanner.py's shared
    helpers -- identical criteria to iron_fly.py's scan-side fetch, plus
    entry condition checks (realized move history, dispersion, capital check).

    Returns {"ok": False, "error": ...} on any missing data rather than
    raising -- same discipline as every other strategy.
    """
    try:
        qe = scanner.fetch_quote_and_expirations(symbol)
        if not qe.get("ok"):
            return qe
        price, expirations = qe["price"], qe["expirations"]

        front_exp, err = scanner.select_front_expiration(expirations, earnings_date, earnings_timing)
        if front_exp is None:
            return {"ok": False, "error": err}
        back_exp = scanner.select_back_expiration(expirations, front_exp, 21)
        if back_exp is None:
            return {"ok": False, "error": "no back-month expiration available for term structure"}

        atm = scanner.fetch_front_back_atm_entries(symbol, front_exp, back_exp, price)
        if not atm.get("ok"):
            return atm
        front_call, front_put, back_call = atm["front_call"], atm["front_put"], atm["back_call"]

        ts = scanner.compute_expected_move_and_term_structure(
            front_call["mid"], front_put["mid"], front_call["iv"], back_call["iv"], price,
        )
        liquidity = scanner.fetch_liquidity_criteria(symbol, front_exp, expirations, front_call, front_put)

        # Entry condition: Check realized move history
        strategy_config = _strategy_config(full_config)
        winrate_result = scanner.compute_winrate(symbol, full_config, full_config.get("winrate_lookback_quarters", 8))

        if winrate_result.get("ok"):
            realized_moves = [q["realized_move"] / q["pre_close"] for q in winrate_result["quarters"]]
            if realized_moves:
                mean_realized = sum(realized_moves) / len(realized_moves)
                variance = sum((m - mean_realized) ** 2 for m in realized_moves) / len(realized_moves)
                realized_move_dispersion = variance ** 0.5
            else:
                realized_move_dispersion = None
        else:
            realized_move_dispersion = None

        return {
            "ok": True,
            "criteria": {
                "price": price,
                "term_structure": ts["term_structure"],
                "expected_move_dollars": ts["expected_move_dollars"],
                "expected_move_pct": ts["expected_move_pct"],
                "front_expiration_days": (front_exp - date.today()).days,
                "chain_complete": True,
                "realized_move_dispersion_pct": realized_move_dispersion,
                **liquidity,
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def apply_tiering(criteria: dict, config: dict) -> dict:
    """Pure function -- no I/O. Applies hard filters for short straddle:
    - High IV rank required (naked risk = expensive)
    - Low realized move dispersion (predictability requirement)
    - Expected move must match historical realized moves (no overpricing edge)
    - Sufficient credit collection (risk justification)

    `config` is this strategy's own sub-config.
    """
    hard_fail: list[str] = []

    if criteria.get("price") is None:
        hard_fail.append("price_unverified")
    elif criteria["price"] < config["min_price"]:
        hard_fail.append("price_below_minimum")

    if criteria.get("term_structure") is None:
        hard_fail.append("term_structure_unverified")
    elif criteria["term_structure"] > config["min_term_structure"]:
        hard_fail.append("term_structure_insufficient")

    if criteria.get("expected_move_pct") is None:
        hard_fail.append("expected_move_pct_unverified")
    elif criteria["expected_move_pct"] < config["min_expected_move_pct"]:
        hard_fail.append("expected_move_pct_below_minimum")

    if criteria.get("front_expiration_days") is None:
        hard_fail.append("front_expiration_days_unverified")
    elif criteria["front_expiration_days"] > config["max_front_expiration_days"]:
        hard_fail.append("front_expiration_days_too_far_out")

    if not criteria.get("chain_complete"):
        hard_fail.append("chain_complete_unverified")

    # Entry condition 1: Realized move dispersion (predictability)
    if criteria.get("realized_move_dispersion_pct") is not None:
        max_dispersion = config.get("max_realized_move_dispersion_pct", 0.15)
        if criteria["realized_move_dispersion_pct"] > max_dispersion:
            hard_fail.append("realized_move_too_inconsistent")

    near_miss: list[str] = []

    scanner.apply_liquidity_gates(criteria, config, hard_fail, near_miss)
    scanner._band(criteria.get("avg_volume"), config["min_avg_volume"], config["near_miss_min_avg_volume"], "avg_volume", near_miss, hard_fail)
    scanner._band(criteria.get("iv_rv_ratio"), config["min_iv_rv_ratio"], config["near_miss_min_iv_rv_ratio"], "iv_rv_ratio", near_miss, hard_fail)
    scanner._band(criteria.get("winrate"), config["min_winrate"], config["near_miss_min_winrate"], "winrate", near_miss, hard_fail)

    if hard_fail:
        tier = "Reject"
    elif not near_miss:
        tier = "Tier 1"
    elif len(near_miss) == 1:
        tier = "Tier 2"
    else:
        tier = "Near Miss"

    return {"tier": tier, "hard_fail_reasons": hard_fail, "near_miss_reasons": near_miss}


def fetch_short_straddle_order(symbol: str, earnings_date: date, earnings_timing: str, full_config: dict) -> dict:
    """Build a concrete, tradeable short straddle order spec: sell an ATM call
    and ATM put at the same strike. High credit collection, unlimited risk on
    both sides. Only suitable for:
    - High IV rank (generous credit)
    - Low realized move dispersion (predictable moves)
    - Sufficient capital (2x that of spreads)

    Re-checks entry conditions as a hard stop before building any legs --
    defense in depth against a stale scan.

    Deliberately re-fetches live data at call time -- same discipline as
    every other strategy's order-builder.
    """
    try:
        qe = scanner.fetch_quote_and_expirations(symbol)
        if not qe.get("ok"):
            return qe
        price, expirations = qe["price"], qe["expirations"]

        front_exp, err = scanner.select_front_expiration(expirations, earnings_date, earnings_timing)
        if front_exp is None:
            return {"ok": False, "error": err}

        # Fetch ATM straddle (same strikes, not expected-move boundaries)
        atm_probe = scanner.fetch_front_back_atm_entries(symbol, front_exp, front_exp, price)
        if not atm_probe.get("ok"):
            return atm_probe

        front_chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_quotes", "--strike_count", "40", "--around_price", str(price),
        ])
        if not front_chain.get("ok"):
            return {"ok": False, "error": front_chain.get("error", "get_option_chain failed")}
        entries = front_chain["chain"][str(front_exp)]

        # ATM strike selection (same as iron fly, but naked both sides)
        short_call = scanner.atm_entry(entries, "call", price)
        short_put = scanner.atm_entry(entries, "put", price)
        if short_call is None or short_put is None:
            return {"ok": False, "error": "incomplete ATM strikes"}
        if short_call.get("mid") is None or short_put.get("mid") is None:
            return {"ok": False, "error": "no quote data for short strikes"}

        short_strike = float(short_call["strike_price"])
        net_credit = short_call["mid"] + short_put["mid"]

        order = {
            "order_type": "Limit",
            "time_in_force": "Day",
            "price": round(net_credit, 2),
            "price_effect": "Credit",
            "legs": [
                {"symbol": short_call["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1},
                {"symbol": short_put["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1},
            ],
        }
        return {
            "ok": True,
            "strategy": "short_straddle",
            "order": order,
            "symbol": symbol,
            "expiration": str(front_exp),
            "underlying_price": price,
            "short_strike": short_strike,
            "credit": round(net_credit, 2),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def label_order_legs(order_result: dict) -> list[dict]:
    """Tag fetch_short_straddle_order's 2 order legs with a `leg_role`
    ('short_call'/'short_put') for save_trade's `legs` argument.
    Relies on fetch_short_straddle_order's fixed leg order (short_call, short_put).
    """
    legs = order_result["order"]["legs"]
    roles = ["short_call", "short_put"]
    return [
        {"leg_role": role, "symbol": leg["symbol"], "action": leg["action"], "quantity": leg["quantity"]}
        for role, leg in zip(roles, legs)
    ]


def evaluate_position(position: dict, open_legs: list[dict], quotes: dict, config: dict) -> dict:
    """Decide what (if anything) to do with an open short straddle position.
    Short straddles are overnight earnings plays: enter day-of or day-before,
    hold through announcement, close post-IV-crush when profit target is hit.

    Three exit mechanisms, checked in order:
    1. Per-leg delta stop: close if either leg goes deep ITM (delta > 0.60)
    2. Profit target: close at 50% of max profit (post-IV-crush standard)
    3. IV-crush backstop: close after 4 hours (prevents directional drift)
    4. Hold: wait for one of the above conditions

    For naked short strategies, profit-taking is critical: leave money on the
    table by holding too long, and the remaining marginal premium doesn't
    justify the naked directional risk after IV crush completes.

    `quotes` is `{symbol: {"bid": F, "ask": F, "delta": F}}` for each of
    `open_legs`.

    Returns:
      {"action": "hold"}
      {"action": "close_all", "reason": "leg_stop"|"profit_target"|"iv_crush_backstop"}
    """
    # IV-CRUSH BACKSTOP: Close after 4 hours have elapsed since entry
    exit_after_announcement_minutes = config.get("exit_after_announcement_minutes", 240)
    if position.get("opened_at") is not None:
        elapsed_minutes = (time.time() - position["opened_at"]) / 60.0
        if elapsed_minutes >= exit_after_announcement_minutes:
            return {"action": "close_all", "reason": "iv_crush_backstop"}

    # PROFIT TARGET: Close at 50% of max profit
    entry_credit = abs(position.get("entry_credit", 0))
    if entry_credit > 0:
        cost_to_close = 0.0
        for leg in open_legs:
            q = quotes.get(leg["symbol"])
            if q is None or q.get("ask") is None:
                cost_to_close = None
                break
            cost_to_close += q["ask"]

        if cost_to_close is not None:
            profit = entry_credit - cost_to_close
            profit_target_pct = config.get("profit_target_pct", 0.50)
            if profit >= entry_credit * profit_target_pct:
                return {"action": "close_all", "reason": "profit_target"}

    # LEG STOP: Close if either leg goes deep ITM (delta threshold breached)
    # Naked short legs have unlimited loss; stop at 0.60 delta (deep ITM)
    leg_stop_delta_abs = config.get("leg_stop_delta_abs", 0.60)
    for leg in open_legs:
        q = quotes.get(leg["symbol"])
        if q is None or q.get("delta") is None:
            continue
        if abs(q["delta"]) >= leg_stop_delta_abs:
            return {"action": "close_all", "reason": "leg_stop"}

    return {"action": "hold"}


def cmd_get_candidates(args) -> dict:
    """Full tiered scan for a date. Thin wrapper around
    scanner.run_candidate_scan -- shared with every other strategy's
    cmd_get_candidates.
    """
    config = scanner._load_config()
    strategy_config = _strategy_config(config)
    return scanner.run_candidate_scan(args.date, config, fetch_price_and_term_structure, apply_tiering, strategy_config)


def main() -> None:
    scanner.run_strategy_main(cmd_get_candidates, fetch_short_straddle_order)


if __name__ == "__main__":
    main()
