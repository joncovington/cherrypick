"""What each lane is doing, and why one is idle (`researchqueue.lane_report`). Pure over rows; the local
lane counts only rows that are not waiting on a queued fetch."""

from __future__ import annotations

from cherrypick.orchestrator import researchqueue as rq

WAIT = "waits for a prerequisite"
HELD = "held: its prerequisite failed"


def row(i, study, step, kind, status, after=None, waits=None):
    return {
        "id": i, "study": study, "step": step, "kind": kind, "status": status, "after": after,
        "waits": waits, "priority": 0,
    }  # fmt: skip


def fixture():
    """A full-set fetch running, one more behind it; item A's plan (local, free to run), its sample fetch
    behind the full set, its smoke and study behind that."""
    fetch = "fetch-intraday-chains"
    return [
        row(1, "fullset", "mid-a", fetch, "running"),
        row(2, "fullset", "mid-b", fetch, "queued", "fullset/mid-a", WAIT),
        row(3, "itemA", "plan", "script", "queued", None, "ready"),
        row(4, "itemA", "fetch-sample", "fetch-chain-paths", "queued", "fullset/mid-b", WAIT),
        row(5, "itemA", "smoke", "script", "queued", "itemA/fetch-sample", WAIT),
        row(6, "itemA", "study", "script", "queued", "itemA/smoke", WAIT),
    ]  # fmt: skip


def test_the_local_lane_counts_only_rows_not_waiting_on_a_fetch():
    lanes = rq.lane_report(fixture())
    # The plan is local work; the smoke and the study wait behind queued fetches and are not.
    assert lanes["local"]["queued"] == 1 and lanes["local"]["ready"] == 1
    assert lanes["local"]["waiting_on_fetch"] == 2
    assert lanes["local"]["idle"] is None
    assert lanes["vendor"]["running"] == "fullset/mid-a" and lanes["vendor"]["idle"] is None


def test_a_local_lane_with_only_fetch_waiting_rows_says_there_is_no_local_work():
    rows = [r for r in fixture() if r["id"] != 3]  # the plan is gone
    lane = rq.lane_report(rows)["local"]
    assert lane["queued"] == 0 and lane["waiting_on_fetch"] == 2
    assert "not waiting on a fetch" in lane["idle"]


def test_a_local_row_behind_a_failed_fetch_is_held_work_not_fetch_bound():
    rows = [
        row(1, "itemB", "fetch", "fetch-chain-paths", "failed"),
        row(2, "itemB", "study", "script", "queued", "itemB/fetch", HELD),
    ]
    lane = rq.lane_report(rows)["local"]
    assert lane["waiting_on_fetch"] == 0 and lane["queued"] == 1
    assert "behind itemB/fetch (failed)" in lane["idle"]


def test_a_row_behind_a_held_fetch_is_held_work_not_waiting_on_a_fetch():
    rows = [
        row(1, "itemC", "smoke", "script", "failed"),
        row(2, "itemC", "fetch", "fetch-chain-paths", "queued", "itemC/smoke", HELD),
        row(3, "itemC", "study", "script", "queued", "itemC/fetch", WAIT),
    ]
    lane = rq.lane_report(rows)["local"]
    assert lane["waiting_on_fetch"] == 0 and lane["queued"] == 1
    assert "behind itemC/smoke (failed)" in lane["idle"]


def test_a_fetch_behind_a_cancelled_row_is_held_as_the_queue_words_it():
    held = rq.after_reason({"after": "itemD/plan"}, "cancelled")
    assert held.startswith(rq.HELD)  # what the queue reports for a cancelled prerequisite
    rows = [
        row(1, "itemD", "plan", "script", "cancelled"),
        row(2, "itemD", "fetch", "fetch-chain-paths", "queued", "itemD/plan", held),
        row(3, "itemD", "study", "script", "queued", "itemD/fetch", WAIT),
    ]
    lane = rq.lane_report(rows)["local"]
    assert lane["waiting_on_fetch"] == 0 and "behind itemD/plan (cancelled)" in lane["idle"]


def test_a_lane_blocked_by_a_time_says_so_in_the_rows_own_words():
    rows = [row(1, "itemE", "fetch", "fetch-chain-paths", "queued", None, "inside the blackout window")]
    assert "inside the blackout window" in rq.lane_report(rows)["vendor"]["idle"]


def test_behind_names_the_end_of_a_chain_only_when_it_is_past_the_prerequisite():
    rows = fixture()
    by_id = {r["id"]: r for r in rows}
    assert rq.behind(by_id[2], rows) is None  # one step: the prerequisite is the answer
    assert rq.behind(by_id[6], rows) == "fullset/mid-a (running)"


def test_a_cycle_ends_the_chain_instead_of_looping():
    rows = [
        row(1, "x", "a", "script", "queued", "x/b", HELD),
        row(2, "x", "b", "script", "queued", "x/a", HELD),
    ]
    assert len(rq.wait_chain(rows[0], rows)) == 1
    rq.lane_report(rows)  # returns
