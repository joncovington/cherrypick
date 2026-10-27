"""The EOD narrative's deterministic half: what it hands the model, and what it will publish.

The model call itself is not tested (it is outside every package, by design). What is tested is the
fence the script puts around it -- no tools at all -- and the filter between the model's
recommendations and the PUBLIC repo's issues: a recommendation carrying a figure, an account or a
licensed-data reference is withheld, never filed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


eod = _module("eod_narrative")


@pytest.mark.parametrize(
    "rec",
    [
        "**Tighten the stop** -- the arm lost $412 this week",
        "**Review curve** -- net -1,250 since the change",
        "**Size down** -- drawdown is 18% of the peak",
        "**Check the account** 5WT01234 margin",
        "**Check the account** ****1234 margin",
        "**Compare with the ThetaData backtest** result",
        "**Long** " + "x" * 600,
    ],
)
def test_a_recommendation_with_a_figure_account_or_licensed_reference_is_withheld(rec):
    assert eod._unpublishable(rec)


@pytest.mark.parametrize(
    "rec",
    [
        "**Watch the pmcc roll rule** -- three sessions since 2026-10-20 closed at the short strike",
        "**Re-read the calendars exit window** -- the control arm and path arm disagree on Fridays",
    ],
)
def test_a_plain_recommendation_may_be_filed(rec):
    assert eod._unpublishable(rec) is None


def test_a_withheld_recommendation_never_reaches_gh(monkeypatch):
    calls = []

    class _Proc:
        returncode = 0
        stdout = "[]"
        stderr = ""

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return _Proc()

    monkeypatch.setattr(eod.shutil, "which", lambda name: "gh")
    monkeypatch.setattr(eod.subprocess, "run", fake_run)
    out = eod._file_issues(["**Lost** $900 on the day"], "2026-10-27", dry_run=False)
    assert out[0]["ok"] is False and "dollar" in out[0]["withheld"]
    assert not [a for a in calls if "create" in a]  # only the dedupe listing ran


def test_the_agent_gets_no_tools_at_all():
    common = _module("_common")
    fence = common.tool_fence(eod.TOOLS)
    assert fence[-2:] == ["--tools", ""]
    assert "--strict-mcp-config" in fence and "--disable-slash-commands" in fence


def test_the_morning_agent_gets_web_search_and_nothing_else():
    common = _module("_common")
    morning = _module("morning_narrative")
    assert common.tool_fence(morning.TOOLS)[-2:] == ["--tools", "WebSearch"]
