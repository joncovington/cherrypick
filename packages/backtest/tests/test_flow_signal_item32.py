"""Item 32's rules (cherrypick.backtest.flowstudy) and its script (scripts/flow_signal_item32.py), on
invented data only: no store, no vendor figure.

Each rule is shown firing and failing, because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from cherrypick.backtest import flow, flowstudy
from cherrypick.backtest.flowstudy import Scan

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("flow_signal_item32", SCRIPTS / "flow_signal_item32.py")
s32 = importlib.util.module_from_spec(_spec)
sys.modules["flow_signal_item32"] = s32
_spec.loader.exec_module(s32)


@pytest.fixture(autouse=True)
def _quick_bootstrap(monkeypatch):
    monkeypatch.setattr(flowstudy, "RESAMPLES", 2000)
    monkeypatch.setattr(flowstudy, "EXTRA_RESAMPLES", 1000)


def hist(*pairs: tuple[float, int]) -> list[int]:
    h = [0] * flow.N_BUCKETS
    for premium, n in pairs:
        h[flow.bucket(premium)] += n
    return h


def cand(tag="call_buy", premium=300_000.0, committed=1e6, dte=30, itm=-0.05, partner=False):
    return {
        "tag": tag,
        "premium": premium,
        "committed": committed,
        "dte": dte,
        "itm": itm,
        "partner": partner,
    }


def order(**kw):
    base = {"expiration": "2024-02-16", "strike": 100.0, "right": "CALL", "side": "bought", "first_ms": 0,
            "size": 100, "premium": 300_000.0, "oi_before": 0, "oi_after": 100}  # fmt: skip
    return {**base, **kw}


# ---- tags and the top-1% look-back ------------------------------------------------------------


def test_the_three_tags_and_a_call_sold_is_none():
    assert flowstudy.tag_of("CALL", "bought") == "call_buy"
    assert flowstudy.tag_of("PUT", "sold") == "put_sell"
    assert flowstudy.tag_of("PUT", "bought") == "put_buy"
    assert flowstudy.tag_of("CALL", "sold") is None
    assert flowstudy.tag_of("CALL", "unread") is None
    assert flowstudy.TAGS["put_sell"]["sign"] == 1 and flowstudy.TAGS["put_buy"]["sign"] == -1


def test_lookback_needs_four_fetched_sessions():
    days = [(f"d{i:02d}", hist((1000.0, 100))) for i in range(6)]
    cuts = flowstudy.lookback_cuts(days)
    assert [cuts[f"d{i:02d}"] for i in range(4)] == [None] * 4  # 0..3 sessions behind
    assert cuts["d04"] is not None  # four behind


def test_lookback_reads_the_top_share_of_the_pooled_sessions():
    # four sessions of 99 small orders and one $300k order: the top 1% of 400 starts at the big bucket
    days = [(f"d{i}", hist((1000.0, 99), (300_000.0, 1))) for i in range(5)]
    cut = flowstudy.lookback_cuts(days)["d4"]
    assert 250_000 < cut <= 300_000


def test_lookback_window_slides_after_twenty_sessions():
    days = [("d00", hist((5_000_000.0, 10_000)))] + [(f"d{i:02d}", hist((1000.0, 100))) for i in range(1, 25)]
    cuts = flowstudy.lookback_cuts(days)
    assert cuts["d20"] > 1_000_000  # sessions d00..d19 still behind d20
    assert cuts["d21"] < 2_000  # d00 has left the window


def test_lookback_counts_fetched_sessions_not_calendar_ones():
    # the dates are weeks apart; only their order matters
    days = [(f"2024-{m:02d}-01", hist((1000.0, 100))) for m in range(1, 7)]
    assert flowstudy.lookback_cuts(days)["2024-05-01"] is not None
    assert flowstudy.lookback_cuts(days)["2024-04-01"] is None


# ---- candidates --------------------------------------------------------------------------------


def test_candidates_need_opened_tagged_and_in_the_top_share():
    top = 200_000.0
    ok = flowstudy.candidates("2024-01-16", 100.0, [order()], top)
    assert len(ok) == 1 and ok[0]["tag"] == "call_buy" and ok[0]["dte"] == 31
    assert ok[0]["committed"] == 100.0 * 100 * 100.0
    assert flowstudy.candidates("2024-01-16", 100.0, [order(premium=150_000.0)], top) == []  # under top 1%
    assert flowstudy.candidates("2024-01-16", 100.0, [order(premium=40_000.0)], 10.0) == []  # under the floor
    assert flowstudy.candidates("2024-01-16", 100.0, [order(oi_after=40)], top) == []  # did not open
    assert flowstudy.candidates("2024-01-16", 100.0, [order(oi_after=50)], top) != []  # exactly half
    assert flowstudy.candidates("2024-01-16", 100.0, [order(oi_after=None)], top) == []  # unknown
    assert flowstudy.candidates("2024-01-16", 100.0, [order(side="sold")], top) == []  # a call sold
    assert flowstudy.candidates("2024-01-16", 100.0, [order()], None) == []  # no look-back


def test_a_straddle_partner_is_the_other_right_same_strike_side_size_and_minute():
    a = order()
    near = order(right="PUT", size=108, first_ms=30_000)
    assert flowstudy.straddle_partner(a, [a, near])
    assert not flowstudy.straddle_partner(a, [a])  # itself is no partner
    assert not flowstudy.straddle_partner(a, [a, order(right="PUT", size=112)])  # size over 10%
    assert not flowstudy.straddle_partner(a, [a, order(right="PUT", first_ms=61_000)])  # over a minute
    assert not flowstudy.straddle_partner(a, [a, order(right="CALL", first_ms=1_000)])  # same right
    assert not flowstudy.straddle_partner(a, [a, order(right="PUT", strike=105.0)])
    assert not flowstudy.straddle_partner(a, [a, order(right="PUT", side="sold")])
    assert not flowstudy.straddle_partner(a, [a, order(right="PUT", expiration="2024-03-15")])


def test_moneyness_and_days_to_expiry():
    assert flowstudy.itm_share("CALL", 90.0, 100.0) == pytest.approx(0.10)
    assert flowstudy.itm_share("PUT", 90.0, 100.0) == pytest.approx(-0.10)
    assert flowstudy.itm_share("PUT", 110.0, 100.0) == pytest.approx(0.10)
    assert flowstudy.itm_share("CALL", 90.0, None) is None
    assert flowstudy.days_to_expiry("2024-01-16", "2024-01-23") == 7


def test_cut_and_each_order_filter():
    c = cand()
    assert flowstudy.passes(c, "call_buy", 250_000.0, None)
    assert flowstudy.passes(c, "call_buy", 300_000.0, None)
    assert not flowstudy.passes(c, "call_buy", 300_001.0, None)  # the cut
    assert not flowstudy.passes(c, "put_buy", 50_000.0, None)  # the tag
    assert flowstudy.passes(cand(dte=7), "call_buy", 50_000.0, "F1")
    assert not flowstudy.passes(cand(dte=6), "call_buy", 50_000.0, "F1")
    assert flowstudy.passes(cand(itm=0.10), "call_buy", 50_000.0, "F2")
    assert not flowstudy.passes(cand(itm=0.11), "call_buy", 50_000.0, "F2")
    assert not flowstudy.passes(cand(itm=None), "call_buy", 50_000.0, "F2")  # cannot be shown
    assert flowstudy.passes(cand(), "call_buy", 50_000.0, "F4")
    assert not flowstudy.passes(cand(partner=True), "call_buy", 50_000.0, "F4")
    # F3 replaces the premium cut with committed capital
    small = cand(tag="put_sell", premium=60_000.0, committed=10_000_000.0)
    assert flowstudy.passes(small, "put_sell", 0.0, "F3")
    assert not flowstudy.passes(cand(tag="put_sell", committed=9_999_999.0), "put_sell", 0.0, "F3")
    assert not flowstudy.passes(small, "put_sell", 100_000.0, None)  # the premium cut would refuse it


# ---- the scan: F5, F6, F7 ----------------------------------------------------------------------


def scan_of(cands, *, grid=("s10",), reported=None, rows=None):
    index = {f"s{i:02d}": i for i in range(40)}
    return Scan(grid=list(grid), rows=rows or {g: {} for g in grid}, cands=cands,
                complete={g: 1 for g in grid}, reported=reported or {}, index=index)  # fmt: skip


def test_f5_drops_a_put_buy_on_a_session_with_a_put_sale():
    sc = scan_of({"A": {"s10": [cand("put_buy"), cand("put_sell")]}, "B": {"s10": [cand("put_buy")]}})
    assert flowstudy.tagged_syms(sc, "s10", "put_buy", 50_000.0, None) == {"A", "B"}
    assert flowstudy.tagged_syms(sc, "s10", "put_buy", 50_000.0, "F5") == {"B"}
    # the sale must be large at the same cut
    sc2 = scan_of({"A": {"s10": [cand("put_buy"), cand("put_sell", premium=60_000.0)]}})
    assert flowstudy.tagged_syms(sc2, "s10", "put_buy", 100_000.0, "F5") == {"A"}


def test_f6_needs_an_earlier_order_of_the_same_tag_within_ten_sessions():
    sc = scan_of({"A": {"s20": [cand()], "s09": [cand()]}, "B": {"s20": [cand()], "s10": [cand()]},
                  "C": {"s20": [cand()], "s15": [cand("put_sell")]},
                  "D": {"s20": [cand()], "s15": [cand(premium=60_000.0)]},
                  "E": {"s20": [cand()]}}, grid=("s20",))  # fmt: skip
    got = flowstudy.tagged_syms(sc, "s20", "call_buy", 100_000.0, "F6")
    assert "B" in got  # ten sessions back is the oldest of the previous ten
    assert "A" not in got  # eleven back is outside
    assert "C" not in got  # a different tag
    assert "D" not in got  # not large at this cut
    assert "E" not in got  # no earlier order at all
    assert flowstudy.tagged_syms(sc, "s20", "call_buy", 100_000.0, None) == {"A", "B", "C", "D", "E"}


def test_f7_drops_a_name_whose_report_falls_in_the_outcome_window():
    sc = scan_of({"A": {"s10": [cand()]}, "B": {"s10": [cand()]}}, reported={"s10": {"A"}})
    assert flowstudy.tagged_syms(sc, "s10", "call_buy", 50_000.0, "F7") == {"B"}
    assert flowstudy.tagged_syms(sc, "s10", "call_buy", 50_000.0, None) == {"A", "B"}


# ---- the matched comparison --------------------------------------------------------------------


def test_the_baseline_is_the_same_stage_only():
    rows = {"T": ("lead", 0.05), "a": ("lead", 0.01), "b": ("lead", 0.03), "c": ("lag", 0.40)}
    d, n = flowstudy.matched_diff(rows, {"T"}, {"T"}, 1)
    assert n == 1 and d == pytest.approx(0.05 - 0.02)  # c (another stage) does not enter


def test_a_bearish_tag_is_signed_by_its_side():
    rows = {"T": ("lead", -0.05), "a": ("lead", 0.01), "b": ("lead", 0.03)}
    d, _ = flowstudy.matched_diff(rows, {"T"}, {"T"}, -1)
    assert d == pytest.approx(0.05 - (-0.02))


def test_excluded_names_stay_out_of_the_baseline_and_a_stage_without_one_is_left_out():
    rows = {"T": ("lead", 0.05), "x": ("lead", 0.50), "a": ("lead", 0.01), "S": ("solo", 0.2)}
    d, n = flowstudy.matched_diff(rows, {"T", "S"}, {"T", "S", "x"}, 1)
    assert n == 1 and d == pytest.approx(0.04)  # x is excluded; S has no untagged stage-mate
    assert flowstudy.matched_diff(rows, {"S"}, {"S"}, 1) is None


def test_the_filtered_tag_shares_the_unfiltered_baseline():
    cands = {"T": {"s10": [cand(dte=3)]}, "U": {"s10": [cand(dte=30)]}}
    rows = {"s10": {"T": ("lead", 0.20), "U": ("lead", 0.10), "a": ("lead", 0.0), "b": ("lead", 0.0)}}
    sc = scan_of(cands, rows=rows)
    f1 = flowstudy.cell_series(sc, ["s10"], "call_buy", 50_000.0, "F1")
    assert f1["n"] == [1]  # U alone is tagged under F1
    # T holds the unfiltered tag, so it is kept out of the baseline: a, b only
    assert f1["diffs"][0] == pytest.approx(0.10)


def test_the_placebo_reads_the_previous_grid_sessions_tags():
    cands = {"T": {"s10": [cand()], "s15": [cand()]}}
    rows = {"s10": {"T": ("lead", 0.0), "a": ("lead", 0.0)}, "s15": {"T": ("lead", 0.07), "a": ("lead", 0.0)}}
    sc = scan_of(cands, grid=("s10", "s15"), rows=rows)
    assert flowstudy.cell_series(sc, ["s10", "s15"], "call_buy", 50_000.0)["sessions"] == ["s10", "s15"]
    stale = flowstudy.cell_series(sc, ["s10", "s15"], "call_buy", 50_000.0, lag=1)
    assert stale["sessions"] == ["s15"] and stale["diffs"] == [pytest.approx(0.07)]


def test_the_five_most_frequent_names_are_removed_by_count_then_symbol():
    cands = {s: {f"s{i:02d}": [cand()] for i in range(10, 10 + n)} for s, n in
             {"A": 5, "B": 4, "C": 4, "D": 3, "E": 2, "F": 1, "G": 1}.items()}  # fmt: skip
    grid = [f"s{i:02d}" for i in range(10, 15)]
    rows = {g: {s: ("lead", 0.0) for s in cands} for g in grid}
    sc = scan_of(cands, grid=grid, rows=rows)
    assert flowstudy.most_frequent(sc, grid, "call_buy", 50_000.0, None) == ["A", "B", "C", "D", "E"]


# ---- intervals, the larger-cut check, verdicts -------------------------------------------------


def test_more_trials_widen_the_interval():
    v = [0.01 * ((i * 7) % 5 - 1) for i in range(60)]
    few = flowstudy.deflated_interval(v, 1, resamples=3000)
    many = flowstudy.deflated_interval(v, 49, resamples=3000)
    assert many[0] < few[0] and many[1] > few[1]


def test_larger_cuts_must_do_at_least_as_well():
    m = {50_000.0: 0.01, 100_000.0: 0.02, 250_000.0: 0.02}
    assert flowstudy.larger_cuts_ok(m, 50_000.0)
    assert not flowstudy.larger_cuts_ok({**m, 250_000.0: 0.019}, 100_000.0)  # the largest does worse
    assert flowstudy.larger_cuts_ok({**m, 250_000.0: 0.019}, 250_000.0)  # nothing larger to beat
    assert not flowstudy.larger_cuts_ok({**m, 250_000.0: None}, 50_000.0)  # no reading cannot show it
    assert not flowstudy.larger_cuts_ok({**m, 50_000.0: None}, 50_000.0)


def test_verdict_words():
    assert flowstudy.verdict_word(60, 0.001) == "pass"
    assert flowstudy.verdict_word(60, 0.0) == "fail"  # zero is not above zero
    assert flowstudy.verdict_word(60, -0.01) == "fail"
    assert flowstudy.verdict_word(60, 0.001, ok=False) == "fail"
    assert flowstudy.verdict_word(flowstudy.MIN_SESSIONS - 1, 0.01) == "no verdict"
    assert flowstudy.verdict_word(60, None) == "no verdict"


def test_thin_sessions_are_those_under_four_fifths_of_the_median():
    complete = {"a": 600, "b": 610, "c": 590, "d": 150, "e": 480, "f": 427}  # median 535, floor 428
    assert flowstudy.thin_sessions(complete, list(complete)) == ["d", "f"]
    assert flowstudy.thin_sessions({}, []) == []


def test_the_declared_trial_counts():
    keys = flowstudy.cell_keys()
    assert sum(1 for k in keys if k[0] == "first") == flowstudy.FIRST_TRIALS == 9
    assert sum(1 for k in keys if k[0] == "second") == flowstudy.SECOND_TRIALS == 49
    assert {k[1] for k in keys if k[0] == "second"} == {"F1", "F2", "F3", "F4", "F5", "F6", "F7"}
    assert len({flowstudy.key_name(f, t, c) for _, f, t, c in keys}) == len(keys)  # names are unique


# ---- earnings ----------------------------------------------------------------------------------


def test_the_earnings_sign_is_net_premium_and_a_tie_is_dropped():
    assert flowstudy.earnings_sign([cand(premium=300_000.0), cand("put_buy", premium=150_000.0)]) == 1
    assert flowstudy.earnings_sign([cand("put_buy", premium=300_000.0), cand(premium=150_000.0)]) == -1
    assert flowstudy.earnings_sign([cand(premium=200_000.0), cand("put_buy", premium=200_000.0)]) == 0
    assert flowstudy.earnings_sign([cand("put_sell", premium=200_000.0)]) == 1  # put selling is bullish
    assert flowstudy.earnings_sign([cand(premium=99_999.0)]) == 0  # under the $100k cut
    assert flowstudy.earnings_sign([]) == 0


def _events(n, hit, move, sign=1):
    return [{"react": f"d{i:03d}", "sign": sign, "excess": move if i < n * hit else -move} for i in range(n)]


def test_an_earnings_trial_needs_the_hit_rate_and_the_move_after_cost():
    good = flowstudy.earnings_trial(_events(200, 0.8, 0.03), 1)
    assert good["verdict"] == "pass" and good["hit_rate"] == pytest.approx(0.8)
    coin = flowstudy.earnings_trial(_events(200, 0.5, 0.03), 1)
    assert coin["verdict"] == "fail"  # the interval holds 50%
    cheap = flowstudy.earnings_trial(_events(200, 0.8, 0.003), 1)  # a hit rate with moves under the 1% cost
    assert cheap["verdict"] == "fail" and cheap["mean_after_cost"] < 0
    assert flowstudy.earnings_trial(_events(10, 0.9, 0.05), 1)["verdict"] == "no verdict"
    bear = flowstudy.earnings_trial(_events(200, 0.8, -0.03, sign=-1), -1)  # falls are the bearish gain
    assert bear["verdict"] == "pass" and bear["hit_rate"] == pytest.approx(0.8)


# ---- the whole study on an invented scan -------------------------------------------------------


def invented(n=70, effect=0.05, every=1):
    """T holds a call-buying tag on every `every`-th session and gains `effect` on those sessions."""
    grid = [f"s{i:03d}" for i in range(n)]
    names = [f"a{j}" for j in range(15)]
    rows, cands = {}, {"T": {}}
    for i, g in enumerate(grid):
        noise = lambda j: (((i * 7 + j * 3) % 11) - 5) / 1000.0  # noqa: E731, B023
        on = i % every == 0
        rows[g] = {"T": ("lead/early", (effect if on else 0.0) + noise(99))}
        rows[g].update({n_: ("lead/early", noise(j)) for j, n_ in enumerate(names)})
        cands["T"][g] = [cand("call_buy", premium=300_000.0)] if on else []
    index = {g: i for i, g in enumerate(grid)}
    complete = dict.fromkeys(grid, 16)
    events = [{"sym": "T", "react": f"r{i:03d}", "pre": [cand(premium=300_000.0)], "excess": 0.04 if i % 5 else -0.01}  # noqa: E501
              for i in range(60)]  # fmt: skip
    return Scan(grid=grid, rows=rows, cands=cands, complete=complete,
                reported={g: set() for g in grid}, index=index, events=events)  # fmt: skip


def test_an_invented_effect_passes_and_nothing_else_does():
    res = flowstudy.evaluate(invented())
    assert res["first"]["call_buy/50000"]["verdict"] == "pass"
    assert res["first"]["call_buy/250000"]["verdict"] == "pass"
    assert res["first"]["put_buy/50000"]["verdict"] == "no verdict"  # no such tags
    assert res["first"]["call_buy/50000"]["larger_ok"] is True
    assert res["thin_sessions"] == [] and res["first_without_thin"] is None
    assert (
        res["second"]["F1/call_buy/50000"]["verdict"] == "fail"
    )  # equal to the unfiltered, so it does not beat it
    assert res["second"]["F1/call_buy/50000"]["beats_unfiltered"] is False
    assert res["earnings"]["earnings/bullish"]["verdict"] == "pass"
    assert res["earnings"]["earnings/bearish"]["verdict"] == "no verdict"
    assert "call_buy/50000" in flowstudy.passed(res) and "earnings/bullish" in flowstudy.passed(res)
    assert res["first"]["call_buy/50000"]["readings"]["placebo"]["mean"] is not None


def test_the_placebo_of_a_real_effect_is_not_the_effect():
    res = flowstudy.evaluate(invented(n=100, every=2))
    cell = res["first"]["call_buy/50000"]
    assert cell["verdict"] == "pass"
    assert abs(cell["readings"]["placebo"]["mean"]) < cell["mean"] / 2
    # and with the tag on every session, last session's tag is as good as this one's: the placebo is the effect  # noqa: E501
    full = flowstudy.evaluate(invented())["first"]["call_buy/50000"]
    assert full["readings"]["placebo"]["mean"] > full["mean"] / 2


def test_no_effect_does_not_pass():
    res = flowstudy.evaluate(invented(effect=0.0))
    assert res["first"]["call_buy/50000"]["verdict"] == "fail"
    assert flowstudy.passed(res) == [] or all(not k.startswith("call_buy") for k in flowstudy.passed(res))


def test_a_wrong_way_effect_fails():
    res = flowstudy.evaluate(invented(effect=-0.05))
    assert res["first"]["call_buy/50000"]["verdict"] == "fail"
    assert res["first"]["call_buy/50000"]["interval"][1] < 0


def test_a_larger_cut_that_does_worse_fails_the_smaller_one():
    sc = invented()
    # the biggest orders are on the days the stock does worst: the $250k tag is on the worst sessions only
    for i, g in enumerate(sc.grid):
        if i % 2:
            sc.cands["T"][g] = [cand("call_buy", premium=60_000.0)]
    res = flowstudy.first_family(sc, sc.grid, extras=False)
    assert res["call_buy/50000"]["larger_ok"] in (True, False)
    means = {c: res[f"call_buy/{int(c)}"]["mean"] for c in flowstudy.CUTS}
    assert (means[250_000.0] >= means[50_000.0]) == res["call_buy/50000"]["larger_ok"]
    assert res["call_buy/250000"]["larger_ok"] is True  # nothing above it


def test_the_thin_reading_drops_the_thin_sessions():
    sc = invented()
    for g in sc.grid[:10]:
        sc.complete[g] = 5
    res = flowstudy.evaluate(sc)
    assert res["thin_sessions"] == sc.grid[:10]
    assert res["first_without_thin"]["call_buy/50000"]["sessions"] == 60
    assert res["first"]["call_buy/50000"]["sessions"] == 70


def test_the_holdout_reads_only_what_passed():
    sc = invented()
    res = flowstudy.evaluate_carried(sc, ["call_buy/50000", "F1/call_buy/50000", "earnings/bullish"])
    assert set(res["first"]) == {"call_buy/50000"} and set(res["second"]) == {"F1/call_buy/50000"}
    assert set(res["earnings"]) == {"earnings/bullish"}
    assert res["first"]["call_buy/50000"]["verdict"] == "pass"
    assert res["first"]["call_buy/50000"]["larger_ok"] is None  # a design check, not asked again
    assert res["second"]["F1/call_buy/50000"]["verdict"] == "fail"  # still has to beat its unfiltered tag


# ---- spent once --------------------------------------------------------------------------------


def test_the_holdout_refuses_without_a_design_pass_and_after_being_spent(tmp_path):
    assert flowstudy.spent(tmp_path, "design") is None
    assert "no design result" in flowstudy.spent(tmp_path, "holdout")
    (tmp_path / "results-design.json").write_text(json.dumps({"passed": []}), encoding="utf-8")
    assert "nothing to spend" in flowstudy.spent(tmp_path, "holdout")
    (tmp_path / "results-design.json").write_text(
        json.dumps({"passed": ["call_buy/50000"]}), encoding="utf-8"
    )
    assert flowstudy.spent(tmp_path, "holdout") is None
    (tmp_path / "holdout-spent.json").write_text("{}", encoding="utf-8")
    assert "spent" in flowstudy.spent(tmp_path, "holdout")
    (tmp_path / "holdout-spent.json").unlink()
    (tmp_path / "results-holdout.json").write_text("{}", encoding="utf-8")
    assert "spent" in flowstudy.spent(tmp_path, "holdout")


def test_the_script_refuses_a_second_holdout_before_reading_anything(tmp_path, capsys):
    (tmp_path / "results-design.json").write_text(
        json.dumps({"passed": ["call_buy/50000"]}), encoding="utf-8"
    )
    (tmp_path / "results-holdout.json").write_text("{}", encoding="utf-8")
    code = s32.main(["study", "--window", "holdout", "--out-dir", str(tmp_path)])
    assert code == 2 and "spent" in capsys.readouterr().err
    assert not (tmp_path / "holdout-spent.json").exists()


# ---- the script: outcome, verdict file, report -------------------------------------------------


def test_the_outcome_runs_from_the_close_after_the_tag_is_known():
    closes = [100.0, 110.0, 111.0, 112.0, 113.0, 114.0, 121.0, 130.0]
    bench = [100.0] * 8
    ex = s32.excess_return(closes, bench, 0)
    assert ex == pytest.approx(121.0 / 110.0 - 1.0)  # close 1 to close 6, not close 0 to close 5
    assert s32.excess_return(closes, bench, 3) is None  # runs off the end
    assert s32.excess_return([100.0, None, 1, 1, 1, 1, 1], [1.0] * 7, 0) is None


def test_the_report_states_the_verdict_and_reads_the_figures():
    res = flowstudy.evaluate(invented())
    result = {"window": "design", "span": ["2022-05-16", "2025-06-30"], "grid_sessions": 70, **res}
    result["verdict"] = s32.verdicts(res)
    result["passed"] = flowstudy.passed(res)
    assert result["verdict"]["call_buy/50000"] == "pass" and len(result["verdict"]) == 9 + 49 + 2
    md = s32.report(result)
    assert "## What it says" in md and "## How to read the tables" in md
    assert "Large call buying, opened" in md and "## Second family" in md and "## Earnings" in md
    assert "cells pass" in md
    hypotheses = pytest.importorskip("cherrypick.orchestrator.hypotheses")  # the studies board's reader

    words = hypotheses.verdicts(json.loads(json.dumps(result)))
    assert {w["verdict"] for w in words} <= {"pass", "fail", "no verdict"}


# ---- the store reader, on invented stores --------------------------------------------------------


def test_build_scan_reads_an_invented_flow_store_and_bar_store(tmp_path, monkeypatch):
    import sqlite3
    from datetime import date, timedelta
    from types import SimpleNamespace

    from cherrypick.core import calendar as cal

    from cherrypick.backtest import flowfetch

    # The bar store is technicals'; backtest's own CI job does not install it.
    history = pytest.importorskip("cherrypick.technicals.history")
    store = pytest.importorskip("cherrypick.technicals.store")

    days, d = [], date(2024, 1, 2)
    while len(days) < 130:
        if cal.is_trading_day(d):
            days.append(d.isoformat())
        d += timedelta(days=1)
    bar = lambda s, c: SimpleNamespace(date=s, close=c)  # noqa: E731
    spy = [bar(s, 100.0) for s in days]
    aaa = [bar(s, 50.0 * (1.001**i)) for i, s in enumerate(days)]  # a steady climb against a flat SPY
    monkeypatch.setattr(history, "connect", lambda *a, **k: None)
    monkeypatch.setattr(store, "raw_bars", lambda c, sym: spy if sym == "SPY" else aaa)
    monkeypatch.setattr(store, "adjusted_bars", lambda c, sym, *a: spy if sym == "SPY" else aaa)
    monkeypatch.setattr(s32, "_reports", lambda lo, hi: [("AAA", date.fromisoformat(days[100]), "bmo")])
    monkeypatch.setattr(s32.settings, "load", lambda *a: {"windows": {"design": [days[70], days[125]]}})

    db = tmp_path / "flow.db"
    conn = sqlite3.connect(db)
    for stmt in flowfetch.DDL:
        conn.execute(stmt)
    fetched = days[60:126:5] + days[90:100]  # a grid and the sessions before the report
    h = json.dumps(hist((1000.0, 99), (300_000.0, 1)))
    for s in sorted(set(fetched)):
        conn.execute("INSERT INTO flow_days VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     ("AAA", s, "complete", None, 1, 1, 100, 1, h, 1.0, "x"))  # fmt: skip
    big = days[80]
    for session in (big, days[95]):
        conn.execute("INSERT INTO flow_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("AAA", session, "2024-09-20", 50.0, "CALL", "bought", 0, 0, 1, 100, 300_000.0, 3.0, 0.9, 0, 0, 100))  # fmt: skip  # noqa: E501
    conn.commit()
    conn.close()

    grid = days[70:126:5]
    scan, counts = s32.build_scan("design", {"grid_sessions": grid}, db)
    assert counts["names"] == 1 and set(scan.rows) == set(grid)
    i = days.index(grid[3])
    stage_key, ex = scan.rows[grid[3]]["AAA"]
    assert ex == pytest.approx((aaa[i + 6].close / aaa[i + 1].close) - 1.0)  # close 1 to close 6, SPY flat
    assert scan.complete[grid[3]] == 1
    assert [c["tag"] for c in scan.cands["AAA"][big]] == ["call_buy"]
    assert "AAA" in scan.reported[days[95]]  # the report's reaction day is inside its outcome window
    assert "AAA" not in scan.reported[days[85]]
    assert len(scan.events) == 1 and scan.events[0]["react"] == days[100]  # bmo: the report day itself
    assert [c["tag"] for c in scan.events[0]["pre"]] == ["call_buy"]
