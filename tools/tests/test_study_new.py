"""tools/study_new.py (#1071): a study's skeleton carries its correction log, an unfilled RULE and its
CHAIN, as literals the suite reads without importing; nothing existing is overwritten."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import study_new as sn  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    (tmp_path / sn.SCRIPTS).mkdir(parents=True)
    (tmp_path / sn.TESTS).mkdir(parents=True)
    return tmp_path


def _literals(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out


def test_it_writes_a_script_and_a_test_that_parse(repo):
    script, test = sn.write("item78", "dividend_spread", repo=repo)
    assert script.as_posix() == "packages/backtest/scripts/dividend_spread_item78.py"
    assert test.as_posix() == "packages/backtest/tests/test_dividend_spread_item78_script.py"
    ast.parse((repo / script).read_text(encoding="utf-8"))
    ast.parse((repo / test).read_text(encoding="utf-8"))


def test_the_script_declares_its_log_an_unfilled_rule_and_its_chain(repo):
    script, _ = sn.write("item78", "dividend_spread", repo=repo)
    lit = _literals(repo / script)
    assert lit["REPLAY_CORRECTIONS"] == ()
    assert lit["RULE"]["t_bar"] is None and lit["RULE"]["tail"]["seed"] == 78
    assert [s["step"] for s in lit["CHAIN"]] == ["plan", "fetch-chains", "study"]
    assert lit["CHAIN"][1]["task_args"] == {"requests": "{results}/path_requests.json"}


def test_nothing_is_overwritten(repo):
    sn.write("item78", "dividend_spread", repo=repo)
    with pytest.raises(SystemExit, match="already there"):
        sn.write("item78", "dividend_spread", repo=repo)


@pytest.mark.parametrize(
    "item, name", [("78", "x_y"), ("item78", "Bad-Name"), ("item78", "x_item78"), ("item78", "_x")]
)
def test_a_bad_item_or_name_is_refused(repo, item, name):
    with pytest.raises(SystemExit):
        sn.write(item, name, repo=repo)
