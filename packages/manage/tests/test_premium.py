"""The premium pace (premium.py): theta against its target, premium collected, premium kept.

Collected is an opening order's net credit in the month it filled (a roll at its net, not the new
short's price); kept is an option leg's flat-to-flat net cash after fees in the month it closed, by
any route out (bought back, expired, assigned, cash settled). History that begins mid-position and
the live loops' 0DTE legs are counted aside, never folded in.
"""

from datetime import date

from cherrypick.manage import premium
from cherrypick.manage import rules as _rules

SESSION = date(2026, 10, 30)  # a Friday; its week starts 10-26
BOOK = {"theta_ex_front": 20.0, "theta": 25.0}


def row(order, at, action, symbol, qty, value, fee=1.0, sub=None, kind="Trade", underlying="AAA"):
    sub = sub or action
    return {
        "order_id": order,
        "executed_at": at,
        "transaction_type": kind,
        "transaction_sub_type": sub,
        "action": action,
        "symbol": symbol,
        "underlying_symbol": underlying,
        "instrument_type": "Index Option" if underlying == "SPX" else "Equity Option",
        "quantity": str(qty),
        "value": str(value),
        "net_value": str(value - fee),
    }


SHORT = "AAA   261120P00045000"
LONG = "AAA   261120P00040000"


def spread_opened_in_september():
    return [
        row(1, "2026-09-15T15:00:00Z", "Sell to Open", SHORT, 1, 150.0),
        row(1, "2026-09-15T15:00:00Z", "Buy to Open", LONG, 1, -50.0),
    ]


def read(rows, **kw):
    return premium.reading(
        rows, None, SESSION, kw.get("book", BOOK), kw.get("net_liq", 10_000.0), _rules.DEFAULT_RULES
    )


def month(out, m):
    return next(x for x in out["months"] if x["month"] == m)


def test_collected_in_the_opening_month_and_kept_in_the_closing_month_after_fees():
    rows = spread_opened_in_september() + [
        row(2, "2026-10-28T15:00:00Z", "Buy to Close", SHORT, 1, -60.0),
        row(2, "2026-10-28T15:00:00Z", "Sell to Close", LONG, 1, 10.0),
    ]
    out = read(rows)
    assert month(out, "2026-09")["collected"] == 100.0 and month(out, "2026-09")["orders"] == 1
    assert month(out, "2026-09")["kept"] == 0.0  # nothing closed yet in September
    # 150 - 50 - 60 + 10 = 50, less four fees of 1
    assert month(out, "2026-10")["kept"] == 46.0 and month(out, "2026-10")["closed"] == 2
    assert out["week"] == {"from": "2026-10-26", "collected": 0.0, "orders": 0, "kept": 46.0, "closed": 2}
    assert [m["month"] for m in out["months"]] == [
        "2026-05",
        "2026-06",
        "2026-07",
        "2026-08",
        "2026-09",
        "2026-10",
    ]


def test_an_expiry_keeps_the_whole_credit_less_fees():
    rows = spread_opened_in_september() + [
        row(
            None,
            "2026-10-17T20:00:00Z",
            "Buy to Close",
            SHORT,
            1,
            0.0,
            fee=0.0,
            sub="Expiration",
            kind="Receive Deliver",
        ),
        row(
            None,
            "2026-10-17T20:00:00Z",
            "Sell to Close",
            LONG,
            1,
            0.0,
            fee=0.0,
            sub="Expiration",
            kind="Receive Deliver",
        ),
    ]
    assert month(read(rows), "2026-10")["kept"] == 98.0


def test_a_roll_collects_its_net_credit_not_the_new_shorts_price():
    nov = "AAA   261218P00045000"
    rows = [
        row(1, "2026-10-01T15:00:00Z", "Sell to Open", SHORT, 1, 120.0),
        row(2, "2026-10-20T15:00:00Z", "Buy to Close", SHORT, 1, -100.0),
        row(2, "2026-10-20T15:00:00Z", "Sell to Open", nov, 1, 150.0),
    ]
    out = read(rows)
    assert month(out, "2026-10")["collected"] == 170.0  # 120 opened + the roll's 50
    assert month(out, "2026-10")["orders"] == 2
    assert (
        month(out, "2026-10")["kept"] == 18.0
    )  # the first short: 120 - 100 - 2 fees; the roll's new short is open


def test_a_debit_opening_is_not_collected():
    rows = [row(1, "2026-10-05T15:00:00Z", "Buy to Open", LONG, 1, -200.0)]
    assert month(read(rows), "2026-10")["collected"] == 0.0


def test_a_cash_settled_exercise_settles_into_its_own_lot():
    spx = "SPXW  261016P07700000"
    rows = [
        row(1, "2026-10-01T15:00:00Z", "Buy to Open", spx, 1, -500.0, underlying="SPX"),
        row(
            None,
            "2026-10-16T21:00:00Z",
            None,
            spx,
            1,
            0.0,
            fee=0.0,
            sub="Exercise",
            kind="Receive Deliver",
            underlying="SPX",
        ),
        row(
            None,
            "2026-10-16T21:00:00Z",
            None,
            spx,
            1,
            700.0,
            fee=5.0,
            sub="Cash Settled Exercise",
            kind="Receive Deliver",
            underlying="SPX",
        ),
    ]
    out = read(rows)
    assert month(out, "2026-10")["kept"] == 194.0  # -500 - 1 + 700 - 5; not 0DTE, so it counts
    assert out["zero_dte"]["legs"] == 0


def test_an_assignment_closes_the_short_at_its_premium():
    rows = [
        row(1, "2026-09-02T15:00:00Z", "Sell to Open", SHORT, 1, 80.0),
        row(
            None,
            "2026-10-16T21:00:00Z",
            None,
            SHORT,
            1,
            0.0,
            fee=0.0,
            sub="Assignment",
            kind="Receive Deliver",
        ),
    ]
    assert month(read(rows), "2026-10")["kept"] == 79.0


def test_history_that_begins_mid_position_is_counted_aside():
    rows = [row(9, "2026-10-02T15:00:00Z", "Buy to Close", SHORT, 1, -40.0)]
    out = read(rows)
    assert month(out, "2026-10")["kept"] == 0.0
    assert out["unmatched"] == {"legs": 1, "net": -41.0}


def test_the_live_loops_zero_dte_legs_are_counted_aside():
    spx = "SPXW  261021C07000000"
    rows = [
        row(1, "2026-10-21T14:00:00Z", "Sell to Open", spx, 1, 300.0, underlying="SPX"),
        row(2, "2026-10-21T19:00:00Z", "Buy to Close", spx, 1, -400.0, underlying="SPX"),
    ]
    out = read(rows)
    assert month(out, "2026-10")["collected"] == 0.0 and month(out, "2026-10")["kept"] == 0.0
    assert out["zero_dte"] == {"legs": 1, "net": -102.0, "underlyings": ["SPX"]}


def test_the_target_is_a_share_of_net_liq_on_the_bands_own_theta():
    out = read([], net_liq=10_000.0)
    assert out["target"] == {"pct_nlv": 0.002, "dollars": 20.0, "theta": 20.0, "ratio": 1.0}


def test_no_history_still_states_the_target_and_says_why():
    out = premium.reading(None, None, SESSION, BOOK, 10_000.0, _rules.DEFAULT_RULES)
    assert out["error"] == "no account-wide history in this snapshot"
    assert out["months"] == [] and out["target"]["dollars"] == 20.0
    failed = premium.reading([], "meic: HTTP 503", SESSION, BOOK, 10_000.0, _rules.DEFAULT_RULES)
    assert failed["error"] == "meic: HTTP 503"
