"""The market-files fetcher's FRED calendar: paging and the declared releases.

The first live run with a key wrote a calendar with no releases in its first ten days -- this
week's jobs report and jobless claims included -- because FRED lists ~40 releases a day and one
newest-first page of 1,000 dropped the nearest two weeks. These tests pin the fix with a fake FRED
that holds more rows than a page, so a return to one page fails here rather than in a pack.

Beside the other script tests because the orchestrator schedules it.
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
