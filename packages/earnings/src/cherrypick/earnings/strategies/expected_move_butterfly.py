"""Expected-move butterfly: a 1-2-1 debit fly whose body sits one expected move from spot -- a call
fly above when the read is bullish, a put fly below when it is bearish -- opened the afternoon
before the print and held to the expiry close or a take-profit. Paper only.

**What history says before this runs** (packages/earnings/docs/expected-move-fly-plan.md,
scripts/emfly_history.py, 4,950 events 2020-2026): at mid the fly is fairly priced, costs take
16-18% of the debit, and a side call needs to be right 68-83% of the time to break even; the best
signal found is right about 53%. This book exists to measure real fills against that, not because
the history says it earns.

**The side** is the study's one stable read, the 25-delta risk reversal over ATM IV
(`(call_iv - put_iv) / atm_iv`) -- and it reads the OPPOSITE way to the published smirk studies: a
rich put wing preceded a higher reaction, a rich call wing a lower one, in 6 of 7 years. Below
`rr25_put_rich_max` buys the call fly, above `rr25_call_rich_min` buys the put fly, and between
them -- the middle third of the study's 2020-2023 sample -- nothing is opened
(`direction_signal_neutral`). The cuts were fitted on Dolt's end-of-day IV and delta; the broker's
greeks are computed differently, so the live thirds will not split exactly at a third.

**The strikes.** Body: the listed strike nearest spot +/- `body_em_multiple` x EM. Wings: the
symmetric listed distance nearest `wing_em_multiple` x EM (0.85, the SCHW example's 3 on 3.51),
refused when none lies within half to one and a half times it -- never snapped one wing at a time
into an asymmetric fly. EM is 0.85 x the front ATM straddle, the suite's convention.

**Not ranked for the agent's single-best path.** The criteria carry neither `term_structure` nor
`skew_abs`, so `scanner.compute_composite_score` returns None and `rank_strategies` never offers it
as a symbol's best; the harness opens every accepted strategy regardless.

Commands (see CLAUDE.md's Tool Reference):
  get_candidates --date MM/DD/YYYY
  get_order --symbol X --earnings_date YYYY-MM-DD --earnings_timing "..."
"""

import json
from datetime import date

from cherrypick.core.structures import STRADDLE_TO_EM_FACTOR

from cherrypick.earnings import scanner

NAME = "expected_move_butterfly"


def _strategy_config(config: dict) -> dict:
    return config.get("strategies", {}).get(NAME, {})


def rr25(call_iv: float | None, put_iv: float | None, atm_iv: float | None) -> float | None:
    """The 25-delta risk reversal over ATM IV; None when any input is missing or ATM IV is not
    positive."""
    if call_iv is None or put_iv is None or not atm_iv or atm_iv <= 0:
        return None
    return (call_iv - put_iv) / atm_iv


def side_for(rr: float | None, config: dict) -> str | None:
    """'call' (bullish), 'put' (bearish) or None (neutral or unmeasured). Pure."""
    if rr is None:
        return None
    if rr < config["rr25_put_rich_max"]:
        return "call"
    if rr > config["rr25_call_rich_min"]:
        return "put"
    return None


def _atm_iv(front_call: dict, front_put: dict) -> float | None:
    ivs = [e.get("iv") for e in (front_call, front_put) if e.get("iv") is not None]
    return sum(ivs) / len(ivs) if ivs else None


def _read(symbol: str, earnings_date: date, earnings_timing: str, config: dict) -> dict:
    """Spot, front expiration, EM and the skew read -- shared by screening and the order build so
    both see the same rule."""
    qe = scanner.fetch_quote_and_expirations(symbol)
    if not qe.get("ok"):
        return qe
    price, expirations = qe["price"], qe["expirations"]
    front_exp, err = scanner.select_front_expiration(expirations, earnings_date, earnings_timing)
    if front_exp is None:
        return {"ok": False, "error": err}
    atm = scanner.fetch_front_back_atm_entries(symbol, front_exp, front_exp, price)
    if not atm.get("ok"):
        return atm
    front_call, front_put = atm["front_call"], atm["front_put"]
    skew = scanner.select_side(symbol, front_exp, price, config)
    if not skew.get("ok"):
        return skew
    rr = rr25(skew["call_iv"], skew["put_iv"], _atm_iv(front_call, front_put))
    return {
        "ok": True,
        "price": price,
        "expirations": expirations,
        "front_exp": front_exp,
        "front_call": front_call,
        "front_put": front_put,
        "expected_move": STRADDLE_TO_EM_FACTOR * (front_call["mid"] + front_put["mid"]),
        "rr25": rr,
        "side": side_for(rr, config),
    }


def fetch_price_and_expected_move(
    symbol: str, earnings_date: date, earnings_timing: str, config: dict
) -> dict:
    """Screening criteria. `config` is the full project config. Never raises."""
    strategy_config = _strategy_config(config)
    try:
        r = _read(symbol, earnings_date, earnings_timing, strategy_config)
        if not r.get("ok"):
            return r
        liquidity = scanner.fetch_liquidity_criteria(
            symbol, r["front_exp"], r["expirations"], r["front_call"], r["front_put"]
        )
        return {
            "ok": True,
            "criteria": {
                "price": r["price"],
                "expected_move_dollars": r["expected_move"],
                "expected_move_pct": r["expected_move"] / r["price"],
                "rr25": r["rr25"],
                "side": r["side"],
                "front_expiration_days": (r["front_exp"] - date.today()).days,
                "chain_complete": True,
                **liquidity,
            },
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def apply_tiering(criteria: dict, config: dict) -> dict:
    """Pure. A missing value is an unverified hard fail, never a silent pass. No move-tail gate: the
    fly's loss is its debit whatever the move, so a fat tail is not a risk it needs refusing on.
    `config` is this strategy's own sub-config."""
    hard_fail: list[str] = []

    if criteria.get("price") is None:
        hard_fail.append("price_unverified")
    elif criteria["price"] < config["min_price"]:
        hard_fail.append("price_below_minimum")

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

    if criteria.get("rr25") is None:
        hard_fail.append("rr25_unverified")
    elif side_for(criteria["rr25"], config) is None:
        hard_fail.append("direction_signal_neutral")

    scanner.apply_liquidity_gates(criteria, config, hard_fail)
    scanner.apply_soft_criteria(criteria, config, hard_fail)

    return {"accepted": not hard_fail, "reject_reasons": hard_fail}


def symmetric_legs(strikes: list[float], spot: float, offset: float, width: float) -> tuple | None:
    """(lower, body, upper) from listed strikes: the body nearest spot + offset, the wings the
    symmetric listed distance nearest `width`; None when no pair lies within 0.5-1.5x of it. Pure."""
    if not strikes:
        return None
    body = min(strikes, key=lambda k: (abs(k - (spot + offset)), k))
    listed = set(strikes)
    gaps = [k - body for k in strikes if k > body and (2 * body - k) in listed]
    gaps = [d for d in gaps if 0.5 * width <= d <= 1.5 * width]
    if not gaps:
        return None
    d = min(gaps, key=lambda x: (abs(x - width), x))
    return body - d, body, body + d


def fetch_expected_move_butterfly_order(
    symbol: str, earnings_date: date, earnings_timing: str, full_config: dict
) -> dict:
    """A concrete 1-2-1 fly on the side the skew read picks. Re-fetches live data at call time, the
    same discipline as every other order builder. Never raises."""
    config = _strategy_config(full_config)
    try:
        r = _read(symbol, earnings_date, earnings_timing, config)
        if not r.get("ok"):
            return r
        side = r["side"]
        if side is None:
            return {"ok": False, "error": "direction_signal_neutral", "rr25": r["rr25"]}
        price, front_exp, em = r["price"], r["front_exp"], r["expected_move"]

        chain = scanner.call_tt(
            [
                "get_option_chain",
                "--symbol",
                symbol,
                "--expiration",
                str(front_exp),
                "--include_quotes",
                "--strike_count",
                "40",
                "--around_price",
                str(price),
            ]
        )
        if not chain.get("ok"):
            return {"ok": False, "error": chain.get("error", "get_option_chain failed")}
        want = side[0]
        quoted = {
            float(e["strike_price"]): e
            for e in chain["chain"][str(front_exp)]
            if str(e.get("option_type", "")).strip().lower().startswith(want) and e.get("mid") is not None
        }
        sign = 1 if side == "call" else -1
        legs = symmetric_legs(
            sorted(quoted),
            price,
            sign * config.get("body_em_multiple", 1.0) * em,
            config.get("wing_em_multiple", 0.85) * em,
        )
        if legs is None:
            return {"ok": False, "error": "no_symmetric_wings"}
        lower, body, upper = (quoted[k] for k in legs)
        width = legs[1] - legs[0]
        net_debit = lower["mid"] - 2 * body["mid"] + upper["mid"]
        if net_debit <= 0 or net_debit >= width:
            return {"ok": False, "error": "debit_outside_zero_to_width", "net_debit": round(net_debit, 2)}

        order = {
            "order_type": "Limit",
            "time_in_force": "Day",
            "price": round(net_debit, 2),
            "price_effect": "Debit",
            "legs": [
                {
                    "symbol": lower["symbol"],
                    "instrument_type": "Equity Option",
                    "action": "Buy to Open",
                    "quantity": 1,
                },
                {
                    "symbol": body["symbol"],
                    "instrument_type": "Equity Option",
                    "action": "Sell to Open",
                    "quantity": 2,
                },
                {
                    "symbol": upper["symbol"],
                    "instrument_type": "Equity Option",
                    "action": "Buy to Open",
                    "quantity": 1,
                },
            ],
        }
        return {
            "ok": True,
            "strategy": NAME,
            "order": order,
            "symbol": symbol,
            "side": side,
            "expiration": str(front_exp),
            "underlying_price": price,
            "lower_strike": legs[0],
            "body_strike": legs[1],
            "upper_strike": legs[2],
            "wing_width": width,
            "expected_move": em,
            "rr25": r["rr25"],
            "net_debit": round(net_debit, 2),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def evaluate_position(
    position: dict, quotes: dict, config: dict, is_first_check_of_day: bool = False
) -> dict:
    """Hold to the expiry close (management's 15:30 rule), closing earlier only at the take-profit:
    the fly worth `take_profit_width_frac` of its width at the close price (far touch, the
    harness's exit convention). In the study this rule lost least of the six it compared on the
    same entries; closing on the reaction day paid a second set of costs on an unmatured fly and
    lost most. No stop: the debit is the most it can lose, and a stop only realises it earlier.
    `is_first_check_of_day` is accepted for the harness's managed-close call and unused."""
    cfg = _strategy_config(config)
    legs = json.loads(position["legs_json"])
    exit_debit = scanner.compute_generic_exit_debit(legs, quotes, scanner.exit_basis(position))
    if exit_debit is None:
        return {"action": "hold"}
    quantity = position.get("quantity") or 1
    value_per_contract = -exit_debit / quantity
    width = _wing_width(legs)
    if width is None:
        return {"action": "hold"}
    if value_per_contract >= cfg.get("take_profit_width_frac", 0.5) * width:
        return {"action": "close_all", "reason": "profit_target"}
    return {"action": "hold"}


def _wing_width(legs: list[dict]) -> float | None:
    """Body minus lower wing, from the legs' own OCC strikes."""
    strikes = []
    for leg in legs:
        try:
            strikes.append(int(leg["symbol"][-8:]) / 1000.0)
        except (KeyError, TypeError, ValueError):
            return None
    if len(strikes) != 3:
        return None
    lo, _, hi = sorted(strikes)
    return (hi - lo) / 2


def cmd_get_candidates(args) -> dict:
    """Full tiered scan for a date, through scanner.run_candidate_scan like every other strategy."""
    config = scanner._load_config()
    return scanner.run_candidate_scan(
        args.date, config, fetch_price_and_expected_move, apply_tiering, _strategy_config(config)
    )


def main() -> None:
    scanner.run_strategy_main(cmd_get_candidates, fetch_expected_move_butterfly_order)


if __name__ == "__main__":
    main()
