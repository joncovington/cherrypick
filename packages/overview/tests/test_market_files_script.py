"""The market-files fetcher's FRED calendar: paging and the declared releases.

The first live run with a key wrote a calendar with no releases in its first ten days -- this
week's jobs report and jobless claims included -- because FRED lists ~40 releases a day and one
newest-first page of 1,000 dropped the nearest two weeks. These tests pin the fix with a fake FRED
that holds more rows than a page, so a return to one page fails here rather than in a pack.

Here rather than beside the orchestrator's script tests because the script imports this package's
parsers, and each package's CI installs only that package.
"""

from __future__ import annotations

import importlib.util
import urllib.parse
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_market_files.py"


def _module():
    spec = importlib.util.spec_from_file_location("fetch_market_files", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fmf = _module()


def _fake_fred(rows: list[dict], page: int):
    """A FRED that pages `rows` by `offset`/`limit` as the API does and records each request."""
    calls: list[dict] = []

    def get(url: str) -> dict:
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        calls.append(q)
        ordered = rows if q.get("sort_order") == "asc" else list(reversed(rows))
        start = int(q.get("offset", 0))
        return {"count": len(rows), "release_dates": ordered[start : start + min(page, int(q["limit"]))]}

    return get, calls


def _rows(n_filler: int) -> list[dict]:
    """A jobs report on day one, then more filler releases than one page holds."""
    head = [{"release_id": 50, "date": "2026-10-02"}, {"release_id": 180, "date": "2026-10-01"}]
    return head + [{"release_id": 999, "date": "2026-11-01"} for _ in range(n_filler)]


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(fmf, "_pause", lambda: None)


def test_every_page_is_read_so_the_nearest_releases_are_kept():
    get, calls = _fake_fred(_rows(1_500), page=fmf.FRED_PAGE)
    pages = fmf.fred_release_pages("KEY", date(2026, 9, 28), get=get)
    rows = fmf.fred_releases({"release_dates": [r for p in pages for r in p["release_dates"]]})
    assert [(r["name"], r["at"]) for r in rows] == [
        ("Unemployment Insurance Weekly Claims", "2026-10-01"),
        ("Employment Situation", "2026-10-02"),
    ]
    assert len(calls) == 2 and calls[1]["offset"] == str(fmf.FRED_PAGE)
    assert all(c["sort_order"] == "asc" for c in calls)


def test_a_calendar_too_long_to_page_is_refused_not_truncated():
    get, _ = _fake_fred(_rows(fmf.FRED_PAGE * fmf.FRED_MAX_PAGES + 10), page=fmf.FRED_PAGE)
    with pytest.raises(RuntimeError):
        fmf.fred_release_pages("KEY", date(2026, 9, 28), get=get)


def test_only_the_declared_releases_are_kept():
    body = {
        "release_dates": [{"release_id": 10, "date": "2026-10-14"}, {"release_id": 999, "date": "2026-10-14"}]
    }
    assert fmf.fred_releases(body) == [{"name": "Consumer Price Index", "at": "2026-10-14", "source": "FRED"}]


def test_each_fetch_folds_into_a_history_that_never_drops_a_date(tmp_path, monkeypatch):
    """fred.json is replaced every run with the window ahead, so a past release date used to vanish
    the day after it. The history keeps every date and the ranges FRED was asked about."""
    import json

    from cherrypick.overview import files

    monkeypatch.setattr(files, "store_dir", lambda: tmp_path)
    fmf._merge_fred_history(
        [{"name": "Employment Situation", "at": "2026-10-02", "source": "FRED"}], date(2026, 9, 1)
    )
    fmf._merge_fred_history(
        [{"name": "Consumer Price Index", "at": "2026-10-14", "source": "FRED"}], date(2026, 10, 3)
    )
    doc = json.loads((tmp_path / "calendar" / "fred_history.json").read_text(encoding="utf-8"))
    assert [r["at"] for r in doc["releases"]] == ["2026-10-02", "2026-10-14"]
    assert doc["coverage"] == [["2026-09-01", "2026-11-17"]]  # overlapping windows merge


def test_census_fetch_folds_into_a_calendar_that_never_drops_a_date(tmp_path, monkeypatch):
    import json

    from cherrypick.overview import files

    monkeypatch.setattr(files, "store_dir", lambda: tmp_path)
    page = (
        "<td>Advance Monthly Sales for Retail and Food Services</td><td>September 16, 2026</td>"
        "<td>8:30 AM</td><td>August 2026</td><td>A202609160830</td>"
    )
    report = {"calendar": {}, "problems": []}
    fmf.fetch_census(report, get=lambda url: page.encode())
    fmf.fetch_census(report, get=lambda url: b"<html>maintenance</html>")
    doc = json.loads((tmp_path / "calendar" / "census.json").read_text(encoding="utf-8"))
    assert [r["at"] for r in doc["releases"]] == ["2026-09-16T08:30"]
    assert report["problems"] == ["Census: the calendar parsed to nothing; kept the old one"]


def test_umich_fetch_folds_the_next_release_and_refuses_a_page_without_one(tmp_path, monkeypatch):
    import json

    from cherrypick.overview import files

    monkeypatch.setattr(files, "store_dir", lambda: tmp_path)
    note = "Next data release: Friday, October 09, 2026 for Preliminary October data at 10am ET"
    report = {"calendar": {}, "problems": []}
    fmf.fetch_umich(report, date(2026, 10, 2), get=lambda url: f"<div>{note}</div>".encode())
    fmf.fetch_umich(report, date(2026, 10, 3), get=lambda url: b"<html>maintenance</html>")
    doc = json.loads((tmp_path / "calendar" / "umich.json").read_text(encoding="utf-8"))
    assert [r["at"] for r in doc["releases"]] == ["2026-10-09T10:00"]
    assert report["calendar"]["umich_next"] == "2026-10-09T10:00"
    assert doc["coverage"] == [["2026-10-02", "2026-10-09"]]
    assert report["problems"] == ["UMich: no next-release note on the page; kept the old dates"]
