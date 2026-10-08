"""The liquidity verdict: once a name is found illiquid, nothing re-checks it until a sweep.

Each test breaks one thing a verdict depends on and checks the verdict follows, because a filter
that silently passes everything reads exactly like a market where everything is liquid.
"""

from __future__ import annotations

from datetime import date

from cherrypick.technicals import liquidity

TODAY = "2026-10-08"


def test_weeklies_and_dollar_volume_make_a_name_tradable():
    assert liquidity.judge("AAPL", 6, 9e9) == ("tradable", [])


def test_no_weeklies_is_illiquid_and_says_so():
    verdict, reasons = liquidity.judge("ALL", 1, 5e8)
    assert verdict == "illiquid" and reasons == ["no weekly options (1 expiries in 35 days)"]


def test_thin_stock_volume_is_illiquid_and_says_so():
    verdict, reasons = liquidity.judge("XYZ", 6, 43e6)
    assert verdict == "illiquid" and reasons == ["$43M a day, under $100M"]


def test_missing_data_is_no_verdict_never_a_guess():
    assert liquidity.judge("NEW", None, 9e9) == (None, [])
    assert liquidity.judge("NEW", 6, None) == (None, [])


def test_reference_series_are_never_judged():
    """AGG and AOR are benchmarks with thin options; hiding them would break the report."""
    for sym in ("AGG", "AOR", "JETS", "SPX"):
        assert liquidity.judge(sym, 1, 1e6) == (None, [])


def test_an_illiquid_verdict_holds_between_sweeps():
    before = {"ALL": {"verdict": "illiquid", "reasons": ["x"], "judged_on": "2026-10-07"}}
    after = liquidity.update(before, {"ALL": ("tradable", [])}, TODAY, sweep=False)
    assert after["ALL"]["verdict"] == "illiquid" and after["ALL"]["judged_on"] == "2026-10-07"


def test_a_sweep_readmits_a_name_that_became_liquid():
    before = {"ALL": {"verdict": "illiquid", "reasons": ["x"], "judged_on": "2026-10-07"}}
    after = liquidity.update(before, {"ALL": ("tradable", [])}, TODAY, sweep=True)
    assert after["ALL"] == {"verdict": "tradable", "reasons": [], "judged_on": TODAY}


def test_a_tradable_name_can_turn_illiquid_any_night():
    before = {"XYZ": {"verdict": "tradable", "reasons": [], "judged_on": "2026-10-07"}}
    after = liquidity.update(before, {"XYZ": ("illiquid", ["thin"])}, TODAY, sweep=False)
    assert after["XYZ"]["verdict"] == "illiquid"


def test_a_name_that_cannot_be_judged_tonight_keeps_its_verdict():
    before = {"XYZ": {"verdict": "tradable", "reasons": [], "judged_on": "2026-10-07"}}
    assert liquidity.update(before, {"XYZ": (None, [])}, TODAY, sweep=False) == before


def test_the_sweep_is_the_first_trading_day_of_the_month():
    assert liquidity.is_sweep_day(date(2026, 10, 1))  # a Thursday
    assert not liquidity.is_sweep_day(date(2026, 10, 2))
    assert liquidity.is_sweep_day(date(2026, 11, 2))  # Nov 1 is a Sunday
    assert not liquidity.is_sweep_day(date(2026, 11, 1))


def test_skip_is_empty_on_a_sweep_day(monkeypatch):
    monkeypatch.setattr(liquidity, "load", lambda: {"ALL": {"verdict": "illiquid"}})
    assert liquidity.skip(date(2026, 10, 8)) == {"ALL"}
    assert liquidity.skip(date(2026, 11, 2)) == set()


def test_listed_drops_only_the_illiquid_and_keeps_the_order():
    assert liquidity.listed(["B", "ALL", "A"], {"ALL"}) == ["B", "A"]
