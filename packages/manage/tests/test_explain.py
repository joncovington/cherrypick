from test_evaluate import leg, run

from cherrypick.manage import evaluate as ev


def test_every_trade_carries_an_explanation_and_its_opening():
    t = run(
        [leg(1, "P", 87.5, "2026-11-20", 3.06, 1.22), leg(-1, "P", 80, "2026-11-20", 0.53, 0.16)], spot=90
    )
    assert t["opening"] == {"net": -253.0, "net_price": 2.53, "net_side": "db"}
    text = t["explanation"]
    assert text.startswith("You paid $253 for this, and it is worth $106 now, a loss of $147 (58%).")
    assert "lost half" in text and text.endswith("Close it.")


def test_a_credit_opening_is_a_credit_received():
    t = run([leg(-1, "P", 95, "2026-11-20", 2.00, 0.95), leg(1, "P", 90, "2026-11-20", 0.80, 0.35)])
    assert t["opening"]["net_side"] == "cr"
    assert t["opening"]["net_price"] == 1.2
    assert "You took in $120" in t["explanation"]


def test_a_hold_says_what_it_is_waiting_for():
    t = run([leg(-1, "P", 90, "2026-11-20", 1.00, 0.80), leg(1, "P", 85, "2026-11-20", 0.40, 0.35)])
    assert t["recommendation"]["action"] == ev.HOLD
    text = t["explanation"]
    assert text.startswith("Nothing to do today.")
    assert "take the profit once buying it back costs $30 or less" in text
    assert "cut it if buying it back reaches $120" in text
    assert "on Oct 30 whatever happens" in text


def test_a_partial_close_names_its_legs_and_their_price():
    t = run(
        [
            leg(1, "C", 60, "2026-11-20", 0.63, 0.175),
            leg(1, "C", 35, "2027-01-15", 6.45, 3.225),
            leg(-2, "C", 50, "2026-11-20", 1.31, 0.24),
        ],
        spot=32.02,
        metrics={"earnings_date": "2026-10-27"},
    )
    text = t["explanation"]
    assert "Close only these legs: -2 2026-11-20 50C, +1 2026-11-20 60C." in text
    assert "Closing at mid would cost about $30." in text
    assert text.endswith("Earnings are due Oct 27 (14 days), inside the trade.")


def test_a_time_exit_on_a_market_holiday_moves_to_the_trading_day_before():
    # 21 days before 2027-01-15 is 2026-12-25, Christmas.
    t = run([leg(-1, "C", 85, "2027-01-15", 0.50, 0.30), leg(1, "C", 80, "2029-01-19", 6.22, 6.20)], spot=78)
    assert t["exit_levels"]["time_exit"]["date"] == "2026-12-24"
