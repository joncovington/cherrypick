"""The QuikOptions capture's checks, exercised on a synthetic report and calendar.

`scripts/fetch_quikoptions.py` is all that stands between a half-drawn page and the console, the
Discord series and the calendar check, so its value is in failing: each test below breaks one thing
and asserts that exactly that check fires. The site's own HTML is not committed (it is their data
and the repo is public), so the tables here are built to the same markup the 2026-10-02 probe saw;
the last tests run the checks over real saved captures when they are on this machine and skip
otherwise.

It lives here because the orchestrator schedules the job.
"""

from __future__ import annotations

import html
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_quikoptions.py"
DAY = date(2026, 10, 2)  # a Friday: a permanent fact


def _module():
    spec = importlib.util.spec_from_file_location("fetch_quikoptions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fq = _module()

MENU = (
    '<td><div class="dropdown"><a href="#">⋮</a><div class="dropdown-menu">'
    "<button><span>About X</span></button><button><span>Save Trade</span></button></div></div></td>"
)


def _symbol(sym: str, name: str) -> str:
    return (
        f'<td class="block-trigger"><i title="Add to Favorites"></i><span class="fw-bold">{sym}</span>'
        f'<span title="Subscribe to see the 4 badges" class="qe-badge qe-badge-all">+4</span>'
        f'<span title="{name}" class="text-truncate">{name}</span></td>'
    )


def _cp(cp: str) -> str:
    return f'<td><div class="custom-icon" title="{"Call" if cp == "C" else "Put"}">{cp}</div></td>'


def _side(sentiment: str = "Bullish", fill: str = "On Ask", edge: str = "1.00") -> str:
    tip = (
        f"&lt;div&gt;&lt;span class='fw-bold'&gt;{sentiment}&lt;/span&gt;"
        f"&lt;br/&gt;{fill}&lt;br/&gt;Edge: {edge}&lt;/div&gt;"
    )
    return f'<td><i class="bi bi-circle-fill" data-bs-title="{tip}"></i></td>'


def _td(text: str, title: str | None = None) -> str:
    return f'<td title="{title}">{text}</td>' if title else f"<td>{text}</td>"


def _table(heading: str, headers: list[str], rows: list[str]) -> str:
    head = "".join(f'<th><span title="">{html.escape(h, quote=False)}</span></th>' for h in headers)
    body = "".join(f"<tr>{r}</tr>" for r in rows)
    return (
        f"<div><h3>{heading}</h3><svg><path d='M1'></path></svg>"
        f'<table id="table-x" class="quikgrid table table-hover"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


BIRDSEYE_HEAD = ["", "Heatmap Off On", *fq.BUCKETS, "Calls", "Puts", "Total"]
# TSLA's row as the site drew it on 2026-10-02: buckets sum to 762.44K against 762.5K shown.
TSLA = (
    "421.8K 93.2K 48.8K 29.2K 52.6K 11.7K 8.1K 8.5K 7.2K 29.6K 21.4K 21.4K 5.9K 2.9K 83 57"
    " 462.9K 299.6K 762.5K"
)


def report(
    *,
    page_date: str = "10/2/2026",
    birdseye: str = TSLA,
    sweep_premium: str = "646,450",
    spread_premium: str = "-921,800",
    voloi: tuple[str, str, str] = ("135,071", "119", "1,135.05"),
    openings: tuple[str, str, str] = ("11,105", "0", "11,105.00"),
    drop: str | None = None,
    sweep_headers: list[str] | None = None,
    expiry: str = "21-Jan-28",
) -> str:
    tables = {
        "Birdseye": (
            BIRDSEYE_HEAD,
            [MENU + _symbol("TSLA", "Tesla, Inc.") + "".join(_td(c) for c in birdseye.split())],
        ),
        "Top Outrights": (
            ["", "Symbol", "Time (ET)", "Size", "Expires", "Strike", "", "Price", ""],
            [
                MENU + _symbol("PCG", "PG&amp;E Corporation") + _td("13:38:45.517", "10/2/2026 1:38:45 PM")
                + _td("40,900") + _td("15-Jan-27") + _td("16") + _cp("C") + _td("0.37") + _side()
            ],
        ),
        "Top Sweeps": (
            sweep_headers or ["", "Symbol", "Size", "Expires", "Strike", "", "Price", "", "Premium"],
            [
                MENU + _symbol("SKHY", "SK Hynix Inc. American Depositary Shares") + _td("70") + _td(expiry)
                + _td("120") + _cp("C") + _td("92.35") + _side("Neutral", "Mid Market", "-0.38")
                + _td(sweep_premium)
            ],
        ),
        "Top Spreads": (
            ["", "Symbol (10)", "Time (ET)", "Size", "Expires", "Type", "", "Spread", "Price", "Delta",
             "Premium", "*", "Ticker", "Exchange"],
            [
                MENU + _symbol("AI", "C3.ai, Inc.") + _td("15:01:34.327", "10/2/2026 3:01:34 PM")
                + _td("41,900") + _td("09-Oct-26") + _td("CS", "CS - Call Spread") + _cp("C")
                + _td("261009 11/11.5 CS") + _td("-0.22") + _td("-0.24") + _td(spread_premium) + _td("")
                + _td("11.12", "11.11 / 11.12") + _td("EDGX")
            ],
        ),
        "Top VolOverOI (OI &gt; 100)": (
            ["", "Symbol", "Volume", "OI", "V/OI", "Expires", "Strike", ""],
            [MENU + _symbol("SPCX", "Space Exploration Technologies Corp.") + "".join(_td(v) for v in voloi)
             + _td("02-Oct-26") + _td("157.5") + _cp("P")],
        ),
        "Top VolOverOI (Openings)": (
            ["", "Symbol", "Volume", "OI", "V/OI", "Expires", "Strike", ""],
            [MENU + _symbol("AXGN", "AxoGen, Inc.") + "".join(_td(v) for v in openings)
             + _td("20-Nov-26") + _td("35") + _cp("C")],
        ),
    }  # fmt: skip
    body = "".join(_table(h, cols, rows) for h, (cols, rows) in tables.items() if h != drop)
    account = '<div class="account">Jane Example jane@example.com</div>'
    return f"<html><body>{account}<span>{page_date}</span><!--!-->{body}</body></html>"


def calendar(rows: list[tuple[str, str, str]] | None = None) -> str:
    rows = rows or [
        ("Fri 10/2 8:30 AM", "H", "Non Farm Payrolls (Sep)"),
        ("Fri 10/9 10:00 AM", "H", "Michigan Consumer Sentiment (Oct)"),
    ]
    body = [
        _td(when) + f'<td><div class="custom-icon econ-high" title="High Impact">{impact}</div></td>'
        + _td(event) + '<td><span title="US - United States" class="world-flag"></span></td>'
        + _td("USD") + _td("133K") + _td("90K") + _td("29K") + _td("-104K") + _td("-78.195%")
        for when, impact, event in rows
    ]  # fmt: skip
    headers = [
        "Date",
        "Impact",
        "Event",
        "Country",
        "Currency",
        "Previous",
        "Estimate",
        "Actual",
        "Change",
        "Change%",
    ]
    return "<html><body><a>Economic</a><a>IPO</a>" + _table("Upcoming", headers, body) + "</body></html>"


def _check(page_html: str, expected: date | None = DAY) -> list[str]:
    fragment = fq.tables_fragment(page_html, fq.HEADINGS, with_date=True)
    return fq.validate_report(fq.parse_report(fragment), expected)


# ------------------------------------------------------------------------------------------------
# A good capture reads, and each identity fires on its own.
# ------------------------------------------------------------------------------------------------


def test_a_good_report_reads_every_table_and_passes():
    fragment = fq.tables_fragment(report(), fq.HEADINGS, with_date=True)
    doc = fq.parse_report(fragment)
    assert fq.validate_report(doc, DAY) == []
    assert doc["session"] == "2026-10-02"
    assert {k: len(v) for k, v in doc["tables"].items()} == dict.fromkeys(fq.TABLES, 1)
    tsla = doc["tables"]["birdseye"][0]
    assert (tsla["symbol"], tsla["name"], tsla["total"], tsla["buckets"]["=>1K"]) == (
        "TSLA",
        "Tesla, Inc.",
        762_500,
        57,
    )
    sweep = doc["tables"]["sweeps"][0]
    assert sweep["side"] == {"sentiment": "Neutral", "fill": "Mid Market", "edge": -0.38}
    assert (sweep["expires"], sweep["cp"], sweep["premium"]) == ("2028-01-21", "call", 646_450)
    spread = doc["tables"]["spreads"][0]
    assert spread["underlying"] == {"last": 11.12, "bid": 11.11, "ask": 11.12}
    assert spread["premium"] == -921_800  # the site's sign, kept


def test_the_badge_and_row_menu_never_leak_into_a_cell():
    doc = fq.parse_report(fq.tables_fragment(report(), fq.HEADINGS, with_date=True))
    pcg = doc["tables"]["outrights"][0]
    assert pcg["symbol"] == "PCG" and pcg["name"] == "PG&E Corporation"
    assert "+4" not in json.dumps(doc) and "Save Trade" not in json.dumps(doc)


def test_a_birdseye_bucket_off_by_more_than_display_rounding_is_rejected():
    # 1,000 trades short. The sixteen shown buckets and the total can hide up to 0.75K between them
    # (each K cell +/-0.05K, the whole counts none), so this is past what rounding explains.
    off = TSLA.replace("421.8K", "420.8K", 1)
    problems = _check(report(birdseye=off))
    assert len(problems) == 1 and "buckets sum to" in problems[0]
    assert _check(report(birdseye=TSLA.replace("421.8K", "421.7K", 1))) == []  # inside rounding


def test_calls_plus_puts_not_total_is_rejected():
    problems = _check(report(birdseye=TSLA.replace("299.6K", "289.6K")))
    assert len(problems) == 1 and "calls + puts" in problems[0]


def test_a_sweep_premium_off_by_a_contract_is_rejected():
    problems = _check(report(sweep_premium="655,685"))  # one more contract at 92.35
    assert len(problems) == 1 and problems[0].startswith("sweeps SKHY")


def test_a_spread_premium_with_the_wrong_sign_is_rejected():
    problems = _check(report(spread_premium="921,800"))
    assert len(problems) == 1 and problems[0].startswith("spreads AI")


def test_vol_over_oi_that_does_not_divide_is_rejected():
    problems = _check(report(voloi=("135,071", "120", "1,135.05")))
    assert len(problems) == 1 and problems[0].startswith("voloi SPCX")


def test_an_opening_with_open_interest_is_rejected():
    problems = _check(report(openings=("11,105", "5", "2,221.00")))
    assert len(problems) == 1 and problems[0].startswith("openings AXGN")


def test_a_missing_table_is_rejected_by_name():
    problems = _check(report(drop="Top Sweeps"))
    assert problems == ["sweeps: table missing ('Top Sweeps')"]


def test_changed_columns_are_rejected_not_read_by_position():
    headers = ["", "Symbol", "Size", "Expires", "Strike", "", "Price", "Premium", ""]
    problems = _check(report(sweep_headers=headers))
    assert len(problems) == 1 and problems[0].startswith("sweeps: columns changed")


def test_an_unreadable_expiry_is_rejected():
    problems = _check(report(expiry="Jan 2028"))
    assert problems == ["sweeps SKHY: expiry did not read"]


def test_another_session_than_expected_is_rejected():
    assert _check(report(page_date="10/1/2026")) == ["page shows session 2026-10-01, expected 2026-10-02"]
    assert "no session date on the page" in _check(report(page_date="Oct 2"), expected=None)


def test_rounding_is_half_the_last_shown_place():
    assert fq.rounding("421.8K") == pytest.approx(50)
    assert fq.rounding("83") == 0 and fq.rounding("40,900") == 0
    assert fq.rounding("0.37") == pytest.approx(0.005)
    assert fq.number("1.2M") == 1_200_000 and fq.number("-921,800") == -921_800 and fq.number("") is None


# ------------------------------------------------------------------------------------------------
# What is kept.
# ------------------------------------------------------------------------------------------------


def test_the_kept_fragment_never_carries_the_account():
    page = report()
    assert "jane@example.com" in page
    fragment = fq.tables_fragment(page, fq.HEADINGS, with_date=True)
    assert "jane@example.com" not in fragment and "Jane" not in fragment
    assert "<svg" not in fragment and "<!--!-->" not in fragment
    assert fq.parse_report(fragment)["session"] == "2026-10-02"


def test_save_refuses_an_email_overwrites_nothing_and_keeps_rejects_aside(tmp_path):
    doc = {"session": "2026-10-02", "tables": {}}
    assert fq.save_capture("hot-options", "2026-10-02", doc, "<table/>", [], root=tmp_path) == "saved"
    assert (
        fq.save_capture("hot-options", "2026-10-02", doc, "<table>v2</table>", [], root=tmp_path) == "exists"
    )
    assert (tmp_path / "hot-options" / "2026-10-02.html").read_text(encoding="utf-8") == "<table/>"

    assert (
        fq.save_capture("hot-options", "2026-10-05", doc, "<td>a@b.com</td>", [], root=tmp_path) == "rejected"
    )
    kept = tmp_path / "hot-options" / "2026-10-05.rejected.html"
    assert kept.read_text(encoding="utf-8") == ""  # the address is not kept, even aside
    assert not (tmp_path / "hot-options" / "2026-10-05.json").exists()
    saved = json.loads((tmp_path / "hot-options" / "2026-10-05.rejected.json").read_text(encoding="utf-8"))
    assert "email address" in saved["problems"][0]


# ------------------------------------------------------------------------------------------------
# The calendar.
# ------------------------------------------------------------------------------------------------


def test_the_calendar_reads_dates_times_and_country():
    doc = fq.parse_calendar(fq.tables_fragment(calendar(), (fq.CALENDAR_HEADING,), False), DAY)
    assert fq.validate_calendar(doc) == []
    nfp, umich = doc["events"]
    assert (nfp["date"], nfp["time_et"], nfp["impact"], nfp["actual"]) == ("2026-10-02", "08:30", "H", "29K")
    assert nfp["country"] == "US - United States"
    assert (umich["date"], umich["time_et"]) == ("2026-10-09", "10:00")


def test_a_weekday_that_disagrees_with_the_date_is_rejected():
    doc = fq.parse_calendar(
        fq.tables_fragment(calendar([("Thu 10/2 8:30 AM", "H", "NFP")]), (fq.CALENDAR_HEADING,), False), DAY
    )
    assert doc["events"][0]["date"] is None
    assert fq.validate_calendar(doc) == ["calendar: date did not read: 'Thu 10/2 8:30 AM'"]


def test_january_seen_in_december_is_next_year():
    assert fq._calendar_when("Sat 1/1 8:30 AM", date(2027, 12, 30)) == ("2028-01-01", "08:30")
    assert fq._calendar_when("Mon 1/3 8:30 AM", date(2027, 12, 30)) == ("2028-01-03", "08:30")
    assert fq._calendar_when("Tue 10/6", DAY) == ("2026-10-06", None)


def test_an_empty_or_unknown_impact_calendar_is_rejected():
    doc = fq.parse_calendar(
        fq.tables_fragment(calendar([("Fri 10/2 8:30 AM", "X", "NFP")]), (fq.CALENDAR_HEADING,), False), DAY
    )
    assert fq.validate_calendar(doc) == ["calendar: NFP has impact 'X'"]
    assert fq.validate_calendar(fq.parse_calendar("<html></html>", DAY)) == [
        "calendar: table missing",
        "calendar: no events",
    ]


# ------------------------------------------------------------------------------------------------
# Real captures on this machine, when there are any.
# ------------------------------------------------------------------------------------------------


def _saved(kind: str) -> list[Path]:
    try:
        root = fq.store_dir() / kind
    except Exception:  # noqa: BLE001 - no cherrypick home here
        return []
    return sorted(p for p in root.glob("????-??-??.html")) if root.exists() else []


@pytest.mark.skipif(not _saved("hot-options"), reason="no saved hot-options captures on this machine")
def test_every_saved_report_still_passes():
    for path in _saved("hot-options"):
        doc = fq.parse_report(path.read_text(encoding="utf-8"))
        assert fq.validate_report(doc, date.fromisoformat(path.stem)) == [], path


@pytest.mark.skipif(not _saved("calendar"), reason="no saved calendar captures on this machine")
def test_every_saved_calendar_still_passes():
    for path in _saved("calendar"):
        doc = fq.parse_calendar(path.read_text(encoding="utf-8"), date.fromisoformat(path.stem))
        assert fq.validate_calendar(doc) == [], path
