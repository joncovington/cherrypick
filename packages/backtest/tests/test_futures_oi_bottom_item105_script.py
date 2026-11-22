"""Item 105's script (futures_oi_bottom_item105.py): invented reports and closes only, nothing fetched."""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "futures_oi_bottom_item105",
    Path(__file__).resolve().parents[1] / "scripts" / "futures_oi_bottom_item105.py",
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

RULE = {**m.FORECAST_RULE, "from": "2000-01-01", "through": "2099-01-01", "high_window": 150, "horizon": 10}


def _sessions(start: date, n: int) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _spx(closes: list[float], start: date = date(2021, 1, 4)) -> list[tuple[str, float]]:
    return list(zip(_sessions(start, len(closes)), closes, strict=True))


def _tuesdays(start: date, n: int) -> list[str]:
    d = start + timedelta(days=(1 - start.weekday()) % 7)
    return [(d + timedelta(weeks=k)).isoformat() for k in range(n)]


def test_the_rule_is_complete_and_the_chain_is_fetch_then_study():
    r = m.FORECAST_RULE
    assert r["kind"] == "forecast" and r["splits"] == ["2019-01-01"]
    assert all(v is not None for v in r.values())
    assert set(r["signals"]) == set(m.SIGNALS)
    assert [s["step"] for s in m.CHAIN] == ["fetch", "study"]


def test_the_roll_window_is_the_two_reports_before_a_quarterly_expiry():
    assert m.expiry_on_or_after(date(2025, 3, 4)) == date(2025, 3, 21)
    assert m.expiry_on_or_after(date(2025, 12, 23)) == date(2026, 3, 20)
    assert m.expiry_on_or_after(date(2025, 3, 21)) == date(2025, 3, 21)
    assert [
        m.is_swell(date.fromisoformat(d), 13)
        for d in ("2025-03-04", "2025-03-11", "2025-03-18", "2025-03-25")
    ] == [
        False,
        True,
        True,
        False,
    ]
    assert m.is_swell(date(2025, 12, 16), 13) and not m.is_swell(date(2025, 12, 23), 13)


def test_only_the_emini_rows_are_parsed_and_dates_come_from_yymmdd():
    text = (
        '"Market_and_Exchange_Names","As_of_Date_In_Form_YYMMDD","CFTC_Contract_Market_Code",'
        '"Open_Interest_All","Lev_Money_Positions_Long_All","Lev_Money_Positions_Short_All"\n'
        '"E-MINI S&P 500",251230,13874A , 1936495, 100, 300\n'
        '"S&P 500 STOCK INDEX",251230,138741 , 5000, 1, 2\n'
        '"E-MINI S&P 500",160105,13874A ,2754179.000000,100.000000,300.000000\n'  # the 2006-2016 file
    )
    assert m.parse_cot(text, "13874A") == [
        {"d": "2025-12-30", "oi": 1936495, "lev_long": 100, "lev_short": 300},
        {"d": "2016-01-05", "oi": 2754179, "lev_long": 100, "lev_short": 300},
    ]


def test_reports_come_from_the_manifests_files_only(tmp_path):
    import json

    (tmp_path / "fin_fut_txt_2006_2016.json").write_text(json.dumps([{"d": "2016-12-27", "oi": 1}]))
    (tmp_path / "fut_fin_txt_2017.json").write_text(json.dumps([{"d": "2017-01-03", "oi": 2}]))
    (tmp_path / "manifest.json").write_text(
        json.dumps({"files": {"fin_fut_txt_2006_2016.zip": {}, "fut_fin_txt_2017.zip": {}}})
    )
    (tmp_path / "manifest.version.json").write_text(json.dumps({"script": "x", "version": 1}))  # the queue's
    assert [r["d"] for r in m.load_reports(tmp_path)] == ["2016-12-27", "2017-01-03"]


def test_a_later_file_wins_a_date_both_hold():
    a = [{"d": "2016-12-27", "oi": 1}, {"d": "2016-12-20", "oi": 1}]
    b = [{"d": "2016-12-27", "oi": 2}]
    assert [r["oi"] for r in m.merge_reports([a, b])] == [1, 2]


def _drawdown_case(oi: list[int]) -> tuple[list[dict], list[tuple[str, float]]]:
    """Closes flat at 100 for 150 sessions (to 2021-02-19), down to 85 by mid-March, then flat: one
    drawdown from the fall on, inside the 150-session high window through July."""
    closes = [100.0] * 150 + [100 - k for k in range(1, 16)] + [85.0] * 120
    spx = _spx(closes, start=date(2020, 7, 27))
    assert spx[149][0] == "2021-02-19"
    tues = [t for t in _tuesdays(date(2021, 2, 16), 30) if t <= spx[-1][0]]
    reports = [{"d": t, "oi": oi[k % len(oi)], "lev_long": 10, "lev_short": 40} for k, t in enumerate(tues)]
    return reports, spx


def test_a_fall_from_the_peak_inside_one_cycle_is_a_signal_and_the_peak_resets_at_expiry():
    # 2021 expiries: Mar 19, Jun 18. Reports from mid-February.
    reports, spx = _drawdown_case([100])
    for r in reports:
        r["oi"] = 100 if r["d"] < "2021-03-23" else (100 if r["d"] < "2021-04-13" else 90)
    rows, dropped = m.build_rows(reports, spx, RULE)
    assert rows and dropped["swell"] >= 1
    by = {r["date"]: r for r in rows}
    assert not by["2021-04-06"]["oi_fall"] and by["2021-04-13"]["oi_fall"]
    # A lower reading after the June expiry is not a fall: the peak belongs to the old cycle.
    for r in reports:
        r["oi"] = 120 if r["d"] < "2021-06-18" else 110
    rows, _ = m.build_rows(reports, spx, RULE)
    assert any(r["date"] < "2021-06-01" for r in rows)  # the old cycle has ordinary weeks
    assert any(r["date"] > "2021-06-18" for r in rows)
    assert not any(r["oi_fall"] for r in rows)
    naive = {**RULE, "cycle_reset": False}
    rows, _ = m.build_rows(reports, spx, naive)
    assert any(r["oi_fall"] for r in rows if r["date"] > "2021-06-18")


def test_roll_window_reports_never_signal_and_are_not_rows():
    reports, spx = _drawdown_case([100])
    rows, _ = m.build_rows(reports, spx, RULE)
    assert not any(m.is_swell(date.fromisoformat(r["date"]), RULE["swell_days"]) for r in rows)


def test_the_leveraged_short_signal_needs_a_net_short_peak_and_a_drop_in_share():
    reports, spx = _drawdown_case([100])
    for r in reports:
        r["lev_short"] = 40 if r["d"] < "2021-04-13" else 30  # share of 100: 0.30 then 0.20
    rows, _ = m.build_rows(reports, spx, RULE)
    by = {r["date"]: r for r in rows}
    assert not by["2021-04-06"]["lev_short_fall"] and by["2021-04-13"]["lev_short_fall"]
    for r in reports:
        r["lev_long"], r["lev_short"] = 50, 40  # net long throughout
    rows, _ = m.build_rows(reports, spx, RULE)
    assert not any(r["lev_short_fall"] for r in rows)


def test_price_is_read_after_the_release_and_held_means_the_low_stands():
    reports, spx = _drawdown_case([100])
    rows, _ = m.build_rows(reports, spx, RULE)
    r = rows[0]
    assert r["ref"] > (date.fromisoformat(r["date"]) + timedelta(days=3)).isoformat()
    assert all(x["held"] for x in rows if x["ref"] > "2021-03-05")  # flat at the low after it


def test_outside_a_drawdown_there_are_no_rows():
    spx = _spx([100.0 + 0.1 * k for k in range(250)], start=date(2020, 10, 5))
    reports = [{"d": t, "oi": 100, "lev_long": 1, "lev_short": 2} for t in _tuesdays(date(2021, 3, 1), 15)]
    rows, dropped = m.build_rows(reports, spx, RULE)
    assert rows == [] and dropped["outside"] > 0


def _row(
    signal: bool, held: bool, *, age: int = 3, bounce: float = 0.0, d: str = "2010-01-05", ep: int = 1
) -> dict:
    return {"date": d, "episode": ep, "age": age, "bounce": bounce, "held": held, "held_ref": held,
            "oi_fall": signal, "lev_short_fall": False}  # fmt: skip


def test_excess_matches_each_signal_week_to_its_own_cell():
    rows = [
        _row(True, True),
        _row(False, False),
        _row(False, True),  # young, no bounce: baseline 0.5
        _row(True, True, age=9, bounce=0.06),
        _row(False, True, age=9, bounce=0.06),  # old, big bounce: baseline 1.0
        _row(True, True, age=9, bounce=0.03),  # no baseline in its cell: left out
    ]
    e = m.excess(rows, "oi_fall", RULE)
    assert e["n"] == 2 and abs(e["excess"] - 0.25) < 1e-12 and e["baseline"] == 0.75


def test_too_few_signal_weeks_is_no_verdict_and_the_holdout_is_not_read():
    rows = [_row(True, True, d=f"2010-01-{k + 1:02d}") for k in range(5)] + [_row(False, False)] * 5
    out = m.judge_one(rows, "oi_fall", RULE)
    assert out["verdict"] == m.NO_VERDICT and out["holdout"]["verdict"] == m.NOT_READ


def test_a_design_fail_leaves_the_holdout_unread():
    design = [
        _row(k % 2 == 0, k % 3 == 0, d=f"2010-{k // 28 + 1:02d}-{k % 28 + 1:02d}", ep=k // 20)
        for k in range(200)
    ]
    holdout = [_row(True, True, d="2020-01-07", ep=99)] * 20 + [
        _row(False, False, d="2020-01-07", ep=99)
    ] * 20
    out = m.judge_one(design + holdout, "oi_fall", RULE, resamples=50)
    assert out["design"]["verdict"] == m.FAIL and out["holdout"]["verdict"] == m.NOT_READ
    assert "excess" not in out["holdout"]


def test_a_design_pass_reads_the_holdout():
    design = []
    for k in range(200):
        sig = k % 2 == 0
        held = sig if k % 10 != 0 else not sig  # a signal week fails now and then
        design.append(_row(sig, held, d=f"2010-{k // 28 + 1:02d}-{k % 28 + 1:02d}", ep=k // 20))
    holdout = [_row(True, True, d="2020-01-07", ep=99)] * 10 + [
        _row(False, False, d="2020-01-07", ep=99)
    ] * 10
    out = m.judge_one(design + holdout, "oi_fall", RULE, resamples=50)
    assert out["design"]["verdict"] == m.PASS and out["verdict"] == m.PASS and out["holdout"]["n"] == 10


def test_the_study_step_writes_a_verdict_per_signal_and_the_reported_cuts(tmp_path, monkeypatch):
    reports, spx = _drawdown_case([100])
    for r in reports:
        r["oi"] = 100 if r["d"] < "2021-04-13" else 90
    spx_file = tmp_path / "SPX.csv"
    spx_file.write_text(
        "DATE,SPX\n" + "".join(f"{d[5:7]}/{d[8:10]}/{d[:4]},{c}\n" for d, c in spx), encoding="utf-8"
    )
    monkeypatch.setattr(m, "FORECAST_RULE", RULE)
    monkeypatch.setattr(m, "load_reports", lambda: reports)
    monkeypatch.setattr(m, "spx_path", lambda: spx_file)
    monkeypatch.setattr(m, "out_dir", lambda: tmp_path)
    monkeypatch.setenv("CHERRYPICK_PROGRESS_FILE", str(tmp_path / "progress.json"))
    assert m.main(["study", "--resamples", "20"]) == 0
    doc = __import__("json").loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert set(doc["verdict"]) == set(m.SIGNALS) and doc["inputs"]["reports"] == len(reports)
    assert {"oi_fall", "lev_short_fall", "oi_fall_without_roll_handling"} <= set(doc["reported"])
    assert "first_signal_per_episode" in doc["reported"]["oi_fall"]
