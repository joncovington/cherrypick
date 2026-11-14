"""#1070: a study script's CHAIN, read without importing it, queued whole and in order."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cherrypick.orchestrator import researchqueue as rq
from cherrypick.orchestrator import studychain as sc

ITEM76_LIKE = '''"""A study."""
CHAIN = (
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/path_requests.json"},
    {"step": "fetch-chains", "kind": "fetch-chain-paths",
     "task_args": {"requests": "{results}/path_requests.json"}},
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)
raise SystemExit("never imported")
'''


@pytest.fixture(autouse=True)
def no_backtest_read(monkeypatch):
    monkeypatch.setattr(rq, "backtest_stores", lambda: None)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "packages" / "backtest" / "scripts").mkdir(parents=True)
    (root / "scripts").mkdir()
    return root


def _script(repo: Path, body: str, name: str = "share_ladder_item90.py") -> str:
    rel = f"packages/backtest/scripts/{name}"
    (repo / rel).write_text(body, encoding="utf-8")
    return rel


def _tracked(root, p):
    return True


def _queue(repo, tmp_path, rel, **kw):
    res = tmp_path / "res" / "item90"
    return sc.queue_chain(rel, path=tmp_path / "q.db", root=repo, tracked=_tracked, results=res, **kw)


def test_a_chain_is_read_from_the_file_never_imported(repo):
    rel = _script(repo, ITEM76_LIKE)
    chain = sc.read_chain(repo / rel)
    assert [s["step"] for s in chain] == ["plan", "fetch-chains", "study"]


def test_queue_chain_adds_every_step_in_order_each_after_the_last(repo, tmp_path):
    rel = _script(repo, ITEM76_LIKE)
    out = _queue(repo, tmp_path, rel)
    assert out["study"] == "item90" and [a["name"] for a in out["added"]] == [
        "item90/plan", "item90/fetch-chains", "item90/study"]  # fmt: skip
    rows = {r["step"]: r for r in rq.rows(tmp_path / "q.db")}
    assert rows["plan"]["after"] is None
    assert rows["fetch-chains"]["after"] == "item90/plan" and rows["study"]["after"] == "item90/fetch-chains"
    res = (tmp_path / "res" / "item90").as_posix()
    assert json.loads(rows["fetch-chains"]["args"]) == {"requests": f"{res}/path_requests.json"}
    assert json.loads(rows["study"]["args"]) == {"path": rel, "args": ["study"]}
    assert rows["study"]["output"] == f"{res}/results.json"


def test_the_fetch_gets_a_placeholder_only_for_a_file_an_earlier_step_writes(repo, tmp_path):
    rel = _script(repo, ITEM76_LIKE)
    out = _queue(repo, tmp_path, rel)
    holder = tmp_path / "res" / "item90" / "path_requests.json"
    assert out["placeholders"] == [str(holder)] and json.loads(holder.read_text()) == {"requests": []}
    # A fetch whose requests file no step writes is refused by the allow-list, and nothing is queued.
    lone = _script(repo, '''CHAIN = (
    {"step": "fetch", "kind": "fetch-chain-paths", "task_args": {"requests": "{results}/missing.json"}},
)''', "lone_item91.py")  # fmt: skip
    with pytest.raises(rq.Refused):
        sc.queue_chain(lone, path=tmp_path / "q2.db", root=repo, tracked=_tracked, results=tmp_path / "r91")
    assert rq.rows(tmp_path / "q2.db") == []


def test_a_sample_fetch_and_the_full_fetch_share_one_placeholder(repo, tmp_path):
    rel = _script(repo, '''CHAIN = (
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/r.json"},
    {"step": "fetch-sample", "kind": "fetch-chain-paths",
     "task_args": {"requests": "{results}/r.json", "limit": 20}},
    {"step": "smoke", "kind": "script", "args": ["study", "--smoke"], "output": "{results}/smoke.json"},
    {"step": "fetch-chains", "kind": "fetch-chain-paths", "task_args": {"requests": "{results}/r.json"}},
)''', "smoke_item95.py")  # fmt: skip
    out = sc.queue_chain(rel, path=tmp_path / "q.db", root=repo, tracked=_tracked, results=tmp_path / "r95")
    assert len(out["placeholders"]) == 1 and len(out["added"]) == 4


def test_a_repeated_fetch_unrolls_into_passes_each_after_the_last(repo, tmp_path):
    # Item 52 refused three times on requests a random vendor error left unanswered: a later pass
    # retries them, and a pass with nothing left to do is done (#1096).
    rel = _script(repo, '''CHAIN = (
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/r.json"},
    {"step": "fetch", "kind": "fetch-chain-paths",
     "task_args": {"requests": "{results}/r.json"}, "repeat": 3},
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/out.json"},
)''', "rpt_item96.py")  # fmt: skip
    out = sc.queue_chain(rel, path=tmp_path / "q.db", root=repo, tracked=_tracked, results=tmp_path / "r96")
    names = [a["name"] for a in out["added"]]
    assert names == ["item96/plan", "item96/fetch", "item96/fetch-2", "item96/fetch-3", "item96/study"]
    rows = {r["step"]: r for r in rq.rows(tmp_path / "q.db")}
    assert rows["fetch-3"]["after"] == "item96/fetch-2" and rows["study"]["after"] == "item96/fetch-3"
    assert len(out["placeholders"]) == 1


@pytest.mark.parametrize(
    "step, why",
    [
        ('{"step": "s", "kind": "script", "repeat": 2}', "only a fetch repeats"),
        ('{"step": "f", "kind": "fetch-oi", "task_args": {}, "repeat": 0}', "whole number"),
        ('{"step": "f", "kind": "fetch-oi", "task_args": {}, "repeat": 11}', "whole number"),
    ],
)
def test_a_bad_repeat_is_refused(repo, step, why):
    rel = _script(repo, f"CHAIN = ({step},)", "bad_item97.py")
    with pytest.raises(sc.BadChain, match=why):
        sc.read_chain(repo / rel)


def test_a_repeat_whose_pass_names_clash_with_another_step_is_refused(repo):
    chain = ('CHAIN = ({"step": "f", "kind": "fetch-oi", "task_args": {}, "repeat": 2},'
             ' {"step": "f-2", "kind": "fetch-oi", "task_args": {}})')  # fmt: skip
    rel = _script(repo, chain, "clash_item98.py")
    with pytest.raises(sc.BadChain, match="appears twice"):
        sc.read_chain(repo / rel)


def test_a_chain_with_a_live_step_queues_nothing(repo, tmp_path):
    rel = _script(repo, ITEM76_LIKE)
    _queue(repo, tmp_path, rel)
    with pytest.raises(rq.Refused, match="already queued"):
        _queue(repo, tmp_path, rel)
    assert len(rq.rows(tmp_path / "q.db")) == 3


def test_a_bad_step_late_in_the_chain_leaves_nothing_queued(repo, tmp_path):
    rel = _script(repo, '''CHAIN = (
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/p.json"},
    {"step": "oops", "kind": "fetch-everything", "task_args": {}},
)''', "bad_item92.py")  # fmt: skip
    with pytest.raises(rq.Refused):
        sc.queue_chain(rel, path=tmp_path / "q.db", root=repo, tracked=_tracked, results=tmp_path / "r92")
    assert rq.rows(tmp_path / "q.db") == []


def test_a_step_refused_only_when_added_cancels_the_rows_before_it(repo, tmp_path):
    # The last step's output is named like a vendor store but sits elsewhere: only `add` sees that.
    rel = _script(repo, '''CHAIN = (
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/p.json"},
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/SPX-60s.db"},
)''', "late_item94.py")  # fmt: skip
    stores = lambda: {"ok": True, "dir": str(tmp_path / "vendor"), "files": ["SPX-60s.db"]}  # noqa: E731
    with pytest.raises(rq.Refused):
        sc.queue_chain(rel, path=tmp_path / "q.db", root=repo, tracked=_tracked, stores=stores,
                       results=tmp_path / "r94")  # fmt: skip
    assert {r["status"] for r in rq.rows(tmp_path / "q.db")} <= {"cancelled"}


def test_the_first_step_waits_on_a_named_prerequisite(repo, tmp_path):
    rq.add("item89", "script", {"path": _script(repo, "print(1)", "x_item89.py")}, step="study",
           path=tmp_path / "q.db", root=repo, tracked=_tracked)  # fmt: skip
    rel = _script(repo, ITEM76_LIKE)
    _queue(repo, tmp_path, rel, after="item89/study")
    plan = next(r for r in rq.rows(tmp_path / "q.db") if r["study"] == "item90" and r["step"] == "plan")
    assert plan["after"] == "item89/study"


def test_a_dry_run_queues_nothing_and_writes_no_placeholder(repo, tmp_path):
    rel = _script(repo, ITEM76_LIKE)
    out = _queue(repo, tmp_path, rel, dry_run=True)
    assert out["dry_run"] and len(out["rows"]) == 3 and out["placeholders"]
    assert rq.rows(tmp_path / "q.db") == [] and not Path(out["placeholders"][0]).exists()


@pytest.mark.parametrize(
    "chain, why",
    [
        ("CHAIN = ()", "non-empty"),
        ('CHAIN = ({"step": "a"},)', "needs step and kind"),
        ('CHAIN = ({"step": "a", "kind": "script"}, {"step": "a", "kind": "script"})', "twice"),
        ('CHAIN = ({"step": "a", "kind": "script", "task_args": {}},)', "its task is the script"),
        ('CHAIN = ({"step": "a", "kind": "fetch-oi", "args": []},)', "takes task_args"),
        ("CHAIN = tuple(range(3))", "must be a literal"),
    ],
)
def test_a_malformed_chain_is_refused_with_why(repo, chain, why):
    rel = _script(repo, chain, "m_item93.py")
    with pytest.raises(sc.BadChain, match=why):
        sc.read_chain(repo / rel)


def test_the_study_is_the_scripts_item_unless_named(repo):
    assert sc.study_of(Path("share_ladder_item76.py")) == "item76"
    assert sc.study_of(Path("reproduce_studies.py"), "reproduce") == "reproduce"
    with pytest.raises(sc.BadChain):
        sc.study_of(Path("reproduce_studies.py"))
