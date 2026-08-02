"""Tests for the GEX study read-out (src/experiment.py).

Synthetic records throughout: the point is to pin the counterfactual logic and the honesty guards,
not to assert against whatever the paper ledger happens to hold.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import experiment  # noqa: E402


def rec(session, net, *, gex_positive=1, gamma_flip=None, gex_spot=None):
    return {
        "session": session,
        "symbol": "SPX",
        "net_pnl": net,
        "slippage": None,
        "gex_net": None,
        "gex_positive": gex_positive,
        "gamma_flip": gamma_flip,
        "gex_spot": gex_spot,
    }


# --------------------------------------------------------------------------- the primary read
def test_block_negative_splits_on_recorded_gex_and_scores_the_gate():
    """The whole experiment in one function: the ungated arm's own trades tell you what the gate
    would have removed."""
    records = [
        rec("2026-08-03", 100.0, gex_positive=1),  # gate would ALLOW
        rec("2026-08-03", 80.0, gex_positive=1),
        rec("2026-08-04", -200.0, gex_positive=0),  # gate would BLOCK
        rec("2026-08-04", -100.0, gex_positive=0),
    ]
    out = experiment.counterfactual(records, "block_negative")
    assert out["buckets"]["allowed"]["sample"] == 2
    assert out["buckets"]["blocked"]["sample"] == 2
    assert out["buckets"]["allowed"]["net_pnl"] == 180.0
    assert out["buckets"]["blocked"]["net_pnl"] == -300.0
    # allowed mean 90, blocked mean -150 -> the gate removed trades worse by 240/trade.
    assert out["mean_advantage_per_trade"] == 240.0
    assert out["verdict_direction"] == "gate helps"


def test_a_gate_that_cuts_the_better_trades_reads_as_hurting():
    """The finding this study exists to be able to return. It must be as easy to express as the
    flattering one -- the pre-close ITM exit was removed on exactly this kind of result."""
    records = [rec("2026-08-03", -50.0, gex_positive=1), rec("2026-08-03", 150.0, gex_positive=0)]
    out = experiment.counterfactual(records, "block_negative")
    assert out["mean_advantage_per_trade"] == -200.0
    assert out["verdict_direction"] == "gate hurts"


def test_unknown_gex_is_its_own_bucket_never_folded_into_a_side():
    """A trade whose GEX could not be read is an instrumentation fact. Counting it as 'allowed'
    would let a coverage gap masquerade as a result."""
    records = [
        rec("2026-08-03", 10.0, gex_positive=1),
        rec("2026-08-03", -10.0, gex_positive=0),
        rec("2026-08-03", 999.0, gex_positive=None),
    ]
    out = experiment.counterfactual(records, "block_negative")
    assert out["buckets"]["unknown"]["sample"] == 1
    assert out["buckets"]["allowed"]["sample"] == 1
    assert out["buckets"]["blocked"]["sample"] == 1
    assert out["buckets"]["allowed"]["net_pnl"] == 10.0  # the 999 is nowhere near it


def test_require_positive_blocks_unknown_where_block_negative_does_not():
    """The two variants differ ONLY in how they treat unknown GEX -- that is the distinction, and
    deriving both from the same rows is why one ungated arm answers three questions."""
    records = [rec("2026-08-03", 5.0, gex_positive=None)]
    assert experiment.counterfactual(records, "block_negative")["buckets"]["unknown"]["sample"] == 1
    assert experiment.counterfactual(records, "require_positive")["buckets"]["blocked"]["sample"] == 1


def test_flip_distance_sweep_moves_the_split_as_the_threshold_tightens():
    """Swept, not hand-picked: a single guessed threshold is how the uncalibrated ones elsewhere in
    this suite got there."""
    records = [
        rec("2026-08-03", 10.0, gex_positive=1, gex_spot=7500.0, gamma_flip=7480.0),  # 0.27% away
        rec("2026-08-03", 20.0, gex_positive=1, gex_spot=7500.0, gamma_flip=7000.0),  # 6.7% away
    ]
    loose = experiment.counterfactual(records, "flip_distance", min_pct=0.001)
    tight = experiment.counterfactual(records, "flip_distance", min_pct=0.01)
    assert loose["buckets"]["allowed"]["sample"] == 2  # both clear a 0.1% bar
    assert tight["buckets"]["allowed"]["sample"] == 1  # only the far one clears 1%
    assert tight["buckets"]["blocked"]["sample"] == 1

    sweep = experiment.flip_distance_sweep(records)
    assert [s["min_flip_distance_pct"] for s in sweep] == [0.001, 0.002, 0.003, 0.005, 0.0075, 0.01]


def test_counterfactual_rejects_an_unknown_gate():
    with pytest.raises(ValueError, match="unknown gate"):
        experiment.counterfactual([], "vibes")


# --------------------------------------------------------------------------- honesty guards
def test_every_reading_reports_sessions_beside_trades():
    """Same-day trades share a regime, so 40 trades over 2 days is a 2-observation result. The
    session count has to travel with every number or it gets read as 40."""
    records = [rec("2026-08-03", 10.0) for _ in range(20)] + [rec("2026-08-04", 10.0) for _ in range(20)]
    out = experiment.counterfactual(records, "block_negative")
    assert out["trades"] == 40
    assert out["sessions"] == 2
    assert out["buckets"]["allowed"]["sessions"] == 2


def test_bootstrap_refuses_to_quote_an_interval_below_the_session_floor():
    """The guard that stops a 3-session reading being dressed up as a measurement."""
    a = [rec("2026-08-03", 10.0), rec("2026-08-04", 20.0)]
    b = [rec("2026-08-03", 5.0), rec("2026-08-04", 1.0)]
    out = experiment.bootstrap_difference(a, b)
    assert out["ok"] is False
    assert "sessions" in out["reason"]
    assert out["sessions"] == 2


def test_bootstrap_resamples_sessions_and_returns_an_interval_once_there_are_enough():
    days = [f"2026-08-{d:02d}" for d in range(3, 3 + experiment.MIN_SESSIONS_FOR_INTERVAL)]
    a = [rec(d, 100.0) for d in days]
    b = [rec(d, 20.0) for d in days]
    out = experiment.bootstrap_difference(a, b, iterations=300, seed=1)
    assert out["ok"] is True
    assert out["sessions"] == experiment.MIN_SESSIONS_FOR_INTERVAL
    # Every session has A=100 and B=20, so every resample gives exactly 80 and the interval is tight.
    assert out["point_estimate"] == pytest.approx(80.0, abs=0.01)
    assert out["excludes_zero"] is True


def test_bootstrap_interval_spans_zero_when_the_arms_are_indistinguishable():
    """The expected answer for a long while, and it must be reported as such rather than as a
    small-but-real effect."""
    days = [f"2026-08-{d:02d}" for d in range(3, 3 + experiment.MIN_SESSIONS_FOR_INTERVAL)]
    a = [rec(d, 10.0 if i % 2 else -10.0) for i, d in enumerate(days)]
    b = [rec(d, -10.0 if i % 2 else 10.0) for i, d in enumerate(days)]
    out = experiment.bootstrap_difference(a, b, iterations=500, seed=2)
    assert out["ok"] is True
    assert out["excludes_zero"] is False
