"""The driver's snapshot hands the engine what a decision reads, not the book's whole history: a long run
(item 90's placebo book holds tens of thousands of trades) stayed linear only once each minute's snapshot
stopped copying every entry and rebuilding every arm's results."""

from __future__ import annotations

from datetime import date, timedelta

from cherrypick.automations import driver


def _days(n: int, last: str) -> list[str]:
    end = date.fromisoformat(last)
    return [(end - timedelta(days=n - i)).isoformat() for i in range(n)]


def test_recent_entries_keep_what_each_rule_reads():
    past = _days(1000, "2026-11-16")
    for days, session in ((past, "2026-11-17"), (past + ["2026-11-17"], "2026-11-17"),
                          (past + ["2026-11-17", "2026-11-17"], "2026-11-17"), (["2026-11-17"], "2026-11-17"),
                          ([], "2026-11-17")):  # fmt: skip
        kept = driver.recent_entries(days, session)
        assert kept.count(session) == days.count(session)  # the per-day limit
        assert (session in kept) == (session in days)  # no_position_today
        assert max(kept, default=None) == max(days, default=None)  # every_days
        assert len(kept) <= days.count(session) + 1


class _Book(driver.Book):
    calls = 0

    def results(self):
        type(self).calls += 1
        return super().results()


def test_results_are_built_only_for_an_arm_with_a_progression():
    book = _Book(trades=[{"arm": "a", "net": {"verdict": -1.0}}, {"arm": "b", "net": {"verdict": 2.0}}])
    plain = {"name": "a", "size": {"contracts": 1}}
    stepped = {"name": "b", "size": {"contracts": 1, "progression": {"after": "loss", "step": 1}}}
    assert driver.results_for([plain], book) == {} and _Book.calls == 0
    assert driver.results_for([plain, stepped], book) == {"b": [2.0]}
