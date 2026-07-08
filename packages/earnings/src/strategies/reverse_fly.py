"""Reverse iron fly earnings strategy: long straddle + short wings (defined-risk hybrid).

Entry conditions:
1. Historical realized move >> expected move (stock consistently surprises)
2. Medium-to-high realized move dispersion (unpredictable moves)
3. Sufficient capital for defined-risk structure (smaller than spreads, larger than straddles)

Exits:
1. Profit target at 50% of max profit (cap-sensitive, not credit-sensitive)
2. Time-based exit at 4 hours post-announcement (IV-crush window close)
3. Stop loss at max loss (defined-risk protection)

Unlike short_straddle (which is naked on both sides), reverse_fly:
- Buys ATM straddle (long call + long put)
- Sells OTM wings (short call/put above/below ATM)
- Defined max loss = wing width - net debit
- Defined max profit = wing width - net debit
- Best for stocks that gap 3-8% (consistent realized > expected)

Uses src/scanner.py's shared engine for everything not specific to this
strategy's structure or thresholds. Strategy-specific config lives under
config.json's "strategies.reverse_fly" key.

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
    return config.get("strategies", {}).get("reverse_fly", {})


def fetch_price_and_term_structure(symbol: str, earnings_date: date, earnings_timing: str, full_config: dict) -> dict:
    """Live price + term structure/expected move via scanner.py's shared
    helpers, including realized move dispersion check for reverse_fly's
    edge: stocks that consistently move more than expected.

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

        # Entry condition: Check realized move history vs expected move
        strategy_config = _strategy_config(full_config)
        winrate_result = scanner.compute_winrate(symbol, full_config, full_config.get("winrate_lookback_quarters", 8))

        realized_move_pct = None
        realized_move_dispersion = None
        if winrate_result.get("ok"):
            realized_moves = [q["realized_move"] / q["pre_close"] for q in winrate_result["quarters"]]
            if realized_moves:
                mean_realized = sum(realized_moves) / len(realized_moves)
                realized_move_pct = mean_realized
                variance = sum((m - mean_realized) ** 2 for m in realized_moves) / len(realized_moves)
                realized_move_dispersion = variance ** 0.5
            else:
                realized_move_pct = None
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
                "realized_move_pct": realized_move_pct,
                "realized_move_dispersion_pct": realized_move_dispersion,
                **liquidity,
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def apply_tiering(criteria: dict, config: dict) -> dict:
    """Pure function -- no I/O. Applies hard filters for reverse_fly:
    - Historical realized move must be > expected move (edge condition)
    - Sufficient dispersion to justify defined-risk structure
    - Expected move must match at least some historical moves
    - Capital must be available (medium tier, between spreads and straddles)

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

    # Entry condition 1: Realized move > expected move (key edge)
    if criteria.get("realized_move_pct") is not None and criteria.get("expected_move_pct") is not None:
        min_realized_mv_ratio = config.get("min_realized_move_ratio", 1.10)
        if criteria["realized_move_pct"] < criteria["expected_move_pct"] * min_realized_mv_ratio:
            hard_fail.append("realized_move_below_expected_threshold")

    # Entry condition 2: Dispersion check (not too extreme; some move consistency)
    if criteria.get("realized_move_dispersion_pct") is not None:
        max_dispersion = config.get("max_realized_move_dispersion_pct", 0.30)
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


def fetch_reverse_fly_order(symbol: str, earnings_date: date, earnings_timing: str, full_config: dict) -> dict:
    """Build a concrete, tradeable reverse fly order spec: long ATM straddle
    + short OTM wings (call above, put below).

    Structure:
    1. Long ATM call
    2. Long ATM put
    3. Short OTM call (wing width above ATM)
    4. Short OTM put (wing width below ATM)

    Defined risk: max loss = (wing strike price - ATM strike price) - net debit
    Max profit: same as max loss (capped at wing width)

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

        # Fetch option chain for wing selection
        front_chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_quotes", "--strike_count", "40", "--around_price", str(price),
        ])
        if not front_chain.get("ok"):
            return {"ok": False, "error": front_chain.get("error", "get_option_chain failed")}
        entries = front_chain["chain"][str(front_exp)]

        # ATM strike selection (long leg)
        long_call = scanner.atm_entry(entries, "call", price)
        long_put = scanner.atm_entry(entries, "put", price)
        if long_call is None or long_put is None:
            return {"ok": False, "error": "incomplete ATM strikes"}
        if long_call.get("mid") is None or long_put.get("mid") is None:
            return {"ok": False, "error": "no quote data for long ATM strikes"}

        atm_strike = float(long_call["strike_price"])
        long_straddle_cost = long_call["mid"] + long_put["mid"]

        # Wing width selection: use config-specified width
        strategy_config = _strategy_config(full_config)
        wing_width_pct = strategy_config.get("wing_width_pct", 0.10)
        wing_width_dollars = atm_strike * wing_width_pct

        call_strike = atm_strike + wing_width_dollars
        put_strike = atm_strike - wing_width_dollars

        # Find short call wing (nearest strike to call_strike)
        short_call = scanner.nearest_strike_entry(entries, "call", call_strike, 1.0)
        if short_call is None or short_call.get("mid") is None:
            return {"ok": False, "error": f"no short call wing available at strike ~{call_strike}"}

        # Find short put wing (nearest strike to put_strike)
        short_put = scanner.nearest_strike_entry(entries, "put", put_strike, -1.0)
        if short_put is None or short_put.get("mid") is None:
            return {"ok": False, "error": f"no short put wing available at strike ~{put_strike}"}

        # Calculate net debit: long straddle cost - short wings credit
        short_wings_credit = short_call["mid"] + short_put["mid"]
        net_debit = long_straddle_cost - short_wings_credit
        max_loss = (float(short_call["strike_price"]) - atm_strike) - net_debit

        order = {
            "order_type": "Limit",
            "time_in_force": "Day",
            "price": round(net_debit, 2),
            "price_effect": "Debit",
            "legs": [
                {"symbol": long_call["symbol"], "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1},
                {"symbol": long_put["symbol"], "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1},
                {"symbol": short_call["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1},
                {"symbol": short_put["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1},
            ],
        }
        return {
            "ok": True,
            "strategy": "reverse_fly",
            "order": order,
            "symbol": symbol,
            "expiration": str(front_exp),
            "underlying_price": price,
            "atm_strike": atm_strike,
            "call_wing_strike": float(short_call["strike_price"]),
            "put_wing_strike": float(short_put["strike_price"]),
            "net_debit": round(net_debit, 2),
            "max_loss": round(max_loss, 2),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def label_order_legs(order_result: dict) -> list[dict]:
    """Tag fetch_reverse_fly_order's 4 order legs with leg_role tags for
    save_trade's `legs` argument.
    Relies on fetch_reverse_fly_order's fixed leg order:
    (long_call, long_put, short_call, short_put).
    """
    legs = order_result["order"]["legs"]
    roles = ["long_call", "long_put", "short_call", "short_put"]
    return [
        {"leg_role": role, "symbol": leg["symbol"], "action": leg["action"], "quantity": leg["quantity"]}
        for role, leg in zip(roles, legs)
    ]


def evaluate_position(position: dict, open_legs: list[dict], quotes: dict, config: dict) -> dict:
    """Decide what (if anything) to do with an open reverse_fly position.
    Reverse flies are overnight earnings plays with defined risk: enter
    day-of or day-before, hold through announcement, close post-IV-crush
    when profit target is hit or stop loss is triggered.

    Two exit mechanisms, checked in order:
    1. IV-crush backstop: close after 4 hours (prevents directional drift)
    2. Profit target: close at 50% of max profit (defined-risk cap)
    3. Stop loss: close at max loss (hard defined-risk stop)
    4. Hold: wait for one of the above conditions

    Unlike spreads (which profit from time decay + IV crush), reverse flies
    profit from stock moves within the wing range (3-8% typical). Exit early
    to capture the move before the wings limit profit.

    `quotes` is `{symbol: {"bid": F, "ask": F}}` for each leg (4 legs total).

    Returns:
      {"action": "hold"}
      {"action": "close_all", "reason": "iv_crush_backstop"|"profit_target"|"stop_loss"}
    """
    # IV-CRUSH BACKSTOP: Close after 4 hours have elapsed since entry
    exit_after_announcement_minutes = config.get("exit_after_announcement_minutes", 240)
    if position.get("opened_at") is not None:
        elapsed_minutes = (time.time() - position["opened_at"]) / 60.0
        if elapsed_minutes >= exit_after_announcement_minutes:
            return {"action": "close_all", "reason": "iv_crush_backstop"}

    # PROFIT TARGET: Close at 50% of max profit (defined-risk cap metric)
    entry_debit = abs(position.get("entry_credit", 0))  # entry_credit stored negative
    max_loss = position.get("max_loss", 0)
    if max_loss > 0:
        max_profit = max_loss
        cost_to_close = 0.0
        for leg in open_legs:
            q = quotes.get(leg["symbol"])
            if q is None or q.get("ask") is None:
                cost_to_close = None
                break
            # Calculate cost: buy legs (long) use ask, sell legs (short) use bid
            if leg.get("leg_role") in ["long_call", "long_put"]:
                cost_to_close += q["ask"]
            else:  # short_call, short_put
                cost_to_close -= q["bid"]

        if cost_to_close is not None:
            profit = entry_debit - cost_to_close
            profit_target_pct = config.get("profit_target_pct", 0.50)
            if profit >= max_profit * profit_target_pct:
                return {"action": "close_all", "reason": "profit_target"}

    # STOP LOSS: Close if loss exceeds max loss (hard defined-risk limit)
    if max_loss > 0 and entry_debit > 0:
        cost_to_close = 0.0
        for leg in open_legs:
            q = quotes.get(leg["symbol"])
            if q is None or q.get("ask") is None:
                cost_to_close = None
                break
            if leg.get("leg_role") in ["long_call", "long_put"]:
                cost_to_close += q["ask"]
            else:  # short_call, short_put
                cost_to_close -= q["bid"]

        if cost_to_close is not None:
            loss = cost_to_close - entry_debit
            if loss >= max_loss:
                return {"action": "close_all", "reason": "stop_loss"}

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
    scanner.run_strategy_main(cmd_get_candidates, fetch_reverse_fly_order)


if __name__ == "__main__":
    main()
