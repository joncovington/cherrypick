"""scripts/flies_restack_read.py: restack entries split from first entries, and the book against control."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "flies_restack_read.py"


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("flies_restack_read", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def row(day, t, center, pnl, completed=True, wing=5.0):
    return {
        "trade_date": day,
        "entry_time": f"{day}T{t}",
        "center": center,
        "wing_width": wing,
        "far_width": None,
        "completed_at": f"{day}T12:00" if completed else None,
        "pnl": pnl,
    }


def test_a_second_entry_on_a_centre_is_a_restack_and_a_new_day_starts_over(script):
    rows = [
        row("d1", "10:00", 6000.0, 40.0),
        row("d1", "10:30", 6005.0, 20.0),
        row("d1", "11:00", 6000.0, -260.0, completed=False),
        row("d1", "11:30", 6000.0, 30.0),
        row("d2", "10:00", 6000.0, 10.0),
        row("d2", "10:10", 6000.0, 15.0, wing=10.0),
    ]
    tagged = script.classify(rows)
    assert [r["depth"] for r in tagged] == [1, 1, 2, 3, 1, 1]
    assert [r["restack"] for r in tagged] == [False, False, True, True, False, False]


def test_the_read_splits_restacks_and_compares_sessions(script):
    arm = [
        row("d1", "10:00", 6000.0, 40.0),
        row("d1", "11:00", 6000.0, -260.0, completed=False),
        row("d1", "11:30", 6000.0, 30.0),
        row("d2", "10:00", 6000.0, 50.0),
        row("d2", "11:00", 6000.0, 25.0),
    ]
    control = [row("d1", "10:00", 6000.0, 40.0), row("d2", "10:00", 6000.0, 50.0)]
    out = script.read(arm, control)
    assert out["restacks"] == {
        "n": 3,
        "completed": 2,
        "completion_rate": 0.667,
        "net": -205.0,
        "per_entry": -68.33,
    }
    assert out["first_entries"]["n"] == 2 and out["first_entries"]["net"] == 90.0
    assert out["arm_net"] == -115.0 and out["control_net"] == 90.0
    assert (out["better_days"], out["worse_days"]) == (1, 1)
    assert out["max_stack_depth"] == 3
    assert out["underpowered"] is True
    assert out["per_session"]["d1"] == (-190.0, 40.0)
