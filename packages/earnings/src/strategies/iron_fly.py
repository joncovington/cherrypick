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

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scanner


def _strategy_config(config: dict) -> dict:
    return config.get("strategies", {}).get("iron_fly", {})


@dataclass
class TermStructureResult:
    symbol: str
    front_expiration: str
    back_expiration: str
    front_atm_iv: float
    back_atm_iv: float
    term_structure: float  # (back_iv - front_iv) / back_iv; negative = front richer
    expected_move: float   # front-month ATM straddle price
    expected_move_pct: float  # expected_move / underlying_price


def compute_term_structure(
    symbol: str,
    underlying_price: float,
    front_expiration: str,
    front_atm_call_mid: float,
    front_atm_put_mid: float,
    front_atm_iv: float,
    back_expiration: str,
    back_atm_iv: float,
) -> TermStructureResult:
    """Pure calculation — no network calls. Caller supplies ATM strike data
    already pulled from `tt.py get_option_chain --include_greeks` for both
    the front (post-earnings) and a later back-month expiration.

    Term structure mirrors EarningsEdgeDetection's convention: negative
    values mean the front-month is richer than the back-month (the
    earnings-event IV premium the trade is designed to capture).

    NOTE: the sign here is (back - front) / back, not (front - back) / back
    -- caught live during testing, a real bug: the naive (front - back)/back
    formula is POSITIVE when front is richer, which is backwards from this
    function's own documented convention and from the -0.004 threshold in
    docs/screening-criteria.md (which expects negative-is-good). With the
    original sign, a real earnings candidate (EPAC, front IV ~64% richer
    than back) would have been *rejected* as term_structure_insufficient --
    exactly the opposite of the intended behavior.

    expected_move applies the standard 0.85x straddle-to-expected-move
    correction (documented convention, e.g. AAPL $14.00 straddle -> $11.90
    expected move) rather than using the raw straddle price directly --
    this only affects the screening threshold comparison
    (min_expected_move_dollars in apply_tiering), not wing sizing, which
    fetch_iron_fly_order computes independently from its own freshly-fetched
    straddle_credit.
    """
    term_structure = (back_atm_iv - front_atm_iv) / back_atm_iv
    expected_move = 0.85 * (front_atm_call_mid + front_atm_put_mid)
    return TermStructureResult(
        symbol=symbol,
        front_expiration=front_expiration,
        back_expiration=back_expiration,
        front_atm_iv=front_atm_iv,
        back_atm_iv=back_atm_iv,
        term_structure=term_structure,
        expected_move=expected_move,
        expected_move_pct=expected_move / underlying_price,
    )


def fetch_price_and_term_structure(symbol: str, earnings_date: date, earnings_timing: str, config: dict) -> dict:
    """Live price + term structure/expected move via tt.py.

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

    Returns {"ok": False, "error": ...} on any missing data (no credentials,
    no chain, incomplete ATM strikes, no suitable back-month expiration)
    rather than raising -- callers (cmd_get_candidates) treat that as
    "insufficient data to verify these hard filters," not as a reason to
    crash the whole scan. On success, returns {"ok": True, "criteria":
    {"price": ..., "term_structure": ..., "expected_move_dollars": ...}}.
    """
    try:
        quote = scanner.call_tt(["get_quote", "--symbol", symbol])
        if not quote.get("ok"):
            return {"ok": False, "error": quote.get("error", "get_quote failed")}
        price = quote.get("price")
        if price is None:
            return {"ok": False, "error": "get_quote returned no price"}

        chain_all = scanner.call_tt(["get_option_chain", "--symbol", symbol])
        if not chain_all.get("ok"):
            return {"ok": False, "error": chain_all.get("error", "get_option_chain failed")}
        expirations = sorted(date.fromisoformat(e) for e in chain_all["chain"].keys())
        if not expirations:
            return {"ok": False, "error": "no expirations in chain"}

        # Weekly-options detection: a name with only monthly cycles can still
        # incidentally have its nearest monthly expiration fall inside the
        # max_front_expiration_days window some weeks (by luck of the
        # calendar), which would otherwise pass the generic expiration-window
        # check without actually being a liquid, weekly-optioned name. Check
        # the real cadence instead: at least one gap between consecutive
        # expirations of <=10 days indicates weeklies exist.
        has_weekly_options = any(
            (expirations[i + 1] - expirations[i]).days <= 10
            for i in range(len(expirations) - 1)
        )

        reaction_date = earnings_date + timedelta(days=1) if earnings_timing == "After market close" else earnings_date

        eligible = [e for e in expirations if e >= reaction_date]
        if not eligible:
            return {"ok": False, "error": f"no expiration on/after reaction date {reaction_date}"}
        front_exp = min(eligible)
        # Prefer a genuine monthly cycle >=21 days out (documented convention
        # for real term-structure separation, not just "some later date");
        # fall back to the nearest expiration >=21 days out if this name has
        # no monthly listing in the fetched window rather than failing outright.
        back_exp = scanner.nearest_expiration_at_least_days_after(expirations, front_exp, 21, monthly_only=True)
        if back_exp is None:
            back_exp = scanner.nearest_expiration_at_least_days_after(expirations, front_exp, 21, monthly_only=False)
        if back_exp is None:
            return {"ok": False, "error": "no back-month expiration available for term structure"}

        front_chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_greeks", "--include_quotes", "--strike_count", "3",
            "--around_price", str(price),
        ])
        back_chain = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(back_exp),
            "--include_greeks", "--strike_count", "3", "--around_price", str(price),
        ])
        if not front_chain.get("ok") or not back_chain.get("ok"):
            return {"ok": False, "error": "front/back chain fetch failed"}

        front_entries = front_chain["chain"][str(front_exp)]
        back_entries = back_chain["chain"][str(back_exp)]
        front_call = scanner.atm_entry(front_entries, "call", price)
        front_put = scanner.atm_entry(front_entries, "put", price)
        back_call = scanner.atm_entry(back_entries, "call", price)
        if front_call is None or front_put is None or back_call is None:
            return {"ok": False, "error": "incomplete ATM strikes in front/back chain"}
        if front_call.get("mid") is None or front_put.get("mid") is None:
            return {"ok": False, "error": "no quote data for front-month ATM strikes"}
        if front_call.get("iv") is None or back_call.get("iv") is None:
            return {"ok": False, "error": "no greeks/iv data for front/back ATM strikes"}

        # Combined OI needs the whole front-month chain, not just the ATM
        # window used for the straddle/IV calc above.
        front_chain_oi = scanner.call_tt([
            "get_option_chain", "--symbol", symbol, "--expiration", str(front_exp),
            "--include_oi", "--strike_count", "999",
        ])
        combined_oi = None
        if front_chain_oi.get("ok"):
            oi_entries = front_chain_oi["chain"].get(str(front_exp), [])
            ois = [e["open_interest"] for e in oi_entries if e.get("open_interest") is not None]
            if ois:
                combined_oi = sum(ois)

        ts = compute_term_structure(
            symbol=symbol,
            underlying_price=price,
            front_expiration=str(front_exp),
            front_atm_call_mid=front_call["mid"],
            front_atm_put_mid=front_put["mid"],
            front_atm_iv=front_call["iv"],
            back_expiration=str(back_exp),
            back_atm_iv=back_call["iv"],
        )
        return {
            "ok": True,
            "criteria": {
                "price": price,
                "term_structure": ts.term_structure,
                "expected_move_dollars": ts.expected_move,
                "combined_open_interest": combined_oi,
                "atm_delta_abs": abs(front_call["delta"]) if front_call.get("delta") is not None else None,
                "front_expiration_days": (front_exp - date.today()).days,
                "chain_complete": True,
                "has_weekly_options": has_weekly_options,
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

    if criteria.get("combined_open_interest") is None:
        hard_fail.append("combined_open_interest_unverified")
    elif criteria["combined_open_interest"] < config["min_combined_open_interest"]:
        hard_fail.append("combined_open_interest_below_minimum")

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

    if config.get("require_weekly_options", True):
        if criteria.get("has_weekly_options") is None:
            hard_fail.append("has_weekly_options_unverified")
        elif not criteria["has_weekly_options"]:
            hard_fail.append("no_weekly_options")

    near_miss: list[str] = []

    def _band(value, min_pass, min_near_miss, name):
        if value is None:
            near_miss.append(f"{name}_unknown")
            return
        if value >= min_pass:
            return
        if value >= min_near_miss:
            near_miss.append(name)
            return
        hard_fail.append(f"{name}_below_near_miss")

    _band(criteria.get("avg_volume"), config["min_avg_volume"], config["near_miss_min_avg_volume"], "avg_volume")
    _band(criteria.get("iv_rv_ratio"), config["min_iv_rv_ratio"], config["near_miss_min_iv_rv_ratio"], "iv_rv_ratio")
    _band(criteria.get("winrate"), config["min_winrate"], config["near_miss_min_winrate"], "winrate")

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
        quote = scanner.call_tt(["get_quote", "--symbol", symbol])
        if not quote.get("ok"):
            return {"ok": False, "error": quote.get("error", "get_quote failed")}
        price = quote.get("price")
        if price is None:
            return {"ok": False, "error": "get_quote returned no price"}

        chain_all = scanner.call_tt(["get_option_chain", "--symbol", symbol])
        if not chain_all.get("ok"):
            return {"ok": False, "error": chain_all.get("error", "get_option_chain failed")}
        expirations = sorted(date.fromisoformat(e) for e in chain_all["chain"].keys())
        if not expirations:
            return {"ok": False, "error": "no expirations in chain"}

        reaction_date = earnings_date + timedelta(days=1) if earnings_timing == "After market close" else earnings_date
        eligible = [e for e in expirations if e >= reaction_date]
        if not eligible:
            return {"ok": False, "error": f"no expiration on/after reaction date {reaction_date}"}
        front_exp = min(eligible)

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


def cmd_get_candidates(args) -> dict:
    """Full tiered scan for a date: pulls the calendar, then for each symbol
    computes every criterion in docs/screening-criteria.md and tiers it via
    apply_tiering(). Price/term-structure/expected-move require a live
    tastytrade session via tt.py (see `tt.py secrets_set`). Volume, IV/RV,
    and winrate are real, live DoltHub-backed signals regardless of tt.py's
    credential status.
    """
    config = scanner._load_config()
    strategy_config = _strategy_config(config)
    iso_date = datetime.strptime(args.date, "%m/%d/%Y").strftime("%Y-%m-%d")
    calendar = scanner.fetch_dolthub_calendar(iso_date, config)
    lookback = config.get("winrate_lookback_quarters", 8)

    candidates = []
    for entry in calendar:
        symbol = entry["symbol"]
        criteria: dict = {}

        broker = fetch_price_and_term_structure(symbol, entry["date"], entry["timing"], config)
        if broker.get("ok"):
            criteria.update(broker["criteria"])
        broker_error = None if broker.get("ok") else broker.get("error")

        criteria["avg_volume"] = scanner.fetch_avg_volume(symbol, config)

        ivrv = scanner.fetch_iv_rv_ratio(symbol, config)
        criteria["iv_rv_ratio"] = ivrv["iv_rv_ratio"] if ivrv.get("ok") else None

        winrate = scanner.compute_winrate(symbol, config, lookback)
        criteria["winrate"] = winrate["winrate"]
        winrate_sample_size = winrate["sample_size"]

        tiering = apply_tiering(criteria, strategy_config)

        candidates.append({
            "symbol": symbol,
            "earnings_timing": entry["timing"],
            "tier": tiering["tier"],
            "hard_fail_reasons": tiering["hard_fail_reasons"],
            "near_miss_reasons": tiering["near_miss_reasons"],
            "criteria": criteria,
            "winrate_sample_size": winrate_sample_size,
            "broker_data_error": broker_error,
        })

    candidates.sort(key=lambda c: {"Tier 1": 0, "Tier 2": 1, "Near Miss": 2, "Reject": 3}[c["tier"]])

    ranked = scanner.rank_candidates(candidates, config)
    selection = scanner.select_positions(ranked, config)

    return {
        "ok": True,
        "date": args.date,
        "candidates": candidates,
        "ranked": ranked,
        "selected": selection["selected"],
        "skipped_for_selection": selection["skipped"],
    }


def cmd_get_order(args) -> dict:
    config = scanner._load_config()
    return fetch_iron_fly_order(
        args.symbol.strip().upper(),
        date.fromisoformat(args.earnings_date),
        args.earnings_timing,
        config,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_cand = sub.add_parser("get_candidates")
    p_cand.add_argument("--date", required=True)

    p_order = sub.add_parser("get_order")
    p_order.add_argument("--symbol", required=True)
    p_order.add_argument("--earnings_date", required=True)
    p_order.add_argument("--earnings_timing", required=True)

    args = parser.parse_args()
    dispatch = {
        "get_candidates": cmd_get_candidates,
        "get_order": cmd_get_order,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout, default=str)


if __name__ == "__main__":
    main()
