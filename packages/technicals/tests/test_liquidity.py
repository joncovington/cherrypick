"""The liquidity verdict: once a name is found illiquid, nothing re-checks it until a sweep.

Each test breaks one thing a verdict depends on and checks the verdict follows, because a filter
that silently passes everything reads exactly like a market where everything is liquid.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from cherrypick.technicals import liquidity

TODAY = "2026-10-08"
LIQUID = {"atm_spread_pct": 0.03, "chain_open_interest_usd": 2e9, "chain_volume_usd": 80e6, "readings": 5}


def test_weeklies_dollar_volume_and_liquid_monthlies_make_a_name_tradable():
    assert liquidity.judge("AAPL", 6, 9e9, LIQUID) == ("tradable", [])


def test_a_name_whose_monthly_options_are_not_yet_measured_is_thin_never_tradable():
    """Unconfirmed is not liquid (2026-10-27): the user never wants a name they would not trade."""
    assert liquidity.judge("AAPL", 6, 9e9) == (
        "thin",
        ["monthly options not yet confirmed (0 of 3 readings)"],
    )
    two = {**LIQUID, "readings": 2}
    assert liquidity.judge("AAPL", 6, 9e9, two)[0] == "thin"


def test_each_monthly_bar_alone_makes_a_name_thin_and_says_which():
    cases = {
        "wide": ({**LIQUID, "atm_spread_pct": 0.08}, "monthly ATM spread 8.0% of mid, over 7.5%"),
        "unquoted": ({**LIQUID, "atm_spread_pct": None}, "monthly ATM spread unquoted, over 7.5%"),
        "open": ({**LIQUID, "chain_open_interest_usd": 249e6}, "monthly open interest $249M, under $250M"),
        "quiet": ({**LIQUID, "chain_volume_usd": 4.9e6}, "monthly volume $4.9M, under $5.0M"),
    }
    for options, reason in cases.values():
        assert liquidity.judge("XYZ", 6, 9e9, options) == ("thin", [reason])
    # At each bar exactly, the name is tradable: the bars are inclusive.
    edge = {"atm_spread_pct": 0.075, "chain_open_interest_usd": 250e6, "chain_volume_usd": 5e6, "readings": 3}
    assert liquidity.judge("XYZ", 6, 9e9, edge) == ("tradable", [])


def test_the_bars_are_dollars_so_share_price_does_not_decide_them():
    """At the same notional, 2,000 contracts of a $400 stock and 80,000 of a $10 one read alike: the
    bias the user rejected in tastytrade's rating (2026-10-04), which a contract count repeats."""

    def entry(spot, contracts):
        k = round(spot)
        opts = [
            _option(k, "C", 1.00, 1.02, oi=contracts, vol=contracts // 40),
            _option(k, "P", 1.00, 1.02, oi=0, vol=0),
        ]
        return {"spot": float(spot), "options": opts}

    dear = liquidity.reading_measures(entry(400.0, 8_000))
    cheap = liquidity.reading_measures(entry(10.0, 320_000))
    assert dear["chain_open_interest"] < cheap["chain_open_interest"]
    assert dear["chain_open_interest_usd"] == pytest.approx(cheap["chain_open_interest_usd"], rel=0.01)


def test_illiquid_stock_outranks_thin_options():
    assert liquidity.judge("XYZ", 1, 9e9, {**LIQUID, "chain_volume_usd": 0})[0] == "illiquid"


def test_a_thin_verdict_is_not_sticky_and_is_measured():
    before = {"XYZ": {"verdict": "thin", "reasons": ["x"], "judged_on": "2026-10-07"}}
    after = liquidity.update(before, {"XYZ": ("tradable", [])}, TODAY, sweep=False)
    assert after["XYZ"]["verdict"] == "tradable"
    assert "thin" in liquidity.MEASURED and "illiquid" not in liquidity.MEASURED
    assert liquidity.tradable_names({"A": {"verdict": "tradable"}, "B": {"verdict": "thin"}}) == {"A"}
    assert liquidity.illiquid({"B": {"verdict": "thin"}}) == set()  # not skipped by the broker scripts


def _option(k, right, bid, ask, oi=1000, vol=50):
    return {"strike": k, "right": right, "bid": bid, "ask": ask, "open_interest": oi, "volume": vol}


def test_a_reading_measures_the_wider_atm_side_and_the_whole_chain():
    entry = {
        "spot": 101.0,
        "options": [
            _option(100, "C", 2.0, 2.1),  # 4.9% of mid
            _option(100, "P", 1.0, 1.1),  # 9.5%: the wider side counts
            _option(110, "C", 0.1, 0.3, oi=40_000, vol=900),
        ],
    }
    m = liquidity.reading_measures(entry)
    assert m["atm_spread_pct"] == pytest.approx(0.1 / 1.05)
    assert m["chain_open_interest"] == 42_000 and m["chain_volume"] == 1_000
    assert m["chain_open_interest_usd"] == pytest.approx(42_000 * 100 * 101.0)
    assert m["chain_volume_usd"] == pytest.approx(1_000 * 100 * 101.0)
    assert liquidity.reading_measures({"error": "no chain listed"}) is None
    assert liquidity.reading_measures({"spot": 100.0, "options": []}) is None


def test_measures_are_medians_over_the_last_readings_only():
    def doc(spread_ask, oi):
        return {"symbols": {"AAA": {"spot": 100.0, "options": [_option(100, "C", 1.0, spread_ask, oi=oi)]}}}

    # Eight readings; only the last five count: one bad reading among them does not decide it.
    docs = [doc(3.0, 1) for _ in range(3)] + [doc(1.02, 30_000)] * 4 + [doc(3.0, 1)]
    m = liquidity.option_measures(docs)["AAA"]
    assert m["readings"] == 5 and m["chain_open_interest"] == 30_000
    assert m["atm_spread_pct"] == pytest.approx(0.02 / 1.01, abs=1e-4)


def test_only_a_sessions_own_reading_file_counts(tmp_path):
    for name in ("2026-10-20.json", "2026-10-20.pre-fund.json", "2026-10-21.json", "2026-10-21.trial.json"):
        (tmp_path / name).write_text(json.dumps({"symbols": {}}), encoding="utf-8")
    _, files = liquidity.recent_readings(tmp_path)
    assert files == ["2026-10-20.json", "2026-10-21.json"]


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
    opts = {"SPY": LIQUID, "XLE": LIQUID}
    out = liquidity.reference_verdicts(["AGG", "SPY", "XLE", "XLK"], rows, dv, TODAY, opts)
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

    from cherrypick.technicals import paths, securities, symbols, tradable

    monkeypatch.setattr(symbols, "all_symbols", lambda: ["BIG", "THIN", "NEW"])
    monkeypatch.setattr(liquidity, "screener_names", lambda: [])
    monkeypatch.setattr(securities, "liquid_optionable", lambda day=None: ([], "none"))
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
    # BIG passes the stock test; with no income readings its monthly options are unconfirmed: thin.
    assert verdicts["BIG"]["verdict"] == "thin" and verdicts["THIN"]["verdict"] == "illiquid"
    assert "NEW" not in verdicts and out["unjudged"] == 1


def test_the_build_judges_the_liquid_optionable_names_too(monkeypatch):
    """A fresh install has no universe builder and no vendor lists; the income lists' universe is
    the security list's rank 1-2 optionable names, so the verdict judges them."""
    import json
    from datetime import date

    from cherrypick.technicals import paths, securities, symbols, tradable

    monkeypatch.setattr(symbols, "all_symbols", lambda: [])
    monkeypatch.setattr(liquidity, "screener_names", lambda: [])
    monkeypatch.setattr(securities, "liquid_optionable", lambda day=None: (["MSFT"], "public list of x"))
    monkeypatch.setattr(tradable, "latest_rows", lambda: ({"MSFT": {"expiries_35d": 6}}, "2026-10-16"))
    root = paths.dollar_volume_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "2026-10-16.json").write_text(
        json.dumps({"names": {"MSFT": {"median": 9e9, "sessions": 50}}}), "utf-8"
    )
    # Three income readings of a liquid monthly chain confirm it.
    readings = liquidity.income_readings_dir()
    readings.mkdir(parents=True, exist_ok=True)
    chain = [_option(400, "C", 10.0, 10.2, oi=30_000, vol=600), _option(400, "P", 9.0, 9.2)]
    for d in ("2026-10-14", "2026-10-15", "2026-10-16"):
        doc = {"symbols": {"MSFT": {"spot": 401.0, "options": chain}}}
        (readings / f"{d}.json").write_text(json.dumps(doc), "utf-8")
    out = liquidity.build(today=date(2026, 10, 20))
    v = liquidity.load()["MSFT"]
    assert v["verdict"] == "tradable" and out["tradable"] == 1 and v["options"]["readings"] == 3
