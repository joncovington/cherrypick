"""The study chain: a register row names its scripts and tests, no two rows own one result file, and
the next item number is free in every register and in the plan. Each guard is broken on purpose."""

from __future__ import annotations

from test_hypotheses import NEW

from cherrypick.orchestrator import hypotheses as hy

RESULT = "{home}/data/backtest/results/research/item18/study.json"


def test_a_study_names_its_scripts_and_tests():
    assert hy.check({"hypotheses": [NEW]}) == []
    for key in ("scripts", "tests"):
        bare = {k: v for k, v in NEW.items() if k != key}
        assert any(f"names its {key}" in p for p in hy.check({"hypotheses": [bare]}))
        assert any(f"{key} must be a list" in p for p in hy.check({"hypotheses": [{**NEW, key: []}]}))


def test_chain_paths_must_exist_under_their_folder():
    problems = hy.check({"hypotheses": [{**NEW, "scripts": ["scripts/no_such_item18.py"]}]})
    assert any("does not exist" in p for p in problems)
    problems = hy.check({"hypotheses": [{**NEW, "tests": ["scripts/weeklyput_item69.py"]}]})
    assert any("must sit under packages/" in p for p in problems)
    problems = hy.check({"hypotheses": [{**NEW, "scripts": ["scripts/../research/hypotheses.json"]}]})
    assert any("must sit under" in p for p in problems)
    # A package's own code is not a study script, though the backtest's scripts folder is.
    src = "packages/backtest/src/cherrypick/backtest/stores.py"
    problems = hy.check({"hypotheses": [{**NEW, "scripts": [src]}]})
    assert any("must sit under scripts/ or packages/backtest/scripts/" in p for p in problems)


def test_legacy_and_retired_rows_may_leave_the_chain_out():
    bare = {k: v for k, v in NEW.items() if k not in ("scripts", "tests")}
    assert hy.check({"hypotheses": [{**bare, "legacy": True}]}) == []
    assert hy.check({"hypotheses": [{**bare, "status": "retired"}]}) == []
    assert hy.check({"hypotheses": [{**bare, "id": hy.CHAIN_FROM - 1, "study": "item17"}]}) == []


def test_two_rows_never_own_one_result_file():
    a = {**NEW, "status": "failed", "results": [RESULT]}
    b = {**NEW, "id": 19, "study": "item19", "status": "failed", "results": [RESULT]}
    assert hy.check({"hypotheses": [a]}) == []
    assert any("already row 18's" in p for p in hy.check({"hypotheses": [a, b]}))


def test_a_shared_script_is_fine():
    b = {**NEW, "id": 19, "study": "item19"}
    assert hy.check({"hypotheses": [NEW, b]}) == []


def test_next_id_reads_every_register_and_the_plan():
    here = {"hypotheses": [{"id": 18}, {"id": 20}]}
    main = {"hypotheses": [{"id": 21}]}
    assert hy.next_id(here) == 21
    assert hy.next_id(here, main) == 22
    assert hy.next_id(here, main, plan_md="### 23. A declared item with no row yet\n") == 24
    assert hy.next_id({}, plan_md="") == 1


def test_taken_names_a_reused_id_or_study():
    main = {"hypotheses": [{"id": 64, "study": "item64"}]}
    assert hy.taken({"id": 64}, {}, main) == ["id 64 is row item64"]
    assert hy.taken({"study": "item64"}, main) == ["study item64 is row 64"]
    assert hy.taken({"id": 65, "study": "item65"}, main) == []
    assert hy.taken({}, main) == []


def test_board_carries_the_chain_and_each_tasks_kind():
    out = hy.board(
        {"hypotheses": [NEW]},
        [{"id": 1, "study": "item18", "kind": "fetch-chain-paths", "step": "fetch", "status": "done"}],
        read=lambda p: (None, None),
    )
    row = out["rows"][0]
    assert row["scripts"] == NEW["scripts"] and row["tests"] == NEW["tests"]
    assert row["queue"]["tasks"][0]["kind"] == "fetch-chain-paths"
