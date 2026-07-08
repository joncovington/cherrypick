"""ATM single calendar earnings strategy: sell a front-month ATM call, buy
the same strike in a later monthly expiration. Net debit. Same underlying
front/back IV-crush term-structure edge as double_calendar.py, expressed
with one option type instead of two -- structurally simpler than
double_calendar in a few ways worth keeping in mind:

- Always the call side -- literature on ATM calendars treats call and put
  results as virtually identical at the same strike, so there's no skew-
  based side selection the way expected_move_butterfly has. A config-fixed
  choice, not a computed one.
- Two legs, not four -- always closes as a single unit, never a partial
  side. Unlike double_calendar, this strategy never populates trade_legs
  and never passes `legs` to save_trade; it relies purely on legs_json,
  the same convention iron_fly/iron_condor/expected_move_butterfly use for
  their own single-unit closes.
- No expected-move-boundary filter -- this isn't targeting where the stock
  lands the way double_calendar's expected-move-boundary strikes are; it's
  a pure ATM term-structure/IV-crush play, so there's no
  min_expected_move_pct hard filter and no realized-move-dispersion check
  (that convention guards double_calendar's 4-leg debit against a single
  blowout move; a 2-leg ATM calendar's risk profile doesn't need it
  duplicated here without evidence it matters as much).

Uses src/scanner.py's shared engine (calendar, IV/RV, winrate, volume,
ATM/monthly-expiration helpers, ranking, generic exit-debit calc) for
everything not specific to this strategy's structure or thresholds.
Strategy-specific config lives under config.json's "strategies.atm_calendar"
key.

Commands (see CLAUDE.md's Tool Reference):
  get_candidates --date MM/DD/YYYY
  get_order --symbol X --earnings_date YYYY-MM-DD --earnings_timing "..."
"""

import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scanner


def _strategy_config(config: dict) -> dict:
    return config.get("strategies", {}).get("atm_calendar", {})


def fetch_price_and_term_structure(symbol: str, earnings_date: date, earnings_timing: str, config: dict) -> dict:
    """Live price, term structure, and expected move for atm_calendar
    screening, via scanner.py's shared helpers. Mirrors double_calendar.py's
    fetch_price_and_expected_move, reading this strategy's own
    back_month_min_days_after for back-month selection, but does not treat
    expected_move_pct as a hard filter -- this strategy's edge is the
    front/back term structure at the ATM strike, not where the stock lands
    relative to an expected-move boundary.

    Returns {"ok": False, "error": ...} on any missing data rather than
    raising -- same discipline as every other strategy module.

    `config` here is the full project config (whatever run_candidate_scan
    passes), not this strategy's own sub-config -- back_month_min_days_after
    is read via `_strategy_config(config)` explicitly, same discipline
    double_calendar.py documents for the same reason.
    """
    strategy_config = _strategy_config(config)
    try:
        qe = scanner.fetch_quote_and_expirations(symbol)
        if not qe.get("ok"):
            return qe
        price, expirations = qe["price"], qe["expirations"]

        front_exp, err = scanner.select_front_expiration(expirations, earnings_date, earnings_timing)
        if front_exp is None:
            return {"ok": False, "error": err}

        min_days = strategy_config.get("back_month_min_days_after", 21)
        back_exp = scanner.select_back_expiration(expirations, front_exp, min_days)
        if back_exp is None:
            return {"ok": False, "error": f"no monthly (or any) back-month expiration >={min_days} days after front"}

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
                "chain_complete": True,
                **liquidity,
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def apply_tiering(criteria: dict, config: dict) -> dict:
    """Pure function -- no I/O. Applies this strategy's own hard filters
    and near-miss bands. `config` is this strategy's own sub-config
    (config["strategies"]["atm_calendar"]), not the full project config.
    Same discipline as every other strategy's apply_tiering: a missing
    value is an unverified hard-fail, never a silent pass; a
    present-but-out-of-range value gets its own distinct rejection reason.
    No expected_move_pct or dispersion filter -- see module docstring.
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


def fetch_atm_calendar_order(symbol: str, earnings_date: date, earnings_timing: str, full_config: dict) -> dict:
    """Build a concrete, tradeable ATM single calendar order spec: sell a
    front-month ATM call, buy the same strike in the back month. Net debit.
    `full_config` is the whole project config, matching every other
    strategy's fetch_*_order signature.

    Deliberately re-fetches live data at call time rather than reusing an
    earlier scan snapshot -- same reasoning as every other strategy's
    order-builder.
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

        min_days = config.get("back_month_min_days_after", 21)
        back_exp = scanner.select_back_expiration(expirations, front_exp, min_days)
        if back_exp is None:
            return {"ok": False, "error": f"no monthly (or any) back-month expiration >={min_days} days after front"}

        # Both chains fetched up front, same reasoning as
        # double_calendar.py's fetch_double_calendar_order: a calendar
        # spread needs the exact same strike in both expirations, and
        # different expirations can list different strike increments, so
        # the strike is chosen from the intersection of what's actually
        # listed in both chains rather than picked from the front chain
        # alone and hoped to exist in the back chain.
        front_chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_greeks", "--include_quotes", "--strike_count", "40",
            "--around_price", str(price),
        ])
        if not front_chain.get("ok"):
            return {"ok": False, "error": front_chain.get("error", "get_option_chain failed")}
        front_entries = front_chain["chain"][str(front_exp)]

        back_chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(back_exp),
            "--include_quotes", "--strike_count", "40", "--around_price", str(price),
        ])
        if not back_chain.get("ok"):
            return {"ok": False, "error": back_chain.get("error", "get_option_chain failed")}
        back_entries = back_chain["chain"][str(back_exp)]

        back_call_strikes = {float(e["strike_price"]) for e in back_entries if str(e.get("option_type", "")).strip().lower().startswith("c")}
        front_calls_with_back_match = [e for e in front_entries if str(e.get("option_type", "")).strip().lower().startswith("c") and float(e["strike_price"]) in back_call_strikes]
        if not front_calls_with_back_match:
            return {"ok": False, "error": "no front-month call strikes with a matching back-month strike"}

        front_call = scanner.atm_entry(front_calls_with_back_match, "call", price)
        if front_call is None or front_call.get("mid") is None:
            return {"ok": False, "error": "no quote data for front-month ATM call"}

        call_strike = float(front_call["strike_price"])
        back_call = scanner.nearest_strike_entry(back_entries, "call", call_strike, -1.0)
        if back_call is None or back_call.get("mid") is None:
            return {"ok": False, "error": "no quote data for back-month calendar strike"}

        # Net debit: pay more for the back-month leg than collected selling
        # the front-month leg (back-month retains more time value).
        net_debit = back_call["mid"] - front_call["mid"]

        order = {
            "order_type": "Limit",
            "time_in_force": "Day",
            "price": round(net_debit, 2),
            "price_effect": "Debit",
            "legs": [
                {"symbol": front_call["symbol"], "instrument_type": "Equity Option", "action": "Sell to Open", "quantity": 1},
                {"symbol": back_call["symbol"], "instrument_type": "Equity Option", "action": "Buy to Open", "quantity": 1},
            ],
        }
        return {
            "ok": True,
            "strategy": "atm_calendar",
            "order": order,
            "symbol": symbol,
            "front_expiration": str(front_exp),
            "back_expiration": str(back_exp),
            "underlying_price": price,
            "call_strike": call_strike,
            "debit": round(net_debit, 2),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def evaluate_position(
    position: dict, quotes: dict, config: dict, is_first_check_of_day: bool = False,
) -> dict:
    """Decide what (if anything) to do with an open atm_calendar position
    this tick. Pure calculation, no I/O -- callers fetch `quotes` (live
    bid/ask per leg symbol, via `tt.py get_option_chain` for each leg's own
    expiration) themselves. Unlike double_calendar.py's evaluate_position,
    there is no `open_legs` parameter and no `close_side` action -- this
    strategy always closes both legs of `position["legs_json"]` together,
    same single-unit-close convention as iron_fly/iron_condor/
    expected_move_butterfly's Step 3c.

    `position` has at least `entry_credit` (stored negative -- see
    fetch_atm_calendar_order's `debit` field), `legs_json` (the entry legs
    verbatim), and `expiration` (the front-month expiration, stored at
    entry).

    `is_first_check_of_day` follows double_calendar.py's documented
    convention: True only on the loop's first management tick since the
    prior session close. It never changes the decision, only relabels a
    loss-driven stop's `reason` with an `_overnight_gap` suffix so scan_log
    can distinguish a stop firing immediately after a gap (not evidence the
    polling cadence is too slow) from one firing mid-session.

    Returns one of:
      {"action": "hold"}
      {"action": "close_all", "reason": "profit_target"|"stop_loss"|"stop_loss_overnight_gap"|"time_exit"}
    """
    cfg = _strategy_config(config)
    legs = json.loads(position["legs_json"])

    front_expiration = datetime.strptime(position["expiration"], "%Y-%m-%d").date()
    days_to_front_expiration = (front_expiration - date.today()).days
    if days_to_front_expiration <= cfg.get("exit_days_before_front_expiration", 5):
        return {"action": "close_all", "reason": "time_exit"}

    exit_debit = scanner.compute_generic_exit_debit(legs, quotes)
    if exit_debit is None:
        return {"action": "hold"}

    result = scanner.evaluate_debit_spread_exit(position["entry_credit"], exit_debit, cfg)
    if result["action"] == "close_all" and result["reason"] == "stop_loss" and is_first_check_of_day:
        return {"action": "close_all", "reason": "stop_loss_overnight_gap"}
    return result


def cmd_get_candidates(args) -> dict:
    """Full tiered scan for a date, mirroring every other strategy's
    cmd_get_candidates. Thin wrapper around scanner.run_candidate_scan --
    no extra_criteria_fn, since this strategy adds no per-candidate signal
    beyond what fetch_price_and_term_structure already computes.
    """
    config = scanner._load_config()
    strategy_config = _strategy_config(config)
    return scanner.run_candidate_scan(args.date, config, fetch_price_and_term_structure, apply_tiering, strategy_config)


def main() -> None:
    scanner.run_strategy_main(cmd_get_candidates, fetch_atm_calendar_order)


if __name__ == "__main__":
    main()
