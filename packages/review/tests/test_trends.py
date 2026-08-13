"""Trends, break discipline, and the suspected-break detector."""

import pytest

from cherrypick.review import facts, render, trends


@pytest.fixture
def store(tmp_path, monkeypatch):
    for mod in (facts, trends, render):
        monkeypatch.setattr(mod._paths, "data_dir", lambda: tmp_path)
        monkeypatch.setattr(mod._paths, "facts_path", lambda s: tmp_path / f"eod-{s}.json")
    return tmp_path


def _session(store, session, *, module="meic", closed=10, net=100.0, wins=5, breaks=None, events=1):
    facts.write(
        {
            "session": session,
            "status": "final",
            "fact_version": facts.FACT_VERSION,
            "modules": {
                module: {
                    "ok": True,
                    "results": {"closed": closed, "net": net, "wins": wins, "gross": net, "cost": 0},
                    "return": {"capital_at_risk": 1000.0, "on_max_risk": net / 1000.0},
                    "carried_overnight": {"positions": 0, "capital_at_risk": None},
                    "expected_vs_observed": {"basis": "x", "expected": None, "observed": None},
                    "health": {"loop_ticked": True},
                    "sample": {"n": closed, "effective_n": events, "breaks": breaks, "suspected_break": None},
                }
            },
        }
    )


# --------------------------------------------------------------------------- break discipline


def test_a_trend_stops_at_the_most_recent_break(store):
    """Results either side of a break are not the same experiment. A five-session line drawn
    through a policy change describes neither side of it."""
    for day in ("2026-08-08", "2026-08-09", "2026-08-10", "2026-08-11", "2026-08-12"):
        _session(store, day, breaks=["2026-08-11"])
    got = trends.trend("meic", "2026-08-12", window=5)
    assert got["sessions_used"] == 1
    assert got["sessions"] == ["2026-08-12"]
    assert got["stopped_at_break"] == "2026-08-11"


def test_a_trend_reports_what_it_covered_not_what_it_was_asked_for(store):
    _session(store, "2026-08-12", breaks=["2026-08-11"])
    got = trends.trend("meic", "2026-08-12", window=5)
    assert (got["sessions_requested"], got["sessions_used"]) == (5, 1)


def test_a_break_on_the_session_itself_leaves_the_window_empty(store):
    """The break lands on the day the change took effect, so nothing after it exists yet. Zero
    sessions is the honest answer, not a one-session trend that predates the change."""
    _session(store, "2026-08-12", breaks=["2026-08-12"])
    got = trends.trend("meic", "2026-08-12", window=5)
    assert got["sessions_used"] == 0 and got["win_rate"] is None


def test_a_module_that_tracks_no_breaks_is_distinguished_from_one_with_none(store):
    """None and [] are different claims, and the trend has to carry the difference: a line through
    a module that never tracked breaks is only as good as an assumption nobody checked."""
    _session(store, "2026-08-12", breaks=None)
    assert trends.trend("meic", "2026-08-12")["tracks_breaks"] is False
    _session(store, "2026-08-12", breaks=[])
    assert trends.trend("meic", "2026-08-12")["tracks_breaks"] is True


def test_a_trend_aggregates_across_the_sessions_it_kept(store):
    for day in ("2026-08-10", "2026-08-11", "2026-08-12"):
        _session(store, day, closed=10, net=50.0, wins=6, breaks=[])
    got = trends.trend("meic", "2026-08-12", window=5)
    assert got["sessions_used"] == 3
    assert got["closed"] == 30 and got["net"] == 150.0
    assert got["win_rate"] == pytest.approx(0.6)
    assert got["effective_n"] == 3  # three sessions, not thirty trades


# --------------------------------------------------------------------------- suspected breaks


def _records(counts: dict[str, int]):
    return [{"session": s, "symbol": "SPX"} for s, n in counts.items() for _ in range(n)]


def test_a_tenfold_step_up_with_no_journaled_break_is_flagged():
    """The real case: MEIC went from ~20 trades a day to ~700 on 2026-08-07 when the four-stream
    test launched, and its journal records 2026-08-11 instead."""
    counts = {f"2026-07-{d:02d}": 20 for d in range(10, 20)}
    counts["2026-08-07"] = 326
    got = facts._suspected_break(_records(counts), "2026-08-07")
    assert got is not None
    assert got["ratio"] > 3 and got["trades"] == 326


def test_a_steady_book_is_not_flagged():
    counts = {f"2026-07-{d:02d}": 20 for d in range(10, 20)}
    counts["2026-08-07"] = 24
    assert facts._suspected_break(_records(counts), "2026-08-07") is None


def test_a_collapse_is_flagged_as_well_as_a_jump():
    """A book that stops trading is as much a regime change as one that starts -- and looks
    identical to a quiet day until something says otherwise."""
    counts = {f"2026-07-{d:02d}": 300 for d in range(10, 20)}
    counts["2026-08-07"] = 2
    assert facts._suspected_break(_records(counts), "2026-08-07") is not None


def test_too_little_history_flags_nothing():
    """Three sessions is not a baseline to call a fourth a departure from."""
    assert facts._suspected_break(_records({"2026-08-06": 5, "2026-08-07": 500}), "2026-08-07") is None


# --------------------------------------------------------------------------- render


def test_the_render_leads_with_what_needs_attention(store):
    _session(store, "2026-08-12", breaks=[])
    out = render.render("2026-08-12")
    assert out.index("Needs attention") < out.index("What each module did")


def test_a_stopped_loop_is_called_out_not_read_as_a_quiet_day(store):
    facts.write(
        {
            "session": "2026-08-12", "status": "final", "fact_version": 2,
            "modules": {"meic": {
                "ok": True,
                "results": {"closed": 0, "net": 0.0, "wins": 0, "gross": 0, "cost": 0},
                "return": {"capital_at_risk": None, "on_max_risk": None},
                "carried_overnight": {"positions": 0, "capital_at_risk": None},
                "expected_vs_observed": {"basis": "x", "expected": None, "observed": None},
                "health": {"loop_ticked": False},
                "sample": {"n": 0, "effective_n": 0, "breaks": [], "suspected_break": None},
            }},
        }
    )
    assert "a stopped loop, not a quiet day" in render.render("2026-08-12")


def test_unmeasured_values_render_as_a_dash_never_as_zero_or_none(store):
    facts.write(
        {
            "session": "2026-08-12", "status": "final", "fact_version": 2,
            "modules": {"flies": {
                "ok": True,
                "results": {"closed": 3, "net": 10.0, "wins": 2, "gross": 10.0, "cost": 0},
                "return": {"capital_at_risk": None, "on_max_risk": None},
                "carried_overnight": {"positions": 0, "capital_at_risk": None},
                "expected_vs_observed": {"basis": "modeled_pnl", "expected": None, "observed": 10.0},
                "health": {"loop_ticked": True},
                "sample": {"n": 3, "effective_n": 1, "breaks": [], "suspected_break": None},
            }},
        }
    )
    out = render.render("2026-08-12")
    assert "None" not in out
    assert "expected —, observed 10.00" in out
    assert "no model recorded" in out


def test_the_render_carries_no_suite_total(store):
    _session(store, "2026-08-12", breaks=[])
    out = render.render("2026-08-12")
    assert "No suite total" in out


def test_the_plateau_after_a_step_is_not_flagged_again():
    """MEIC's launch flagged on 08-07, 08-10 and 08-12 before this: the trailing median takes many
    sessions to catch up to a tenfold change, so one event was reported three times. A shift that
    already happened yesterday is not news today."""
    counts = {f"2026-07-{d:02d}": 20 for d in range(10, 20)}
    counts["2026-08-07"] = 326  # the step
    counts["2026-08-10"] = 641  # the plateau after it
    assert facts._suspected_break(_records(counts), "2026-08-07") is not None
    assert facts._suspected_break(_records(counts), "2026-08-10") is None


def test_a_ratio_on_trivial_counts_is_not_evidence():
    """Earnings going from 6 trades to 2 is a 0.33x 'departure' and means nothing -- at these
    counts the ratio is arithmetic, not a regime change."""
    counts = {f"2026-07-{d:02d}": 6 for d in range(10, 20)}
    counts["2026-07-24"] = 2
    assert facts._suspected_break(_records(counts), "2026-07-24") is None
