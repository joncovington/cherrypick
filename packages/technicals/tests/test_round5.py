"""Round 5: the fundamentals score, the 1-10 score on history, the triggers and the matched pool."""

from __future__ import annotations

import random

from cherrypick.technicals import fundamentals as fu
from cherrypick.technicals import levels, round5, setups, stage, trend
from cherrypick.technicals.adjust import Bar

# ------------------------------------------------------------------------------ the measures


def test_growth_is_measured_against_the_size_of_the_prior_figure_whatever_its_sign():
    assert fu.growth(12.0, 10.0) == 0.2
    assert fu.growth(-1.0, -2.0) == 0.5, "a smaller loss is growth"
    assert fu.growth(1.0, 0.0) is None and fu.growth(None, 1.0) is None


def test_net_margin_counts_four_quarters_only_once_each_is_published():
    qs = [
        ("2023-03-31", 100.0, 10.0),
        ("2023-06-30", 100.0, 10.0),
        ("2023-09-30", 100.0, 20.0),
        ("2023-12-31", 100.0, 40.0),
        ("2024-03-31", 100.0, 100.0),
    ]
    # 2024-03-31 is published only 45 days later; before then the trailing year is 2023
    assert fu.trailing_margin(qs, "2024-05-14") == (10 + 10 + 20 + 40) / 400
    assert fu.trailing_margin(qs, "2024-05-15") == (10 + 20 + 40 + 100) / 400
    assert fu.trailing_margin(qs[:3], "2024-06-01") is None, "three quarters are not a year"
    holed = [qs[0], qs[1], qs[3], ("2024-12-31", 100.0, 5.0)]
    assert fu.trailing_margin(holed, "2025-03-01") is None, "a missing quarter is not skipped over"


def test_an_unprofitable_name_has_no_pe_and_is_valued_on_sales():
    m = fu.measures(50.0, (-1.0, -2.0), (1000.0, 800.0), 10.0, -0.1)
    assert m.pe is None and m.ps == 50.0 * 10.0 / 1000.0
    assert fu.valuation_key(m) == (1, m.ps)
    assert fu.valuation_key(fu.measures(50.0, (5.0, 4.0), (1000.0, 800.0), 10.0, 0.1)) == (0, 10.0)
    assert fu.measures(None, (5.0, 4.0), (1.0, 1.0), 1.0, 0.1) is None


def test_percentiles_run_from_worst_to_best_and_ties_share_a_place():
    p = fu._percentiles({"a": 1.0, "b": 2.0, "c": 2.0, "d": 3.0})
    assert p == {"a": 0.0, "b": 1 / 3, "c": 1 / 3, "d": 1.0}
    q = fu._percentiles({"a": 1.0, "b": 2.0, "c": 3.0}, higher_is_better=False)
    assert q == {"a": 1.0, "b": 0.5, "c": 0.0}


def _m(pe, eg, rg, mar, ps=1.0):
    return fu.Measures(pe, ps, eg, rg, mar)


def test_the_score_weights_margin_twice_and_puts_every_loss_maker_behind_on_valuation():
    names = {
        "CHEAP": _m(10, 0.1, 0.1, 0.10),
        "MID": _m(20, 0.1, 0.1, 0.10),
        "RICH": _m(30, 0.1, 0.1, 0.10),
        "LOSS": _m(None, 0.1, 0.1, 0.10, ps=0.1),
        "FAT": _m(20, 0.1, 0.1, 0.30),
    }
    s = fu.scores(names, {k: "Tech" for k in names})
    assert s["CHEAP"] > s["MID"] > s["RICH"] > s["LOSS"], "valuation order, loss makers last"
    # FAT differs from MID only in margin: top margin (1.0) vs a shared bottom place (0.0), x2 / 5
    assert abs((s["FAT"] - s["MID"]) - 2 * 1.0 / 5) < 1e-9


def test_no_score_without_every_measure_or_in_a_group_too_small_to_rank():
    names = {f"N{k}": _m(10 + k, 0.1, 0.1, 0.1) for k in range(5)}
    names["GAP"] = _m(12, None, 0.1, 0.1)
    s = fu.scores(names, {**{k: "A" for k in names}, "LONE": "B"})
    assert "GAP" not in s and len(s) == 5
    assert fu.scores({k: v for k, v in list(names.items())[:4]}, {k: "A" for k in names}) == {}


def test_labels_take_the_top_and_bottom_fifteen_percent():
    sc = {f"N{k:02d}": float(k) for k in range(20)}
    lab = fu.labels(sc)
    assert sorted(k for k, v in lab.items() if v == "compelling") == ["N17", "N18", "N19"]
    assert sorted(k for k, v in lab.items() if v == "weak") == ["N00", "N01", "N02"]
    assert fu.labels({"a": 1.0, "b": 2.0}) == {}


def test_a_sunday_snapshot_first_applies_to_mondays_session():
    snaps = ["2024-06-02", "2024-06-09"]
    assert round5._snapshot_before(snaps, "2024-06-03") == "2024-06-02"
    assert round5._snapshot_before(snaps, "2024-06-09") == "2024-06-02", "never the same day's"
    assert round5._snapshot_before(snaps, "2024-06-01") is None


# ------------------------------------------------------------------------------ the 1-10 score


def _cal(n):
    return {f"d{k:04d}": k for k in range(n)}


def test_market_scores_are_the_live_rank_score_with_the_live_exclusions():
    n = 300
    idx = _cal(n)
    rnd = random.Random(4)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + rnd.gauss(0, 0.01)))
    raw = [Bar(d, c, c, c, c, 1e5) for d, c in zip(sorted(idx), closes, strict=True)]
    got = dict(round5.market_scores(idx, raw, []))
    for i in (126, 200, 299):
        assert abs(got[i] - levels.rank_score(closes[: i + 1])) < 1e-12
    assert 125 not in got
    # a split inside the 126-session window drops those sessions; one outside does not
    split = dict(round5.market_scores(idx, raw, ["d0250"]))
    assert 249 in split and 250 not in split and 299 not in split
    thin = [Bar(b.date, b.open, b.high, b.low, b.close, 1.0) for b in raw]
    assert round5.market_scores(idx, thin, []) == [], "under $100k a day is not the market"


def test_the_decile_comes_from_that_sessions_cutoffs():
    cut = levels.rank_cutoffs([float(k) for k in range(100)])
    assert round5.decile(0.0, cut) == 1 and round5.decile(99.0, cut) == 10
    assert round5.decile(1.0, None) is None and round5.decile(None, cut) is None


# ------------------------------------------------------------------------------ the triggers


def test_the_stage_series_is_the_live_rule_on_every_session():
    rnd = random.Random(9)
    n = 160
    bench, name = [100.0], [50.0]
    for i in range(n - 1):
        bench.append(bench[-1] * (1 + rnd.gauss(0.0003, 0.01)))
        name.append(name[-1] * (1 + rnd.gauss(0.001 * (1 if (i // 30) % 2 else -1), 0.02)))
    name[70] = None  # a missing close is never a reading
    days = [f"d{k:04d}" for k in range(n)]
    got = round5.stage_series(name, bench)
    b = dict(zip(days, bench, strict=True))
    c = {d: x for d, x in zip(days, name, strict=True) if x is not None}
    for k, d in enumerate(days):
        assert got[k] == stage.stages_on(d, {"X": c}, b).get("X"), d
    assert any(s and s.stage == "early" for s in got), "the fixture should reach an early stage"


def test_the_stage_trigger_is_an_early_reading_with_none_of_its_side_before():
    lead = stage.Stage("leader", "early")
    lag = stage.Stage("laggard", "early")
    seq = [None] * 10 + [lead]
    assert round5.stage_trigger(seq, 10, "bull")
    seq2 = [None] * 5 + [stage.Stage("leader", "building")] + [None] * 4 + [lead]
    assert not round5.stage_trigger(seq2, 10, "bull"), "a leader reading inside the fresh window"
    seq3 = [lag] * 10 + [lead]
    assert round5.stage_trigger(seq3, 10, "bull"), "the other side does not count against it"
    assert not round5.stage_trigger([None] * 10 + [stage.Stage("leader", "building")], 10, "bull")
    assert round5.stage_trigger([None] * 10 + [lag], 10, "bear")


def test_the_trend_trigger_is_the_crossing_into_the_strong_label():
    s = [2, 3, 4, 3, -2, -3, -4]
    assert [round5.trend_trigger(s, i, "bull") for i in range(len(s))] == [
        False,
        True,
        False,
        False,
        False,
        False,
        False,
    ]
    assert [round5.trend_trigger(s, i, "bear") for i in range(len(s))] == [
        False,
        False,
        False,
        False,
        False,
        True,
        False,
    ]
    assert not round5.trend_trigger([None, 4], 1, "bull")
    assert trend.label(3) == "Bullish" and trend.label(-3) == "Bearish"


# ------------------------------------------------------------------------------ the pool


def test_the_baseline_is_the_other_scores_same_trigger_and_label_within_ten_sessions():
    dates = [f"d{k:04d}" for k in range(100)]

    def row(day, dec, r, side="bull", trig="stage", sector=True):
        return {
            "side": side,
            "trigger": trig,
            "symbol": f"S{day}{dec}",
            "date": dates[day],
            "decile": dec,
            "label_sector": sector,
            "label_universe": True,
            "r": r,
        }

    rows = [
        row(50, 2, 1.0),  # tested
        row(45, 7, 0.4),
        row(60, 9, 0.2),  # pool: other deciles inside +-10
        row(61, 5, 9.0),  # outside the window
        row(52, 3, 5.0),  # another tested entry: never in the pool
        row(50, 6, 7.0, trig="trend"),  # another trigger
        row(50, 6, 7.0, sector=False),  # not labelled in this variant
        row(50, 9, 3.0, side="bear"),  # the bear side tests 8-10; this is its tested entry
    ]
    out = {(r["side"], r["date"], r["decile"]): r for r in round5.pools(rows, "sector", dates)}
    first = out[("bull", dates[50], 2)]
    assert first["base_n"] == 2 and abs(first["base"] - 0.3) < 1e-12
    assert ("bull", dates[45], 7) not in out, "only the tested deciles are entries"
    assert out[("bear", dates[50], 9)]["base"] is None


def test_the_study_only_records_carry_each_side():
    by = {s.id: s for s in setups.STUDIED}
    assert by["rs-fund"].side == "long" and by["rs-fund-short"].side == "short"
    assert not {"rs-fund", "rs-fund-short"} & set(setups.RUN)
