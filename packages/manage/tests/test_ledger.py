"""The ledger (ledger.py): each name's earnings by source and window, read from the whole history.

Puts and calls are an option leg's flat-to-flat net after fees on its closing day; shares a stock
span's; dividends on their day. Nothing still held is kept. The live loops' 0DTE legs and history that
begins mid-position are counted aside. Synthetic names and rows, as in test_premium.py.
"""

from datetime import date

import pytest
from test_premium import row

from cherrypick.manage import ledger
from cherrypick.manage import rules as _rules

SESSION = date(2026, 11, 4)
PUT = "AAA   261120P00045000"
CALL = "AAA   261218C00055000"


def share(order, at, action, qty, value, fee=0.0, name="AAA"):
    r = row(order, at, action, name, qty, value, fee=fee, underlying=name)
    r["instrument_type"] = "Equity"
    r["underlying_symbol"] = None
    return r


def dividend(at, amount, name="AAA"):
    return {
        "executed_at": at,
        "transaction_type": "Money Movement",
        "transaction_sub_type": "Dividend",
        "action": None,
        "symbol": name,
        "underlying_symbol": None,
        "instrument_type": "Equity",
        "quantity": None,
        "value": str(amount),
        "net_value": str(amount),
    }


def read(rows):
    return ledger.reading(rows, None, SESSION, _rules.DEFAULT_RULES)


def name(out, n):
    return next(x for x in out["names"] if x["underlying"] == n)["windows"]


def test_puts_calls_shares_and_dividends_each_once_and_they_add_up():
    rows = [
        # A put sold and assigned: its premium is the put's; the shares bought at the strike are the stock's.
        row(1, "2026-08-03T15:00:00Z", "Sell to Open", PUT, 1, 150.0),
        row(
            None,
            "2026-08-21T22:00:00Z",
            "Buy to Close",
            PUT,
            1,
            0.0,
            fee=0.0,
            sub="Assignment",
            kind="Receive Deliver",
        ),
        share(None, "2026-08-24T12:00:00Z", "Buy to Open", 100, -4500.0),
        # A call sold against them, bought back; the shares sold above the strike.
        row(2, "2026-09-02T15:00:00Z", "Sell to Open", CALL, 1, 80.0),
        row(3, "2026-10-01T15:00:00Z", "Buy to Close", CALL, 1, -20.0),
        share(4, "2026-10-01T15:05:00Z", "Sell to Close", 100, 4700.0, fee=0.5),
        dividend("2026-09-15T12:00:00Z", 26.0),
    ]
    out = read(rows)
    a = name(out, "AAA")["ALL"]
    assert a["puts"] == 149.0 and a["calls"] == 58.0 and a["shares"] == 199.5 and a["dividends"] == 26.0
    assert a["net"] == pytest.approx(149.0 + 58.0 + 199.5 + 26.0) and a["fees"] == pytest.approx(-3.5)
    assert out["windows"]["ALL"]["net"] == a["net"]
    # The months hold the same money, each on its closing day.
    by = {m["month"]: m for m in out["months"]}
    assert (
        by["2026-08"]["puts"] == 149.0
        and by["2026-10"]["calls"] == 58.0
        and by["2026-09"]["dividends"] == 26.0
    )
    assert sum(m["net"] for m in out["months"]) == pytest.approx(a["net"])
    hows = {(r["right"], r["how"]) for r in out["lots"]}
    assert (
        ("P", "assigned") in hows
        and ("C", "bought back") in hows
        and ("S", "sold") in hows
        and ("D", "dividend") in hows
    )


def test_windows_count_back_from_the_session_by_the_closing_day():
    rows = [
        row(1, "2026-03-02T15:00:00Z", "Sell to Open", "AAA   260320P00045000", 1, 100.0),
        row(
            None,
            "2026-03-20T22:00:00Z",
            "Buy to Close",
            "AAA   260320P00045000",
            1,
            0.0,
            fee=0.0,
            sub="Expiration",
            kind="Receive Deliver",
        ),
        row(2, "2026-10-20T15:00:00Z", "Sell to Open", PUT, 1, 50.0),
        row(3, "2026-10-29T15:00:00Z", "Buy to Close", PUT, 1, -10.0),
    ]
    w = name(read(rows), "AAA")
    assert w["1M"]["puts"] == 38.0 and w["3M"]["puts"] == 38.0
    assert w["YTD"]["puts"] == 137.0 and w["ALL"]["puts"] == 137.0 and w["ALL"]["legs"] == 2


def test_nothing_still_held_is_kept_and_it_is_listed_apart():
    out = read([row(1, "2026-10-20T15:00:00Z", "Sell to Open", PUT, 1, 150.0)])
    assert out["names"] == [] and out["windows"]["ALL"]["net"] == 0.0
    (o,) = out["open"]
    assert o["symbol"] == PUT and o["right"] == "P" and o["net"] == 149.0


def test_the_live_loops_0dte_legs_are_counted_aside():
    spx = "SPXW  261103P07600000"
    rows = [
        row(1, "2026-11-03T15:00:00Z", "Sell to Open", spx, 1, 300.0, underlying="SPX"),
        row(
            None,
            "2026-11-03T21:00:00Z",
            "Buy to Close",
            spx,
            1,
            0.0,
            fee=0.0,
            sub="Expiration",
            kind="Receive Deliver",
            underlying="SPX",
        ),
    ]
    out = read(rows)
    assert (
        out["names"] == []
        and out["excluded"]["zero_dte"]["legs"] == 1
        and out["excluded"]["zero_dte"]["net"] == 299.0
    )


def test_no_history_says_so():
    out = ledger.reading(None, "HTTP 503", SESSION, _rules.DEFAULT_RULES)
    assert out["error"] == "HTTP 503" and out["names"] == []
