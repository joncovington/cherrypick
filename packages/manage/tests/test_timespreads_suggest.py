"""The time-spread rows sized to the account (made-up names, account and chain)."""

from datetime import date

from test_timespreads_structures import entry

from cherrypick.manage import rules as _rules
from cherrypick.manage import timespreads as ts

SESSION = date(2026, 11, 6)
RULES = _rules.effective(None)[0]


def reading():
    return {
        "session": "2026-11-06",
        "taken_at": "2026-11-06T15:30:00+00:00",
        "complete": True,
        "read": 3,
        "passing": 1,
        "rule": ts.RULE,
        "rows": [
            {
                "symbol": "AAA",
                "ratio": 1.3,
                "iv_rank": 0.5,
                "sector": "Tech",
                "beta": 1.2,
                "earnings_date": None,
            },
            {"symbol": "HHH", "sector": "Tech"},
            {"symbol": "EEE", "sector": "Energy"},
        ],
        "shortlist": ["AAA"],
        "chains": {"AAA": entry()},
        "error": None,
    }


def book(bwd=20.0, used=2000.0, net_liq=20000.0):
    return {"net_liq": net_liq, "buying_power_used": used, "bwd": bwd, "bands": {}, "underlyings": []}


def run(b=None, trades=(), r=None, rules=RULES):
    return ts.suggest(
        r or reading(), b or book(), list(trades), SESSION, rules, {"margin_or_cash": "Margin"}, 100.0
    )


def test_rows_are_sized_and_say_they_have_no_tested_edge():
    out = run(book(net_liq=60000.0))  # big enough that the diagonals clear the per-trade cap
    assert out["edge"] == "no tested edge" and out["order"] == ts.HYPOTHESIS
    assert out["fits"] and all(r["units"] >= 1 for r in out["fits"])
    assert all(r["underlying"] == "AAA" for r in out["fits"])
    assert not out["delta_filter"]["bullish_held_back"]
    assert any(r["direction"] == "bullish" for r in out["fits"])


def test_over_the_delta_limit_bullish_structures_are_held_back():
    out = run(book(bwd=150.0, net_liq=60000.0))
    assert out["delta_filter"]["bullish_held_back"]
    assert not any(r["direction"] == "bullish" for r in out["fits"] + out["too_big"])
    assert out["dropped"]["bullish while the book is over its delta limit"] >= 1


def test_the_buying_power_ceiling_binds_and_is_named():
    # 11,800 used of 20,000: 200 of room under the 60% ceiling, less than any structure's debit.
    out = run(book(used=11800.0))
    assert out["fits"] == []
    # Those inside the per-trade cap (3% of 20,000) are stopped by the ceiling; the rest by that cap.
    small = [r for r in out["too_big"] if r["max_loss"] <= 600]
    assert small and all(r["binding"] == "buying power 60%" for r in small)


def test_the_per_trade_risk_cap_binds_on_a_small_account():
    out = run(book(net_liq=5000.0, used=0.0))
    big = [r for r in out["too_big"] if r["max_loss"] > 0.03 * 5000]
    assert big and all(r["binding"] == "per-trade risk" for r in big)


def test_a_held_name_or_sector_is_flagged_not_dropped():
    out = run(trades=[{"underlying": "HHH", "legs": []}])
    assert out["fits"] and all(r["held_sector"] and not r["held_name"] for r in out["fits"])
    same = run(trades=[{"underlying": "AAA", "legs": []}])
    assert all(r["held_name"] for r in same["fits"])


def test_no_reading_and_the_switch():
    missing = ts.suggest({"error": "no time-spread reading today"}, book(), [], SESSION, RULES, None, 100.0)
    assert missing["error"] == "no time-spread reading today" and missing["fits"] == []
    off, _ = _rules.effective({"portfolio": {"time_spreads": {"enabled": False}}})
    assert run(rules=off) is None
