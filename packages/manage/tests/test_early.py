"""The opening half hour (evaluate.early): marks taken between 09:30 and `early.verdicts_from` New York
time say so, from the re-mark's time when there is one; before the open is pre_open's, from then on
nothing. And each evaluated leg carries the broker's previous close, for a live day P&L."""

from datetime import date

from test_pmcc import evaluate as evaluate_account
from test_pmcc import history, legs, snapshot

from cherrypick.manage import evaluate as ev
from cherrypick.manage import rules as _rules

SESSION = date(2026, 10, 13)
RULES = _rules.DEFAULT_RULES


def at(generated_at, remark_at=None):
    snap = {"generated_at": generated_at}
    if remark_at:
        snap["remark"] = {"at": remark_at}
    return ev.early(snap, SESSION, RULES)


def test_between_the_open_and_ten_is_early():
    assert at("2026-10-13T09:41:00-0400") == {"taken_at": "09:41", "verdicts_from": "10:00"}
    assert at("2026-10-13T06:59:00-0700") == {"taken_at": "09:59", "verdicts_from": "10:00"}


def test_before_the_open_at_ten_and_after_or_another_day_is_not():
    assert at("2026-10-13T09:29:00-0400") is None  # pre_open's
    assert at("2026-10-13T10:00:00-0400") is None
    assert at("2026-10-12T09:45:00-0400") is None
    assert at("not a time") is None


def test_the_re_mark_s_time_decides_over_the_snapshot_s():
    assert at("2026-10-13T09:36:00-0400", remark_at="2026-10-13T10:01:00-04:00") is None
    assert at("2026-10-13T09:20:00-0400", remark_at="2026-10-13T09:50:00-04:00")["taken_at"] == "09:50"


def test_the_threshold_is_the_ruleset_s():
    rules, warnings = _rules.effective({"early": {"verdicts_from": "09:45"}})
    assert warnings == []
    assert ev.early({"generated_at": "2026-10-13T09:50:00-0400"}, SESSION, rules) is None
    assert ev.early({"generated_at": "2026-10-13T09:40:00-0400"}, SESSION, rules)["verdicts_from"] == "09:45"


def test_each_leg_carries_its_previous_close():
    cur = legs()
    cur[0]["prev_close"] = 13.5
    a = evaluate_account(snapshot(history(), cur))
    by_symbol = {leg["symbol"]: leg for t in a["trades"] for leg in t["legs"]}
    assert by_symbol[cur[0]["symbol"]]["prev_close"] == 13.5
    assert by_symbol[cur[1]["symbol"]]["prev_close"] is None  # no close given: null, never zero
