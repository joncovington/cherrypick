"""The skate screen's forward record: what was shown, what happened, the declared test."""

from __future__ import annotations

import json

import pytest

from cherrypick.technicals import skate_record as sr
from cherrypick.technicals import store


def _leg(band, expiry, strike, hard=True, gap=0.08, implied=0.85, role="pick"):
    return {
        "band": band,
        "role": role,
        "expiry": expiry,
        "strike": strike,
        "hard": hard,
        "delta": 0.15,
        "implied_skate": implied,
        "skate": implied + gap,
        "gap": gap,
        "per_day_bid": 3.0,
        "soft": {"all": True},
    }


def _run_doc(strike=110.0, hard=True):
    best = {"expiry": "2026-10-30", "strike": strike}
    return {
        "rows": [
            {
                "symbol": "XYZ",
                "side": "C",
                "spot": 100.0,
                "best": {"fixed": best, "by_expiry": None},
                "legs": [
                    _leg("fixed", "2026-10-30", strike, hard=hard),
                    _leg("fixed", "2026-10-30", strike - 5, role="closer"),
                ],
            }
        ]
    }


def _write_run(root, session, run, doc):
    d = root / session
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{run}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_a_shown_row_is_the_bands_pick_on_its_best_expiry_and_only_if_it_passed_the_hard_filters():
    rows = sr.shown_rows(_run_doc())
    assert [(r["band"], r["strike"]) for r in rows] == [("fixed", 110.0)]  # not the neighbour, no null band
    assert sr.shown_rows(_run_doc(hard=False)) == []


def test_the_newest_scheduled_run_is_read_and_a_trial_never(tmp_path):
    _write_run(tmp_path, "2026-10-21", "1200", _run_doc())
    _write_run(tmp_path, "2026-10-21", "1505", _run_doc())
    (tmp_path / "2026-10-21" / "1852.trial.json").write_text("{}", encoding="utf-8")
    assert sr.newest_run(tmp_path / "2026-10-21") == "1505"


def test_a_strike_shown_again_is_recorded_once_and_runs_before_the_band_fix_never(tmp_path):
    root = tmp_path / "quotes"
    _write_run(root, "2026-10-19", "1505", _run_doc(strike=105.0))  # before RECORD_FROM
    _write_run(root, "2026-10-20", "1505", _run_doc())
    _write_run(root, "2026-10-21", "1505", _run_doc())
    conn = sr.connect(tmp_path / "record.db")
    added = sr.record(conn, root)
    assert added == {"2026-10-20": 1, "2026-10-21": 0}
    (only,) = conn.execute("SELECT session, strike FROM shown").fetchall()
    assert tuple(only) == ("2026-10-20", 110.0)
    assert sr.record(conn, root) == {}  # a recorded session is not read twice


@pytest.mark.parametrize(
    ("side", "strike", "path", "want"),
    [
        ("C", 110.0, [("d1", 105.0, 99.0, 104.0), ("d2", 108.0, 101.0, 107.0)], "skated"),
        ("C", 110.0, [("d1", 111.0, 99.0, 104.0), ("d2", 108.0, 101.0, 107.0)], "tested"),
        ("C", 110.0, [("d1", 105.0, 99.0, 104.0), ("d2", 112.0, 101.0, 110.0)], "through"),  # at the strike
        ("P", 90.0, [("d1", 101.0, 89.0, 95.0), ("d2", 99.0, 92.0, 96.0)], "tested"),
        ("P", 90.0, [("d1", 101.0, 93.0, 95.0), ("d2", 99.0, 88.0, 89.5)], "through"),
    ],
)
def test_the_outcome_is_read_at_expiry_and_a_touch_on_the_way_is_a_test(side, strike, path, want):
    assert sr.outcome(side, strike, path) == want


def test_settle_waits_for_the_expirys_bar_then_reads_it(tmp_path):
    root = tmp_path / "quotes"
    _write_run(root, "2026-10-20", "1505", _run_doc())
    conn = sr.connect(tmp_path / "record.db")
    sr.record(conn, root)
    hist = store.connect(tmp_path / "history.db")
    bars = [("XYZ", d, 100.0, 103.0, 99.0, 101.0, 1e6) for d in ("2026-10-20", "2026-10-21", "2026-10-29")]
    store.upsert_bars(hist, bars)
    hist.commit()
    assert sr.settle(conn, hist)["waiting"] == 1  # no 10-30 bar yet
    store.upsert_bars(hist, [("XYZ", "2026-10-30", 101.0, 111.0, 100.0, 108.0, 1e6)])
    hist.commit()
    assert sr.settle(conn, hist)["tested"] == 1  # closed below 110, but reached it that day
    (row,) = conn.execute("SELECT outcome, settle_close FROM shown").fetchall()
    assert tuple(row) == ("tested", 108.0)


def _settled(n, gap, implied, skated):
    return [
        {
            "band": "fixed",
            "expiry": f"2026-{10 + i // 28:02d}-{1 + i % 28:02d}",
            "gap": gap,
            "implied": implied,
            "outcome": "skated" if i < skated else "through",
        }
        for i in range(n)
    ]


def test_the_declared_test_is_not_judged_before_twenty_expiries():
    v = sr.verdict(_settled(10, 0.08, 0.80, 10))
    assert v["decision_grade"] is False and v["bands"]["fixed"]["passes"] is None


def test_the_declared_test_passes_only_when_high_gap_rows_beat_both_their_odds_and_the_low_rows():
    # 60 high-gap rows priced at 80% that skated 57 times, against 60 below-zero rows that skated 46.
    good = _settled(60, 0.08, 0.80, 57) + _settled(60, -0.02, 0.80, 46)
    v = sr.verdict(good)
    assert v["decision_grade"] and v["bands"]["fixed"]["passes"] is True
    # The same high rows skating only at their priced rate fail, however the low rows did.
    flat = _settled(60, 0.08, 0.80, 48) + _settled(60, -0.02, 0.80, 30)
    assert sr.verdict(flat)["bands"]["fixed"]["passes"] is False
