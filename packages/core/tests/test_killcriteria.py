"""B1: a live arm's frozen kill criteria -- derived from its evidence, evaluated on its live book,
latched on a block until a person clears it."""

import pytest

from cherrypick.core import killcriteria as kc

CRITERIA = {
    "written": "2026-10-24",
    "drawdown_reduce": 500.0,
    "drawdown_block": 800.0,
    "streak_reduce": 5,
    "streak_block": 8,
}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))


def test_drawdown_and_streak_by_hand():
    assert kc.drawdown_and_streak([100, -50, -70, 30, -10, 200]) == (120.0, 2)
    assert kc.drawdown_and_streak([-10, -10]) == (20.0, 2)
    assert kc.current_drawdown_and_streak([100, -50, -70]) == (120.0, 2)
    assert kc.current_drawdown_and_streak([100, -50, 80]) == (0.0, 0)


def test_derive_refuses_thin_evidence():
    out = kc.derive([1.0] * 99)
    assert out["insufficient"] and out["evidence_trades"] == 99


def test_derive_is_seeded_and_ordered():
    nets = [40.0] * 80 + [-150.0] * 20
    a = kc.derive(nets, sessions=50, resamples=2000)
    assert a == kc.derive(nets, sessions=50, resamples=2000)
    assert 0 < a["drawdown_reduce"] <= a["drawdown_block"]
    assert 1 <= a["streak_reduce"] <= a["streak_block"]
    assert a["mean_block"] == 10.0  # a week of sessions at two trades each


def test_clustered_evidence_widens_the_streak_band():
    # Losses that arrive in runs: an independent-trade resample understates the streak.
    nets = ([30.0] * 24 + [-100.0] * 6) * 5
    ind = kc.derive(nets, resamples=2000)
    blk = kc.derive(nets, sessions=5, resamples=2000)
    assert blk["streak_block"] > ind["streak_block"]


def test_evaluate_grades_normal_reduced_blocked():
    assert kc.evaluate([100, -50], CRITERIA)["state"] == kc.NORMAL
    assert kc.evaluate([-600], CRITERIA)["state"] == kc.REDUCED
    assert kc.evaluate([-900], CRITERIA)["state"] == kc.BLOCKED
    assert kc.evaluate([-1] * 5, CRITERIA)["state"] == kc.REDUCED
    v = kc.evaluate([-1] * 8, CRITERIA)
    assert v["state"] == kc.BLOCKED and "losing streak 8" in v["reasons"][0]


@pytest.mark.parametrize(
    "bad", [None, {}, {"written": "soon"}, {"written": "2026-10-24", "drawdown_block": 1}]
)
def test_missing_or_malformed_criteria_block(bad):
    v = kc.evaluate([], bad)
    assert v["state"] == kc.BLOCKED and v["reasons"] == [kc.UNWRITTEN]


def test_hand_limit_sizes_to_one_and_blocks_at_the_limit():
    hand = {"written": "2026-10-24", "hand_limit_dollars": 300}
    assert kc.evaluate([-200], hand)["state"] == kc.NORMAL
    assert kc.evaluate([-300], hand)["state"] == kc.BLOCKED
    assert kc.sized(4, kc.evaluate([], hand), hand) == 1


def test_sized_halves_when_reduced():
    assert kc.sized(4, {"state": kc.REDUCED}, CRITERIA) == 2
    assert kc.sized(1, {"state": kc.REDUCED}, CRITERIA) == 1
    assert kc.sized(4, {"state": kc.NORMAL}, CRITERIA) == 4


def test_live_nets_counts_only_the_arm_since_written():
    recs = [
        {"arm": "control", "session": "2026-10-23", "net_pnl": -999.0},
        {"arm": "control", "session": "2026-10-25", "net_pnl": -10.0},
        {"arm": "delta", "session": "2026-10-25", "net_pnl": -500.0},
        {"arm": "control", "session": "2026-10-24", "net_pnl": None},
        {"arm": "control", "session": "2026-10-24", "net_pnl": 5.0},
    ]
    assert kc.live_nets(recs, "control", CRITERIA) == [5.0, -10.0]
    # The row the nets skip is named, not lost; one before the criteria were written is not.
    early = {"arm": "control", "session": "2026-10-01", "net_pnl": None}
    assert kc.unrecorded([*recs, early], "control", CRITERIA) == [recs[3]]
    assert kc.unrecorded(recs, "control", None) == []


def test_a_block_latches_until_cleared():
    recs = [{"arm": "control", "session": "2026-10-24", "net_pnl": -900.0}]
    assert kc.gate("bwb", "control", recs, CRITERIA)["state"] == kc.BLOCKED
    assert kc.latched("bwb")["arm"] == "control"
    # The book recovering does not unlatch it.
    v = kc.gate("bwb", "control", [], CRITERIA)
    assert v["state"] == kc.BLOCKED and v["reasons"][0] == kc.LATCHED
    assert kc.clear("bwb") and kc.gate("bwb", "control", [], CRITERIA)["state"] == kc.NORMAL


def test_unwritten_criteria_refuse_without_latching():
    assert kc.gate("meic", "pm-200", [], None)["reasons"] == [kc.UNWRITTEN]
    assert kc.latched("meic") is None
