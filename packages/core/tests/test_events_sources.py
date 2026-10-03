"""cherrypick.core.events: Census as the source for its own releases, and the rule-computed events."""

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


def _store(tmp_path, *, census=True):
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
    assert doc["known"] and doc["degraded"] == ["census"]
    assert [(e["label"], e["source"]) for e in doc["events"]] == [("Retail", "FRED")]


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
