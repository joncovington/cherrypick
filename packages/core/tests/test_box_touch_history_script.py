"""The box-touch study (scripts/box_touch_history.py): boxes, touches, placebos, the fade and trend
reads, the resampled rates and the declared pass test.

Synthetic paths and synthetic stores only. Each pass test is shown to FAIL on data built to fail it,
because a check that cannot fire reads as coverage it is not.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "box_touch_history.py"


def _module():
    spec = importlib.util.spec_from_file_location("box_touch_history", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bt = _module()
OPEN = 9 * 60 + 30


def path(spots: list[float], start: int = OPEN) -> list[tuple[int, float]]:
    return [(start + i, s) for i, s in enumerate(spots)]


def test_the_widest_box_measures_the_gap_side_from_the_open():
    lo, hi = bt.boxes(opn=110.0, prior=100.0, em=5.0)["widest"]
    assert (lo, hi) == (95.0, 115.0)  # gap up: lower from the prior close, upper from the open
    lo, hi = bt.boxes(opn=90.0, prior=100.0, em=5.0)["widest"]
    assert (lo, hi) == (85.0, 105.0)
    assert bt.boxes(opn=110.0, prior=100.0, em=5.0)["open"] == (105.0, 115.0)


def test_a_touch_records_the_side_the_overshoot_and_whether_the_close_came_back():
    p = path([100, 103, 106, 108, 104, 102])
    t = bt.touch_record(p, close=102, lo=94, hi=105, em=5.0, cutoff=OPEN + 100)
    assert t["side"] == "up" and t["index"] == 2
    assert t["overshoot"] == pytest.approx((108 - 105) / 5)
    assert t["back_inside"] is True
    t2 = bt.touch_record(p, close=107, lo=94, hi=105, em=5.0, cutoff=OPEN + 100)
    assert t2["back_inside"] is False


def test_a_touch_after_the_cutoff_does_not_count():
    p = path([100, 101, 102, 106])
    assert bt.touch_record(p, 106, lo=94, hi=105, em=5.0, cutoff=OPEN + 3) is None


def test_placebo_levels_score_the_close_against_each_first_touch():
    p = path([100, 103, 97, 101])
    # +0.5 EM = 102.5 touched then closed below it (back); -0.5 EM = 97.5 touched, closed above (back)
    assert bt.placebo_record(p, close=101, opn=100, em=5.0, k=0.5, cutoff=OPEN + 100) == [True, True]


def test_the_fade_reads_the_price_w_minutes_after_the_touch():
    spots = [100.0] * 10 + [106.0] + [103.0] * 70  # touch 105 at minute 10, back inside after
    p = path(spots)
    t = bt.touch_record(p, close=103, lo=94, hi=105, em=5.0, cutoff=OPEN + 200)
    f = bt.fade_record(p, t, lo=94, hi=105, windows=(30, 60, 120))
    assert f[30] is True and f[60] is True and f[120] is None  # the path ends before 120 minutes


def test_the_trend_read_scores_the_close_against_the_ema_side():
    rising = [7000 + i * 0.5 for i in range(390)]  # 09:30 to 16:00, steady up
    p = path(rising)
    hits = bt.trend_records(p, close=rising[-1])
    assert hits and all(hits)  # the 20 EMA leads the 40 up, and the close is higher
    assert not any(bt.trend_records(p, close=6900.0))


def test_boot_rate_resamples_sessions_not_outcomes():
    groups = [[True, True, True, True], [False]]
    r = bt.boot_rate(groups, n=400)
    assert r["rate"] == pytest.approx(0.8) and r["sessions"] == 2
    assert r["lo"] <= 0.2 and r["hi"] >= 0.99  # two sessions: the interval spans both


def _rep(back_rate: float, placebo_rate: float, years=("2022", "2023", "2024", "2025", "2026")):
    """A report shaped like analyse()'s, built to a chosen edge and placebo rate per year."""
    back, plac, by_year = [], [], {}
    for y in years:
        nb, npl = 100, 200
        bg = [[i < back_rate * nb] for i in range(nb)]
        pg = [[i < placebo_rate * npl] for i in range(npl)]
        back += bg + [[] for _ in range(npl - nb)]
        plac += pg
        by_year[y] = {"touches": nb, "back_inside": back_rate, "placebo": placebo_rate}
    widest = {
        "edge_minus_placebo": bt.boot_diff(back, plac, n=300),
        "by_year": by_year,
        "fade": {60: bt.boot_rate([[True] * 7 + [False] * 3] * 50, n=300)},
    }
    return {"widest": widest, "trend": bt.boot_rate([[True, False]] * 100, n=300)}


def test_the_box_test_passes_a_real_reversion_and_fails_none():
    good = bt.verdict(_rep(0.70, 0.50))
    assert good["box"]["pass"] is True
    flat = bt.verdict(_rep(0.50, 0.50))
    assert flat["box"]["pass"] is False
    assert flat["box"]["checks"]["diff_at_least_10pts"] is False


def test_the_fade_and_trend_tests_need_the_interval_above_half():
    v = bt.verdict(_rep(0.5, 0.5))
    assert v["fade"]["pass"] is True  # 70% on 500 outcomes, interval well above 50%
    assert v["trend"]["pass"] is False  # exactly half


HOLDOUT = ("2025-07-01", "2026-10-12")


def _store(tmp_path, days: list[str]) -> Path:
    """A history db in the recorder's schema whose every session is a short path (dropped)."""
    db = tmp_path / "history.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE gex_spot_history (symbol TEXT, trade_date TEXT, ts REAL, spot REAL)")
    conn.execute("CREATE TABLE daily_closes (symbol TEXT, trade_date TEXT, close REAL)")
    for d in days:
        ts = datetime.fromisoformat(f"{d}T10:00").replace(tzinfo=bt.ET).timestamp()
        conn.execute("INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, 7000.0)", (d, ts))
        conn.execute("INSERT INTO daily_closes VALUES ('SPX', ?, 7000.0)", (d,))
    conn.commit()
    conn.close()
    return db


def test_a_window_reads_up_to_its_end_and_from_the_session_before_its_start():
    stored = ["2025-06-27", "2025-06-30", "2025-07-01", "2025-07-02", "2025-07-03"]
    assert bt.days_to_read(stored, "2025-07-01", "2025-07-02") == ["2025-06-30", "2025-07-01", "2025-07-02"]
    assert bt.days_to_read(stored, None, "2025-06-30") == ["2025-06-27", "2025-06-30"]
    assert bt.days_to_read(stored, "2025-07-02", None) == ["2025-07-01", "2025-07-02", "2025-07-03"]
    assert bt.days_to_read(stored, None, None) == stored


def test_the_holdout_is_refused_on_what_is_read_unless_allowed(tmp_path, monkeypatch, capsys):
    db = _store(tmp_path, ["2025-06-27", "2025-06-30", "2025-07-01", "2026-01-02", "2026-10-13"])
    monkeypatch.setattr(bt, "holdout_window", lambda: HOLDOUT)
    called = []
    monkeypatch.setattr(bt, "load_sessions", lambda *a, **k: called.append(a) or ([], {}))
    run = lambda *extra: bt.main(["--history-db", str(db), *extra])  # noqa: E731
    # An open end reads to the store's last session: the bypass an argument-only check has.
    assert run("--start", "2026-01-01") == 2
    assert run() == 2
    assert run("--start", "2026-01-01", "--end", "2026-12-31") == 2
    # After the holdout, the session before the start is read for its close, and it is in it.
    assert run("--start", "2026-10-13") == 2
    assert "pass --allow-holdout" in capsys.readouterr().err
    assert not called
    assert run("--end", "2025-06-30") == 0
    assert run("--start", "2026-01-01", "--allow-holdout") == 0
    assert len(called) == 2


def test_the_holdout_is_the_backtest_settings_window(monkeypatch):
    settings = pytest.importorskip("cherrypick.backtest.settings")  # not installed in core's own CI
    moved = settings.resolve(
        {"windows": {"design": ["2022-05-16", "2025-01-31"], "holdout": ["2025-02-03", "2026-01-30"]}}
    )
    monkeypatch.setattr(settings, "load", lambda *a, **k: moved)
    assert bt.holdout_window() == ("2025-02-03", "2026-01-30")


def test_drops_count_only_the_window(tmp_path):
    db = _store(tmp_path, ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"])
    assert bt.load_sessions(db, "SPX", 0.85) == ([], {"short_path": 4})
    assert bt.load_sessions(db, "SPX", 0.85, "2025-01-03", "2025-01-06") == ([], {"short_path": 2})
    assert bt.load_sessions(db, "SPX", 0.85, None, "2025-01-02") == ([], {"short_path": 1})


def test_a_year_window_keeps_only_its_sessions():
    """B14: the re-test runs the declared test unchanged on one year's sessions."""
    m = _module()
    days = [{"date": d} for d in ("2025-12-31", "2026-01-02", "2026-06-30", "2027-01-04")]
    assert [x["date"] for x in m.in_window(days, "2026-01-01", "2026-12-31")] == ["2026-01-02", "2026-06-30"]
    assert len(m.in_window(days, None, None)) == 4
