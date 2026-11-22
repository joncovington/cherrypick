"""Item 107's script (futures_oi_week_item107.py): invented reports and closes only, nothing fetched."""

from __future__ import annotations

import importlib.util
import io
import random
from datetime import date, timedelta
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "futures_oi_week_item107", Path(__file__).resolve().parents[1] / "scripts" / "futures_oi_week_item107.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def _sessions(start: date, n: int) -> list[str]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _case():
    """Flat at 100 for 150 sessions (to 2021-02-19), down half a point a session to 80 by 2021-04-16,
    then flat: one drawdown from early March, entered before `since`."""
    closes = [100.0] * 150 + [100 - 0.5 * k for k in range(1, 41)] + [80.0] * 100
    days = _sessions(date(2020, 7, 27), len(closes))
    spx = list(zip(days, closes, strict=True))
    vix = {d: 30.0 + k % 7 for k, d in enumerate(days)}
    reports = [
        {
            "d": (date(2021, 2, 16) + timedelta(weeks=k)).isoformat(),
            "oi": 100 + (k * 7) % 5,
            "lev_long": 1,
            "lev_short": 2,
        }
        for k in range(30)
    ]
    return reports, spx, vix


RULE = {**m.FORECAST_RULE, "history_from": "2000-01-01", "since": "2021-04-01", "high_window": 150}


def test_the_rule_is_complete_forward_only_and_frozen():
    r = m.FORECAST_RULE
    assert r["kind"] == "forecast" and r["since"] == "2026-11-24" and r["splits"] == []
    assert r["horizon"] == 5 and set(r["scale"]) == set(m.i106.FEATURES)
    assert all(v is not None for v in r.values())
    assert [s["step"] for s in m.CHAIN] == ["fetch", "study"]


def test_no_row_before_since_and_the_drawdown_state_comes_from_history():
    reports, spx, vix = _case()
    rows, _ = m.rows_since(reports, spx, vix, RULE)
    assert rows and all(r["date"] >= "2021-04-01" for r in rows)
    assert rows[0]["date"] == "2021-04-06"  # the drawdown began in March, before since
    every, _ = m.i106.build_rows(reports, spx, vix, {**RULE, "from": "2000-01-01", "through": "9999-12-31"})
    assert any(r["date"] < "2021-04-01" for r in every)


def _rows(n: int, slope: float, noise: float, seed: int = 1) -> list[dict]:
    rng = random.Random(seed)
    sc = m.FORECAST_RULE["scale"]
    out = []
    for k in range(n):
        doi = rng.gauss(0.05, 0.02)  # a different mean and spread from the frozen scaling
        vixv, prior, depth = rng.uniform(15, 45), rng.gauss(0, 0.03), rng.uniform(0.07, 0.3)
        fall = -2 + slope * (doi - sc["doi"][0]) / sc["doi"][1] + rng.gauss(0, noise)
        out.append({"date": (date(2027, 1, 5) + timedelta(weeks=k)).isoformat(), "doi": doi, "vix": vixv,
                    "prior": prior, "depth": depth, "fall": fall, "ret": fall, "week_ret": 0.0})  # fmt: skip
    return out


def test_fewer_than_min_n_rows_is_no_verdict_with_the_count():
    out = m.judge(_rows(39, -2.0, 0.1), m.FORECAST_RULE)
    assert out["verdict"] == {"oi_slope_5": m.NO_VERDICT} and out["forward"]["n"] == 39


def test_the_slope_is_read_on_the_frozen_scaling_not_the_forward_rows():
    rows = _rows(60, -1.0, 0.0)
    out = m.judge(rows, m.FORECAST_RULE, resamples=30)
    assert abs(out["forward"]["slope"] - (-1.0)) < 1e-6  # data scaling would give -1.0 * 0.02 / 0.039
    assert out["verdict"] == {"oi_slope_5": m.PASS}


def test_a_small_or_noisy_slope_fails():
    assert m.judge(_rows(60, -0.2, 0.05), m.FORECAST_RULE, resamples=30)["verdict"] == {"oi_slope_5": m.FAIL}
    out = m.judge(_rows(60, 0.0, 3.0), m.FORECAST_RULE, resamples=30)
    assert out["verdict"] == {"oi_slope_5": m.FAIL} and not out["forward"]["checks"]["t"]


def test_a_shorter_spx_history_is_refused_and_the_kept_file_stays(tmp_path, monkeypatch):
    kept = "DATE,SPX\n01/02/2026,6000\n01/05/2026,6010\n"
    (tmp_path / "SPX.csv").write_text(kept, encoding="utf-8")
    monkeypatch.setattr(m, "out_dir", lambda: tmp_path)
    monkeypatch.setattr(m.i105, "cmd_fetch", lambda args: 0)
    monkeypatch.setenv("CHERRYPICK_PROGRESS_FILE", str(tmp_path / "progress.json"))

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        m.urllib.request, "urlopen", lambda req, timeout: _Resp(b"DATE,SPX\n01/02/2026,6000\n")
    )
    assert m.main(["fetch"]) == 1
    assert (tmp_path / "SPX.csv").read_text(encoding="utf-8") == kept
    longer = b"DATE,SPX\n01/02/2026,6000\n01/05/2026,6010\n01/06/2026,6020\n"
    monkeypatch.setattr(m.urllib.request, "urlopen", lambda req, timeout: _Resp(longer))
    assert m.main(["fetch"]) == 0
    assert (tmp_path / "SPX.csv").read_text(encoding="utf-8").count("\n") == 4
