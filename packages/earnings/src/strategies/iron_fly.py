"""Iron fly earnings strategy: sell an ATM straddle around an earnings
reaction, buy wings sized off the credit received. Uses src/scanner.py's
shared engine (calendar, IV/RV, winrate, volume, ATM helpers, ranking) for
everything not specific to this strategy's structure or thresholds.

Strategy-specific config lives under config.json's "strategies.iron_fly"
key, not at the top level -- this is what keeps a second strategy's own
thresholds (e.g. a calendar spread's own min_term_structure) from
colliding with this one's.

Commands (see CLAUDE.md's Tool Reference):
  get_candidates --date MM/DD/YYYY
  get_order --symbol X --earnings_date YYYY-MM-DD --earnings_timing "..."
"""

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scanner


def _strategy_config(config: dict) -> dict:
    return config.get("strategies", {}).get("iron_fly", {})


def fetch_price_and_term_structure(symbol: str, earnings_date: date, earnings_timing: str, config: dict) -> dict:
    """Live price + term structure/expected move via tt.py/scanner.py's
    shared helpers.

    `earnings_date`/`earnings_timing` pick the correct front-month: the
    nearest expiration on or after the earnings *reaction* date (the day
    the market can actually trade on the news -- the next trading day for
    an "After market close" report, the report day itself for "Before
    market open"). This must NOT be "nearest expiration to today" -- a
    same-day 0DTE expiration having nothing to do with a multi-day-out
    earnings event was caught live during testing (AAPL: front_exp
    defaulted to today's 0DTE chain, producing a nonsensical positive
    term-structure reading for a symbol that wasn't even an earnings
    candidate on that date).

    Term structure sign convention: negative means front-month is richer
    than back-month (the earnings-event IV premium this trade captures) --
    see scanner.compute_expected_move_and_term_structure's docstring for the
    real sign bug this caught during testing. expected_move applies the
    standard 0.85x straddle-to-expected-move correction -- this only affects
    the screening threshold (min_expected_move_dollars in apply_tiering),
    not wing sizing, which fetch_iron_fly_order computes independently from
    its own freshly-fetched straddle_credit.

    Returns {"ok": False, "error": ...} on any missing data (no credentials,
    no chain, incomplete ATM strikes, no suitable back-month expiration)
    rather than raising -- callers (cmd_get_candidates) treat that as
    "insufficient data to verify these hard filters," not as a reason to
    crash the whole scan.
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
                "atm_delta_abs": abs(front_call["delta"]) if front_call.get("delta") is not None else None,
                "front_expiration_days": (front_exp - date.today()).days,
                "chain_complete": True,
                **liquidity,
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def apply_tiering(criteria: dict, config: dict) -> dict:
    """Pure function -- no I/O. Applies docs/screening-criteria.md's hard
    filters and near-miss bands to an already-computed criteria dict.
    `config` here is this strategy's own sub-config
    (config["strategies"]["iron_fly"]), not the full project config.
    `criteria` keys are all optional; a missing/None value for any
    criterion is treated as an unverified hard-filter failure, not a
    silent pass -- see the winrate/IV-RV precedent of never defaulting an
    unknown to "pass." A present-but-out-of-range value for
    combined_open_interest/atm_delta_abs/front_expiration_days is a
    distinct, correctly-labeled rejection reason, not just "_unverified"
    (a real gap caught during testing: this function originally only
    checked these three for None, never against their actual thresholds
    in config, so an out-of-range chain would have passed silently).
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

    if criteria.get("expected_move_dollars") is None:
        hard_fail.append("expected_move_unverified")
    elif criteria["expected_move_dollars"] < config["min_expected_move_dollars"]:
        hard_fail.append("expected_move_below_minimum")

    if criteria.get("atm_delta_abs") is None:
        hard_fail.append("atm_delta_abs_unverified")
    elif criteria["atm_delta_abs"] > config["max_atm_delta_abs"]:
        hard_fail.append("atm_delta_abs_above_maximum")

    if criteria.get("front_expiration_days") is None:
        hard_fail.append("front_expiration_days_unverified")
    elif criteria["front_expiration_days"] > config["max_front_expiration_days"]:
        hard_fail.append("front_expiration_days_too_far_out")

    if not criteria.get("chain_complete"):
        hard_fail.append("chain_complete_unverified")

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


def _wing_width_multiple(iv_rv_ratio: float | None, config: dict) -> float:
    """Wing width sizing scaled by this candidate's own IV/RV ratio, rather
    than one fixed wing_width_credit_multiple regardless of how overpriced
    IV actually is. Deliberately does NOT use a market-wide VIX-style band
    (the pattern MEICAgent uses for its own delta-target scaling) -- an
    earnings move is an idiosyncratic, single-stock event, and this
    candidate's own iv_rv_ratio (already computed per-symbol from its own
    options and realized volatility) is a more relevant regime signal than
    broad market VIX would be here.

    Bands (config: wing_width_multiple_low/mid/high, iv_rv_ratio thresholds
    wing_width_band_low_max/mid_max): a stronger IV/RV edge gets wider
    wings (more protective margin, since the position can afford it), a
    barely-qualifying edge gets tighter wings (less excess width paid for
    against a marginal signal). This is a reasoned proposal, not backtested
    for this strategy -- same caveat as MEICAgent's VIX-banded delta scale.
    """
    if iv_rv_ratio is None:
        return config.get("wing_width_credit_multiple", 3.0)
    low_max = config.get("wing_width_band_low_max", 1.25)
    mid_max = config.get("wing_width_band_mid_max", 1.75)
    if iv_rv_ratio < low_max:
        return config.get("wing_width_multiple_low", 2.5)
    if iv_rv_ratio < mid_max:
        return config.get("wing_width_multiple_mid", 3.0)
    return config.get("wing_width_multiple_high", 3.5)


def fetch_iron_fly_order(symbol: str, earnings_date: date, earnings_timing: str, full_config: dict) -> dict:
    """Build a concrete, tradeable iron fly order spec for `symbol`'s next
    earnings-reaction front-month expiration: sell an ATM straddle, buy
    wings sized by _wing_width_multiple() (scaled to this candidate's own,
    freshly-refetched IV/RV ratio, not the credit_multiple config value
    alone). `full_config` is the whole project config (not just this
    strategy's sub-config) since fetching IV/RV ratio needs the top-level
    DoltHub connection settings. Returns
    {"ok": True, "order": {...tt.py execute_trade-shaped spec...},
    "expiration": ..., "short_strike": ..., "wing_width": ..., "credit": ...}
    or {"ok": False, "error": ...}.

    Deliberately re-fetches live data at call time rather than reusing
    fetch_price_and_term_structure's earlier snapshot -- this is meant to
    be called at actual entry time (afternoon), potentially hours after
    the scan that surfaced the candidate, so prices/strikes must be fresh.
    IV/RV ratio is re-fetched here too, for the same reason -- it may have
    moved since the scan.
    """
    config = _strategy_config(full_config)
    try:
        qe = scanner.fetch_quote_and_expirations(symbol)
        if not qe.get("ok"):
            return qe
        price, expirations = qe["price"], qe["expirations"]

        front_exp, err = scanner.select_front_expiration(expirations, earnings_date, earnings_timing)
        if front_exp is None:
            return {"ok": False, "error": err}

        # Wide strike window: need both the ATM short strike and wings
        # potentially far from it, unlike fetch_price_and_term_structure's
        # narrow +/-3-strike window (which only needs the ATM point).
        front_chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_quotes", "--strike_count", "40", "--around_price", str(price),
        ])
        if not front_chain.get("ok"):
            return {"ok": False, "error": front_chain.get("error", "get_option_chain failed")}
        entries = front_chain["chain"][str(front_exp)]

        short_call = scanner.atm_entry(entries, "call", price)
        short_put = scanner.atm_entry(entries, "put", price)
        if short_call is None or short_put is None:
            return {"ok": False, "error": "incomplete ATM strikes"}
        if short_call.get("mid") is None or short_put.get("mid") is None:
            return {"ok": False, "error": "no quote data for ATM strikes"}

        short_strike = float(short_call["strike_price"])
        straddle_credit = short_call["mid"] + short_put["mid"]
        ivrv = scanner.fetch_iv_rv_ratio(symbol, full_config)
        iv_rv_ratio = ivrv["iv_rv_ratio"] if ivrv.get("ok") else None
        wing_multiple = _wing_width_multiple(iv_rv_ratio, config)
        wing_width = wing_multiple * straddle_credit

        long_call = scanner.nearest_strike_entry(entries, "call", short_strike + wing_width, short_strike)
        long_put = scanner.nearest_strike_entry(entries, "put", short_strike - wing_width, short_strike)
        if long_call is None or long_put is None:
            return {"ok": False, "error": "no valid wing strikes found"}
        if long_call.get("mid") is None or long_put.get("mid") is None:
            return {"ok": False, "error": "no quote data for wing strikes"}

        net_credit = straddle_credit - long_call["mid"] - long_put["mid"]

        order = {
            "order_type": "Limit",
            "time_in_force": "Day",
            "price": round(net_credit, 2),
            "price_effect": "Credit",
            "legs": [
                {"symbol": short_call["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1},
                {"symbol": short_put["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1},
                {"symbol": long_call["symbol"], "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1},
                {"symbol": long_put["symbol"], "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1},
            ],
        }
        return {
            "ok": True,
            "strategy": "iron_fly",
            "order": order,
            "symbol": symbol,
            "expiration": str(front_exp),
            "underlying_price": price,
            "short_strike": short_strike,
            "long_call_strike": float(long_call["strike_price"]),
            "long_put_strike": float(long_put["strike_price"]),
            "wing_width_target": wing_width,
            "wing_width_multiple_used": wing_multiple,
            "iv_rv_ratio": iv_rv_ratio,
            "credit": round(net_credit, 2),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def evaluate_position(position: dict, quotes: dict, config: dict) -> dict:
    """Decide whether to close an open iron fly position early (CLAUDE.md
    Step 3c, the narrow market-open-to-close-window slot) ahead of Step 3's
    unconditional close-window sweep, which remains the final backstop
    regardless of what this returns. Pure calculation, no I/O -- caller
    fetches `quotes` via `scanner.fetch_quotes_by_symbol` for this
    position's `legs_json` symbols.

    No `open_legs` argument (unlike double_calendar/short_strangle/
    jade_lizard's evaluate_position()) since iron_fly never populates
    `trade_legs` -- it always closes as a single unit via `legs_json`, so
    there's no partial-close state to track.

    Returns {"action": "hold"} or {"action": "close_all", "reason":
    "profit_target"|"stop_loss"} -- see scanner.evaluate_credit_spread_exit
    for the thresholds (config: profit_target_pct, stop_loss_credit_multiple).
    """
    legs = json.loads(position["legs_json"])
    exit_debit = scanner.compute_generic_exit_debit(legs, quotes)
    if exit_debit is None:
        return {"action": "hold"}
    return scanner.evaluate_credit_spread_exit(position["entry_credit"], exit_debit, config)


def cmd_get_candidates(args) -> dict:
    """Full tiered scan for a date: pulls the calendar, then for each symbol
    computes every criterion in docs/screening-criteria.md and tiers it via
    apply_tiering(). Price/term-structure/expected-move require a live
    tastytrade session via tt.py (see `tt.py secrets_set`). Volume, IV/RV,
    and winrate are real, live DoltHub-backed signals regardless of tt.py's
    credential status. Thin wrapper around scanner.run_candidate_scan --
    shared with every other strategy's cmd_get_candidates.
    """
    config = scanner._load_config()
    strategy_config = _strategy_config(config)
    return scanner.run_candidate_scan(args.date, config, fetch_price_and_term_structure, apply_tiering, strategy_config)


def main() -> None:
    scanner.run_strategy_main(cmd_get_candidates, fetch_iron_fly_order)


if __name__ == "__main__":
    main()
