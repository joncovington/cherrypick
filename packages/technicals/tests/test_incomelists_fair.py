"""The fair-value fields on the income-list rows (incomelists.py version 3)."""

from __future__ import annotations

from test_incomelists import SESSION, _doc, _entry

from cherrypick.technicals import incomelists

VALUED = {
    "state": "valued",
    "fair_value": 120.0,
    "low": 100.0,
    "high": 140.0,
    "gap": 100.0 / 120.0 - 1,
    "flags": ["terminal_heavy"],
}


def test_every_row_carries_the_fair_value_fields_from_the_file():
    out = incomelists.build_lists(_doc({"AAA": _entry()}), fair={"AAA": VALUED})
    for r in (out["lists"]["short-puts"][0], out["lists"]["covered-calls"][0]):
        assert r["fair_value"] == 120.0 and r["fair_low"] == 100.0 and r["fair_high"] == 140.0
        assert r["fair_gap"] == VALUED["gap"] and r["fair_state"] == "valued"
        assert r["fair_flags"] == ["terminal_heavy"]


def test_no_file_reads_unbuilt_and_a_name_the_file_lacks_reads_unlisted():
    out = incomelists.build_lists(_doc({"AAA": _entry()}))
    r = out["lists"]["short-puts"][0]
    assert r["fair_state"] == "unbuilt" and r["fair_value"] is None and r["fair_flags"] == []
    out = incomelists.build_lists(_doc({"AAA": _entry()}), fair={"BBB": VALUED})
    assert out["lists"]["short-puts"][0]["fair_state"] == "unlisted"


def test_a_fund_or_a_refused_name_keeps_its_state_and_no_figure():
    fair = {"SPY": {"state": "not-applicable", "reason": "fund"}}
    out = incomelists.build_lists(_doc({"SPY": _entry()}), {"SPY": "Exchange Traded Fund"}, fair=fair)
    r = out["lists"]["short-puts"][0]
    assert r["fair_state"] == "not-applicable" and r["fair_value"] is None


def test_the_list_stays_by_symbol_never_by_gap():
    cheap = dict(VALUED, gap=-0.5)
    rich = dict(VALUED, gap=0.5)
    out = incomelists.build_lists(_doc({"ZZZ": _entry(), "AAA": _entry()}), fair={"ZZZ": cheap, "AAA": rich})
    assert [r["symbol"] for r in out["lists"]["short-puts"]] == ["AAA", "ZZZ"]
    assert SESSION  # the shared fixture module is the one the other list tests use
