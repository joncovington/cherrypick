"""Why live and paper differ, per entry (docs/strategy-analysis-plan.md Phase 2): each entry one book
took and the other did not carries the other side's reason and a category, and each matched pair the
category of its largest gap.

The guards, each broken on purpose while this was written: a live entry order that never filled
explains a paper-only entry before any refusal does; a refusal on the same centre wins over a busier
one elsewhere; a reason the table does not name reads "other" with the raw reason kept.
"""

from test_evidence_reads import _ledger, _pos

from cherrypick.flies import analytics

DAY = "2026-10-06"


def _decision(conn, reason, first, last, center, occurrences=1):
    conn.execute(
        "INSERT INTO fly_decisions (trade_date, arm, symbol, mode, reason, accepted, first_seen, last_seen, "
        "occurrences, center_first, center_last) VALUES (?, 'control', 'SPX', 'entry', ?, 0, ?, ?, ?, ?, ?)",
        (DAY, reason, f"{DAY}T{first}:00-04:00", f"{DAY}T{last}:00-04:00", occurrences, center, center),
    )
    conn.commit()


def _attempt(conn, minute, outcome, center):
    conn.execute(
        "INSERT INTO fly_entry_attempts (ts, trade_date, arm, symbol, outcome, center) "
        "VALUES (?, ?, 'control', 'SPX', ?, ?)",
        (f"{DAY}T{minute}:00-04:00", DAY, outcome, center),
    )
    conn.commit()


def _books(tmp_path):
    live, paper = _ledger(tmp_path / "live.db"), _ledger(tmp_path / "paper.db")
    _pos(live, "L1", DAY, "control", center=7700, minute="11:00", pnl=20.0)  # live's only entry
    return live, paper


def _unmatched(live, paper):
    (session,) = analytics.live_paper_gap(live, paper, arm="control")["sessions"]
    return {u["position"]: u for u in session["unmatched"]}


def test_a_paper_only_entry_carries_the_live_refusal_covering_that_minute_same_centre_first(tmp_path):
    live, paper = _books(tmp_path)
    _pos(paper, "P1", DAY, "control", center=7750, minute="12:30", pnl=-80.0)
    _decision(live, "max_open_margin_reached", "12:00", "13:00", center=7760, occurrences=40)
    _decision(live, "duplicate_structure", "12:29", "12:31", center=7750, occurrences=2)
    u = _unmatched(live, paper)["P1"]
    assert (u["which"], u["other_side_reason"], u["category"], u["net"]) == (
        "paper only",
        "duplicate_structure",
        "gate",
        -80.0,
    )


def test_a_live_entry_order_that_never_filled_explains_it_before_any_refusal(tmp_path):
    live, paper = _books(tmp_path)
    _pos(paper, "P1", DAY, "control", center=7750, minute="12:30", pnl=-80.0)
    _pos(live, "LX", DAY, "control", center=7750, minute="12:32", status="cancelled")
    _decision(live, "duplicate_structure", "12:29", "12:31", center=7750)
    u = _unmatched(live, paper)["P1"]
    assert (u["other_side_reason"], u["category"]) == ("entry_unfilled", "fill")


def test_a_live_only_entry_carries_paper_s_attempt_at_that_minute(tmp_path):
    live, paper = _books(tmp_path)
    _attempt(paper, "10:59", "cadence_blocked", 7690)
    _attempt(paper, "11:00", "duplicate_blocked", 7700)
    u = _unmatched(live, paper)["L1"]
    assert (u["which"], u["other_side_reason"], u["category"]) == ("live only", "duplicate_blocked", "gate")


def test_paper_filling_a_different_structure_then_is_named_as_such(tmp_path):
    live, paper = _books(tmp_path)
    _attempt(paper, "11:00", "filled", 7705)
    assert _unmatched(live, paper)["L1"]["other_side_reason"] == "entered_other_structure"


def test_no_record_on_the_other_side_says_so(tmp_path):
    live, paper = _books(tmp_path)
    u = _unmatched(live, paper)["L1"]
    assert (u["other_side_reason"], u["category"]) == ("not_recorded", "data")


def test_an_unnamed_reason_reads_other_and_keeps_the_raw_reason(tmp_path):
    live, paper = _books(tmp_path)
    _pos(paper, "P1", DAY, "control", center=7750, minute="12:30")
    _decision(live, "some_new_refusal", "12:29", "12:31", center=7750)
    u = _unmatched(live, paper)["P1"]
    assert (u["other_side_reason"], u["category"]) == ("some_new_refusal", "other")


def test_a_matched_pair_is_categorised_by_its_largest_gap(tmp_path):
    live, paper = _ledger(tmp_path / "live.db"), _ledger(tmp_path / "paper.db")
    # 0.15 credit gap ($15) beats a 0.05 debit gap ($5) and a $0.50 fee gap.
    _pos(live, "L1", DAY, "control", center=7700, credit=2.45, debit=2.20, fees=6.5, minute="11:00")
    _pos(paper, "P1", DAY, "control", center=7700, credit=2.30, debit=2.25, fees=7.0, minute="11:05")
    # Only paper completed: the completion, not any price, is the story.
    _pos(live, "L2", DAY, "control", center=7720, kind="short_vertical", minute="12:00")
    _pos(paper, "P2", DAY, "control", center=7720, minute="12:02")
    (session,) = analytics.live_paper_gap(live, paper, arm="control")["sessions"]
    cats = {p["live_position"]: p["category"] for p in session["pairs"]}
    assert cats == {"L1": "entry fill", "L2": "completion"}


def test_the_era_summary_adds_up_what_each_kind_of_refusal_cost(tmp_path):
    live, paper = _books(tmp_path)
    _pos(paper, "P1", DAY, "control", center=7750, minute="12:30", pnl=-80.0)
    _pos(paper, "P2", DAY, "control", center=7760, minute="12:45", pnl=30.0)
    _decision(live, "max_open_margin_reached", "12:00", "13:00", center=7750)
    why = analytics.live_paper_gap(live, paper, arm="control")["eras"]["modelled_debit"]["why"]
    slot = why["unmatched"]["paper only: live limit"]
    assert slot == {"entries": 2, "net": -50.0, "reasons": {"max_open_margin_reached": 2}}
