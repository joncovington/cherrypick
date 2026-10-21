from datetime import date

import pytest

from cherrypick.manage import evaluate as ev
from cherrypick.manage import pricing
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


# --------------------------------------------------------------------------- a leg's IV
def test_a_wide_quote_takes_the_brokers_iv_and_a_tight_one_its_own():
    # 2026-10-21 before the open: NFLX's 70C was bid 0.01 ask 1.50, and the IV solved off that mid
    # sat 55 points under its spread partner's; the broker's surface had the two within 15.
    rules = _rules.effective(None)[0]
    at_042 = pricing.price(100.0, 105, 38 / 365.0, 0.42, "C", float(rules["pricing"]["rate"]))
    wide = leg(-1, "C", 105, "2026-11-20", 2.00, 1.50) | {
        "wide": True,
        "bid": at_042 - 0.6,
        "ask": at_042 + 0.6,
    }
    tight = leg(1, "C", 110, "2026-11-20", 1.00, 0.60)
    broker = {wide["symbol"]: {"iv": 0.42}, tight["symbol"]: {"iv": 0.99}}
    t = ev.evaluate_trade("XYZ", [wide, tight], {"last": 100.0}, SESSION, 1e5, rules, broker_greeks=broker)
    legs = {x["symbol"]: x for x in t["legs"]}
    assert legs[wide["symbol"]]["iv"] == 0.42 and legs[wide["symbol"]]["iv_source"] == "broker"
    # A tight quote keeps the IV solved from its own mid, whatever the broker says.
    assert legs[tight["symbol"]]["iv_source"] == "mid" and legs[tight["symbol"]]["iv"] != pytest.approx(0.99)
    # No broker IV for a wide leg: its own mid, as before.
    alone = ev.evaluate_trade("XYZ", [wide], {"last": 100.0}, SESSION, 1e5, rules)
    assert alone["legs"][0]["iv_source"] == "mid" and alone["legs"][0]["iv"] != pytest.approx(0.42)


def test_a_brokers_iv_that_cannot_price_the_quote_at_our_spot_is_not_taken():
    # 2026-10-21, 16:35 ET on META's report night: the broker's IV for the long 705C priced it at
    # $10.40 at the snapshot's spot against a 21.80/24.40 quote (its greeks were on the after-hours
    # print), and beside the short 712.5C solved from its mid the bull call spread read a negative
    # delta. Both legs now solve from their mids, and the long's delta is the larger.
    rules = _rules.effective(None)[0]
    long_ = leg(1, "C", 705, "2026-10-26", 26.37, 23.10, "META", "META") | {
        "bid": 21.80,
        "ask": 24.40,
        "wide": True,
    }
    short = leg(-1, "C", 712.5, "2026-10-26", 22.72, 19.275, "META", "META") | {"bid": 18.80, "ask": 19.75}
    broker = {long_["symbol"]: {"iv": 0.4186}, short["symbol"]: {"iv": 0.4679}}
    session = date(2026, 10, 21)
    t = ev.evaluate_trade("META", [long_, short], {"last": 697.41}, session, 1e5, rules, broker_greeks=broker)
    legs = {x["symbol"]: x for x in t["legs"]}
    assert legs[long_["symbol"]]["iv_source"] == "mid"
    assert legs[long_["symbol"]]["delta"] > legs[short["symbol"]]["delta"]
    # The same leg with no quote to check against keeps its own mid too.
    bare = {k: v for k, v in long_.items() if k not in ("bid", "ask")}
    t = ev.evaluate_trade("META", [bare], {"last": 697.41}, session, 1e5, rules, broker_greeks=broker)
    assert t["legs"][0]["iv_source"] == "mid"


def test_a_mid_under_the_models_floor_leaves_the_leg_unmodelled():
    # TLT's 2029-01-19 80C on 2026-10-21: mid 7.10 with TLT at 80.25, under the no-dividend model's
    # floor of about 10.00 (the fund pays ~4% a year). The solver ran to its lower bound and the leg
    # read IV 0 and delta 1.00 against the broker's 0.55. It is now left unmodelled, not given a delta.
    t = run([leg(1, "C", 80, "2029-01-19", 6.22, 7.10, "TLT", "TLT")], spot=80.25, underlying="TLT")
    x = t["legs"][0]
    assert x["iv"] is None and x["delta"] is None and x["iv_source"] == "unpriceable"
    # A deep in-the-money leg worth only its intrinsic is a real zero-extrinsic reading and keeps it.
    t = run([leg(-1, "P", 120, "2026-11-20", 20.0, 20.0)], spot=100.0)
    assert t["legs"][0]["iv_source"] == "mid" and t["legs"][0]["delta"] == pytest.approx(-1.0, abs=1e-6)


# --------------------------------------------------------------------------- credit trades
def test_credit_take_profit_at_half_the_credit():
    t = run([leg(-1, "P", 95, "2026-11-20", 2.00, 0.95), leg(1, "P", 90, "2026-11-20", 0.80, 0.35)])
    assert t["kind"] == "credit"
    assert t["capture"] == pytest.approx(0.50)
    assert t["recommendation"]["action"] == ev.TAKE_PROFIT


def test_the_payoff_profile_is_stated_as_pnl_to_the_cent():
    # $1.20 credit on a $5 wide put spread: at expiry it can lose $380 from open and win the $120.
    t = run([leg(-1, "P", 95, "2026-11-20", 2.00, 0.95), leg(1, "P", 90, "2026-11-20", 0.80, 0.35)])
    pf = t["profile"]
    assert pf["max_loss_from_open"] == -380.0 and pf["max_gain_from_open"] == 120.0
    for k in (
        "min_value",
        "max_value",
        "max_loss_from_open",
        "max_gain_from_open",
        "downside_from_now",
        "upside_from_now",
    ):
        assert pf[k] == round(pf[k], 2)


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
    assert out["pre_open"] is None  # a snapshot with no time is never called pre-open


def _expiring(mark):
    legs = [
        {"expiration": "2026-10-21", "mark": mark},
        {"expiration": "2026-11-20", "mark": None},  # unquoted, but not expiring today
    ]
    return [
        {
            "trades": [
                {"trade_id": "SPCX", "underlying": "SPCX", "legs": legs},
                {"underlying": "SPY", "legs": []},
            ]
        }
    ]


def test_a_snapshot_taken_before_the_open_says_so_in_new_york_time():
    day = date(2026, 10, 21)
    # 05:17 in Denver is 07:17 in New York: before the open, and today's SPCX leg has no mark.
    out = ev.pre_open("2026-10-21T05:17:04-0600", day, _expiring(None))
    assert out == {"taken_at": "07:17", "expiring_unpriced": ["SPCX"]}
    # 07:29 in Denver is 09:29 in New York, still before the open; a quoted leg is not listed.
    assert ev.pre_open("2026-10-21T07:29:59-0600", day, _expiring(1.2)) == {
        "taken_at": "09:29",
        "expiring_unpriced": [],
    }
    assert ev.pre_open("2026-10-21T07:30:00-0600", day, _expiring(None)) is None


def test_a_snapshot_after_the_open_or_from_another_day_is_not_pre_open():
    day = date(2026, 10, 21)
    assert ev.pre_open("2026-10-21T09:30:00-0400", day, _expiring(None)) is None
    assert ev.pre_open("2026-10-20T23:00:00-0400", day, _expiring(None)) is None
    assert ev.pre_open("2026-10-21T09:29:00-0400", day, _expiring(2.0)) == {
        "taken_at": "09:29",
        "expiring_unpriced": [],
    }
    assert ev.pre_open("not a time", day, []) is None
