"""Item 106's script (futures_oi_pace_item106.py): invented reports and closes only, nothing read."""

from __future__ import annotations

import importlib.util
import json
import random
from datetime import date, timedelta
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "futures_oi_pace_item106", Path(__file__).resolve().parents[1] / "scripts" / "futures_oi_pace_item106.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

RULE = {
    **m.FORECAST_RULE,
    "from": "2000-01-01",
    "through": "2099-01-01",
    "high_window": 150,
    "horizon": 10,
    "min_n": 30,
    "holdout_min_n": 10,
}


def _sessions(start: date, n: int) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _case(oi_by_date=None):
    """Flat at 100 for 150 sessions (to 2021-02-19), down half a point a session to 80 by 2021-04-16,
    then flat: one drawdown from early March. Weekly Tuesday reports from 2021-02-16; VIX 30 to 36."""
    closes = [100.0] * 150 + [100 - 0.5 * k for k in range(1, 41)] + [80.0] * 100
    days = _sessions(date(2020, 7, 27), len(closes))
    assert days[189] == "2021-04-16"
    spx = list(zip(days, closes, strict=True))
    vix = {d: 30.0 + k % 7 for k, d in enumerate(days)}
    d0 = date(2021, 2, 16)
    reports = []
    for k in range(30):
        d = (d0 + timedelta(weeks=k)).isoformat()
        if d > days[-1]:
            break
        reports.append({"d": d, "oi": (oi_by_date or {}).get(d, 100 + k), "lev_long": 1, "lev_short": 2})
    return reports, spx, vix


def test_the_rule_is_complete_and_the_chain_is_one_study_row():
    r = m.FORECAST_RULE
    assert r["kind"] == "forecast" and r["splits"] == ["2019-01-01"]
    assert all(v is not None for v in r.values())
    assert r["max_slope"] < 0 and r["t_bar"] < 0
    assert [s["step"] for s in m.CHAIN] == ["study"]


def test_vix_closes_are_read_by_date():
    text = (
        "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/1990,17.24,17.24,17.24,17.24\n11/19/2026,22.06,29.13,21.84,28.44\n"
    )
    assert m.parse_vix(text) == {"1990-01-02": 17.24, "2026-11-19": 28.44}


def test_a_change_never_spans_an_expiry_or_a_roll_report():
    reports, spx, vix = _case()
    rows, dropped = m.build_rows(reports, spx, vix, RULE)
    dates = [r["date"] for r in rows]
    assert dates and dropped["swell"] >= 2 and dropped["no_prior"] >= 1
    # 2021 expiries Mar 19 and Jun 18: Mar 9 and 16 are in the roll window, and Mar 23's prior report
    # is one of them, so Mar 23 has no change; Jun 22's prior (Jun 15) is in the window too.
    assert "2021-03-16" not in dates and "2021-03-23" not in dates and "2021-03-30" in dates
    assert "2021-06-22" not in dates
    for r in rows:
        d = date.fromisoformat(r["date"])
        prior = d - timedelta(weeks=1)
        assert m.i105.expiry_on_or_after(prior) == m.i105.expiry_on_or_after(d)
        assert not m.i105.is_swell(prior, RULE["swell_days"])


def test_the_cycle_check_alone_keeps_a_change_inside_one_cycle():
    # With no roll window, only the cycle check stands between Mar 16 and Mar 23 (Mar 19 expiry).
    reports, spx, vix = _case()
    rows, _ = m.build_rows(reports, spx, vix, {**RULE, "swell_days": -1})
    dates = [r["date"] for r in rows]
    assert "2021-03-16" in dates and "2021-03-23" not in dates and "2021-03-30" in dates


def test_the_change_fall_and_reference_session_are_read_as_declared():
    reports, spx, vix = _case({"2021-04-06": 120, "2021-04-13": 132})
    rows, _ = m.build_rows(reports, spx, vix, RULE)
    r = {x["date"]: x for x in rows}["2021-04-13"]
    assert abs(r["doi"] - 0.10) < 1e-12
    assert r["ref"] > "2021-04-16" and r["fall"] == 0.0  # flat at 80 after
    assert r["vix"] == vix["2021-04-13"]
    early = {x["date"]: x for x in rows}["2021-03-30"]
    assert early["ref"] == "2021-04-05" and early["fall"] < 0  # still falling in the ten sessions after
    assert early["low_day"] == 9 and r["low_day"] == 0  # the low lands 2021-04-16; none lower after it


def test_weeks_outside_a_drawdown_are_rows_only_when_asked():
    reports, spx, vix = _case()
    spx = [(d, 100.0 + 0.01 * k) for k, (d, _) in enumerate(spx)]
    rows, dropped = m.build_rows(reports, spx, vix, RULE)
    assert rows == [] and dropped["outside"] > 0
    rows, _ = m.build_rows(reports, spx, vix, RULE, in_drawdown_only=False)
    assert rows


def _rows(
    n: int, slope: float, *, start: str = "2010-01-05", noise: float = 0.0, seed: int = 1
) -> list[dict]:
    rng = random.Random(seed)
    d0 = date.fromisoformat(start)
    out = []
    for k in range(n):
        doi, vixv, prior, depth = (
            rng.gauss(0, 0.02),
            rng.uniform(15, 45),
            rng.gauss(0, 0.03),
            rng.uniform(0.07, 0.3),
        )
        fall = -3 + slope * doi / 0.02 - 0.05 * (vixv - 30) + rng.gauss(0, noise)
        out.append({"date": (d0 + timedelta(weeks=k)).isoformat(), "doi": doi, "vix": vixv, "prior": prior,
                    "depth": depth, "fall": fall, "ret": fall, "week_ret": 0.0})  # fmt: skip
    return out


def test_the_fit_recovers_a_known_slope_per_standard_deviation():
    rows = _rows(300, -1.5)
    scale = m.scaling(rows)
    f = m.fit(rows, scale)
    sd = scale["doi"][1]
    assert abs(f["coef"]["doi"] - (-1.5 * sd / 0.02)) < 1e-6


def test_too_few_design_weeks_is_no_verdict_and_the_holdout_is_not_read():
    out = m.judge(_rows(20, -2.0), RULE)
    assert out["verdict"] == {"oi_slope": m.NO_VERDICT} and out["holdout"]["verdict"] == m.NOT_READ


def test_a_design_fail_leaves_the_holdout_unread():
    rows = _rows(200, 0.0, noise=1.0) + _rows(60, -3.0, start="2019-01-08", noise=0.5)
    out = m.judge(rows, RULE, resamples=40)
    assert out["design"]["verdict"] == m.FAIL and out["holdout"]["verdict"] == m.NOT_READ
    assert "slope" not in out["holdout"]


def test_a_design_pass_reads_the_holdout():
    rows = _rows(200, -2.0, noise=0.5) + _rows(60, -2.0, start="2019-01-08", noise=0.5, seed=2)
    out = m.judge(rows, RULE, resamples=40)
    assert out["design"]["verdict"] == m.PASS and out["verdict"] == {"oi_slope": m.PASS}
    assert out["holdout"]["slope"] < 0 and out["provisional"] is False


def test_each_end_is_read_against_the_middle():
    rows = [{"doi": x, "fall": f, "ret": 0.0} for x, f in ((-0.03, -1.0), (0.0, -2.0), (0.03, -4.0))]
    t = m.terciles(rows, (-0.01, 0.01))
    assert t["falling"]["fall_less_middle"] == 1.0 and t["rising"]["fall_less_middle"] == -2.0


def test_the_study_step_writes_its_verdict_and_reported_cuts(tmp_path, monkeypatch):
    reports, spx, vix = _case()
    rng = random.Random(3)
    for r in reports:
        r["oi"] = 100 + rng.randint(-5, 5)
    (tmp_path / "SPX.csv").write_text(
        "DATE,SPX\n" + "".join(f"{d[5:7]}/{d[8:10]}/{d[:4]},{c}\n" for d, c in spx), encoding="utf-8"
    )
    (tmp_path / "VIX.csv").write_text(
        "DATE,OPEN,HIGH,LOW,CLOSE\n"
        + "".join(f"{d[5:7]}/{d[8:10]}/{d[:4]},1,1,1,{v}\n" for d, v in vix.items()),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "FORECAST_RULE", {**RULE, "min_n": 5, "splits": ["2021-05-01"]})
    monkeypatch.setattr(m.i105, "load_reports", lambda: reports)
    monkeypatch.setattr(m.i105, "spx_path", lambda: tmp_path / "SPX.csv")
    monkeypatch.setattr(m, "vix_path", lambda: tmp_path / "VIX.csv")
    monkeypatch.setattr(m, "out_dir", lambda: tmp_path)
    monkeypatch.setenv("CHERRYPICK_PROGRESS_FILE", str(tmp_path / "progress.json"))
    assert m.main(["study", "--resamples", "10"]) == 0
    doc = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert set(doc["verdict"]) == {"oi_slope"} and doc["rows"] > 0
    assert {"terciles", "without_controls", "all_weeks", "same_week", "time_profile"} <= set(doc["reported"])
    assert list(doc["reported"]["time_profile"]) == [str(h) for h in m.TIME_PROFILE]
