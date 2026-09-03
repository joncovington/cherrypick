"""The pre-market forward scan, and the pre-filter it feeds.

Screening splits into a slow, stable half (the earnings calendar and every Dolt-derived metric) and
a fast, perishable half (live chain, spread, expected move). The slow half is computed pre-market so
the entry window is not spent on it. The property that matters most here is the one that keeps that
safe: a stale or borderline morning reading must never decide an entry.
"""

import time
from datetime import datetime

import pytest

from cherrypick.earnings import paper_loop, symbol_watch

ET = paper_loop.ET
CONFIG = {"symbol_watch": {"at": "06:30", "days": 10}, "near_miss_min_market_cap": 1_000_000_000}


def at(hhmm, day="2026-08-12"):
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=ET)


# --------------------------------------------------------------------------- the phase
def test_the_forward_scan_owns_the_pre_market_slot():
    assert (
        paper_loop.phase_for(at("06:30"), CONFIG, entry_done=False, forward_scan_done=False) == "forward_scan"
    )


def test_it_runs_once_a_day():
    """Once done, the slot goes quiet rather than re-running every minute until 09:00."""
    assert paper_loop.phase_for(at("06:45"), CONFIG, entry_done=False, forward_scan_done=True) == "off_hours"


def test_it_never_runs_inside_the_session():
    """It is pre-market work: a Dolt-heavy walk of the next ten trading days has no business
    competing with position marking."""
    for hhmm in ("09:05", "10:00", "15:45"):
        phase = paper_loop.phase_for(at(hhmm), CONFIG, entry_done=False, forward_scan_done=False)
        assert phase != "forward_scan", hhmm


def test_it_can_be_turned_off():
    disabled = {"symbol_watch": {"enabled": False}}
    assert (
        paper_loop.phase_for(at("06:30"), disabled, entry_done=False, forward_scan_done=False) == "off_hours"
    )


def test_it_does_not_run_on_a_non_trading_day():
    assert (
        paper_loop.phase_for(at("06:30", "2026-08-15"), CONFIG, entry_done=False, forward_scan_done=False)
        == "off_hours"
    )


# --------------------------------------------------------------------------- the pre-filter
def _entry(symbol="AAPL", **fields):
    return {"symbol": symbol, "winrate": 0.8, "avg_volume": 50_000_000, "market_cap": 3e12, **fields}


def test_a_structurally_disqualified_name_is_dropped():
    drop, reason = symbol_watch.stable_prefilter_verdict(_entry(winrate=0.10), CONFIG)
    assert drop and "winrate" in reason


def test_a_qualifying_name_is_kept():
    assert symbol_watch.stable_prefilter_verdict(_entry(), CONFIG) == (False, None)


def test_a_missing_value_never_drops_a_name():
    """'Couldn't determine' is not 'known bad' — the same posture the tier badge takes."""
    assert symbol_watch.stable_prefilter_verdict(_entry(winrate=None), CONFIG)[0] is False


def test_iv_rv_is_deliberately_not_a_prefilter_criterion():
    """Implied vol RISES into an announcement, so a name below the floor this morning can clear it
    by the afternoon — filtering on it would drop exactly what the strategy exists to find."""
    below = _entry(iv_rv_ratio=0.4)
    assert symbol_watch.stable_prefilter_verdict(below, CONFIG) == (False, None)
    assert "iv_rv_ratio" not in symbol_watch._STABLE_PREFILTER


def test_perishable_chain_readings_are_not_prefilter_criteria():
    for key in ("price", "term_structure", "expected_move_pct", "combined_open_interest"):
        assert key not in symbol_watch._STABLE_PREFILTER


def test_the_filter_measures_against_the_loosest_floor():
    """Near-miss, not the strict bar: this may only drop a name that could not pass under ANY
    symbol_screen setting, because it runs before anyone has chosen one."""
    thresholds = symbol_watch._tier_thresholds(CONFIG)
    just_above_near_miss = _entry(winrate=thresholds["near_miss_min_winrate"] + 0.01)
    assert symbol_watch.stable_prefilter_verdict(just_above_near_miss, CONFIG) == (False, None)


# --------------------------------------------------------------------------- freshness
@pytest.fixture
def snapshot(monkeypatch):
    def use(symbols, completed_at):
        monkeypatch.setattr(
            symbol_watch,
            "read_snapshot",
            lambda: {"pass_completed_at": completed_at, "symbols": symbols},
        )

    return use


def test_todays_snapshot_narrows_the_calendar(snapshot):
    snapshot({"BAD": _entry("BAD", winrate=0.05), "GOOD": _entry("GOOD")}, time.time())
    keep, dropped = symbol_watch.prefilter_symbols(["BAD", "GOOD"], CONFIG)
    assert keep == ["GOOD"] and "BAD" in dropped


def test_a_stale_snapshot_is_ignored_entirely(snapshot):
    """Filtering today's calendar against last week's readings is exactly the quiet wrongness this
    module exists to avoid — so an old pass is not partially trusted, it is not trusted."""
    snapshot({"BAD": _entry("BAD", winrate=0.05)}, time.time() - 7 * 86400)
    keep, dropped = symbol_watch.prefilter_symbols(["BAD"], CONFIG)
    assert keep == ["BAD"] and dropped == {}


def test_a_never_written_snapshot_keeps_everything(snapshot):
    snapshot({}, None)
    assert symbol_watch.prefilter_symbols(["AAPL"], CONFIG) == (["AAPL"], {})


def test_a_symbol_absent_from_the_snapshot_is_kept(snapshot):
    """It was never scanned — that is not evidence against it."""
    snapshot({"OTHER": _entry("OTHER")}, time.time())
    keep, _ = symbol_watch.prefilter_symbols(["UNSEEN"], CONFIG)
    assert keep == ["UNSEEN"]


# --------------------------------------------------------------------------- covered_symbols
def test_covered_symbols_is_this_mornings_measured_universe(snapshot):
    snapshot({"CSCO": _entry("CSCO"), "AMAT": _entry("AMAT")}, time.time())
    assert symbol_watch.covered_symbols() == {"CSCO", "AMAT"}


def test_covered_symbols_keeps_a_name_whose_own_scan_errored(snapshot):
    """Still in the liquid universe, and the entry scan re-screens every survivor on live data —
    a failed morning chain fetch is not grounds to make it unreachable all day."""
    broken = {**_entry("CSCO"), "error": "chain fetch failed"}
    snapshot({"CSCO": broken}, time.time())
    assert symbol_watch.covered_symbols() == {"CSCO"}


def test_a_stale_snapshot_covers_nothing(snapshot):
    """Empty degrades the entry calendar to exact-timing-only, which is the safe direction: the
    alternative is admitting every unannotated row on a ~250-name calendar."""
    snapshot({"CSCO": _entry("CSCO")}, time.time() - 7 * 86400)
    assert symbol_watch.covered_symbols() == set()


def test_a_never_written_snapshot_covers_nothing(snapshot):
    snapshot({}, None)
    assert symbol_watch.covered_symbols() == set()


def test_the_forward_scan_leaves_a_run_trail(tmp_path, monkeypatch):
    """The top of the funnel has to be readable from the run log like every other phase.

    It was the one phase that logged nothing, and that is exactly how eleven starved sessions hid:
    the Dolt clone's earnings calendar had aged out, so this found zero symbols every morning, and
    the only trace anywhere was the entry phase's `opened: []` -- which reads identically to
    "candidates were screened and none cleared". Recording the COUNT is what separates a scan that
    had nothing to look at from one whose universe filter is broken.
    """
    import json as _json

    from cherrypick.core import home as _home

    monkeypatch.setattr(_home, "logs_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(symbol_watch, "refresh_symbol_watch", lambda **kw: {"ok": True, "symbols": {}})

    paper_loop.run_iteration(CONFIG, at("06:30"))

    lines = (tmp_path / "logs" / "earnings_paper.log").read_text(encoding="utf-8").strip().splitlines()
    scans = [r for r in (_json.loads(x) for x in lines) if r["phase"] == "forward_scan"]
    assert scans, "a forward scan that logs nothing is a starved session nobody can see"
    assert scans[-1]["symbols"] == 0, "the count is the whole point -- 0 must be recorded, not omitted"


# --------------------------------------------------------------------------- the count itself
def _scan_record(tmp_path, monkeypatch, result):
    """Drive one forward-scan tick and return what it wrote to the run trail."""
    import json as _json

    from cherrypick.core import home as _home

    monkeypatch.setattr(_home, "logs_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(symbol_watch, "refresh_symbol_watch", lambda **kw: result)
    # The once-a-day gate reads `loop_iterations`, so without this a second tick in the same test
    # (or a test running after one that already scanned this session) lands in `off_hours` and logs
    # nothing. That gate has its own test above; these are about the number it records.
    monkeypatch.setattr(paper_loop, "forward_scan_already_ran", lambda _session: False)
    paper_loop.run_iteration(CONFIG, at("06:30"))
    lines = (tmp_path / "logs" / "earnings_paper.log").read_text(encoding="utf-8").strip().splitlines()
    return [r for r in (_json.loads(x) for x in lines) if r["phase"] == "forward_scan"][-1]


def test_the_count_is_what_the_scan_actually_measured(tmp_path, monkeypatch):
    """The counter read `result["entries"]` or `result["symbols"]`; `refresh_symbol_watch` returns
    neither -- it returns `{"ok", "total", "done", "skipped"}`.

    So it reported 0 on EVERY session from the day it was added, including 2026-09-02, which went on
    to screen 198 candidates and open 19 positions. A number that can only ever take its own alarm
    value is worse than no number: it made the 2026-08-17..24 block read as a dead scanner when the
    snapshot was in fact fine, which is the exact confusion the count exists to prevent.
    """
    rec = _scan_record(tmp_path, monkeypatch, {"ok": True, "total": 22, "done": 18, "skipped": ["AAA"]})

    assert rec["symbols"] == 18, "the completed count, not zero"
    assert rec["total"] == 22


def test_a_partial_pass_is_distinguishable_from_a_complete_one(tmp_path, monkeypatch):
    """`refresh_symbol_watch` names this difference deliberately -- a pass that measured 18 of 22
    reads exactly like one that measured all 22 if only the completed count survives the hop."""
    partial = _scan_record(tmp_path, monkeypatch, {"ok": True, "total": 22, "done": 18, "skipped": ["AAA"]})
    complete = _scan_record(tmp_path, monkeypatch, {"ok": True, "total": 18, "done": 18, "skipped": []})

    assert partial["symbols"] == complete["symbols"] == 18
    assert partial["total"] != complete["total"], "only `total` can tell the two apart"
    assert partial["skipped"] == ["AAA"] and complete["skipped"] == []


def test_a_result_without_counts_degrades_to_zero(tmp_path, monkeypatch):
    """Telemetry never fails the tick: an older or truncated result must not take the loop down."""
    rec = _scan_record(tmp_path, monkeypatch, {"ok": True})

    assert rec["symbols"] == 0 and rec["total"] == 0
