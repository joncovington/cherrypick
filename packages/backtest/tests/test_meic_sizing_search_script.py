"""The MEIC sizing search (scripts/meic_sizing_search.py): what may be picked, and that the pick is the
best CAGR per unit of drawdown among them. Invented sessions only, no store.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from cherrypick.backtest import paths

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_spec = importlib.util.spec_from_file_location("meic_sizing_search", SCRIPTS / "meic_sizing_search.py")
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)


def row(**over):
    base = {
        "arm": "live-1400",
        "risk_pct": 6.0,
        "units_start": 1,
        "units_max": 2,
        "implied_cap_pct": 30.0,
        "cagr": 0.4,
        "max_drawdown_pct": 0.2,
        "mar": 2.0,
        "stopped_on": None,
    }
    return {**base, **over}


def test_a_row_is_refused_for_ruin_no_contract_an_over_cap_size_or_short_history():
    assert st.eligible(row(), 90) == (True, None)
    assert st.eligible(row(stopped_on="2024-03-01"), 90) == (False, "ran the account to zero")
    assert st.eligible(row(units_max=0), 90) == (False, "never sized a whole contract")
    assert st.eligible(row(implied_cap_pct=91.0), 90) == (False, "implied cap over 90%")
    assert st.eligible({"risk_pct": 2.0, "why": "too little history"}, 90) == (False, "too little history")


def test_the_pick_is_the_best_eligible_ratio_never_an_ineligible_one():
    rows = [
        row(arm="a", mar=1.0),
        row(arm="b", mar=3.0, stopped_on="2024-01-02"),  # better, but ran the account to zero
        row(arm="c", mar=2.5, implied_cap_pct=95.0),  # better, but over the cap
        row(arm="d", mar=1.8),
    ]
    assert st.pick(rows, 90)["arm"] == "d"
    assert st.pick([row(units_max=0)], 90) is None


def _series(n=150):
    out, rows = [], {}
    for i in range(n):
        day = f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}"
        out.append({"session": day, "net": (60.0, 60.0, -100.0)[i % 3], "trades": 1})
        rows[day] = [{"capital": 800.0, "stop_ratio": 1.0, "credit_dollars": 200.0, "overshoots": [1.0, 1.3]}]
    return out, rows


def test_evaluate_reports_the_lens_and_the_cap_its_starting_size_implies():
    series, rows = _series()
    e = st.evaluate(series, rows, capital=20_000, risk_pct=6.0)
    # stop + overshoot: (2 x 1.0 x 1.3 - 1) x 200 = 320 a condor; 6% of 20,000 = 1,200 -> 3 condors to start.
    assert e["units_start"] == 3 and e["implied_cap_pct"] > 0
    assert set(e) >= {"cagr", "max_drawdown_pct", "mar", "stopped_on", "units_max"}


def test_too_little_history_is_said_not_scored():
    series, rows = _series(n=40)
    assert st.evaluate(series, rows, capital=20_000, risk_pct=6.0) == {
        "risk_pct": 6.0,
        "eligible": False,
        "why": "too little history",
    }


def test_a_study_without_both_windows_is_refused():
    folder = paths.results_dir() / "research" / "item99"
    folder.mkdir(parents=True)
    (folder / "study-design-2022.json").write_text(
        json.dumps({"window": "design", "run_id": "r1"}), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="no holdout"):
        st.study_runs("item99")
