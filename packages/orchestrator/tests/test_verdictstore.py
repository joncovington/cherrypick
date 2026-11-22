"""Study verdicts recorded on this machine (`verdictstore`): the kit's words, a record per study written
once, and the overlay every register reader sees. Synthetic results only."""

from __future__ import annotations

import pytest

from cherrypick.orchestrator import hypotheses
from cherrypick.orchestrator import verdictstore as vs

RULE = {"t_bar": 2.0, "min_n": 150, "holdout_min_n": 60, "tail": {}, "delta": True, "splits": ["2025-07-01"]}
RESULT = "{home}/data/backtest/results/research/item90/results.json"


def doc(**verdicts):
    arms = {a: {"n": 300, "parts": [], "verdict": w} for a, w in verdicts.items()}
    return {"rule": RULE, "arms": arms, "verdict": dict(verdicts), "reported": {"x": {"mean": 1.5}}}


def test_the_status_follows_the_arms_words():
    assert vs.status_of({"a": "fail", "b": "fail"}) == "failed"
    assert vs.status_of({"a": "fail", "b": "pass"}) == "passed"
    assert vs.status_of({"a": "no verdict", "b": "not read"}) == "no_verdict"
    assert vs.status_of({"a": "no verdict", "b": "fail"}) == "failed"


def test_the_outcome_is_words_only():
    out = vs.outcome_of(vs.kit_verdicts(doc(**{"orb-put-spread": "fail", "adx-condor": "fail"})))
    assert out.startswith("fail: orb-put-spread fail; adx-condor fail")
    assert not any(c.isdigit() for c in out), "no figure is recorded"


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
    with pytest.raises(vs.Refused, match=why):
        vs.kit_verdicts(bad)


def test_a_study_is_recorded_once_in_words_under_the_home(isolated_verdicts):
    rec = vs.record("item90", "failed", "fail: a fail, by the declared rule", RESULT, by="by hand")
    assert rec["status"] == "failed" and rec["results"] == [RESULT]
    assert vs.read()["item90"]["outcome"] == "fail: a fail, by the declared rule"
    with pytest.raises(vs.Refused, match="recorded already"):
        vs.record("item90", "passed", "pass: a pass", RESULT, by="by hand")
    with pytest.raises(vs.Refused, match="words only"):
        vs.record("item91", "failed", "fail: mean $12.40", RESULT.replace("90", "91"), by="by hand")
    with pytest.raises(vs.Refused, match="words only"):
        vs.record("item91", "failed", "fail: t 1.85", RESULT.replace("90", "91"), by="by hand")
    with pytest.raises(vs.Refused, match="status is one of"):
        vs.record("item91", "declared", "fail", RESULT.replace("90", "91"), by="by hand")
    with pytest.raises(vs.Refused, match=r"\{home\}"):
        vs.record("item91", "failed", "fail", "C:/elsewhere/results.json", by="by hand")
    with pytest.raises(vs.Refused, match="study slug"):
        vs.record("ideas", "failed", "fail", RESULT, by="by hand")
    assert set(vs.read()) == {"item90"}


def test_a_result_outside_the_home_is_refused(tmp_path):
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    assert vs.home_path(home / "data" / "r.json", home) == "{home}/data/r.json"
    with pytest.raises(vs.Refused, match="not under the home"):
        vs.home_path(tmp_path / "r.json", home)


def test_the_overlay_sets_declared_rows_only_and_keeps_the_repos_verdicts():
    reg = {
        "hypotheses": [
            {
                "id": 89,
                "study": "item89",
                "status": "failed",
                "outcome": "fail: kept",
                "results": ["{home}/a.json"],
            },
            {"id": 90, "study": "item90", "status": "declared", "outcome": None, "results": []},
            {
                "id": 91,
                "study": "item91",
                "status": "declared",
                "outcome": None,
                "results": ["{home}/listed.json"],
            },
            {"id": 92, "study": "item92", "status": "declared", "outcome": None, "results": []},
        ]
    }
    recorded = {
        "item89": {"status": "passed", "outcome": "pass: x", "results": ["{home}/b.json"]},
        "item90": {"status": "failed", "outcome": "fail: y", "results": ["{home}/r90.json"]},
        "item91": {"status": "no_verdict", "outcome": "no verdict: z", "results": ["{home}/r91.json"]},
    }
    rows = {r["study"]: r for r in vs.overlay(reg, recorded)["hypotheses"]}
    assert rows["item89"]["status"] == "failed" and "recorded" not in rows["item89"]
    assert rows["item90"] | {} == {**reg["hypotheses"][1], "status": "failed", "outcome": "fail: y",
                                   "results": ["{home}/r90.json"], "recorded": "local"}  # fmt: skip
    assert rows["item91"]["results"] == ["{home}/listed.json"]  # the declared result stays
    assert rows["item92"]["status"] == "declared"
    assert reg["hypotheses"][1]["status"] == "declared", "the register itself is not changed"


def test_load_lays_the_records_over_the_register_and_the_check_reads_the_repo(isolated_verdicts):
    repo = hypotheses.load(local=False)
    declared = next(r for r in repo["hypotheses"] if r["status"] == "declared")
    vs.record(declared["study"], "failed", "fail: a fail, by the declared rule",
              f"{{home}}/data/{declared['study']}/results.json", by="by hand")  # fmt: skip
    seen = next(r for r in hypotheses.load()["hypotheses"] if r["study"] == declared["study"])
    assert seen["status"] == "failed" and seen["recorded"] == "local"
    assert (
        next(r for r in hypotheses.load(local=False)["hypotheses"] if r["study"] == declared["study"])
        == declared
    )
    # A recorded study is no longer awaiting its verdict.
    assert declared["study"] not in {a["study"] for a in hypotheses.awaiting_verdict(hypotheses.load(), [])}
