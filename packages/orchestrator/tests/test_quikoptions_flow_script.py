"""Derived flow (`scripts/quikoptions_flow.py`): each read, flag and factor shown to do its job.

The score is provisional by design — its constants are to be judged against the outcome record — so
what these guard is the plumbing that must not drift silently: a fill read the wrong way round, a
roll scored as two bets, a deep in-the-money or dividend trade counted as a view, an unread flow
ranked, a confirmation that moves the wrong factor, and the outcome record. No broker: the market
numbers are passed in.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "quikoptions_flow.py"


def _module():
    spec = importlib.util.spec_from_file_location("quikoptions_flow", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


qf = _module()
SESSION = "2026-10-02"


def _out(
    symbol="PCG",
    expires="2027-01-15",
    strike=16.0,
    cp="call",
    size=40_900,
    price=0.37,
    fill="On Ask",
    edge=1.0,
    time="13:38:45.517",
):
    side = None if fill is None else {"sentiment": "Bullish", "fill": fill, "edge": edge}
    return {
        "symbol": symbol,
        "time_et": time,
        "size": size,
        "expires": expires,
        "strike": strike,
        "cp": cp,
        "price": price,
        "side": side,
    }


def _capture(**tables):
    base = {"outrights": [], "sweeps": [], "spreads": [], "voloi": [], "openings": []}
    return {"session": SESSION, "tables": {**base, **tables}}


MARKET = {
    "names": {"PCG": {"close": 12.32, "iv": 0.40}, "VST": {"close": 140.02}, "AI": {"close": 11.12}},
    "contracts": {},
}


def _flows(capture, market=MARKET):
    return qf.derive_flow(capture, market)


# ------------------------------------------------------------------------------------------------


def test_the_option_math_round_trips_and_deep_itm_falls_back_to_one():
    price, delta = qf.black_scholes(100, 105, 0.25, 0.3, "call")
    assert qf.implied_vol(100, 105, 0.25, price, "call") == pytest.approx(0.3, abs=1e-4)
    assert 0 < delta < 0.5
    # At or under intrinsic no volatility prices it: deep in the money, delta 1.
    assert qf.option_delta(1097.39, 450, 0, 644.45, "call") == (1.0, "intrinsic")
    assert qf.option_delta(None, 16, 105, 0.37, "call") == (None, "no spot")


def test_symbols_and_spread_legs():
    assert qf.occ_symbol("PCG", "2027-01-15", 16.0, "call") == "PCG   270115C00016000"
    assert qf.occ_symbol("VST", "2027-12-17", 195.0, "put") == "VST   271217P00195000"
    assert qf.spread_legs("261009 11/11.5 CS", "CS") == [
        {"expires": "2026-10-09", "strike": 11.0, "cp": "call"},
        {"expires": "2026-10-09", "strike": 11.5, "cp": "call"},
    ]
    assert [leg["expires"] for leg in qf.spread_legs("261016 23/261030 22 CSCAL", "CSCAL")] == [
        "2026-10-16",
        "2026-10-30",
    ]
    assert qf.spread_legs("261016 23/25/27 BFLY", "BFLY") is None  # never guessed at


def test_the_fill_reads_bought_sold_or_not_at_all_and_the_view_follows_call_or_put():
    vst = _out(symbol="VST", expires="2027-12-17", strike=195.0, cp="put", size=14_400, price=64.55)
    vst.update(time_et="15:38:55.167", side={"fill": "Mid Market", "edge": 0.28})
    sold_put = _out(cp="put", strike=11.0, price=0.40, fill="On Bid", edge=-1.0, time="10:00:00.000")
    doc = _flows(_capture(outrights=[_out(), vst, sold_put]))
    read = {(f["symbol"], f["what"]): (f["direction"], f["view"]) for f in doc["flows"]}
    assert read[("PCG", "15 Jan 27 16C")] == ("bought", "bullish")
    assert read[("PCG", "15 Jan 27 11P")] == ("sold", "bullish")
    assert [f["symbol"] for f in doc["unread"]] == ["VST"]  # a mid fill is listed, never ranked
    assert doc["unread"][0]["score"] is None


def test_a_label_that_disagrees_with_the_edge_halves_conviction():
    doc = _flows(_capture(sweeps=[_out(fill="Mid Market", edge=0.72, time=None)]))
    f = doc["flows"][0]
    assert "label disagrees" in f["flags"]
    assert f["factors"]["conviction"] == pytest.approx((0.3 + 0.7 * 0.72 + 0.1) * 0.5, abs=1e-3)


def test_paired_prints_and_rolls_are_not_counted_as_bets():
    pair = [
        _out(cp="put", strike=11.0, price=0.25, fill="Mid Market", edge=-0.7),
        _out(cp="put", strike=11.0, price=0.24, fill="On Bid", edge=-1.0),
    ]
    pair[0]["side"]["fill"] = "On Bid"
    doc = _flows(_capture(outrights=[pair[0], {**pair[1], "side": {"fill": "On Ask", "edge": 1.0}}]))
    assert all(
        "paired prints, opposite" in f["flags"] and f["factors"]["purity"] == 0.3 for f in doc["flows"]
    )
    leg = {
        "symbol": "AI",
        "time_et": "15:01:34.327",
        "size": 41_900,
        "type": "CS",
        "group": "AI 15:01:34.327 41900",
    }
    leg["underlying"] = {"last": 11.12}
    roll = [
        {
            **leg,
            "expires": "2026-10-02",
            "spread": "261002 10.5/11 CS",
            "price": 0.47,
            "delta": 0.24,
            "premium": 1_969_300,
        },
        {
            **leg,
            "expires": "2026-10-09",
            "spread": "261009 11/11.5 CS",
            "price": -0.22,
            "delta": -0.24,
            "premium": -921_800,
        },
    ]
    doc = _flows(_capture(spreads=roll))
    assert all("roll" in f["flags"] for f in doc["flows"])
    assert "near max" in doc["flows"][0]["flags"] or "near max" in doc["flows"][1]["flags"]  # 0.47 of 0.50
    ai = next(n for n in doc["names"] if n["symbol"] == "AI")
    assert ai["net"] == 0  # the two legs cancel: a roll, not a view


def test_deep_itm_lottery_sold_and_dividend_trades_lose_purity():
    deep = _out(strike=5.0, price=7.40)
    lottery = _out(strike=25.0, price=0.01, time="11:00:00.000")
    sold = _out(fill="On Bid", edge=-1.0, time="12:00:00.000")
    doc = _flows(_capture(outrights=[deep, lottery, sold]))
    purity = {f["what"]: (f["factors"]["purity"], tuple(f["flags"])) for f in doc["flows"]}
    assert purity["15 Jan 27 5C"][0] == 0.3 and "deep ITM" in purity["15 Jan 27 5C"][1]
    assert purity["15 Jan 27 25C"][0] == 0.5 and "lottery" in purity["15 Jan 27 25C"][1]
    assert purity["15 Jan 27 16C"][0] == pytest.approx(0.75)  # sold: x0.75
    dividend = {**MARKET, "names": {"PCG": {"close": 12.32, "ex_dividend": "2026-12-30"}}}
    doc = _flows(_capture(outrights=[deep]), dividend)
    assert "before ex-dividend" in doc["flows"][0]["flags"] and doc["flows"][0]["factors"]["purity"] == 0.0


def test_size_is_a_fixed_scale_so_days_compare():
    assert qf.size_factor(1e5, None) == pytest.approx(0.05)
    assert qf.size_factor(5e7, None) == pytest.approx(1.0)
    assert qf.size_factor(1e9, None) == 1.0 and qf.size_factor(10, None) == 0.05
    assert qf.size_factor(None, 2e6) == qf.size_factor(1e6, None)  # premium stands in, halved


def test_opening_evidence_and_the_next_morning_confirmation():
    market = {**MARKET, "contracts": {"PCG   270115C00016000": {"open_interest": 91_606, "volume": 49_357}}}
    doc = _flows(_capture(outrights=[_out()]), market)
    f = doc["flows"][0]
    assert f["legs"][0]["start_oi"] == 91_606 and f["factors"]["opening"] == 0.6
    assert qf.classify_change(91_606, 132_000, 40_900) == "opened"
    assert qf.classify_change(91_606, 50_000, 40_900) == "closed"
    assert qf.classify_change(91_606, 95_000, 40_900) == "mixed"
    first = f["score"]
    doc = qf.confirm_flows(doc, {"PCG   270115C00016000": 132_000})
    assert (f["confirmed"], f["confirmed_factors"]["opening"]) == ("opened", 1.0)
    assert f["confirmed_score"] > first and f["score"] == first  # the first score is kept beside
    doc = qf.confirm_flows(doc, {"PCG   270115C00016000": 50_000})
    assert f["confirmed"] == "closed" and abs(f["confirmed_score"]) < abs(first)


def test_volume_over_the_starting_open_interest_counts_as_opening_evidence():
    market = {**MARKET, "contracts": {"PCG   270115C00016000": {"open_interest": 13_353, "volume": 49_357}}}
    f = _flows(_capture(outrights=[_out()]), market)["flows"][0]
    assert "volume over OI" in f["flags"] and f["factors"]["opening"] == 0.85


def test_outcomes_are_recorded_for_one_and_five_sessions_back(tmp_path, monkeypatch):
    monkeypatch.setattr(qf, "store", lambda: tmp_path)
    days = ["2026-09-25", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
    for d in days:
        (tmp_path / f"{d}.json").write_text("{}", encoding="utf-8")
        doc = {"names": [{"symbol": "PCG"}], "closes": {"PCG": 10.0}}
        (tmp_path / f"{d}.flow.json").write_text(json.dumps(doc), encoding="utf-8")
    assert qf.record_outcomes({"PCG": 11.0}, "2026-10-05") == ["2026-10-02", "2026-09-28"]
    one = json.loads((tmp_path / "2026-10-02.flow.json").read_text(encoding="utf-8"))
    assert one["outcomes"]["1d"] == {"through": "2026-10-05", "returns": {"PCG": 0.1}}
    five = json.loads((tmp_path / "2026-09-28.flow.json").read_text(encoding="utf-8"))
    assert five["outcomes"]["5d"]["returns"] == {"PCG": 0.1}
