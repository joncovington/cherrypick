"""Our own short-put and covered-call lists (incomelists.py): the strike rule, the price and the flags.

Chains here are priced by the model itself, so every IV solves back and every delta is exact; each
test breaks one thing a row depends on and checks the row follows.
"""

from __future__ import annotations

import json

import pytest
from cherrypick.core import bs, incomerules

from cherrypick.technicals import incomelists, paths

SESSION = "2026-10-20"


def _chain(spot=100.0, iv=0.30, dte=52, strikes=range(60, 141), rights="PC", width=0.04, passes=True):
    out = []
    for k in strikes:
        for right in rights:
            if (right == "P" and k >= spot) or (right == "C" and k <= spot):
                continue
            mid = bs.price(spot, float(k), dte / 365.0, iv, right, incomerules.RATE)
            if mid < 0.05:
                continue
            out.append(
                {
                    "strike": float(k),
                    "right": right,
                    "option": f"X{right}{k}",
                    "bid": round(mid - width / 2, 4),
                    "ask": round(mid + width / 2, 4),
                    "open_interest": 500,
                    "volume": 20,
                    "passes": passes,
                    "quiet": False,
                }
            )
    return out


def _entry(**over):
    e = {
        "spot": 100.0,
        "expiry": "2026-12-11",
        "dte": 52,
        "options": _chain(),
        "earnings_date": None,
        "ex_dividend_date": None,
        "iv_index": 0.3,
        "cc": {"expiry": "2026-11-20", "dte": 31, "window": "30-45", "options": _chain(dte=31, rights="C")},
    }
    e.update(over)
    return e


def _doc(symbols):
    return {"session": SESSION, "taken_at": "t", "complete": True, "symbols": symbols}


def test_the_put_is_the_one_nearest_16_delta_on_the_cycle_and_the_call_on_its_own_expiry():
    out = incomelists.build_lists(_doc({"AAA": _entry()}))
    (put,) = out["lists"]["short-puts"]
    (call,) = out["lists"]["covered-calls"]
    assert abs(put["delta"]) == pytest.approx(0.16, abs=0.02) and put["strike"] < 100
    assert (put["expiry"], put["dte"]) == ("2026-12-11", 52)
    assert call["delta"] == pytest.approx(0.16, abs=0.02) and call["strike"] > 100
    assert (call["expiry"], call["dte"], call["window"]) == ("2026-11-20", 31, "30-45")


def test_the_price_is_the_working_one_and_the_return_is_on_the_cash_it_ties_up():
    out = incomelists.build_lists(_doc({"AAA": _entry()}))
    put, call = out["lists"]["short-puts"][0], out["lists"]["covered-calls"][0]
    for r in (put, call):
        assert r["working"] == pytest.approx(r["mid"] - 0.01, abs=1e-4)  # the mid less a quarter of 0.04
        assert r["premium"] == pytest.approx(r["working"] * 100, abs=0.01)
    assert put["return"] == pytest.approx(
        put["working"] / put["strike"], rel=1e-4
    )  # cash-secured: the strike
    assert call["return"] == pytest.approx(call["working"] / 100.0, rel=1e-4)  # covered: the share price
    assert put["annual"] == pytest.approx(put["return"] * 365 / 52, rel=1e-4)


def test_earnings_are_judged_against_each_trades_own_expiry():
    # A report after the call's expiry and before the put's: in the put's trade, clear of the call's.
    out = incomelists.build_lists(_doc({"AAA": _entry(earnings_date="2026-12-01")}))
    assert out["lists"]["short-puts"][0]["earnings"] == "in-trade"
    assert out["lists"]["covered-calls"][0]["earnings"] == "clear"
    out = incomelists.build_lists(_doc({"AAA": _entry()}))
    assert out["lists"]["short-puts"][0]["earnings"] == "unknown"  # a company with no date is never clear
    out = incomelists.build_lists(_doc({"SPY": _entry()}), {"SPY": "Exchange Traded Fund"})
    assert out["lists"]["short-puts"][0]["earnings"] == "none"


def test_a_call_across_an_ex_date_is_flagged():
    out = incomelists.build_lists(_doc({"AAA": _entry(ex_dividend_date="2026-11-10")}))
    assert out["lists"]["covered-calls"][0]["ex_dividend_in_trade"] is True
    assert out["lists"]["short-puts"][0]["ex_dividend_in_trade"] is None


def test_the_ex_date_is_carried_with_a_state_never_a_bare_false():
    # in-trade, after the expiry, already past (the next one not posted), or no date at all: four
    # different things a bare False used to collapse into one blank.
    cases = {
        "2026-11-10": "in-trade",
        "2026-12-15": "after",
        "2026-08-17": "past",
        None: "unknown",
    }
    for ex, state in cases.items():
        call = incomelists.build_lists(_doc({"AAA": _entry(ex_dividend_date=ex)}))["lists"]["covered-calls"][
            0
        ]
        assert (call["ex_dividend"], call["ex_dividend_date"]) == (state, ex)
    put = incomelists.build_lists(_doc({"AAA": _entry(ex_dividend_date="2026-11-10")}))["lists"][
        "short-puts"
    ][0]
    assert put["ex_dividend"] is None  # a put is never assigned for the dividend


def test_a_report_before_the_open_on_the_session_day_is_already_out_of_the_trade():
    # The chain is quoted at 14:45 ET: a before-the-open report that morning has happened.
    for spelling in ("BTO", "BMO", "bmo"):
        e = _entry(earnings_date=SESSION, earnings_time=spelling)
        assert incomelists.build_lists(_doc({"AAA": e}))["lists"]["short-puts"][0]["earnings"] == "unknown"
    # After the close, or untimed, it is still ahead of the trade: in it, conservatively.
    for spelling in ("AMC", None):
        e = _entry(earnings_date=SESSION, earnings_time=spelling)
        assert incomelists.build_lists(_doc({"AAA": e}))["lists"]["short-puts"][0]["earnings"] == "in-trade"
    # A before-the-open report on a LATER day is still in the trade.
    e = _entry(earnings_date="2026-10-28", earnings_time="BTO")
    assert incomelists.build_lists(_doc({"AAA": e}))["lists"]["short-puts"][0]["earnings"] == "in-trade"


def test_rows_are_by_symbol_and_what_cannot_be_built_is_counted():
    symbols = {
        "ZZZ": _entry(),
        "AAA": _entry(),
        "ERR": {"error": "no chain listed"},
        "OLD": {k: v for k, v in _entry().items() if k != "cc"},  # a reading from before the cc block
        "ONE": _entry(options=[o for o in _chain() if o["right"] == "C"]),  # no puts quoted
    }
    out = incomelists.build_lists(_doc(symbols))
    assert [r["symbol"] for r in out["lists"]["short-puts"]] == ["AAA", "OLD", "ZZZ"]
    assert [r["symbol"] for r in out["lists"]["covered-calls"]] == ["AAA", "ONE", "ZZZ"]
    assert out["dropped"]["short-puts"] == {"no chain": 1, "no priced put out of the money": 1}
    assert out["dropped"]["covered-calls"] == {"no chain": 1, "reading has no covered-call expiry": 1}


def test_a_declared_leveraged_fund_is_badged_on_both_lists_and_never_dropped():
    out = incomelists.build_lists(_doc({"TQQQ": _entry(), "AAA": _entry()}), leveraged={"TQQQ": "leveraged"})
    for name in ("short-puts", "covered-calls"):
        by = {r["symbol"]: r for r in out["lists"][name]}
        assert by["TQQQ"]["leveraged"] == "leveraged" and by["AAA"]["leveraged"] is None


def test_the_badge_reads_the_list_skate_leaves_these_funds_out_by():
    from cherrypick.technicals import skate

    lev = skate.leveraged_funds()
    assert {"TQQQ", "TSLL", "KORU", "TMF", "UVXY"} <= set(lev)
    assert all(f.get("kind") in ("leveraged", "inverse", "volatility") for f in lev.values())


def test_the_leg_verdict_is_the_readings_own():
    out = incomelists.build_lists(_doc({"AAA": _entry(options=_chain(passes=False))}))
    assert out["lists"]["short-puts"][0]["passes"] is False


def test_the_build_reads_the_newest_real_reading_and_never_a_trial():
    root = incomelists.income_dir()
    root.mkdir(parents=True)
    (root / "2026-10-19.json").write_text(
        json.dumps(_doc({"OLD": _entry()}) | {"session": "2026-10-19"}), "utf-8"
    )
    (root / "2026-10-20.json").write_text(json.dumps(_doc({"AAA": _entry()})), "utf-8")
    (root / "2026-10-21.trial.json").write_text(json.dumps(_doc({"TRIAL": _entry()})), "utf-8")
    out = incomelists.build(day="2026-10-21")
    assert (
        out["ok"] and out["session"] == "2026-10-20" and out["rows"] == {"short-puts": 1, "covered-calls": 1}
    )
    doc = json.loads((paths.data_dir() / "income-lists" / "2026-10-20.json").read_text(encoding="utf-8"))
    assert (
        doc["rule"]["short_put_delta"] == incomerules.SHORT_PUT_DELTA and doc["source"] == "2026-10-20.json"
    )
    assert incomelists.build(day="2026-10-18")["ok"] is False


def test_a_pick_outside_the_studys_delta_tolerance_is_no_row():
    # Strikes $10 apart on a $100 name: the nearest put to 16 delta sits well away from it.
    sparse = _chain(strikes=range(60, 141, 10))
    out = incomelists.build_lists(_doc({"AAA": _entry(options=sparse)}))
    picked = out["lists"]["short-puts"]
    if picked:
        assert abs(abs(picked[0]["delta"]) - 0.16) <= incomerules.DELTA_TOLERANCE
    else:
        assert out["dropped"]["short-puts"] == {"no strike within the delta tolerance": 1}
    far = [{**o, "strike": 99.0} for o in _chain(strikes=[99], rights="P")]  # one strike, near the money
    out = incomelists.build_lists(_doc({"AAA": _entry(options=far)}))
    assert out["lists"]["short-puts"] == []
    assert out["dropped"]["short-puts"] == {"no strike within the delta tolerance": 1}
