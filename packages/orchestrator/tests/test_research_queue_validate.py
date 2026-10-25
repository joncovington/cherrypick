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
