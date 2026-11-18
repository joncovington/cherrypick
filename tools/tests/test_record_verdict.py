"""tools/record_verdict.py: a finished study's register row from the kit's verdict block, words only."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "record_verdict.py"
spec = importlib.util.spec_from_file_location("record_verdict", TOOL)
rv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rv)

RULE = {"t_bar": 2.0, "min_n": 150, "holdout_min_n": 60, "tail": {}, "delta": True, "splits": ["2025-07-01"]}


def doc(**verdicts):
    arms = {a: {"n": 300, "parts": [], "verdict": w} for a, w in verdicts.items()}
    return {"rule": RULE, "arms": arms, "verdict": dict(verdicts), "reported": {"x": {"mean": 1.5}}}


def test_the_status_follows_the_arms_words():
    assert rv.status_of({"a": "fail", "b": "fail"}) == "failed"
    assert rv.status_of({"a": "fail", "b": "pass"}) == "passed"
    assert rv.status_of({"a": "no verdict", "b": "not read"}) == "no_verdict"
    assert rv.status_of({"a": "no verdict", "b": "fail"}) == "failed"


def test_the_outcome_is_words_only():
    out = rv.outcome_of(rv.kit_verdicts(doc(**{"orb-put-spread": "fail", "adx-condor": "fail"})))
    assert out.startswith("fail: orb-put-spread fail; adx-condor fail")
    assert not any(c.isdigit() for c in out), "no figure reaches the repo"


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        ({"verdict": {"a": "pass"}}, "no study-kit rule"),
        ({**doc(a="pass"), "rule": {"t_bar": 2.0, "min_n": 80, "holdout": True}}, "no study-kit rule"),
        ({**doc(a="pass"), "arms": {"a": {"verdict": "pass"}}}, "no study-kit arms"),
        ({**doc(a="pass", b="fail"), "verdict": {"a": "pass"}}, "does not name each arm"),
    ],
)
def test_a_result_without_the_kits_block_is_refused(bad, why):
    with pytest.raises(rv.Refused, match=why):
        rv.kit_verdicts(bad)


REG = """{
  "hypotheses": [
    {
      "id": 89,
      "study": "item89",
      "status": "failed",
      "outcome": "fail: kept",
      "retests": []
    },
    {
      "id": 90,
      "study": "item90",
      "status": "declared",
      "rule": {"nested": {"a": 1}},
      "outcome": null,
      "retests": []
    }
  ]
}
"""


def test_the_edit_sets_two_fields_of_its_own_row_and_keeps_the_rest():
    new = rv.edit(REG, "item90", {"a": "fail"})
    assert json.loads(new)["hypotheses"][0] == json.loads(REG)["hypotheses"][0]
    row = json.loads(new)["hypotheses"][1]
    assert row["status"] == "failed" and row["outcome"].startswith("fail: a fail")
    assert new.replace(row["outcome"], "X").count("\n") == REG.count("\n")  # formatting kept, line for line
    with pytest.raises(rv.Refused, match="recorded already"):
        rv.edit(REG, "item89", {"a": "fail"})
    with pytest.raises(rv.Refused, match="no register row"):
        rv.edit(REG, "item91", {"a": "fail"})
