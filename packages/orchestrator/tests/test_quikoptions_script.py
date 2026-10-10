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

import copy
import html
import importlib.util
import json
import re
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
    title = {"C": "Call", "P": "Put", "M": "Mixed"}[cp]
    return f'<td><div class="custom-icon" title="{title}">{cp}</div></td>'


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
    mixed_premium: str | None = None,
) -> str:
    # VALE's risk reversal as the site drew it on 2026-10-07: 0.08 x 10,000 printed a premium of
    # 10,000, which price x size x 100 (80,000) does not reproduce.
    mixed = (
        [
            MENU + _symbol("VALE", "Vale S.A.") + _td("12:45:54.837", "10/2/2026 12:45:54 PM")
            + _td("10,000") + _td("15-Jan-27") + _td("RR", "RR - Risk Reversal") + _cp("M")
            + _td("270115 10/17 RR") + _td("0.08") + _td("0.17") + _td(mixed_premium) + _td("*")
            + _td("13.63", "13.62 / 13.63") + _td("BOST")
        ]
        if mixed_premium is not None
        else []
    )  # fmt: skip
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
                + _td("11.12", "11.11 / 11.12") + _td("EDGX"),
                *mixed,
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


_SIDE_TIP = re.compile(
    r'<td><i class="bi bi-circle-fill" data-bs-title="&lt;div&gt;&lt;span class=\'fw-bold\'&gt;(.*?)'
    r'&lt;/span&gt;&lt;br/&gt;(.*?)&lt;br/&gt;Edge: (.*?)&lt;/div&gt;"></i></td>'
)


def _redesigned(page: str) -> str:
    """The same report in the markup the site drew from 2026-10-09: no `quikgrid` class, and the
    side dot's facts in a popover's text rather than a tooltip attribute."""
    page = page.replace(
        'id="table-x" class="quikgrid table table-hover"',
        'id="table-8809a398-4273-405b-9050-f6c17394b539" class="w-full border-none" data-has-header=""',
    )
    return _SIDE_TIP.sub(
        lambda m: (
            '<td class="text-center"><div class="relative w-fit m-auto">'
            '<div class="group dropdown-menu-trigger"><svg></svg></div><div popover="manual">'
            '<div class="text-secondary flex flex-col font-bold"><span class="mb-1 flex items-center gap-1">'
            f'<span class="h-[5px]"><svg></svg></span><span class="h-3">{m.group(1)}</span></span>'
            f'<span>{m.group(2)}</span><span>Edge: <span class="text-primary">{m.group(3)}</span></span>'
            "</div></div></div></td>"
        ),
        page,
    )


def test_the_redesigned_markup_reads_the_same_as_the_old():
    old = fq.parse_report(fq.tables_fragment(report(), fq.HEADINGS, with_date=True))
    page = _redesigned(report())
    assert "quikgrid" not in page and "data-bs-title" not in page
    assert fq._report_ready(page)
    new = fq.parse_report(fq.tables_fragment(page, fq.HEADINGS, with_date=True))
    assert fq.validate_report(new, DAY) == []
    assert new["tables"] == old["tables"]
    assert new["tables"]["sweeps"][0]["side"] == {"sentiment": "Neutral", "fill": "Mid Market", "edge": -0.38}


def _body(page: str, heading: str) -> re.Match:
    at = page.index(f"<h3>{heading}</h3>")
    return re.compile(r"<tbody>.*?</tbody>", re.S).search(page, at)


def _voloi_drawn_as(page: str, body: str) -> str:
    m = _body(page, "Top VolOverOI (OI &gt; 100)")
    return page[: m.start()] + body + page[m.end() :]


def test_an_oi_panel_drawn_as_a_copy_of_openings_is_dropped_and_named():
    # 2026-10-09 on: the site fills the OI > 100 panel with the Openings list, OI 0 throughout.
    page = report()
    page = _voloi_drawn_as(page, _body(page, "Top VolOverOI (Openings)").group(0))
    doc = fq.parse_report(fq.tables_fragment(page, fq.HEADINGS, with_date=True))
    assert fq.validate_report(doc, DAY) == []
    assert "voloi" not in doc["tables"] and set(doc["tables"]) == set(fq.TABLES) - {"voloi"}
    assert doc["unavailable"] == {"voloi": "the site drew the Openings list in this panel"}


def test_an_oi_panel_that_is_wrong_but_not_a_copy_still_rejects():
    page = report()
    copy_ = _body(page, "Top VolOverOI (Openings)").group(0).replace("11,105", "11,106")
    problems = _check(_voloi_drawn_as(page, copy_))
    assert problems and all(p.startswith("voloi AXGN") for p in problems)
    assert fq.parse_report(fq.tables_fragment(report(), fq.HEADINGS, with_date=True))["unavailable"] == {}


def test_a_table_that_is_not_a_report_grid_is_not_read():
    page = report().replace('id="table-x" class="quikgrid table table-hover"', 'id="nav" class="w-full"')
    assert not fq._report_ready(page)
    assert "table missing" in _check(page)[0]


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


def test_an_opening_with_a_little_open_interest_passes_when_it_divides():
    # 2026-10-09: TAL on the Openings list at OI 1, V/OI = 20,062 / 1.
    assert _check(report(openings=("11,105", "5", "2,221.00"))) == []


def test_an_opening_whose_ratio_does_not_divide_is_rejected():
    problems = _check(report(openings=("11,105", "5", "11,105.00")))
    assert len(problems) == 1 and problems[0].startswith("openings AXGN")


def test_an_opening_past_the_oi_panels_band_is_rejected():
    problems = _check(report(openings=("11,105", "101", "109.95")))
    assert len(problems) == 1 and problems[0].startswith("openings AXGN: OI 101")


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


# ------------------------------------------------------------------------------------------------
# Findings from the review of #21: each one shown to fire.
# ------------------------------------------------------------------------------------------------


def test_only_the_sites_own_refusals_count_as_throttling():
    assert fq.is_refusal(429, "https://app.quikoptions.com/_blazor/negotiate")
    assert fq.is_refusal(403, "https://app.quikoptions.com/Market/Options/THOR/Stock")
    assert fq.is_refusal(429, "https://quikoptions.us.auth0.com/u/login")
    # A third party's 403 (an ad-blocked pixel) must not cost a good capture and the next day's.
    assert not fq.is_refusal(403, "https://www.google-analytics.com/g/collect")
    assert not fq.is_refusal(429, "https://fonts.gstatic.com/x.woff2")
    assert not fq.is_refusal(403, "https://quikoptions.us.auth0.com/u/login")  # a sign-in asking
    assert not fq.is_refusal(200, "https://app.quikoptions.com/")


class _Page:
    url = "https://app.quikoptions.com/Market/Options/THOR/Stock"

    def content(self):
        return "<html></html>"


def test_a_429_while_tables_draw_is_throttling_not_a_person(monkeypatch):
    monkeypatch.setattr(fq.time, "sleep", lambda s: None)
    with pytest.raises(fq.Throttled):
        fq._wait_for_tables(_Page(), lambda html: False, ["429 https://app.quikoptions.com/_blazor"], 0)
    with pytest.raises(fq.NeedsPerson):
        fq._wait_for_tables(_Page(), lambda html: False, [], 0)


class _Request:
    def __init__(self, method, post):
        self.method, self.post_data, self.resource_type = method, post, "document"


class _Response:
    def __init__(self, url, method="POST", post="username=a&password=secret", status=200):
        self.url, self.status = url, status
        self.headers = {"content-type": "text/html"}
        self.request = _Request(method, post)

    def text(self):
        return "<html>id_token=abc</html>"


def test_the_probe_never_keeps_a_request_body_or_a_sign_in_page(tmp_path):
    rec = fq.Recorder(tmp_path)
    rec.on_response(_Response("https://quikoptions.us.auth0.com/u/login"))
    rec.on_response(_Response("https://app.quikoptions.com/callback", post="id_token=abc&state=x"))
    rec.close()
    written = (tmp_path / "responses.jsonl").read_text(encoding="utf-8")
    assert "secret" not in written and "id_token=abc" not in written
    assert '"post_bytes": 26' in written


def test_an_impossible_session_date_is_a_problem_not_a_crash():
    doc = fq.parse_report(fq.tables_fragment(report(page_date="13/45/2026"), fq.HEADINGS, with_date=True))
    assert doc["session"] is None and "session date did not read: '13/45/2026'" in doc["problems"]


def test_a_separator_row_is_skipped_and_a_short_row_is_reported():
    page = calendar().replace(
        "</tbody>",
        '<tr><td colspan="10">No more events</td></tr><tr><td>Fri 10/9</td><td>H</td></tr></tbody>',
    )
    doc = fq.parse_calendar(fq.tables_fragment(page, (fq.CALENDAR_HEADING,), False), DAY)
    assert len(doc["events"]) == 2
    assert fq.validate_calendar(doc) == ["calendar: a row of 2 cells: ['Fri 10/9', 'H']"]


def test_an_all_day_event_reads_with_no_time():
    assert fq._calendar_when("Mon 10/12 All Day", DAY) == ("2026-10-12", None)


def test_premium_checks_allow_the_sites_own_abbreviation():
    # SKHY: 70 x 92.35 x 100 = 646,450. Printed as 646.5K it is inside the 50 the K hides.
    assert _check(report(sweep_premium="646.5K")) == []  # 646,450 shown as 646.5K
    problems = _check(report(sweep_premium="650.5K"))  # 4,050 off: more than rounding
    assert len(problems) == 1 and problems[0].startswith("sweeps SKHY")


# ------------------------------------------------------------------------------------------------
# What the capture derives for the console and the Discord series.
# ------------------------------------------------------------------------------------------------


def _derived(**kw):
    return fq.derive(fq.parse_report(fq.tables_fragment(report(**kw), fq.HEADINGS, with_date=True)))


def test_derive_adds_bands_outright_premium_and_side_totals():
    doc = _derived()
    tsla = doc["tables"]["birdseye"][0]
    assert tsla["bands"] == {"1": 421_800, "2-10": 288_900, "11-99": 48_700, "100+": 3_040}
    assert tsla["call_share"] == pytest.approx(462_900 / 762_500)
    pcg = doc["tables"]["outrights"][0]
    assert pcg["premium"] == 1_513_300 and pcg["premium_derived"] is True  # 40,900 x 0.37 x 100
    assert doc["derived"]["premium_by_side"] == {"Bullish": 1_513_300, "Neutral": 646_450}
    assert doc["derived"]["trades_by_side"] == {"Bullish": 1, "Neutral": 1}
    # By size of premium across outrights, sweeps and spreads, whatever the sign: PCG's 1,513,300
    # beats the sold spread's -921,800 and the sweep's 646,450.
    assert doc["derived"]["largest_trade"] == {"table": "outrights", "symbol": "PCG", "premium": 1_513_300}
    assert fq.validate_report(doc, DAY) == []  # deriving never breaks an identity
    bigger = copy.deepcopy(doc)
    bigger["tables"]["spreads"][0]["premium"] = -2_000_000.0
    assert fq.derive(bigger)["derived"]["largest_trade"] == {
        "table": "spreads",
        "symbol": "AI",
        "premium": 2_000_000,
    }


def test_spread_direction_reads_the_price_and_checks_the_delta_by_call_or_put():
    assert fq.spread_direction(0.47, 0.24, "call") == "bought"
    assert fq.spread_direction(-0.22, -0.24, "call") == "sold"
    assert fq.spread_direction(0.47, -0.24, "call") is None  # a call spread debit short delta: not read
    # A put spread bought is a debit short its delta (NVDA 220/170 PS, 2026-10-05); sold, the reverse.
    assert fq.spread_direction(7.02, -0.23, "put") == "bought"
    assert fq.spread_direction(-1.10, 0.12, "put") == "sold"
    assert fq.spread_direction(7.02, 0.23, "put") is None
    assert fq.spread_direction(0.0, 0.1, "call") is None and fq.spread_direction(None, 0.1, "call") is None
    assert fq.spread_direction(0.47, 0.24, None) is None  # call or put unknown: not guessed at


def test_spreads_printed_together_share_a_group_and_names_span_tables():
    doc = _derived()
    roll = dict(doc["tables"]["spreads"][0], price=0.47, delta=0.24, premium=1_969_300.0)
    doc["tables"]["spreads"].append(roll)  # the AI roll: two rows, one time, one size
    doc = fq.derive(doc)
    groups = {r["group"] for r in doc["tables"]["spreads"]}
    assert groups == {"AI 15:01:34.327 41900"}
    assert [r["direction"] for r in doc["tables"]["spreads"]] == ["sold", "bought"]
    assert doc["derived"]["names"] == []  # every synthetic name sits in one table
    doc["tables"]["voloi"][0]["symbol"] = "TSLA"
    names = fq.derive(doc)["derived"]["names"]
    assert names == [{"symbol": "TSLA", "name": "Tesla, Inc.", "tables": ["birdseye", "voloi"]}]


# ------------------------------------------------------------------------------------------------
# A mixed structure (call and put legs, the site's `M`), first seen 2026-10-07.
# ------------------------------------------------------------------------------------------------


def _report_doc(**kw) -> dict:
    return fq.derive(fq.parse_report(fq.tables_fragment(report(**kw), fq.HEADINGS, with_date=True)))


def test_a_mixed_spread_reads_as_mixed_and_its_premium_is_not_checked():
    assert _check(report(mixed_premium="10,000")) == []
    vale = next(r for r in _report_doc(mixed_premium="10,000")["tables"]["spreads"] if r["symbol"] == "VALE")
    assert vale["cp"] == "mixed" and vale["premium"] == 10_000 and vale["premium_unverified"] is True


def test_a_mixed_spread_still_needs_its_numbers():
    assert _check(report(mixed_premium="")) == ["spreads VALE: price, size or premium did not read"]


def test_a_one_sided_spread_is_still_checked_beside_a_mixed_one():
    problems = _check(report(mixed_premium="10,000", spread_premium="921,800"))
    assert len(problems) == 1 and problems[0].startswith("spreads AI")


def test_an_unverified_premium_is_never_the_largest_trade():
    doc = _report_doc(mixed_premium="99,999,999")
    ai = next(r for r in doc["tables"]["spreads"] if r["symbol"] == "AI")
    assert ai["premium_unverified"] is False
    assert doc["derived"]["largest_trade"]["symbol"] != "VALE"


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(fq, "store_dir", lambda: tmp_path)
    folder = tmp_path / "hot-options"
    folder.mkdir()
    return folder


def _reject(folder, day: str, page: str) -> None:
    fragment = fq.tables_fragment(page, fq.HEADINGS, with_date=True)
    (folder / f"{day}.rejected.html").write_text(fragment, encoding="utf-8")
    (folder / f"{day}.rejected.json").write_text(
        json.dumps({"saved_at": "2026-10-02T20:00:00+00:00"}), encoding="utf-8"
    )


def test_reparse_promotes_a_rejected_day_that_now_passes(tmp_path, monkeypatch):
    folder = _store(tmp_path, monkeypatch)
    _reject(folder, "2026-10-02", report(mixed_premium="10,000"))
    assert fq.cmd_reparse(type("A", (), {"session": None})()) == 0
    doc = json.loads((folder / "2026-10-02.json").read_text(encoding="utf-8"))
    assert doc["problems"] == [] and doc["promoted_from_rejected"] is True
    assert doc["saved_at"] == "2026-10-02T20:00:00+00:00"
    assert (folder / "2026-10-02.html").exists()
    assert not list(folder.glob("*.rejected.*"))


def test_reparse_never_replaces_a_saved_day_and_keeps_a_still_failing_reject(tmp_path, monkeypatch):
    folder = _store(tmp_path, monkeypatch)
    _reject(folder, "2026-10-02", report(mixed_premium=""))  # still fails: premium did not read
    assert fq.cmd_reparse(type("A", (), {"session": None})()) == 1
    assert (folder / "2026-10-02.rejected.html").exists() and not (folder / "2026-10-02.json").exists()


class _FakeBrowser:
    version = "151.0.7922.34"

    def close(self):
        pass


class _FakeChromium:
    def __init__(self):
        self.persistent: dict = {}
        self.launches: list[dict] = []

    def launch(self, **kw):
        self.launches.append(kw)
        return _FakeBrowser()

    def launch_persistent_context(self, profile, **kw):
        self.persistent = kw
        return object()


class _FakePlaywright:
    def __init__(self):
        self.chromium = _FakeChromium()


def test_headless_presents_as_the_headed_chrome_it_is(tmp_path, monkeypatch):
    """2026-10-08: the scheduled capture went headless. Headless Chrome announces itself in its
    user-agent; this one sends the regular Chrome user-agent of the installed version instead, so a
    site that began refusing headless browsers would not refuse it for that word alone."""
    monkeypatch.setattr(fq, "store_dir", lambda: tmp_path)
    pw = _FakePlaywright()
    fq._open_browser(pw, headed=False)
    ua = pw.chromium.persistent["user_agent"]
    assert "Headless" not in ua and "Chrome/151.0.0.0 " in ua
    assert pw.chromium.persistent["headless"] is True
    assert pw.chromium.launches == [{"channel": "chrome", "headless": True}]  # the INSTALLED Chrome's version


def test_a_headed_run_leaves_the_user_agent_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(fq, "store_dir", lambda: tmp_path)
    pw = _FakePlaywright()
    fq._open_browser(pw, headed=True)
    assert "user_agent" not in pw.chromium.persistent and pw.chromium.launches == []


def test_quikoptions_browser_commands_share_the_collector_lock(monkeypatch, tmp_path):
    # 2026-10-09: the same profile lock as the vendor collector (loaded from beside it).
    monkeypatch.setattr(fq, "store_dir", lambda: tmp_path)
    shared = fq._shared()
    seen = []
    monkeypatch.setattr(
        fq, "cmd_smoke", lambda args: seen.append(shared.profile_holder(tmp_path / "browser-profile")) or 0
    )
    assert fq.main(["smoke"]) == 0 and seen[0] is not None
    assert not shared.profile_lock_path(tmp_path / "browser-profile").exists()
    assert {"login", "hot-options", "probe", "smoke"} <= fq.BROWSER_COMMANDS
