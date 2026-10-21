from datetime import date

import pytest

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules

SESSION = date(2026, 10, 13)


def leg(qty, right, strike, exp, opened, mid, root="XYZ", underlying="XYZ"):
    occ_exp = exp.replace("-", "")[2:]
    return {
        "symbol": f"{root:<6}{occ_exp}{right}{int(strike * 1000):08d}",
        "underlying": underlying,
        "instrument_type": "Equity Option",
        "quantity": qty,
        "expiration": exp,
        "right": right,
        "strike": strike,
        "open_price": opened,
        "mid": mid,
        "priced": True,
        "value": qty * mid * 100,
        "open_pl": qty * (mid - opened) * 100,
    }


def run(legs, spot=100.0, rules=None, metrics=None, net_liq=100_000.0, underlying="XYZ"):
    rules = rules or _rules.effective(None)[0]
    m = {"last": spot, **(metrics or {})}
    return ev.evaluate_trade(underlying, legs, m, SESSION, net_liq, rules)


def fired(trade):
    return {f["rule"]: f["action"] for f in trade["fired"]}


# --------------------------------------------------------------------------- credit trades
def test_credit_take_profit_at_half_the_credit():
    t = run([leg(-1, "P", 95, "2026-11-20", 2.00, 0.95), leg(1, "P", 90, "2026-11-20", 0.80, 0.35)])
    assert t["kind"] == "credit"
    assert t["capture"] == pytest.approx(0.50)
    assert t["recommendation"]["action"] == ev.TAKE_PROFIT


def test_credit_stop_when_loss_equals_the_credit():
    # $1.20 credit on a $10 wide spread, now costing $2.40 to close: loss = credit.
    t = run([leg(-1, "P", 100, "2026-11-20", 3.00, 5.40), leg(1, "P", 90, "2026-11-20", 1.80, 3.00)], spot=97)
    assert fired(t)["credit.stop"] == ev.CLOSE
    assert t["exit_levels"]["stop"]["basis"] == "credit"


def test_credit_stop_falls_back_to_half_max_loss_when_the_credit_stop_cannot_fire():
    # $3.50 credit on a $5 wide spread: max loss $150 < credit $350, so the stop is half of $150.
    t = run(
        [leg(-1, "C", 100, "2026-11-20", 5.00, 4.00), leg(1, "C", 105, "2026-11-20", 1.50, 1.50)], spot=99
    )
    assert t["exit_levels"]["stop"]["basis"] == "max_loss"
    assert t["exit_levels"]["stop"]["close_value"] == pytest.approx(-(350 + 75), abs=2)


def test_credit_time_exit_rolls_a_winner_and_closes_a_loser():
    winner = run([leg(-1, "P", 90, "2026-10-30", 1.00, 0.70), leg(1, "P", 85, "2026-10-30", 0.40, 0.30)])
    assert fired(winner)["credit.time_exit"] == ev.ROLL_OR_CLOSE
    loser = run([leg(-1, "P", 99, "2026-10-30", 1.00, 1.30), leg(1, "P", 94, "2026-10-30", 0.40, 0.45)])
    assert fired(loser)["credit.time_exit"] == ev.CLOSE


def test_breached_credit_trade_that_would_not_be_opened_today_is_closed():
    # An iron condor (4.24 taken in, under the 5-wide call wing, so not a jade lizard): put spread far
    # below, call spread fully in the money.
    t = run(
        [
            leg(1, "C", 725, "2026-11-20", 51.52, 63.80),
            leg(-1, "C", 720, "2026-11-20", 53.76, 67.425),
            leg(1, "P", 635, "2026-11-20", 8.00, 4.575),
            leg(-1, "P", 655, "2026-11-20", 10.00, 6.775),
        ],
        spot=762.47,
    )
    assert t["kind"] == "credit"
    assert t["recommendation"]["action"] == ev.CLOSE
    assert t["recommendation"]["rule"] == "credit.breached"


# --------------------------------------------------------------------------- debit trades
def test_debit_stop_at_minus_half():
    t = run(
        [leg(1, "P", 87.5, "2026-11-20", 3.06, 1.22), leg(-1, "P", 80, "2026-11-20", 0.53, 0.16)], spot=90
    )
    assert t["kind"] == "debit"
    assert t["recommendation"] == {**t["recommendation"], "action": ev.CLOSE, "rule": "debit.stop"}


def test_debit_take_profit_at_plus_three_quarters():
    t = run([leg(1, "C", 100, "2026-12-18", 4.00, 7.20)], spot=106)
    assert fired(t)["debit.take_profit"] == ev.TAKE_PROFIT


def test_long_only_trade_is_out_before_the_last_two_weeks():
    t = run([leg(1, "C", 100, "2026-10-23", 2.00, 1.80)], spot=99)
    assert fired(t)["debit.time_exit"] == ev.CLOSE


def test_short_leg_take_profit_buys_back_the_short_and_its_wing():
    # ENPH-shaped: long Jan call, 2x short Nov 50C at 82% captured, long Nov 60C wing.
    t = run(
        [
            leg(1, "C", 60, "2026-11-20", 0.63, 0.175),
            leg(1, "C", 35, "2027-01-15", 6.45, 3.225),
            leg(-2, "C", 50, "2026-11-20", 1.31, 0.24),
        ],
        spot=32.02,
    )
    assert t["recommendation"]["action"] == ev.CLOSE_LEGS
    assert {row["leg"] for row in t["order"]["legs"]} == {"-2 2026-11-20 50C", "+1 2026-11-20 60C"}
    assert t["order"]["net_side"] == "db"


# --------------------------------------------------------------------------- expiry
def test_physically_settled_legs_are_closed_before_expiry():
    t = run(
        [leg(-1, "P", 109, "2026-10-16", 3.61, 6.775), leg(1, "P", 107, "2026-10-16", 2.65, 5.175)],
        spot=102.84,
    )
    assert t["recommendation"]["action"] == ev.CLOSE
    assert t["recommendation"]["rule"] == "expiry"


def test_expired_cash_settled_trade_only_settles():
    legs = [
        leg(-2, "P", 7795, "2026-10-13", 4.85, 0.025, root="SPXW", underlying="SPX"),
        leg(2, "P", 7800, "2026-10-13", 5.725, 0.025, root="SPXW", underlying="SPX"),
    ]
    t = run(legs, spot=7805.45, underlying="SPX")
    assert [f["action"] for f in t["fired"]] == [ev.SETTLES]
    assert t["order"] is None


# --------------------------------------------------------------------------- grouping, sizing, overrides
def test_split_keeps_diagonals_whole_and_separates_independent_spreads():
    weekly = [leg(-1, "P", 352.5, "2026-10-16", 5.75, 3.75), leg(1, "P", 347.5, "2026-10-16", 4.05, 2.315)]
    monthly = [leg(-1, "C", 390, "2026-11-20", 13.27, 7.05), leg(1, "C", 380, "2026-11-20", 17.12, 9.625)]
    assert len(ev.split_trades(weekly + monthly, "expiry_chain")) == 2
    diagonal = [leg(-1, "P", 725, "2026-11-20", 7.15, 2.51), leg(1, "P", 750, "2026-12-18", 15.6, 8.4)]
    assert len(ev.split_trades(diagonal, "expiry_chain")) == 1
    assert len(ev.split_trades(weekly + monthly, "underlying")) == 1


def test_ex_dividend_flags_an_itm_short_call_with_less_time_value_than_the_dividend():
    legs = [leg(-1, "C", 145, "2026-11-20", 3.00, 4.10), leg(1, "C", 155, "2026-11-20", 0.50, 0.40)]
    t = run(legs, spot=149, metrics={"ex_dividend_date": "2026-10-20", "dividend_rate_per_share": "0.30"})
    assert fired(t)["ex_dividend"] == ev.REVIEW
    rich = run(legs, spot=149, metrics={"ex_dividend_date": "2026-10-20", "dividend_rate_per_share": "0.05"})
    assert "ex_dividend" not in fired(rich)


def test_oversize_flags_a_trade_whose_downside_dwarfs_the_budget():
    t = run([leg(-1, "P", 100, "2026-11-20", 3.00, 2.00)], spot=101, net_liq=10_000)
    assert fired(t)["risk.oversize"] == ev.REVIEW


def test_oversize_states_a_fractional_budget_as_it_is_set():
    # A 2.5% budget reads as 2.5%, not rounded to 2%.
    rules = _rules.effective({"risk": {"max_risk_pct": 0.025, "oversize_multiple": 1.0}})[0]
    legs = [leg(-1, "P", 95, "2026-11-20", 2.00, 0.95), leg(1, "P", 90, "2026-11-20", 0.80, 0.35)]
    t = run(legs, rules=rules, net_liq=10_000)
    reason = next(f["reason"] for f in t["fired"] if f["rule"] == "risk.oversize")
    assert "the $250 per-trade budget (2.5% of net liq)" in reason


def test_hold_carries_a_confidence_and_loses_some_to_earnings():
    legs = [leg(-1, "P", 90, "2026-11-20", 1.00, 0.80), leg(1, "P", 85, "2026-11-20", 0.40, 0.35)]
    plain = run(legs)
    assert plain["recommendation"]["action"] == ev.HOLD
    assert plain["recommendation"]["confidence"] == 70
    earn = run(legs, metrics={"earnings_date": "2026-10-27"})
    assert earn["recommendation"]["confidence"] == 60


def test_position_override_disables_a_rule():
    rules, warnings = _rules.effective({"positions": {"XYZ": {"intent": "hedge", "disable": ["debit.stop"]}}})
    assert warnings == []
    t = run([leg(1, "P", 87.5, "2026-11-20", 3.06, 1.22)], spot=90, rules=rules)
    assert "debit.stop" not in fired(t)
    assert t["intent"] == "hedge"


def test_unpriced_trade_is_reviewed_not_judged():
    bad = leg(1, "C", 100, "2026-11-20", 2.0, 1.0)
    bad.update(priced=False, value=None)
    t = run([bad])
    assert t["recommendation"]["action"] == ev.REVIEW
    assert t["pnl_gross"] is None


def test_evaluate_whole_snapshot_counts_actions():
    snap = {
        "session": "2026-10-13",
        "positions": {
            "ok": True,
            "accounts": [
                {
                    "account": "****1234",
                    "balances": {"net_liquidating_value": "50000"},
                    "underlyings": [
                        {"underlying": "XYZ", "legs": [leg(1, "P", 87.5, "2026-11-20", 3.06, 1.22)]},
                    ],
                }
            ],
        },
        "metrics": {"XYZ": {"last": 90}},
    }
    out = ev.evaluate(snap, _rules.effective(None)[0])
    assert out["accounts"][0]["counts"] == {ev.CLOSE: 1}
    assert out["accounts"][0]["risk_budget"] == pytest.approx(1000)
