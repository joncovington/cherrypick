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


def test_the_sites_own_sentiment_is_the_second_vote():
    """Its fill wording ("Mid Market") is broader than its edge, so the wording is not the vote: on
    2026-10-02 the site's sentiment agreed with every read where the wording looked like a
    disagreement."""
    agrees = _out(fill="Mid Market", edge=0.72, time=None)  # sentiment Bullish, a call bought: agrees
    f = _flows(_capture(sweeps=[agrees]))["flows"][0]
    assert f["site_vote"] == "agrees" and f["factors"]["conviction"] == pytest.approx(
        0.3 + 0.7 * 0.72 + 0.1, abs=1e-3
    )
    opposite = {**_out(time=None), "side": {"sentiment": "Bearish", "fill": "On Ask", "edge": 1.0}}
    f = _flows(_capture(sweeps=[opposite]))["flows"][0]
    assert f["site_vote"] == "opposite" and "sentiment opposite" in f["flags"]
    assert f["factors"]["conviction"] == pytest.approx(1.0 * 0.5)
    neutral = {**_out(time=None), "side": {"sentiment": "Neutral", "fill": "On Ask", "edge": 1.0}}
    f = _flows(_capture(sweeps=[neutral]))["flows"][0]
    assert f["site_vote"] == "neutral" and f["factors"]["conviction"] == pytest.approx(0.75)


def test_the_brokers_delta_comes_first_and_the_model_is_checked_against_it():
    key = "PCG   270115C00016000"
    doc = _flows(_capture(outrights=[_out()]), {**MARKET, "greeks": {key: 0.213}})
    f = doc["flows"][0]
    assert (f["delta_from"], f["delta"]) == ("broker", 0.213)
    assert f["model_delta"] == pytest.approx(0.22, abs=0.02) and "delta check" not in f["flags"]
    assert doc["checks"]["delta"] == {"broker": 1, "singles": 1, "compared": 1, "off": []}
    # A model far from the broker is flagged and counted: the model is what has to explain itself.
    doc = _flows(_capture(outrights=[_out()]), {**MARKET, "greeks": {key: 0.45}})
    assert "delta check" in doc["flows"][0]["flags"]
    assert doc["checks"]["delta"]["off"] == ["PCG 15 Jan 27 16C: broker +0.45, model +0.22"]
    # No greeks: the model stands.
    assert _flows(_capture(outrights=[_out()]))["flows"][0]["delta_from"] == "trade"


def test_the_close_check_and_the_hand_audit():
    out = qf.close_check({"PCG": 12.32, "VST": 140.02, "SKHY": 195.13}, {"PCG": 12.32, "VST": 141.0})
    assert out == {"compared": 2, "of": 3, "off": ["VST: broker 140.02, dolt 141.00"]}
    doc = {"session": SESSION, "flows": [{"symbol": "PCG", "what": "15 Jan 27 16C", "direction": "bought"}]}
    good = qf.audit_entry(doc, 1, "bought")
    bad = qf.audit_entry(doc, 1, "middle", "printed between the quotes")
    assert good["agrees"] and not bad["agrees"]
    summary = qf.audit_summary([good, bad])
    assert (summary["checked"], summary["agree"], summary["rate"]) == (2, 1, 0.5)
    assert summary["disagree"] == ["2026-10-02 #1 PCG 15 Jan 27 16C: ours bought, tape middle"]


def test_the_fixed_review_counts_hits_and_waits_for_enough_sessions():
    day = (
        {
            "flows": [
                {"symbol": "PCG", "score": 46.0},
                {"symbol": "MU", "score": -12.0},
                {
                    "symbol": "F",
                    "score": 31.0,
                    "confirmed_score": -5.0,
                },  # the confirmed score is the one judged
            ],
            "names": [{"symbol": "PCG", "net": 1e7}, {"symbol": "MU", "net": -5e6}],
            "outcomes": {"5d": {"returns": {"PCG": 0.04, "MU": 0.02, "F": -0.01}}},
        },
        {"tables": {"outrights": [{"symbol": "MU", "premium": 400_000, "side": {"sentiment": "Bullish"}}]}},
    )
    out = qf.review([day])
    assert out["hit_rates"]["strong"] == (1.0, 1)  # PCG: +46 and up
    assert out["hit_rates"]["all_read"] == (round(2 / 3, 3), 3)  # PCG yes, MU no, F (-5) yes
    assert out["hit_rates"]["net_delta_dollars"] == (0.5, 2)
    assert out["hit_rates"]["site_premium"] == (1.0, 1)
    assert out["ready"] is False and out["passed"] is None and out["needed"] == qf.REVIEW_SESSIONS


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
        "cp": "call",
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


def test_a_put_spread_bought_is_a_debit_short_its_delta():
    """2026-10-05, the first put spreads seen: a debit with a negative delta is a put spread bought,
    bearish — not a disagreement. The call-spread rule (signs alike) left NVDA's $17.5M unread."""
    nvda = {
        "symbol": "NVDA",
        "time_et": "11:12:13.000",
        "size": 25_000,
        "expires": "2027-01-15",
        "type": "PS",
        "cp": "put",
        "spread": "270115 220/170 PS",
        "price": 7.02,
        "delta": -0.23,
        "premium": 17_550_000,
        "underlying": {"last": 185.0},
        "group": None,
    }
    sold = {**nvda, "price": -1.10, "delta": 0.12, "premium": -2_750_000, "time_et": "11:30:00.000"}
    odd = {**nvda, "price": 7.02, "delta": 0.23, "time_et": "12:00:00.000"}  # a debit long delta: no put spread
    doc = _flows(_capture(spreads=[nvda, sold, odd]))
    read = {f["time_et"]: (f["direction"], f["view"]) for f in doc["flows"]}
    assert read == {"11:12:13.000": ("bought", "bearish"), "11:30:00.000": ("sold", "bullish")}
    assert [f["time_et"] for f in doc["unread"]] == ["12:00:00.000"]


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


def test_every_flow_reads_date_strike_then_kind():
    assert qf.describe("outright", [{"expires": "2027-01-15", "strike": 16.0, "cp": "call"}]) == (
        "15 Jan 27 16C",
        "outright",
    )
    legs = qf.spread_legs("261009 43.5/45.5 CS", "CS")
    assert qf.describe("spread", legs, "261009 43.5/45.5 CS", "CS") == ("09 Oct 26 43.5/45.5C", "call spread")
    legs = qf.spread_legs("261016 23/261030 22 CSCAL", "CSCAL")
    assert qf.describe("spread", legs, "261016 23/261030 22 CSCAL", "CSCAL") == (
        "16 Oct 26 23C / 30 Oct 26 22C",
        "call diagonal",  # two dates at two strikes: the site's CSCAL, but not a calendar
    )
    legs = qf.spread_legs("261016 23/261030 23 CSCAL", "CSCAL")
    assert qf.describe("spread", legs, "261016 23/261030 23 CSCAL", "CSCAL")[1] == "call calendar"
    legs = qf.spread_legs("261030 28/261218 25 PSCAL", "PSCAL")  # CMG, 2026-10-05
    assert qf.describe("spread", legs, "261030 28/261218 25 PSCAL", "PSCAL")[1] == "put diagonal"
    assert qf.describe("spread", [], "261016 23/25/27 BFLY", "BFLY") == (
        "261016 23/25/27 BFLY",
        "bfly",
    )  # never guessed


def test_confirmation_waits_until_the_overnight_open_interest_is_out():
    """2026-10-03, a Saturday: every contract still showed Friday's starting figure, and the first
    version called all 30 flows `mixed`. Nothing moved means nothing published, not a mixed day."""
    market = {**MARKET, "contracts": {"PCG   270115C00016000": {"open_interest": 91_606, "volume": 49_357}}}
    doc = _flows(_capture(outrights=[_out()]), market)
    assert qf.oi_published(doc, {"PCG   270115C00016000": 91_606}) is False
    assert qf.oi_published(doc, {"PCG   270115C00016000": 132_000}) is True


def test_the_scheduled_score_takes_only_todays_capture(tmp_path, monkeypatch):
    """A day with no capture must not re-score the last one under a post already made."""
    monkeypatch.setattr(qf, "store", lambda: tmp_path)
    monkeypatch.setattr(qf, "_today", lambda: "2026-10-05")
    (tmp_path / f"{SESSION}.json").write_text(json.dumps(_capture()), encoding="utf-8")
    monkeypatch.setattr(qf, "_market", lambda *a, **k: pytest.fail("scored a day that was not today"))
    assert qf.main(["score", "--require-today"]) == 0
    assert not qf.flow_path(SESSION).exists()


def test_a_step_waits_for_the_one_before_it_and_gives_up_at_the_bound(monkeypatch):
    clock = {"t": 0.0, "polls": 0}
    monkeypatch.setattr(qf.time, "monotonic", lambda: clock["t"])

    def sleep(s):
        clock["t"] += s
        clock["polls"] += 1

    monkeypatch.setattr(qf.time, "sleep", sleep)
    assert qf.wait_for(lambda: clock["polls"] >= 3, minutes=45) is True
    assert clock["polls"] == 3
    clock.update(t=0.0, polls=0)
    assert qf.wait_for(lambda: False, minutes=2) is False
    assert clock["polls"] == 4  # 2 minutes of 30-second polls, then it stops
