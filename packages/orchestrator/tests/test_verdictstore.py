"""Recording a study's verdict in the register on this machine (`verdictstore`): the kit's words, a row
recorded once with its Result paragraph, and the register's home. Synthetic registers only."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

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


ROWS = [
    {"id": 89, "study": "item89", "status": "failed", "outcome": "fail: kept", "results": ["{home}/a.json"]},
    {"id": 90, "study": "item90", "status": "declared", "outcome": None, "results": []},
    {"id": 91, "study": "item91", "status": "declared", "outcome": None, "results": ["{home}/listed.json"]},
]


def _row(study):
    return next(r for r in hypotheses.load()["hypotheses"] if r["study"] == study)


def test_a_verdict_goes_into_its_row_once_with_a_result_paragraph(isolated_register, register_rows):
    register_rows(ROWS)
    when = datetime(2026, 11, 22, 10, 0, tzinfo=vs.ET)
    rec = vs.record("item90", "failed", "fail: a fail, by the declared rule", RESULT, by="by hand", now=when)
    row = _row("item90")
    assert row["status"] == "failed" and row["outcome"] == "fail: a fail, by the declared rule"
    assert row["results"] == [RESULT] and row["recorded_by"] == "by hand"
    assert row["recorded_at"] == "2026-11-22T10:00:00-05:00"
    # Its Result paragraph is written beside the register, and the check reads it as recorded.
    md = hypotheses.results_path("item90").read_text(encoding="utf-8")
    assert md.startswith("- **Result (2026-11-22): fail.**") and rec["result_md"]
    assert hypotheses.result_state(md) == "recorded"
    # The other rows are untouched; a row's own listed result stays.
    assert _row("item89")["outcome"] == "fail: kept"
    vs.record("item91", "no_verdict", "no verdict: a no verdict", RESULT.replace("90", "91"), by="by hand")
    assert _row("item91")["results"] == ["{home}/listed.json"]
    # Recorded once: a re-run never rewrites it, and a recorded row is refused.
    with pytest.raises(vs.Refused, match="recorded already"):
        vs.record("item90", "passed", "pass: a pass", RESULT, by="by hand")
    with pytest.raises(vs.Refused, match="recorded already"):
        vs.record("item89", "passed", "pass: a pass", RESULT, by="by hand")
    with pytest.raises(vs.Refused, match="no register row"):
        vs.record("item92", "failed", "fail", RESULT, by="by hand")


def test_an_existing_result_paragraph_is_kept(isolated_register, register_rows):
    register_rows(ROWS, results={"item90": "- **Result (2026-11-01): fail.**\n  written\n"})
    rec = vs.record("item90", "failed", "fail: a fail", RESULT, by="by hand", paragraph="- **Result: other**")
    assert rec["result_md"] is None
    assert "written" in hypotheses.results_path("item90").read_text(encoding="utf-8")


def test_a_record_is_refused_without_words_a_verdict_or_a_home_result(isolated_register, register_rows):
    register_rows(ROWS)
    with pytest.raises(vs.Refused, match="words only"):
        vs.record("item90", "failed", "fail: mean $12.40", RESULT, by="by hand")
    with pytest.raises(vs.Refused, match="words only"):
        vs.record("item90", "failed", "fail: t 1.85", RESULT, by="by hand")
    with pytest.raises(vs.Refused, match="status is one of"):
        vs.record("item90", "declared", "fail", RESULT, by="by hand")
    with pytest.raises(vs.Refused, match=r"\{home\}"):
        vs.record("item90", "failed", "fail", "C:/elsewhere/results.json", by="by hand")
    with pytest.raises(vs.Refused, match="study slug"):
        vs.record("ideas", "failed", "fail", RESULT, by="by hand")
    assert _row("item90")["status"] == "declared"


def test_a_kit_result_is_recorded_with_its_drafted_paragraph(
    isolated_register, register_rows, tmp_path, monkeypatch
):
    from cherrypick.core import home as core_home

    monkeypatch.setattr(core_home, "home", lambda: tmp_path)
    register_rows(ROWS)
    out = tmp_path / "data" / "item90" / "results.json"
    vs.record_finished("item90", out, doc(a="fail", b="pass"), item=90, by="queue task 7")
    row = _row("item90")
    assert row["status"] == "passed" and row["recorded_by"] == "queue task 7"
    md = hypotheses.results_path("item90").read_text(encoding="utf-8")
    assert md.startswith("- **Result (") and "pass, `b`" in md and "~/.cherrypick/data/item90/" in md


def test_a_result_outside_the_home_is_refused(tmp_path):
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    assert vs.home_path(home / "data" / "r.json", home) == "{home}/data/r.json"
    with pytest.raises(vs.Refused, match="not under the home"):
        vs.home_path(tmp_path / "r.json", home)


def _link_dir(link: Path, target: Path) -> None:
    """A directory link: a symlink, else on Windows a junction (which needs no privilege)."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


def test_a_result_through_a_linked_data_folder_is_still_under_the_home(tmp_path):
    # This machine's `data/backtest` is a junction to another drive: the path is read as written.
    home, elsewhere = tmp_path / "home", tmp_path / "other-drive" / "backtest"
    (home / "data").mkdir(parents=True)
    elsewhere.mkdir(parents=True)
    _link_dir(home / "data" / "backtest", elsewhere)
    result = home / "data" / "backtest" / "results" / "item90" / "results.json"
    assert vs.home_path(result, home) == "{home}/data/backtest/results/item90/results.json"


def test_the_register_lives_in_the_home_and_a_home_without_one_reads_empty(isolated_register, register_rows):
    assert hypotheses.register_path() == isolated_register / "hypotheses.json"
    assert hypotheses.load() == {"hypotheses": [], "rule_changes": []}
    assert hypotheses.check(hypotheses.load()) == []
    register_rows(ROWS)
    assert [r["study"] for r in hypotheses.load()["hypotheses"]] == ["item89", "item90", "item91"]
    assert not (hypotheses.repo_root() / "research" / "hypotheses.json").exists(), "never in the repo"
