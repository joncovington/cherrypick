"""cherrypick.core.events: which releases a day had, when, and whether the calendar can say."""

import json
from datetime import date

import pytest

from cherrypick.core import events as ev


def _store(tmp_path, *, bea_year=True, fred_from=None, fred_rows=()):
    cal = tmp_path / "calendar"
    cal.mkdir(parents=True)
    if bea_year:
        bea = {
            "Personal Income and Outlays": {"release_dates": ["2026-09-30T12:30:00+00:00"]},
            "U.S. International Trade in Goods and Services": {
                "release_dates": ["2026-09-03T12:30:00+00:00"]
            },
        }
    else:
        bea = {"X": {"release_dates": ["2025-01-01T12:30:00+00:00"]}}
    (cal / "bea.json").write_text(json.dumps(bea), encoding="utf-8")
    if fred_from:
        doc = ev.merge_fred_history(None, list(fred_rows), fred_from, "2026-12-31")
        (cal / "fred_history.json").write_text(json.dumps(doc), encoding="utf-8")
    return cal


def test_a_known_day_lists_its_releases_with_et_times(tmp_path):
    cal = _store(
        tmp_path,
        fred_from="2026-07-01",
        fred_rows=[{"name": "Job Openings and Labor Turnover Survey", "at": "2026-09-30"}],
    )
    doc = ev.day_events(date(2026, 9, 30), root=cal)
    assert doc["known"] and doc["missing"] == []
    assert [(e["label"], e["time_et"], e["major"]) for e in doc["events"]] == [
        ("PCE", "08:30", True),
        ("JOLTS", "10:00", True),
    ]


def test_unknown_is_never_quiet(tmp_path):
    """No FRED coverage for the day (a machine with no key, a fetcher that stopped) must read as
    unknown -- a session with no FRED data looks exactly like one with no releases otherwise."""
    doc = ev.day_events(date(2026, 9, 30), root=_store(tmp_path / "a"))
    assert not doc["known"] and doc["missing"] == ["fred"]
    assert ev.phase(doc, 600) == ("unknown", None, None)
    no_bea = ev.day_events(
        date(2026, 9, 30), root=_store(tmp_path / "b", bea_year=False, fred_from="2026-07-01")
    )
    assert no_bea["missing"] == ["bea"]


def test_the_fomc_year_must_be_bundled(tmp_path):
    cal = _store(tmp_path, fred_from="2026-07-01")
    assert "FOMC" in [e["label"] for e in ev.day_events(date(2026, 9, 16), root=cal)["events"]]
    assert "fomc" in ev.day_events(date(2099, 9, 16), root=cal)["missing"]


@pytest.mark.parametrize(
    "now_min, expected",
    [(9 * 60 + 59, ("before", None)), (10 * 60, ("after", 0.0)), (11 * 60, ("after", 60.0))],
)
def test_phase_reads_the_latest_major_release_at_or_before_now(now_min, expected):
    doc = {
        "known": True,
        "events": [
            {"label": "Claims", "time_et": "08:30", "major": False},
            {"label": "JOLTS", "time_et": "10:00", "major": True},
        ],
    }
    bucket, value, labels = ev.phase(doc, now_min)
    assert (bucket, value) == expected
    assert labels == "Claims 08:30, JOLTS 10:00", "minor releases are recorded, not dropped"


def test_a_day_with_only_minor_releases_is_none():
    doc = {"known": True, "events": [{"label": "Claims", "time_et": "08:30", "major": False}]}
    assert ev.phase(doc, 700) == ("none", None, "Claims 08:30")


def test_history_merges_rows_and_coverage_without_dropping_either():
    a = ev.merge_fred_history(None, [{"name": "CPI-ish", "at": "2026-08-12"}], "2026-08-01", "2026-09-15")
    b = ev.merge_fred_history(a, [{"name": "CPI-ish", "at": "2026-09-11"}], "2026-09-16", "2026-10-31")
    c = ev.merge_fred_history(b, [], "2026-12-01", "2026-12-31")
    assert [r["at"] for r in c["releases"]] == ["2026-08-12", "2026-09-11"]
    assert c["coverage"] == [["2026-08-01", "2026-10-31"], ["2026-12-01", "2026-12-31"]]


def test_parse_bea_collapses_duplicates():
    text = json.dumps({"GDP": {"release_dates": ["2026-09-30T12:30:00+00:00", "2026-09-30T12:30:00+00:00"]}})
    assert ev.parse_bea(text) == [{"name": "GDP", "at": "2026-09-30T12:30:00+00:00", "source": "BEA"}]
    assert ev.parse_bea("not json") == []
