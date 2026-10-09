"""Round 4's relative-strength breakout and its runner.

Each setup is checked against its rule in the test's own words, both ways (every entry meets it,
and every bar that meets it with no position open is an entry), as test_round3.py does.
"""

from __future__ import annotations

import math
import random
import sys
from collections import defaultdict

from cherrypick.technicals import history, round4, setups, store

sys.path.insert(0, __file__.rsplit("tests", 1)[0] + "tests")
from test_setups import _check  # noqa: E402
from test_study_run import _land  # noqa: E402


def _series(n=2000, seed=3):
    """A name that trends up and down against a flatter benchmark, with volume spikes."""
    rnd = random.Random(seed)
    closes, bench, vols = [], [], []
    c = b = 100.0
    for i in range(n):
        c *= 1 + (0.9 * math.sin(i / 40) + rnd.gauss(0, 1.3)) / 100
        b *= 1 + rnd.gauss(0.02, 0.8) / 100
        closes.append(c)
        bench.append(b)
        vols.append(None if i == 500 else 1e6 * (2.5 if rnd.random() < 0.35 else 1))
    dates = [f"d{i:04d}" for i in range(n)]
    return dates, closes, dict(zip(dates, bench, strict=True)), vols


# ------------------------------------------------------------------------------------- the rules


def test_a_new_high_is_strictly_above_every_one_of_the_prior_sessions():
    v = [1.0] * 21 + [1.0]
    assert not round4.new_high(v, 21), "a tie is not a new high"
    v[-1] = 1.01
    assert round4.new_high(v, 21)
    assert not round4.new_high(v[1:], 20), "fewer than 21 prior sessions cannot qualify"
    v[5] = None
    assert not round4.new_high(v, 21), "an unknown prior session cannot be a high to beat"


def test_the_ratio_is_unknown_where_the_benchmark_did_not_trade():
    assert round4.ratio(["a", "b"], [10.0, 12.0], {"a": 5.0}) == [2.0, None]


def test_volume_confirms_at_one_and_a_half_times_the_prior_thirty_and_never_when_missing():
    v = [100.0] * 30 + [150.0]
    assert round4.volume_confirms(v, 30), "at least 1.5x confirms (the vendor's 'at least')"
    v[-1] = 149.9
    assert not round4.volume_confirms(v, 30)
    v[-1] = 1000.0
    v[3] = None
    assert not round4.volume_confirms(v, 30)
    assert not round4.volume_confirms([None] * 30 + [1e9], 30)


def test_each_setup_enters_exactly_where_its_rule_says_and_holds_21_sessions():
    dates, closes, bench, vols = _series()
    rs = [c / bench[d] for d, c in zip(dates, closes, strict=True)]

    def both(i):
        return i >= 21 and closes[i] > max(closes[i - 21 : i]) and rs[i] > max(rs[i - 21 : i])

    def fresh(i):
        return both(i) and not any(both(j) for j in range(i - 10, i))

    def loud(i):
        if i < 30:
            return False
        w = vols[i - 30 : i]
        return vols[i] is not None and None not in w and vols[i] >= 1.5 * sum(w) / 30

    st = round4.states(closes, round4.ratio(dates, closes, bench))
    plain = round4.positions("rs-break", st, vols)
    _check(plain, fresh, lambda j, t: j - t.entry == 21, len(closes))
    with_vol = round4.positions("rs-break-vol", st, vols)
    _check(with_vol, lambda i: fresh(i) and loud(i), lambda j, t: j - t.entry == 21, len(closes))
    assert len(with_vol) < len(plain)


def test_every_entry_fires_in_the_state_its_matched_baseline_is_drawn_from():
    dates, closes, bench, vols = _series(seed=8)
    rs = round4.ratio(dates, closes, bench)
    st = round4.states(closes, rs)
    for setup_id in round4.FAMILY:
        trades = round4.positions(setup_id, st, vols)
        assert trades
        for t in trades:
            assert round4.state(closes, rs, t.entry), (setup_id, t)


def test_the_baseline_trade_is_held_the_same_21_sessions():
    assert round4.held(5, 100) == setups.Trade(5, exit=26, reason="time")
    assert round4.held(90, 100) == setups.Trade(90), "past the data's end it is still open"


def test_draws_never_cross_into_the_slice_the_exploratory_look_saw():
    """The tested entries are the $20M-$300M slice; a baseline draw from the $300M slice would
    compare them with names the look already saw."""
    by_date = defaultdict(list)
    by_name = defaultdict(list)
    for k in range(60):
        sl = "tested" if k % 2 else "seen"
        for d in ("2015-01-02", "2015-01-05", "2015-01-06"):
            by_date[(d, sl)].append(f"N{k}")
            by_name[(f"N{k}", sl)].append(d)
    rows = [{"setup": "rs-break", "symbol": "N1", "date": "2015-01-05", "slice": "tested"}]
    work = round4.draw_plan(rows, by_date, by_name)
    drawn = set(work) - {"N1"}
    assert drawn and all(int(s[1:]) % 2 == 1 for s in drawn)
    assert {d for _, _, d in work["N1"]} == {"2015-01-02", "2015-01-06"}


def test_the_slice_boundary_is_the_studys_300m_view():
    assert round4.in_slice(299_999_999.0) == "tested"
    assert round4.in_slice(300e6) == "seen"
    assert round4.in_slice(None) == "tested"


def test_the_study_only_setups_stay_off_the_chart():
    ids = set(round4.FAMILY)
    assert ids <= {s.id for s in setups.STUDIED}
    assert not ids & {s.id for s in setups.SETUPS} and not ids & set(setups.RUN)


# ------------------------------------------------------------------------------------- the runner


def test_round4_runs_end_to_end_and_judges_nothing_on_a_tiny_sample(tmp_path):
    path = tmp_path / "history.db"
    _land(path, names=6, sessions=700)
    conn = history.connect(path)
    days = [r[0] for r in conn.execute("SELECT DISTINCT date FROM bars ORDER BY date")]
    rnd = random.Random(99)
    c, rows = 100.0, []
    for d in days:
        c *= 1 + rnd.gauss(0.03, 0.7) / 100
        rows.append(("SPY", d, c, c * 1.005, c * 0.995, c, 1e8))
    store.upsert_bars(conn, rows)
    conn.commit()
    conn.close()
    result = round4.run(workers=2, path=path, progress=lambda m: None)
    assert result["names_in_universe"] == 7
    assert set(result["hypotheses"]) == {round4.hypothesis_id(s, b) for s, b in round4.TESTS}
    traded = 0
    for v in result["setups"].values():
        d = v["describe"]
        if d["entries"]:
            traded += 1
            # the names trade ~$100M a day, so the tested slice; SPY ($10B) is the other slice and
            # never a draw, so each entry gets the other five names and up to 40 of its own days
            assert d["median_baseline_draws"] >= 40
            assert d["median_matched_draws"] < d["median_baseline_draws"]
        assert v["views"]["seen_300m"]["describe"]["entries"] == 0
    assert traded == 2
    assert not any(v["test"].get("judged") for v in result["hypotheses"].values())
    assert not any(v["passed"] for v in result["hypotheses"].values())
