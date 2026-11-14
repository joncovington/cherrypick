"""#1069: from item 78 on, a declaration's first script carries its pass rule as a RULE literal."""

from __future__ import annotations

from cherrypick.orchestrator import hypotheses as hy


def _repo(tmp_path, body):
    p = tmp_path / "packages" / "backtest" / "scripts" / "x_item78.py"
    p.parent.mkdir(parents=True)
    p.write_text(body, encoding="utf-8")
    return "packages/backtest/scripts/x_item78.py"


def _reg(rid, script, **kw):
    return {
        "hypotheses": [{"id": rid, "study": f"item{rid}", "status": "declared", "scripts": [script], **kw}]
    }


def test_a_declaration_from_item_78_without_a_rule_is_listed(tmp_path):
    rel = _repo(tmp_path, "X = 1\n")
    assert hy.rule_missing(_reg(78, rel), root=tmp_path) == [f"row 78: {rel} declares no RULE literal"]


def test_a_rule_literal_clears_it_and_a_computed_one_does_not(tmp_path):
    rel = _repo(tmp_path, "RULE = {'t_bar': 2.0}\nraise SystemExit('never imported')\n")
    assert hy.rule_missing(_reg(78, rel), root=tmp_path) == []
    (tmp_path / rel).write_text("RULE = dict(t_bar=2.0)\n", encoding="utf-8")
    assert hy.rule_missing(_reg(78, rel), root=tmp_path) != []


def test_earlier_items_legacy_and_retired_rows_are_not_asked(tmp_path):
    rel = _repo(tmp_path, "X = 1\n")
    assert hy.rule_missing(_reg(77, rel), root=tmp_path) == []
    assert hy.rule_missing(_reg(78, rel, legacy=True), root=tmp_path) == []
    assert hy.rule_missing(_reg(78, rel, status="retired"), root=tmp_path) == []


def test_the_repo_register_lists_nothing_today():
    assert hy.rule_missing(hy.load()) == []
