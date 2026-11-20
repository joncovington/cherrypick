"""The market-files fetcher: FRED's paging and declared releases, and the risk reversal's window.

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
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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


TREASURY_CSV = 'Date,"3 Mo","10 Yr"\n12/30/{y},1.50,2.50\n12/29/{y},1.40,2.40\n'


def test_treasury_history_writes_each_missing_past_year_and_skips_one_on_disk(tmp_path, monkeypatch, capsys):
    """The daily fetch keeps only this year and last, so a replay over 2016-2024 read a zero rate.
    The seed writes every missing past year once and never this year (the daily fetch's file)."""
    import json

    from cherrypick.overview import files

    monkeypatch.setattr(files._treasury._home, "data_dir", lambda name: tmp_path / name)
    asked = []

    def get(url: str) -> bytes:
        year = int(url.split("field_tdr_date_value=")[1].split("&")[0])
        asked.append(year)
        return TREASURY_CSV.format(y=year).encode()

    monkeypatch.setattr(fmf, "_get", get)
    this_year = fmf.datetime.now(files.ET).date().year
    files.treasury_path(this_year - 2).parent.mkdir(parents=True)
    files.treasury_path(this_year - 2).write_text(TREASURY_CSV.format(y=this_year - 2), encoding="utf-8")
    assert fmf.main(["treasury-history", "--since", str(this_year - 4)]) == 0
    assert asked == [this_year - 4, this_year - 3, this_year - 1]
    assert json.loads(capsys.readouterr().out)["ok"]
    assert not files.treasury_path(this_year).exists()
    assert (
        files.parse_treasury(files.treasury_path(this_year - 4).read_text(encoding="utf-8"))[-1][1]["3m"]
        == 1.5
    )


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


def test_a_clean_run_after_a_warning_says_it_recovered_once(monkeypatch, tmp_path):
    # 2026-10-21: FRED's empty body warned three times that evening; the next morning's clean run
    # said nothing, so the warning stayed the last word in the channel.
    from cherrypick.overview import files

    fmf = _module()
    monkeypatch.setattr(files, "store_dir", lambda: tmp_path)
    sent = []
    monkeypatch.setattr(fmf._common, "notify", lambda level, source, title, msg: sent.append((level, title)))
    fmf._note_outcome([])
    assert sent == []  # nothing to recover from
    fmf._note_outcome(["FRED: Expecting value: line 1 column 1 (char 0)"])
    assert sent == [("WARNING", "Market files incomplete")]
    fmf._note_outcome([])
    assert sent[-1] == ("INFO", "Recovered: Market files incomplete")
    fmf._note_outcome([])
    assert len(sent) == 2  # said once, then forgotten


# --------------------------------------------------------------------------- the risk reversal
# 2026-11-19: Cboe's delayed chain moved behind a host that refuses scripts, and the row now comes
# from the broker's greeks. SPX options trade overnight from 20:15 ET, so their IVs describe the
# close only between the close and then; outside that window nothing is read.


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("America/New_York"))


@pytest.mark.parametrize(
    ("now", "session", "open_"),
    [
        (_et(2026, 11, 19, 18, 45), date(2026, 11, 19), True),  # the evening run
        (_et(2026, 11, 19, 16, 10), date(2026, 11, 18), False),  # SPX options still trading
        (_et(2026, 11, 19, 20, 15), date(2026, 11, 19), False),  # the overnight session has opened
        (_et(2026, 11, 20, 7, 45), date(2026, 11, 19), False),  # the morning retry
        (_et(2026, 11, 22, 18, 45), date(2026, 11, 20), False),  # a Sunday
        (_et(2026, 11, 27, 13, 20), date(2026, 11, 27), True),  # the early close: 13:00 + 15 min
    ],
)
def test_the_risk_reversal_is_read_only_between_the_close_and_the_overnight_session(now, session, open_):
    assert fmf.rr_window(now) == (session, open_)


def _rr_options(session: date):
    exp = date.fromordinal(session.toordinal() + 30)
    return [
        {"expiration": exp, "right": "C", "delta": 0.30, "iv": 0.10, "bid": 1.0, "ask": 1.1},
        {"expiration": exp, "right": "C", "delta": 0.20, "iv": 0.12, "bid": 1.0, "ask": 1.1},
        {"expiration": exp, "right": "P", "delta": -0.30, "iv": 0.16, "bid": 1.0, "ask": 1.1},
        {"expiration": exp, "right": "P", "delta": -0.20, "iv": 0.18, "bid": 1.0, "ask": 1.1},
    ]


def test_the_evening_run_writes_the_session_row_beside_the_old_ones(monkeypatch, tmp_path):
    import json

    from cherrypick.overview import files

    monkeypatch.setattr(files, "store_dir", lambda: tmp_path)
    files.risk_reversal_path().parent.mkdir(parents=True)
    old = {"session": "2026-11-18", "rr_vol_pts": -6.46, "source": "cboe_delayed_spx_chain"}
    files.risk_reversal_path().write_text(json.dumps({"2026-11-18": old}), encoding="utf-8")
    report = {"problems": []}
    fmf.fetch_risk_reversal(report, _et(2026, 11, 19, 18, 45), read=lambda d: (7175.83, _rr_options(d)))
    rows = json.loads(files.risk_reversal_path().read_text(encoding="utf-8"))
    assert report["problems"] == []
    assert rows["2026-11-18"] == old
    assert (rows["2026-11-19"]["rr_vol_pts"], rows["2026-11-19"]["source"]) == (-6.0, files.RR_SOURCE)


def test_outside_the_window_nothing_is_read_and_only_a_missing_row_is_a_problem(monkeypatch, tmp_path):
    import json

    from cherrypick.overview import files

    monkeypatch.setattr(files, "store_dir", lambda: tmp_path)

    def never(_day):
        raise AssertionError("the broker was read outside the window")

    report = {"problems": []}
    fmf.fetch_risk_reversal(report, _et(2026, 11, 20, 7, 45), read=never)
    assert report["problems"] == [
        "SPX risk reversal: no row for 2026-11-19; it is read between the close and 20:15 ET"
    ]
    files.risk_reversal_path().parent.mkdir(parents=True)
    files.risk_reversal_path().write_text(
        json.dumps({"2026-11-19": {"session": "2026-11-19"}}), encoding="utf-8"
    )
    report = {"problems": []}
    fmf.fetch_risk_reversal(report, _et(2026, 11, 20, 7, 45), read=never)
    assert report["problems"] == []


def test_a_broker_failure_or_an_unreadable_chain_is_a_problem_and_writes_nothing(monkeypatch, tmp_path):
    from cherrypick.overview import files

    monkeypatch.setattr(files, "store_dir", lambda: tmp_path)

    def down(_day):
        raise RuntimeError("broker credentials are missing")

    report = {"problems": []}
    fmf.fetch_risk_reversal(report, _et(2026, 11, 19, 18, 45), read=down)
    fmf.fetch_risk_reversal(report, _et(2026, 11, 19, 18, 45), read=lambda d: (7175.83, []))
    assert report["problems"] == [
        "SPX risk reversal: broker: RuntimeError: broker credentials are missing",
        "SPX risk reversal: the broker's chain gave no 25-delta reading (0 options read)",
    ]
    assert not files.risk_reversal_path().exists()
