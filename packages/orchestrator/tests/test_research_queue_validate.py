"""The queue's `validate` kind: a module's replay validation, by its own verb, nothing else."""

import pytest

from cherrypick.orchestrator import researchqueue as rq


def test_validate_builds_the_backtest_verb_for_a_known_module():
    assert rq.build_argv("validate", {"module": "meic"}, "py") == [
        "py",
        "-m",
        "cherrypick.backtest",
        "validate",
        "--module",
        "meic",
    ]
    assert rq.KINDS["validate"] == ((), False)  # no vendor session, no stop-by


@pytest.mark.parametrize("args", [{}, {"module": "mieic"}, {"module": "bwb"}])
def test_an_unknown_module_is_refused(args):
    with pytest.raises(rq.Refused, match="validate needs"):
        rq.build_argv("validate", args, "py")


def test_meic_validation_can_name_one_delta_source_for_both_replays():
    argv = rq.build_argv("validate", {"module": "meic", "cadence": 60, "delta_source": "computed"}, "py")
    assert argv[-4:] == ["--cadence", "60", "--delta-source", "computed"]


@pytest.mark.parametrize(
    "args",
    [
        {"module": "meic", "delta_source": "Computed"},
        {"module": "meic", "delta_source": "lagged"},
        {"module": "flies", "delta_source": "computed"},  # flies picks no strike by delta
    ],
)
def test_a_delta_source_that_is_not_meics_is_refused(args):
    with pytest.raises(rq.Refused, match="delta_source"):
        rq.build_argv("validate", args, "py")


def test_a_cadence_picks_the_store_validated():
    argv = rq.build_argv("validate", {"module": "meic", "cadence": 60}, "py")
    assert argv[-2:] == ["--cadence", "60"]


@pytest.mark.parametrize("cadence", ["60", 60.0, True, 0, -15, 7200, "60; rm"])
def test_a_cadence_that_is_not_whole_seconds_is_refused(cadence):
    with pytest.raises(rq.Refused, match="cadence"):
        rq.build_argv("validate", {"module": "meic", "cadence": cadence}, "py")
