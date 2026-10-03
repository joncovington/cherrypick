"""cherrypick.core.events: Census and Michigan as the sources for their own releases, and the
rule-computed events."""

import json
from datetime import date

from cherrypick.core import events as ev

# A slice of Census's list view, as served: name | date | time | reference period | code | code.
CENSUS_PAGE = """
<tr><td>Advance Monthly Sales for Retail and Food Services</td><td>September 16, 2026</td>
<td>8:30 AM</td><td>August 2026</td><td>A202609160830</td><td>A202608</td></tr>
<tr><td>New Residential Sales</td><td>August 25, 2026</td><td>10:00 AM</td><td>July 2026</td>
<td>A202608251000</td><td>A202607</td></tr>
<tr><td>New Residential Sales</td><td>August 25, 2026</td><td>10:00 AM</td><td>June 2026</td>
<td>A202608251000</td><td>A202606</td></tr>
"""


def test_census_rows_carry_their_own_date_and_time_and_collapse_duplicates():
    assert ev.parse_census(CENSUS_PAGE) == [
        {"name": "New Residential Sales", "at": "2026-08-25T10:00", "source": "Census"},
        {
            "name": "Advance Monthly Sales for Retail and Food Services",
            "at": "2026-09-16T08:30",
            "source": "Census",
        },
    ]
    assert ev.parse_census("") == []


def test_merge_census_never_drops_a_date():
    old = ev.merge_census(None, [{"name": "A", "at": "2025-12-15T08:30"}])
    new = ev.merge_census(old, [{"name": "B", "at": "2026-01-15T08:30"}])
    assert [r["at"] for r in new["releases"]] == ["2025-12-15T08:30", "2026-01-15T08:30"]


# Michigan's home page note, as served on 2026-10-02.
UMICH_PAGE = """
<div class="next_release_note">
  Next data release: Friday, October 09, 2026 for Preliminary October data at 10am ET
</div>
"""


def _store(tmp_path, *, census=True, umich=True):
    cal = tmp_path / "calendar"
    cal.mkdir(parents=True)
    (cal / "bea.json").write_text(
        json.dumps({"X": {"release_dates": ["2026-01-01T12:30:00+00:00"]}}), encoding="utf-8"
    )
    fred_rows = [
        {"name": "Advance Monthly Sales for Retail and Food Services", "at": "2026-09-16"},
        {"name": "Advance Monthly Sales for Retail and Food Services", "at": "2026-09-28"},
    ]
    (cal / "fred_history.json").write_text(
        json.dumps(ev.merge_fred_history(None, fred_rows, "2026-07-01", "2026-12-31")), encoding="utf-8"
    )
    if census:
        (cal / "census.json").write_text(
            json.dumps(ev.merge_census(None, ev.parse_census(CENSUS_PAGE))), encoding="utf-8"
        )
    if umich:
        (cal / "umich.json").write_text(
            json.dumps(ev.merge_umich(None, ev.parse_umich(UMICH_PAGE), "2026-10-02")), encoding="utf-8"
        )
    fred_umich = {"name": "Surveys of Consumers (University of Michigan)", "at": "2026-10-23"}
    hist = json.loads((cal / "fred_history.json").read_text(encoding="utf-8"))
    hist["releases"].append({**fred_umich, "source": "FRED"})
    (cal / "fred_history.json").write_text(json.dumps(hist), encoding="utf-8")
    return cal


def test_census_is_the_source_for_its_own_releases(tmp_path):
    """FRED lists retail sales on 09-28 too; Census, whose release it is, has nothing that day."""
    cal = _store(tmp_path)
    assert [e["label"] for e in ev.day_events(date(2026, 9, 28), root=cal)["events"]] == []
    sept16 = ev.day_events(date(2026, 9, 16), root=cal)["events"]
    assert [(e["label"], e["time_et"], e["source"]) for e in sept16 if e["label"] == "Retail"] == [
        ("Retail", "08:30", "Census")
    ]


def test_without_census_fred_is_the_fallback_and_the_day_is_degraded_not_unknown(tmp_path):
    cal = _store(tmp_path, census=False)
    doc = ev.day_events(date(2026, 9, 28), root=cal)
    # 09-28 is also before Michigan's first note, so that source says it cannot speak for it either.
    assert doc["known"] and doc["degraded"] == ["census", "umich"]
    assert [(e["label"], e["source"]) for e in doc["events"]] == [("Retail", "FRED")]


def test_umich_note_reads_the_preliminary_with_its_time():
    assert ev.parse_umich(UMICH_PAGE) == [
        {
            "name": "Surveys of Consumers (University of Michigan) - Preliminary",
            "period": "2026-10",
            "at": "2026-10-09T10:00",
            "source": "UMich",
        }
    ]
    final = UMICH_PAGE.replace("Friday, October 09", "Friday, October 23").replace("Preliminary", "Final")
    assert ev.parse_umich(final)[0]["name"].endswith("- Final")
    assert ev.parse_umich("<div>Data releases resume soon</div>") == []


def test_the_preliminary_reading_fred_never_lists_is_on_the_calendar(tmp_path):
    """2026-10-09 read as a known, release-free day: FRED's release 91 is the final reading only.
    QuikOptions' calendar showed the gap (docs/quikoptions-plan.md)."""
    cal = _store(tmp_path)
    oct9 = ev.day_events(date(2026, 10, 9), root=cal)
    assert [(e["label"], e["time_et"], e["source"]) for e in oct9["events"]] == [("UMich", "10:00", "UMich")]
    # The final, which FRED does list, is still there, once.
    oct23 = ev.day_events(date(2026, 10, 23), root=cal)["events"]
    assert [(e["label"], e["time_et"]) for e in oct23] == [("UMich", "10:00")]
    # Without Michigan's file the day degrades, it does not become unknown, and the gap shows.
    bare = ev.day_events(date(2026, 10, 9), root=_store(tmp_path / "bare", umich=False))
    assert bare["known"] and "umich" in bare["degraded"] and bare["events"] == []


def test_merge_umich_keeps_every_release_and_covers_each_fetch_to_its_release():
    old = ev.merge_umich(None, [{"name": "A", "period": "2026-10", "at": "2026-10-09T10:00"}], "2026-10-02")
    new = ev.merge_umich(old, [{"name": "B", "period": "2026-10", "at": "2026-10-23T10:00"}], "2026-10-12")
    assert [r["at"] for r in new["releases"]] == ["2026-10-09T10:00", "2026-10-23T10:00"]
    # 10-10 and 10-11 were never looked at: not covered.
    assert new["coverage"] == [["2026-10-02", "2026-10-09"], ["2026-10-12", "2026-10-23"]]


def test_a_release_michigan_moves_replaces_its_old_date():
    """The note said Oct 9; a later note says the same release (October's preliminary) is Oct 16.
    Keeping both would put a release on a day that had none."""
    first = ev.parse_umich(UMICH_PAGE)
    moved = ev.parse_umich(UMICH_PAGE.replace("Friday, October 09", "Friday, October 16"))
    doc = ev.merge_umich(ev.merge_umich(None, first, "2026-10-02"), moved, "2026-10-05")
    assert [r["at"] for r in doc["releases"]] == ["2026-10-16T10:00"]


def test_days_the_note_never_spoke_for_are_degraded_not_quiet(tmp_path):
    """A restamp of past sessions must not write 'no preliminary reading' into history for days
    before the first fetch."""
    cal = _store(tmp_path)
    assert "umich" in ev.day_events(date(2026, 9, 11), root=cal)["degraded"]  # before the first fetch
    assert "umich" not in ev.day_events(date(2026, 10, 5), root=cal)["degraded"]  # covered, quiet
    assert "umich" in ev.day_events(date(2026, 10, 12), root=cal)["degraded"]  # past what it named


def test_source_paths_names_every_file_day_events_reads(tmp_path, monkeypatch):
    """flies caches day_events on these files' modification times; a source missing from the list
    would leave a session tagged from a stale read."""
    read = []
    real = ev._read_json

    def spy(path):
        read.append(path.name)
        return real(path)

    monkeypatch.setattr(ev, "_read_json", spy)
    monkeypatch.setattr(ev, "_read_text", lambda path: read.append(path.name) or "")
    ev.day_events(date(2026, 10, 9), root=_store(tmp_path))
    assert set(read) <= {p.name for p in ev.source_paths(tmp_path / "calendar")}


def _rules(d):
    return [e["label"] for e in ev.rule_events(d)]


def test_rule_events_land_on_their_published_days():
    assert _rules(date(2026, 10, 1)) == ["ISM-Mfg"]  # first business day
    assert _rules(date(2026, 10, 5)) == ["ISM-Svcs"]  # third: Thu 1, Fri 2, Mon 5
    assert _rules(date(2026, 9, 29)) == ["ConfBoard"]  # last Tuesday
    assert _rules(date(2026, 10, 7)) == ["FOMC-Minutes"]  # three weeks after 09-16
    assert _rules(date(2026, 10, 16)) == ["OPEX"]  # third Friday
    # Juneteenth is the third Friday of June 2026, so expiry moves to the Thursday.
    assert _rules(date(2026, 6, 18)) == ["OPEX"] and "OPEX" not in _rules(date(2026, 6, 19))
    assert _rules(date(2026, 10, 2)) == []
