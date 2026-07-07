"""Short strangle earnings strategy: sell a strangle at the expected-move
boundaries (short call at price + expected_move, short put at
price - expected_move) -- same short strikes as iron_condor.py, but with
NO protective wings. Undefined risk: this is a genuine naked strategy, not
a defined-risk approximation, gated by scanner.naked_strategies_allowed()
(project-wide `allow_naked_strategies` config flag, default false) --
**paper mode is always allowed regardless of that flag** (no real capital
or margin at risk in paper mode), live mode still requires the flag
deliberately enabled.

Uses src/scanner.py's shared engine for everything not specific to this
strategy's structure or thresholds. Strategy-specific config lives under
config.json's "strategies.short_strangle" key.

Wired into the live/paper trading loop's Step 4b for paper-mode entries;
live-mode entries are hard-blocked there regardless of ranking outcome --
position-level risk-cap for live entry-time re-verification can't use the
existing max_risk_per_trade_pct formula (wing width - credit) since
there's no defined max loss here -- the natural mechanism whenever that's
wired in is the account's actual live margin requirement (tt.py
execute_trade's dry-run, which CLAUDE.md already documents as performing a
real margin check), not a synthetic max-loss number.

Commands (see CLAUDE.md's Tool Reference):
  get_candidates --date MM/DD/YYYY
  get_order --symbol X --earnings_date YYYY-MM-DD --earnings_timing "..."
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scanner


def _strategy_config(config: dict) -> dict:
    return config.get("strategies", {}).get("short_strangle", {})


def fetch_price_and_term_structure(symbol: str, earnings_date: date, earnings_timing: str, full_config: dict) -> dict:
    """Live price + term structure/expected move via scanner.py's shared
    helpers -- identical criteria to iron_condor.py's scan-side fetch, plus
    `naked_strategies_allowed` threaded from the full project config (this
    function receives the full config, same as every other strategy's
    scan-side fetch when called via scanner.run_candidate_scan) since
    apply_tiering only ever sees this strategy's own sub-config, not the
    top-level allow_naked_strategies flag.

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

        return {
            "ok": True,
            "criteria": {
                "price": price,
                "term_structure": ts["term_structure"],
                "expected_move_dollars": ts["expected_move_dollars"],
                "expected_move_pct": ts["expected_move_pct"],
                "front_expiration_days": (front_exp - date.today()).days,
                "chain_complete": True,
                "naked_strategies_allowed": scanner.naked_strategies_allowed(full_config),
                **liquidity,
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def apply_tiering(criteria: dict, config: dict) -> dict:
    """Pure function -- no I/O. Same shape as iron_condor.py's apply_tiering
    minus any wing-width-related checks (none apply -- no wings), plus the
    naked_strategy_disabled hard filter. `config` is this strategy's own
    sub-config.
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

    if not criteria.get("naked_strategies_allowed"):
        hard_fail.append("naked_strategy_disabled")

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


def fetch_short_strangle_order(symbol: str, earnings_date: date, earnings_timing: str, full_config: dict) -> dict:
    """Build a concrete, tradeable short strangle order spec: sell a
    strangle at the expected-move boundaries, no wings. Same short-strike
    selection as fetch_iron_condor_order, but the order is just the two
    short legs.

    Re-checks scanner.naked_strategies_allowed() (paper mode is always
    allowed; live mode needs allow_naked_strategies=true) as a hard stop
    before building any legs -- defense in depth against a stale scan or a
    manually-invoked get_order bypassing the screening-time gate.

    Deliberately re-fetches live data at call time -- same discipline as
    every other strategy's order-builder.
    """
    if not scanner.naked_strategies_allowed(full_config):
        return {"ok": False, "error": "naked strategies disabled (allow_naked_strategies=false)"}
    try:
        qe = scanner.fetch_quote_and_expirations(symbol)
        if not qe.get("ok"):
            return qe
        price, expirations = qe["price"], qe["expirations"]

        front_exp, err = scanner.select_front_expiration(expirations, earnings_date, earnings_timing)
        if front_exp is None:
            return {"ok": False, "error": err}

        atm_probe = scanner.fetch_front_back_atm_entries(symbol, front_exp, front_exp, price)
        if not atm_probe.get("ok"):
            return atm_probe
        expected_move = 0.85 * (atm_probe["front_call"]["mid"] + atm_probe["front_put"]["mid"])

        front_chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_quotes", "--strike_count", "40", "--around_price", str(price),
        ])
        if not front_chain.get("ok"):
            return {"ok": False, "error": front_chain.get("error", "get_option_chain failed")}
        entries = front_chain["chain"][str(front_exp)]

        short_call = scanner.nearest_strike_entry(entries, "call", price + expected_move, -1.0)
        short_put = scanner.nearest_strike_entry(entries, "put", price - expected_move, -1.0)
        if short_call is None or short_put is None:
            return {"ok": False, "error": "no strikes found near expected-move boundaries"}
        if short_call.get("mid") is None or short_put.get("mid") is None:
            return {"ok": False, "error": "no quote data for short strikes"}

        short_call_strike = float(short_call["strike_price"])
        short_put_strike = float(short_put["strike_price"])
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
            "strategy": "short_strangle",
            "order": order,
            "symbol": symbol,
            "expiration": str(front_exp),
            "underlying_price": price,
            "short_call_strike": short_call_strike,
            "short_put_strike": short_put_strike,
            "expected_move": expected_move,
            "credit": round(net_credit, 2),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def cmd_get_candidates(args) -> dict:
    """Full tiered scan for a date. Thin wrapper around
    scanner.run_candidate_scan -- shared with every other strategy's
    cmd_get_candidates.
    """
    config = scanner._load_config()
    strategy_config = _strategy_config(config)
    return scanner.run_candidate_scan(args.date, config, fetch_price_and_term_structure, apply_tiering, strategy_config)


def main() -> None:
    scanner.run_strategy_main(cmd_get_candidates, fetch_short_strangle_order)


if __name__ == "__main__":
    main()
