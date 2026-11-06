"""A diagonal or calendar whose short is written week by week runs on the long's clock (user, 2026-11-06).

The 21-day time exit counted on the nearest short fires the day a one-week short is sold: such a
diagonal was told to roll or close on its first morning, with a time-exit date before it was opened.
"""

from datetime import date

from test_evaluate import fired, leg

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules

RULES = _rules.effective(None)[0]
LONG = "2027-01-15"
SHORT = "2026-12-04"
SOLD = "2026-11-25"


def occ(exp: str, strike: float) -> str:
    return f"XYZ   {exp.replace('-', '')[2:]}C{int(strike * 1000):08d}"


def opened(on: str = SOLD, short_exp: str = SHORT, long_strike: float = 95, strike: float = 110) -> dict:
    """The order chain: one order that bought the long and sold the short on `on`."""
    return {
        "cash": -1000.0,
        "fees": -2.25,
        "orders": [
            {
                "executed_at": f"{on}T20:25:36Z",
                "role": "open",
                "net": -1000.0,
                "fees": -2.25,
                "legs": [
                    {"symbol": occ(LONG, long_strike), "action": "Buy to Open", "quantity": 1, "price": 12.0},
                    {"symbol": occ(short_exp, strike), "action": "Sell to Open", "quantity": 1, "price": 2.0},
                ],
            }
        ],
    }


def diagonal(short_mid=2.6, long_mid=14.0, short_exp=SHORT, strike=110):
    return [leg(1, "C", 95, LONG, 12.0, long_mid), leg(-1, "C", strike, short_exp, 2.0, short_mid)]


def chains(mid=4.80):
    """The snapshot's roll-out chains for the short: Dec 11 and Dec 18, the 110 strike quoted."""

    def chain(exp, m):
        return {
            "underlying": "XYZ",
            "right": "C",
            "expiration": exp,
            "options": [{"strike": 110.0, "right": "C", "bid": m - 0.05, "ask": m + 0.05}],
        }

    return {
        "taken_at": "2026-12-01T10:00:00-0500",
        "chains": {
            "XYZ|C|2026-12-11": chain("2026-12-11", mid),
            "XYZ|C|2026-12-18": chain("2026-12-18", 5.6),
        },
    }


def run(session, legs=None, chain=None, roll_out=None, rules=RULES, spot=105.0):
    return ev.evaluate_trade(
        "XYZ",
        legs or diagonal(),
        {"last": spot},
        session,
        100_000.0,
        rules,
        chain,
        roll_out=roll_out,
    )


def test_a_weekly_short_runs_on_the_longs_clock():
    t = run(date(2026, 11, 30), chain=opened())
    assert t["short_term_short"] == {"expiration": SHORT, "sold_on": SOLD, "days": 9}
    assert "debit.time_exit" not in fired(t)
    # Jan 15 less 21 days is Christmas Day: the trading day before it.
    assert t["exit_levels"]["time_exit"]["date"] == "2026-12-24"
    assert t["recommendation"]["action"] == ev.HOLD


def test_without_a_chain_the_same_trade_keeps_the_shorts_clock_and_fires_on_day_one():
    # The guard shown failing: with the sale date unknown, the old behaviour stands.
    t = run(date(2026, 11, 30), chain=None)
    assert t["short_term_short"] is None
    assert fired(t)["debit.time_exit"] == ev.ROLL_OR_CLOSE
    assert t["exit_levels"]["time_exit"]["date"] < SOLD


def test_a_short_sold_a_month_out_keeps_the_21_day_exit():
    legs = diagonal(short_exp="2026-12-18")
    t = run(date(2026, 11, 30), legs=legs, chain=opened("2026-11-13", short_exp="2026-12-18"))
    assert t["short_term_short"] is None  # sold 35 days before its expiry
    assert "debit.time_exit" in fired(t)


def test_the_long_inside_21_days_fires_the_time_exit():
    t = run(
        date(2026, 12, 28), legs=diagonal(short_exp="2027-01-08"), chain=opened("2026-12-28", "2027-01-08")
    )
    assert fired(t)["debit.time_exit"] == ev.ROLL_OR_CLOSE
    assert "the long is 18 DTE" in next(f["reason"] for f in t["fired"] if f["rule"] == "debit.time_exit")
    assert "runs on its long's clock" in t["explanation"]


def test_at_the_expiry_close_the_weekly_short_rolls_a_week_out_on_the_quoted_chain():
    t = run(date(2026, 12, 1), chain=opened(), roll_out=chains())
    assert fired(t)["expiry"] == ev.CLOSE_LEGS
    rec = t["recommendation"]
    assert rec["action"] == ev.ROLL_FOR_CREDIT and rec["rule"] == "roll.diagonal_short"
    assert "expiry" in rec["replaces"]
    roll = t["roll"]
    assert roll["expiration"] == "2026-12-11" and roll["modelled"] is False
    assert abs(roll["credit"] - (4.80 - 2.6)) < 1e-9
    assert any("(quoted mid)" in row for row in roll["legs"])
    assert "at today's quoted mids" in t["explanation"]


def test_without_quoted_chains_the_weekly_roll_falls_back_to_the_monthly_and_is_modelled():
    t = run(date(2026, 12, 1), chain=opened())
    roll = t["roll"]
    assert roll["expiration"] == "2026-12-18" and roll["modelled"] is True


def test_a_weekly_roll_that_would_outlast_the_longs_exit_is_blocked():
    # A short expiring Jan 8 rolls to Jan 15 at the earliest: the long's own expiry.
    legs = diagonal(short_exp="2027-01-08")
    t = run(date(2027, 1, 5), legs=legs, chain=opened("2026-12-28", "2027-01-08"))
    assert t["roll_blocked"]["reason"] == "long_too_short" and t["roll_blocked"]["min_days"] == 21


def test_a_calendar_written_weekly_runs_on_the_longs_clock_too():
    legs = [leg(1, "C", 110, LONG, 6.0, 6.4), leg(-1, "C", 110, SHORT, 2.0, 2.2)]
    t = run(date(2026, 11, 30), legs=legs, chain=opened(long_strike=110))
    assert t["shape"] == "calendar"
    assert t["short_term_short"]["days"] == 9
    assert "debit.time_exit" not in fired(t)


def test_a_roll_counts_from_the_order_that_sold_the_current_short():
    c = opened("2026-11-13", short_exp="2026-12-18")
    c["orders"].append(
        {
            "executed_at": "2026-12-28T15:00:00Z",
            "role": "roll",
            "net": 150.0,
            "fees": -2.0,
            "legs": [
                {"symbol": occ("2026-12-18", 110), "action": "Buy to Close", "quantity": 1, "price": 1.0},
                {"symbol": occ("2027-01-08", 110), "action": "Sell to Open", "quantity": 1, "price": 2.5},
            ],
        }
    )
    t = run(date(2026, 12, 29), legs=diagonal(short_exp="2027-01-08"), chain=c)
    assert t["short_term_short"] == {"expiration": "2027-01-08", "sold_on": "2026-12-28", "days": 11}
