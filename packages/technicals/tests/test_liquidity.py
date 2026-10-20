"""The liquidity verdict: once a name is found illiquid, nothing re-checks it until a sweep.

Each test breaks one thing a verdict depends on and checks the verdict follows, because a filter
that silently passes everything reads exactly like a market where everything is liquid.
"""

from __future__ import annotations

import json
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


def test_reference_funds_are_judged_apart_for_the_income_candidates(tmp_path, monkeypatch):
    """SPY and the sector funds pass the same test, kept apart from the names so none is ever hidden.
    2026-10-19: the income candidates never saw SPY, QQQ, IWM or TLT, because they read the names only."""
    rows = {"SPY": {"expiries_35d": 13}, "AGG": {"expiries_35d": 1}, "XLE": {"expiries_35d": 9}}
    dv = {"SPY": 4e10, "AGG": 6e8, "XLE": 2e9}
    out = liquidity.reference_verdicts(["AGG", "SPY", "XLE", "XLK"], rows, dv, TODAY)
    assert out["SPY"]["verdict"] == "tradable" and out["XLE"]["verdict"] == "tradable"
    assert out["AGG"]["verdict"] == "illiquid" and "XLK" not in out  # no data: no verdict
    path = tmp_path / "verdicts.json"
    path.write_text(json.dumps({"names": {}, "reference": out}), encoding="utf-8")
    monkeypatch.setattr(liquidity, "verdicts_path", lambda: path)
    assert liquidity.reference_tradable() == {"SPY", "XLE"}
    assert liquidity.illiquid() == set()  # a fund judged illiquid is still never hidden


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


def test_the_build_judges_dollar_volume_from_the_candle_file_alone(monkeypatch):
    """No store and no Dolt: a name with the full 50 sessions is judged, one with fewer is not."""
    import json
    from datetime import date

    from cherrypick.technicals import paths, symbols, tradable

    monkeypatch.setattr(symbols, "all_symbols", lambda: ["BIG", "THIN", "NEW"])
    monkeypatch.setattr(liquidity, "screener_names", lambda: [])
    rows = {s: {"expiries_35d": 5} for s in ("BIG", "THIN", "NEW")}
    monkeypatch.setattr(tradable, "latest_rows", lambda: (rows, "2026-10-16"))
    root = paths.dollar_volume_dir()
    root.mkdir(parents=True, exist_ok=True)
    names = {
        "BIG": {"median": 500e6, "sessions": 50},
        "THIN": {"median": 20e6, "sessions": 50},
        "NEW": {"median": 900e6, "sessions": 30},
    }
    (root / "2026-10-16.json").write_text(json.dumps({"names": names}), encoding="utf-8")
    out = liquidity.build(today=date(2026, 10, 20))
    verdicts = liquidity.load()
    assert verdicts["BIG"]["verdict"] == "tradable" and verdicts["THIN"]["verdict"] == "illiquid"
    assert "NEW" not in verdicts and out["unjudged"] == 1
