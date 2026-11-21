"""The cost-strike call (costcall.py): the call at a wheel cycle's cost, shown when it bids 2% of the
stock's price. Synthetic names and prices, as in test_wheel.py."""

import copy
from datetime import date

import pytest
from test_wheel import assigned, occ, opt_leg, share_leg, tx

from cherrypick.manage import costcall, remark, stream_request
from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules

SESSION = "2026-10-13"
MONTHLY = "2026-11-20"  # the third Friday, 38 days out
WEEKLY = "2026-11-13"
# A put sold for 1.00 and assigned at 50: the shares cost 49.00 once the premium is counted, 49.061 with
# the 6.10 of fees the history carries (the wheel basis counts them).
ASSIGNED = [
    tx(1, "2026-09-01T15:00:00Z", "Sell to Open", occ("2026-10-09", "P", 50), 1, 1.00),
    *assigned("2026-10-09T22:00:00Z", occ("2026-10-09", "P", 50), 1, 50),
]
COST = 49.061


def call(strike, bid, ask, expiry=MONTHLY):
    return {
        "symbol": occ(expiry, "C", strike),
        "strike": strike,
        "right": "C",
        "bid": bid,
        "ask": ask,
        "mid": None if bid is None or ask is None else (bid + ask) / 2,
    }


def chain(*options, error=None):
    return {"quotes_complete": True, "options": list(options), "error": error}


def cost_call(chains, quotes=None):
    return {
        "taken_at": "2026-10-13T10:00:00-0400",
        "names": {"XYZ": {"instance": "main", "chains": chains}},
        "quotes": quotes or {},
    }


# Spot 49: 2% is 0.98.
OPTIONS = [call(48, 2.10, 2.20), call(49, 1.60, 1.70), call(50, 1.20, 1.30), call(51, 0.60, 0.70)]


def evaluate(legs=None, history=None, spot=49.0, cc=None, overrides=None, rules=None):
    acct = {
        "account": "****1234",
        "designated": True,
        "underlyings": [
            {"underlying": "XYZ", "legs": legs if legs is not None else [share_leg(100, spot, 50.0)]}
        ],
        "balances": {"net_liquidating_value": 100_000},
    }
    snap = {
        "session": SESSION,
        "positions": {"ok": True, "accounts": [acct]},
        "metrics": {"XYZ": {"last": spot}},
        "transactions": {"XYZ": ASSIGNED if history is None else history},
        "working_orders": [],
    }
    if cc is not None:
        snap["cost_call"] = cc
    return ev.evaluate(snap, rules or _rules.effective(overrides)[0])["accounts"][0]["attention"]["cost_call"]


def only(reading):
    (row,) = reading["rows"]
    return row


# --------------------------------------------------------------------------- the decision
def test_the_cost_strike_call_that_bids_two_percent_is_flagged():
    row = only(evaluate(cc=cost_call({MONTHLY: chain(*OPTIONS)})))
    assert row["effective_cost"] == pytest.approx(COST)
    # The lowest listed strike at or above the 49.061 cost is 50: 49 is under it, 51 above.
    assert (row["strike"], row["expiration"], row["window"], row["dte"]) == (50, MONTHLY, "standard", 38)
    assert row["bid"] == 1.20 and row["bid_pct"] == pytest.approx(1.2 / 49, abs=1e-5)
    assert row["status"] == "pays" and row["pays"] is True
    assert row["source"] == "snapshot chain"
    # Whole-position dollars: one contract of 1.20.
    assert row["contracts"] == 1 and row["credit"] == 120.0


def test_a_bid_under_the_bar_is_below_not_flagged():
    options = [call(49, 0.90, 1.10), call(50, 0.90, 1.10), call(51, 0.40, 0.50)]
    row = only(evaluate(cc=cost_call({MONTHLY: chain(*options)})))
    assert row["status"] == "below" and row["pays"] is False and row["bid"] == 0.90


def test_a_bid_of_exactly_the_bar_pays():
    row = only(evaluate(cc=cost_call({MONTHLY: chain(call(50, 0.98, 1.10))})))
    assert row["status"] == "pays"


def test_the_bar_and_the_window_are_the_rulesets():
    cc = cost_call({MONTHLY: chain(*OPTIONS)})
    strict = evaluate(cc=cc, overrides={"attention": {"cost_call": {"min_bid_pct": 0.03}}})
    assert only(strict)["status"] == "below"
    narrow = evaluate(cc=cc, overrides={"attention": {"cost_call": {"dte_max": 35, "fallback_max": 35}}})
    assert only(narrow)["status"] == "no quote"
    off = evaluate(cc=cc, overrides={"attention": {"cost_call": {"enabled": False}}})
    assert off["enabled"] is False and off["rows"] == []


def test_the_stream_caches_bid_beats_the_chains_and_says_so():
    sym = ".XYZ261120C50"
    row = only(
        evaluate(cc=cost_call({MONTHLY: chain(*OPTIONS)}, {sym: {"bid": 0.50, "ask": 0.60, "mid": 0.55}}))
    )
    assert row["bid"] == 0.50 and row["source"] == "stream cache" and row["status"] == "below"
    assert row["symbol"] == sym


# --------------------------------------------------------------------------- no quote is never "below"
@pytest.mark.parametrize(
    "cc, why",
    [
        (None, "no chain"),
        ({"taken_at": "x", "names": {}, "quotes": {}}, "no chain"),
        (cost_call({}), "monthly"),
        (cost_call({WEEKLY: chain(*OPTIONS)}), "monthly"),
        (cost_call({MONTHLY: chain(error="boom")}), "boom"),
        (cost_call({MONTHLY: chain(call(47, 3.0, 3.1), call(48, 2.0, 2.1))}), "tops out at 48"),
        (cost_call({MONTHLY: chain(call(50, None, 1.30), call(51, 0.8, 0.9))}), "no bid"),
    ],
)
def test_a_missing_quote_reads_no_quote_never_below(cc, why):
    row = only(evaluate(cc=cc))
    assert row["status"] == "no quote" and row["pays"] is False and row["bid"] is None
    assert why in row["why"]


def test_a_chain_cut_off_above_the_cost_is_no_quote_but_a_short_one_is_not():
    # 40 strikes either side of 49, as the snapshot reads: the low side is full, so a strike under the
    # lowest read may be listed. A cost near 7 sits under the lowest read, 10.
    full = [call(k, 40.0 - k + 1, 40.0 - k + 1.1) for k in range(10, 60)]
    cc = {**cost_call({MONTHLY: chain(*full)}), "window_strikes": 40}
    cheap = [  # assigned at 8, a put sold for 1.00
        tx(1, "2026-09-01T15:00:00Z", "Sell to Open", occ("2026-10-09", "P", 8), 1, 1.00),
        *assigned("2026-10-09T22:00:00Z", occ("2026-10-09", "P", 8), 1, 8),
    ]
    row = only(evaluate(history=cheap, cc=cc))
    assert row["status"] == "no quote" and "not in it" in row["why"] and row["strike"] is None
    # The same chain with the window short of full (the listing ended there) is the lowest listed.
    short = {**cost_call({MONTHLY: chain(call(51, 0.9, 1.0), call(52, 0.5, 0.6))}), "window_strikes": 40}
    row = only(evaluate(cc=short))
    assert row["strike"] == 51 and row["status"] == "below"


def test_no_price_for_the_stock_is_no_quote():
    acct_rules = _rules.effective(None)[0]
    cycle = {"underlying": "XYZ", "wheel_basis": 49.0, "shares": 100}
    row = costcall.row_for(
        cycle,
        None,
        cost_call({MONTHLY: chain(*OPTIONS)})["names"]["XYZ"],
        {},
        date(2026, 10, 13),
        acct_rules["attention"]["cost_call"],
    )
    assert row["status"] == "no quote" and "price" in row["why"]


# --------------------------------------------------------------------------- which cycles
def test_only_assigned_shares_with_no_call_written_are_read():
    cc = cost_call({MONTHLY: chain(*OPTIONS)})
    written = [share_leg(100, 49.0, 50.0), opt_leg(occ(MONTHLY, "C", 55), -1, 0.30, 0.50)]
    history = [*ASSIGNED, tx(2, "2026-10-10T15:00:00Z", "Sell to Open", occ(MONTHLY, "C", 55), 1, 0.50)]
    assert evaluate(legs=written, history=history, cc=cc)["rows"] == []
    # Shares bought outright were not delivered by an assignment: no cost strike.
    bought = [tx(3, "2026-10-01T15:00:00Z", "Buy to Open", "XYZ", 100, 49.0, kind="Equity")]
    assert evaluate(history=bought, cc=cc)["rows"] == []
    # Less than a round lot.
    assert evaluate(legs=[share_leg(50, 49.0, 50.0)], cc=cc)["rows"] == []


def test_the_effective_cost_counts_every_premium_banked():
    more = [
        tx(1, "2026-09-01T15:00:00Z", "Sell to Open", occ("2026-10-09", "P", 50), 1, 1.00),
        tx(4, "2026-08-20T15:00:00Z", "Sell to Open", occ("2026-09-18", "P", 50), 1, 0.50),
        tx(5, "2026-09-18T15:00:00Z", "Buy to Close", occ("2026-09-18", "P", 50), 1, 0.20),
        *assigned("2026-10-09T22:00:00Z", occ("2026-10-09", "P", 50), 1, 50),
    ]
    row = only(evaluate(history=more, cc=cost_call({MONTHLY: chain(*OPTIONS)})))
    # 50 - 1.00 - 0.50 + 0.20 plus 8.30 of fees on a hundred shares = 48.783: 49 is the first strike over it.
    assert row["effective_cost"] == pytest.approx(48.783)
    assert row["strike"] == 49


# --------------------------------------------------------------------------- expiry and strike
def test_expiry_prefers_the_monthly_30_to_45_then_falls_back_to_60():
    cfg = _rules.DEFAULT_RULES["attention"]["cost_call"]
    listed = [WEEKLY, MONTHLY, "2026-12-18"]
    assert costcall.pick_expiry(listed, date(2026, 10, 13), cfg) == (MONTHLY, "standard")
    # 26 days to November, 54 to December: only the fallback reaches one.
    assert costcall.pick_expiry(listed, date(2026, 10, 25), cfg) == ("2026-12-18", "fallback")
    # Weeklies are never picked, and 66 days is out of every window.
    assert costcall.pick_expiry([WEEKLY], date(2026, 10, 13), cfg) == (None, None)
    assert costcall.pick_expiry(["2026-12-18"], date(2026, 10, 13), cfg) == (None, None)


def test_the_strike_is_the_lowest_listed_at_or_above_the_cost():
    assert costcall.cost_strike(49.0, [48, 49, 50]) == 49
    assert costcall.cost_strike(49.01, [48, 49, 50]) == 50
    assert costcall.cost_strike(51, [48, 49, 50]) is None


# --------------------------------------------------------------------------- row order, and the streamer
def test_rows_put_the_flagged_first_and_the_reading_names_the_leg_to_quote():
    reading = evaluate(cc=cost_call({MONTHLY: chain(*OPTIONS)}))
    assert reading["names"] == ["XYZ"] and costcall.legs(reading) == [".XYZ261120C50"]
    rows = [
        {"underlying": "B", "status": "below", "symbol": None},
        {"underlying": "A", "status": "pays", "symbol": ".A1"},
        {"underlying": "C", "status": "no quote", "symbol": None},
    ]
    assert costcall.legs({"rows": rows}) == [".A1"]


def test_the_streamer_is_asked_for_the_cost_strike_leg_beside_the_held_ones():
    result = {
        "accounts": [
            {
                "portfolio": {"benchmark": "SPY"},
                "trades": [],
                "attention": {"cost_call": {"rows": [{"symbol": ".XYZ261120C50"}]}},
            }
        ]
    }
    assert stream_request.held_symbols(result) == [".XYZ261120C50", "SPY"]


# --------------------------------------------------------------------------- the minute re-mark
def test_a_remark_carries_the_caches_newer_bid_for_each_call_read():
    snap = {
        "session": SESSION,
        "generated_at": "2026-10-13T10:00:00-0400",
        "positions": {"ok": True, "accounts": []},
        "metrics": {"XYZ": {"last": 49.0}},
        "cost_call": cost_call({MONTHLY: chain(*OPTIONS)}, {".XYZ261120C50": {"bid": 1.2, "mid": 1.25}}),
    }
    overlay = {
        "session": SESSION,
        "base_generated_at": snap["generated_at"],
        "at": "2026-10-13T10:05:00-0400",
        "quotes": {
            ".XYZ261120C50": {"bid": 0.7, "ask": 0.8, "mid": 0.75},
            "OTHER": {"bid": 1, "ask": 2, "mid": 1.5},
        },
    }
    before = copy.deepcopy(snap)
    out = remark.apply(snap, overlay)
    assert out["cost_call"]["quotes"] == {".XYZ261120C50": overlay["quotes"][".XYZ261120C50"]}
    assert snap == before
    assert ".XYZ261120C50" in remark.wanted(snap)


def test_the_default_ruleset_documents_the_flag():
    cfg = _rules.DEFAULT_RULES["attention"]["cost_call"]
    assert (cfg["min_bid_pct"], cfg["dte_min"], cfg["dte_max"], cfg["fallback_max"]) == (0.02, 30, 45, 60)
