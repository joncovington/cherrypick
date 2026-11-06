"""The time-spread scanner's structures, gates and model columns, on a made-up chain priced by
Black-Scholes at known IVs (front week 60%, back month 45%)."""

from datetime import date

from cherrypick.manage import pricing
from cherrypick.manage import rules as _rules
from cherrypick.manage import timespreads as ts

SESSION = date(2026, 11, 6)
RULES = _rules.effective(None)[0]
RATE = float(RULES["pricing"]["rate"])
FRONT = {"expiration": "2026-11-13", "dte": 7}
BACK = {"expiration": "2026-12-18", "dte": 42}


def chain(dte, iv, spot=100.0, half=0.01, oi=500.0, strikes=None):
    out = []
    for k in strikes or [80 + 2.5 * i for i in range(17)]:
        for right in ("C", "P"):
            px = pricing.price(spot, k, dte / 365, iv, right, RATE)
            if px < 0.03:
                continue
            w = max(px * half, 0.01)
            out.append(
                {
                    "strike": k,
                    "right": right,
                    "option": f"XYZ {right}{k:g}",
                    "bid": round(px - w, 2),
                    "ask": round(px + w, 2),
                    "volume": 50.0,
                    "open_interest": oi,
                }
            )
    return out


def entry(front_iv=0.60, back_iv=0.45, **kw):
    return {
        "spot": 100.0,
        "front": {**FRONT, "options": chain(7, front_iv, **kw)},
        "back": {**BACK, "options": chain(42, back_iv, **kw)},
    }


def test_calendars_sit_at_the_money_and_one_strike_either_side():
    rows, _ = ts.structures(entry(), SESSION, RULES)
    cals = sorted((r["kind"], r["long"]["strike"]) for r in rows if "calendar" in r["kind"])
    assert cals == [
        ("call calendar", 97.5),
        ("call calendar", 100.0),
        ("call calendar", 102.5),
        ("put calendar", 97.5),
        ("put calendar", 100.0),
        ("put calendar", 102.5),
    ]
    atm = next(r for r in rows if r["kind"] == "put calendar" and r["long"]["strike"] == 100)
    assert atm["direction"] == "neutral" and atm["theta_day"] > 0 and atm["modelled"]
    assert abs(atm["front_iv"] - 0.60) < 0.02 and abs(atm["back_iv"] - 0.45) < 0.02
    # Best at the strike: the flat case is the most the trade is modelled to make of the three cases.
    assert atm["pnl_flat"] > atm["pnl_up_5"] and atm["pnl_flat"] > atm["pnl_down_10"]
    lo, hi = atm["breakeven"]
    slo, shi = atm["price_stop"]
    assert slo < lo < 100 < hi < shi
    assert abs(atm["take_profit"] - atm["debit_mid"] * 1.25) < 0.02
    assert abs(atm["value_stop"] - atm["debit_mid"] * 0.5) < 0.02
    assert atm["roll_by"] == "2026-11-10"  # three days before the Nov 13 short


def test_diagonals_keep_their_delta_ranges_and_the_width_rule():
    rows, dropped = ts.structures(entry(), SESSION, RULES)
    diags = [r for r in rows if "diagonal" in r["kind"]]
    assert {r["kind"] for r in diags} == {"call diagonal", "put diagonal"}
    for r in diags:
        assert r["debit_share_of_gap"] <= 0.75
        if r["kind"] == "call diagonal":
            assert r["short"]["strike"] > max(r["long"]["strike"], 100) and r["direction"] == "bullish"
        else:
            assert r["short"]["strike"] < min(r["long"]["strike"], 100) and r["direction"] == "bearish"


def test_wide_quotes_fail_the_cost_gate():
    rows, dropped = ts.structures(entry(half=0.20), SESSION, RULES)
    assert rows == []
    assert any("natural fill" in k or "leg bars" in k for k in dropped)


def test_thin_open_interest_fails_the_leg_bars():
    rows, dropped = ts.structures(entry(oi=10.0), SESSION, RULES)
    assert rows == [] and dropped.get("fails the leg bars as one order")


def test_a_name_without_a_chain_is_counted_not_built():
    rows, dropped = ts.structures({"error": "no price"}, SESSION, RULES)
    assert rows == [] and dropped == {"no price": 1}


def test_build_all_orders_by_the_ratio_and_says_it_is_untested():
    doc = {
        "rule": ts.RULE,
        "rows": [
            {"symbol": "AAA", "ratio": 1.2, "iv_rank": 0.4, "sector": "Tech", "earnings_date": None},
            {"symbol": "BBB", "ratio": 1.5, "iv_rank": 0.5, "sector": "Energy", "earnings_date": None},
        ],
        "shortlist": ["BBB", "AAA"],
        "chains": {"AAA": entry(), "BBB": entry(front_iv=0.70)},
    }
    out = ts.build_all(doc, SESSION, RULES)
    assert out["order"] == ts.HYPOTHESIS
    names = [r["underlying"] for r in out["rows"]]
    assert names == sorted(names, key=lambda s: 0 if s == "BBB" else 1)
    assert out["rows"][0]["ratio"] == 1.5 and out["rows"][0]["sector"] == "Energy"
