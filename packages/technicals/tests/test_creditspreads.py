"""Our own credit-spread list (creditspreads.py): the signal, the short, the user's width rule and the
width table.

Chains are priced by the model itself, so every IV solves back and every delta is exact; each test
breaks one thing a row depends on and checks the row follows.
"""

from __future__ import annotations

import pytest
from cherrypick.core import bs, incomerules

from cherrypick.technicals import creditspreads, incomelists
from cherrypick.technicals.setups import Trade

SESSION = "2026-10-20"
CS_EXPIRY, CS_DTE = "2026-11-20", 31


def _chain(spot=100.0, iv=0.30, dte=CS_DTE, strikes=range(60, 141), rights="PC", width=0.04):
    out = []
    for k in strikes:
        for right in rights:
            if (right == "P" and k >= spot) or (right == "C" and k <= spot):
                continue
            mid = bs.price(spot, float(k), dte / 365.0, iv, right, incomerules.RATE)
            if mid < 0.02:
                continue
            out.append(
                {
                    "strike": float(k),
                    "right": right,
                    "option": f"X{right}{k}",
                    "bid": round(max(0.0, mid - width / 2), 4),
                    "ask": round(mid + width / 2, 4),
                    "open_interest": 500,
                    "volume": 20,
                    "passes": True,
                    "quiet": False,
                }
            )
    return out


BARS = {"min_bid": 0.05, "min_open_interest": 100, "max_spread_pct": 0.25, "max_spread_abs": 0.10}


def _entry(**over):
    e = {
        "spot": 100.0,
        "expiry": "2026-12-11",
        "dte": 52,
        "options": [],
        "earnings_date": None,
        "ex_dividend_date": None,
        "iv_index": 0.3,
        "cs": {"expiry": CS_EXPIRY, "dte": CS_DTE, "options": _chain()},
    }
    e.update(over)
    return e


def _doc(symbols):
    return {"session": SESSION, "taken_at": "t", "complete": True, "bars": BARS, "symbols": symbols}


PUT_SIGNAL = [{"setup": "pullback", "side": "P", "date": "2026-10-19", "ago": 0}]
CALL_SIGNAL = [{"setup": "reversion-short", "side": "C", "date": "2026-10-16", "ago": 1}]


def _spreads(symbols, signals):
    return incomelists.build_lists(_doc(symbols), signals=signals)


# --- the signal --------------------------------------------------------------------------------


def test_a_fresh_entry_is_an_open_position_entered_on_one_of_the_last_closes():
    dates = [f"d{i}" for i in range(20)]
    runs = {
        "trend": [Trade(17)],  # open, 2 sessions ago: fresh
        "pullback": [Trade(18, exit=19, reason="stop")],  # entered lately but already out
        "reversion": [Trade(14)],  # open, but 5 sessions ago: one too old
        "breakout-short": [Trade(19)],  # open, on the last close
        "st-vortex": [Trade(19)],  # study-only: never a spread
    }
    got = creditspreads.fresh_entries(runs, dates)
    assert got == [
        {"setup": "breakout-short", "side": "C", "date": "d19", "ago": 0},
        {"setup": "trend", "side": "P", "date": "d17", "ago": 2},
    ]


def test_a_long_setup_is_a_put_spread_and_its_mirror_a_call_spread():
    assert {s for s, side in creditspreads.SPREAD_SIDE.items() if side == "P"} == {
        "trend",
        "pullback",
        "reversion",
        "breakout",
    }
    assert all(
        creditspreads.SPREAD_SIDE[f"{s}-short"] == "C" for s in ("trend", "pullback", "reversion", "breakout")
    )


# --- the row -----------------------------------------------------------------------------------


def test_the_short_is_the_option_nearest_40_delta_on_the_30_to_45_day_monthly():
    (row,) = _spreads({"AAA": _entry()}, {"AAA": PUT_SIGNAL})["lists"]["credit-spreads"]
    assert row["spread"] == "put" and row["setups"] == ["pullback"]
    assert (row["expiry"], row["dte"]) == (CS_EXPIRY, CS_DTE)
    assert abs(row["short"]["delta"]) == pytest.approx(0.40, abs=0.03) and row["short"]["strike"] < 100
    assert row["conflict"] is False and row["ex_dividend"] is None


def test_the_pick_is_the_widest_long_giving_35_to_40_percent_of_the_width():
    (row,) = _spreads({"AAA": _entry()}, {"AAA": PUT_SIGNAL})["lists"]["credit-spreads"]
    pick = row["pick"]
    assert pick is not None and 0.35 <= pick["credit_width"] <= 0.40
    assert pick["long"] < row["short"]["strike"]
    assert pick["max_loss"] == pytest.approx(pick["width"] - pick["credit"])
    # Nothing wider lands in the range: every wider listed long gives less or more than 35-40%.
    short = next(
        o for o in incomelists.priced(_chain(), 100.0, CS_DTE) if o["option"] == row["short"]["option"]
    )
    pool = incomelists.priced(_chain(), 100.0, CS_DTE)
    for o in creditspreads.beyond(pool, short):
        if o["strike"] < pick["long"]:
            s = creditspreads.spread(short, o, BARS)
            assert s is None or not (0.35 <= s["credit_width"] <= 0.40)


def _q(strike, bid, ask, delta, right="P"):
    return {
        "strike": strike,
        "right": right,
        "option": f"{right}{strike}",
        "bid": bid,
        "ask": ask,
        "delta": delta,
    }


def test_no_width_in_the_range_is_no_pick_and_the_widest_in_it_wins():
    short = _q(95.0, 1.98, 2.02, -0.40)  # mid 2.00
    # Working credit per width: 94 -> 0.50, 93 -> ~0.45, 90 -> ~0.30: none inside 35-40%.
    pool = [short, _q(94.0, 1.48, 1.52, -0.36), _q(93.0, 1.08, 1.12, -0.32), _q(90.0, 0.48, 0.52, -0.2)]
    assert creditspreads.pick_width(short, pool, None) is None
    # Add strikes landing at ~38% (92) and ~36% (91): the wider of the two is the pick.
    pool += [_q(92.0, 0.84, 0.88, -0.29), _q(91.0, 0.54, 0.58, -0.24)]
    pick = creditspreads.pick_width(short, pool, None)
    assert pick["long"] == 91.0 and 0.35 <= pick["credit_width"] <= 0.40


def test_the_width_table_has_every_declared_width_in_both_units():
    (row,) = _spreads({"AAA": _entry()}, {"AAA": PUT_SIGNAL})["lists"]["credit-spreads"]
    units = [(w["unit"], w["size"]) for w in row["widths"]]
    assert units == [("strikes", 1), ("strikes", 2), ("strikes", 3), ("strikes", 5)] + [
        ("move", 0.25),
        ("move", 0.5),
        ("move", 0.75),
    ]
    steps = [w["width"] for w in row["widths"] if w["unit"] == "strikes"]
    assert steps == [1.0, 2.0, 3.0, 5.0]  # $1 strikes
    # The move widths: the listed long nearest the short less that share of one SD to expiry.
    move = 100.0 * 0.3 * (CS_DTE / 365.0) ** 0.5
    assert row["move"] == pytest.approx(move, abs=1e-3)
    for w in (w for w in row["widths"] if w["unit"] == "move"):
        assert w["long"] == pytest.approx(round(row["short"]["strike"] - w["size"] * move), abs=1.0)
    # The credit's share of the width falls as the spread widens.
    shares = [w["credit_width"] for w in row["widths"] if w["unit"] == "strikes"]
    assert shares == sorted(shares, reverse=True)


def test_a_width_past_the_chain_is_a_row_with_no_figures():
    short_chain = _entry(cs={"expiry": CS_EXPIRY, "dte": CS_DTE, "options": _chain(strikes=range(96, 141))})
    (row,) = _spreads({"AAA": short_chain}, {"AAA": PUT_SIGNAL})["lists"]["credit-spreads"]
    beyond = [
        o for o in short_chain["cs"]["options"] if o["right"] == "P" and o["strike"] < row["short"]["strike"]
    ]
    assert 1 <= len(beyond) < 5  # the chain stops under five strikes past the short
    by_size = {w["size"]: w for w in row["widths"] if w["unit"] == "strikes"}
    assert "credit" in by_size[1]
    assert by_size[5] == {"unit": "strikes", "size": 5}


def test_a_call_spread_carries_the_ex_dividend_state_and_both_sides_are_a_conflict():
    e = _entry(ex_dividend_date="2026-11-02")
    rows = _spreads({"AAA": e}, {"AAA": PUT_SIGNAL + CALL_SIGNAL})["lists"]["credit-spreads"]
    put, call = sorted(rows, key=lambda r: r["spread"], reverse=True)
    assert (put["spread"], call["spread"]) == ("put", "call")
    assert put["conflict"] and call["conflict"]
    assert call["ex_dividend"] == "in-trade" and put["ex_dividend"] is None
    assert call["short"]["strike"] > 100 and call["short"]["delta"] == pytest.approx(0.40, abs=0.03)


def test_earnings_inside_the_trade_are_flagged_never_dropped():
    e = _entry(earnings_date="2026-11-05", earnings_time="AMC")
    (row,) = _spreads({"AAA": e}, {"AAA": PUT_SIGNAL})["lists"]["credit-spreads"]
    assert row["earnings"] == "in-trade"


# --- what builds no row ------------------------------------------------------------------------


def test_no_signal_no_monthly_and_no_store_each_say_so():
    out = _spreads({"AAA": _entry(), "BBB": _entry(cs=None)}, {"BBB": PUT_SIGNAL})
    assert out["lists"]["credit-spreads"] == []
    assert out["dropped"]["credit-spreads"] == {"no fresh setup entry": 1, "no 30-45 or 45-60 day monthly": 1}
    # No history store: no credit-spread list at all, never an empty one that reads as "no signals".
    out = incomelists.build_lists(_doc({"AAA": _entry()}))
    assert "credit-spreads" not in out["lists"]


def test_the_list_says_no_selection_has_a_tested_edge():
    assert "no setup, side, short or width passed" in creditspreads.rule()["tested"]
    assert set(creditspreads.rule()["selections"]) == {"credit-share", "reversion-d20-s5", "reversion-d40-s5"}


REVERSION_PUT = [{"setup": "reversion", "side": "P", "date": "2026-10-19", "ago": 0}]


def test_a_mean_reversion_selection_lists_only_mean_reversion_puts():
    (row,) = _spreads({"AAA": _entry()}, {"AAA": PUT_SIGNAL})["lists"]["credit-spreads"]  # pullback
    assert row["selections"]["reversion-d20-s5"] is None and row["selections"]["reversion-d40-s5"] is None
    assert row["selections"]["credit-share"] is not None  # the credit rule reads every setup
    (call,) = _spreads({"AAA": _entry()}, {"AAA": CALL_SIGNAL})["lists"]["credit-spreads"]  # reversion-short
    assert call["selections"]["reversion-d20-s5"] is None  # puts only


def test_each_mean_reversion_selection_has_its_own_short_and_a_5_strike_long():
    (row,) = _spreads({"AAA": _entry()}, {"AAA": REVERSION_PUT})["lists"]["credit-spreads"]
    d20, d40 = row["selections"]["reversion-d20-s5"], row["selections"]["reversion-d40-s5"]
    assert abs(d20["short"]["delta"]) == pytest.approx(0.20, abs=0.03)
    assert abs(d40["short"]["delta"]) == pytest.approx(0.40, abs=0.03)
    assert d20["short"]["strike"] < d40["short"]["strike"] < 100
    for sel in (d20, d40):
        assert sel["width"] == pytest.approx(5.0)  # five listed $1 strikes beyond the short
        assert sel["long"] == sel["short"]["strike"] - 5
        assert sel["max_loss"] == pytest.approx(sel["width"] - sel["credit"])
    # The credit-share selection is the row's own pick, on the row's own short.
    assert row["selections"]["credit-share"]["long"] == row["pick"]["long"]


def test_a_row_says_which_expiry_window_it_came_from():
    (row,) = _spreads({"AAA": _entry()}, {"AAA": PUT_SIGNAL})["lists"]["credit-spreads"]
    assert row["window"] == "30-45"  # a chain written before the fallback has no window: the studied one
    late = _entry(cs={"expiry": "2026-12-18", "dte": 56, "window": "45-60", "options": _chain(dte=56)})
    (row,) = _spreads({"AAA": late}, {"AAA": PUT_SIGNAL})["lists"]["credit-spreads"]
    assert row["window"] == "45-60" and row["dte"] == 56
