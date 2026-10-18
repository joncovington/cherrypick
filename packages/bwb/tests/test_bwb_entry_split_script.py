"""The bwb entry split (scripts/bwb_entry_split.py): the previous-session state each entry sees, and the
declared pass test on the down-day subset, shown to FAIL as well as pass.

Synthetic closes and rows only: no Cboe file, no store.
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bwb_entry_split as split  # noqa: E402


def test_each_session_sees_the_previous_return_and_its_distance_below_the_high():
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    closes = [100.0, 110.0, 99.0, 104.0]
    st = split.states(dates, closes)
    prev_ret, below = st["2024-01-05"]
    assert prev_ret == pytest.approx(99 / 110 - 1)  # 01-04's return, known before 01-05's entry
    assert below == pytest.approx(1 - 99 / 110)  # 01-04's close against the 110 high
    # an entry on a day missing from the file takes the next session's state
    assert split.state_for("2024-01-04", st, dates) == st["2024-01-04"]


def _rows(year_nets: dict[str, list[float]]) -> list[dict]:
    return [{"entry": f"{y}-03-01", "net_control": n} for y, nets in year_nets.items() for n in nets]


def test_the_down_day_test_needs_a_positive_total_and_most_years():
    good = split.verdict(_rows({"2015": [5.0] * 12, "2016": [3.0] * 12, "2017": [-1.0] * 12}))
    assert good["pass"] is True
    bad = split.verdict(_rows({"2015": [5.0] * 12, "2016": [-3.0] * 12, "2017": [-4.0] * 12}))
    assert bad["pass"] is False
    assert bad["checks"]["two_thirds_of_years"] is False
    # a year with fewer than 10 entries is not counted either way
    thin = split.verdict(_rows({"2015": [5.0] * 12, "2016": [-50.0] * 5}))
    assert thin["years_counted"] == 1
