"""The report collector's edition checks, exercised on a synthetic edition.

The collector (`scripts/fetch_vendor_edition.py`) is the only thing standing between a half-loaded
page and the market-report fixture, so its value is entirely in failing: each test below breaks one
thing and asserts that exactly that check fires. The vendor's own HTML cannot be committed, so the
edition here is built to the same shape; the last test runs the checks over the real saved editions
when they are present on this machine and skips otherwise.

It lives here because the orchestrator schedules the job; it moves to the market-report package
once that package exists (docs/market-report-plan.md, "Where the code goes").
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_vendor_edition.py"
DAY = date(2026, 9, 25)  # a Friday: a permanent fact, nothing in it to expire


def _module():
    spec = importlib.util.spec_from_file_location("fetch_vendor_edition", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fve = _module()


def _ticker(sym: str, colour: str) -> str:
    style = f"font-weight: bold; color: #{colour}; text-decoration: none;"
    return f'<a style="{style}" href="x?symbol={sym}">{sym}</a>'


def edition(
    *,
    dateline: str = "Friday, September 25, 2026",
    covers: str | None = "Covers Thursday, September 24’s closing prices",
    stated: tuple[int, int] = (2, 3),
    leaders: tuple[str, ...] = ("1B5E20", "43A047"),
    laggards: tuple[str, ...] = ("7A0030", "C60651", "E5384F"),
    footer: bool = True,
) -> str:
    rows = (
        "<table><tr><th>Sector</th><th>Net</th></tr><tr><td>Technology</td><td>"
        + ", ".join(_ticker("L" + "ABCDEFGH"[i], c) for i, c in enumerate(leaders))
        + "</td><td>"
        + ", ".join(_ticker("G" + "ABCDEFGH"[i], c) for i, c in enumerate(laggards))
        + "</td></tr></table><p>The three shades show how long a name has been beating...</p>"
    )
    body = (
        f"<div>{dateline}</div>"
        + (f"<div>{covers}, updated with pre-market futures.</div>" if covers else "")
        + "<p>"
        + "Market prose. " * 600
        + "</p>"
        + f'<div>Outperforming</div><div style="x">{stated[0]}</div>'
        + f'<div>Underperforming</div><div style="x">{stated[1]}</div>'
        + rows
        + (
            "<p>Prior to buying or selling an option, a person must receive a copy of "
            "Characteristics and Risks of Standardized Options.</p>"
            if footer
            else ""
        )
    )
    return (
        "<!doctype html><html><head><title>Vendor Report - September 25, 2026</title></head>"
        "<body><h1>Vendor Report - September 25, 2026</h1>" + body + "</body></html>"
    )


def test_a_complete_edition_passes():
    assert fve.validate_edition(edition(), DAY) == []


def test_a_wrong_dateline_fails():
    problems = fve.validate_edition(edition(dateline="Thursday, September 24, 2026"), DAY)
    assert any("dateline" in p for p in problems)


def test_the_wrapper_title_cannot_stand_in_for_the_dateline():
    """The <h1> we write ourselves carries the date; only the vendor's own dateline may count."""
    problems = fve.validate_edition(edition(dateline=""), DAY)
    assert any("dateline" in p for p in problems)


def test_a_missing_covers_line_fails():
    assert any("Covers" in p for p in fve.validate_edition(edition(covers=None), DAY))


def test_a_covers_date_that_is_not_an_earlier_session_fails():
    problems = fve.validate_edition(edition(covers="Covers Friday, September 25’s closing prices"), DAY)
    assert any("not a session before" in p for p in problems)


def test_a_card_cut_off_before_the_footer_fails():
    assert any("disclaimer" in p for p in fve.validate_edition(edition(footer=False), DAY))


def test_a_ticker_in_the_wrong_colour_fails_the_count_check():
    problems = fve.validate_edition(edition(leaders=("1B5E20", "7A0030")), DAY)
    assert problems == ["stage colours decode to 1/4; the edition states 2/3"]


def test_an_empty_page_shell_fails_every_content_check():
    shell = "<html><body><h1>Vendor Report - September 25, 2026</h1></body></html>"
    problems = fve.validate_edition(shell, DAY)
    assert len(problems) == 6


def test_an_existing_edition_is_never_overwritten(tmp_path):
    (tmp_path / "2026-09-25.html").write_text("the good one", encoding="utf-8")
    saved, problems = fve.save_edition(edition(), DAY, tmp_path)
    assert not saved and "already exists" in problems[0]
    assert (tmp_path / "2026-09-25.html").read_text(encoding="utf-8") == "the good one"


def test_a_failing_edition_is_kept_aside_not_saved(tmp_path):
    saved, _ = fve.save_edition(edition(footer=False), DAY, tmp_path)
    assert not saved
    assert not (tmp_path / "2026-09-25.html").exists()
    assert (tmp_path / "2026-09-25.rejected.html").exists()


def test_a_passing_edition_is_saved_under_its_date(tmp_path):
    saved, problems = fve.save_edition(edition(), DAY, tmp_path)
    assert saved and problems == []
    assert (tmp_path / "2026-09-25.html").exists()


REAL = Path.home() / ".cherrypick" / "data" / "market-report" / "vendor-editions"


@pytest.mark.skipif(not list(REAL.glob("????-??-??.html")), reason="no saved editions on this machine")
def test_every_saved_edition_passes_its_own_checks():
    for path in sorted(REAL.glob("????-??-??.html")):
        when = fve.date_from_filename(path)
        assert fve.validate_edition(path.read_text(encoding="utf-8"), when) == [], path.name


# --- pacing: the vendor must never see fast consecutive requests -------------------------------


def test_pacing_limits_are_not_loosened():
    """The user's standing instruction: never request fast enough to risk throttling or a block.
    These floors fail loudly if anyone shortens a pause or raises a cap."""
    assert fve.PAUSE_RANGE_S[0] >= 20
    assert fve.CHART_PAUSE_RANGE_S[0] >= 30
    assert fve.MAX_BACKFILL <= 3
    assert fve.MAX_CHARTS <= 40
    assert fve.COOLDOWN.total_seconds() >= 24 * 3600


# --- chart captures --------------------------------------------------------------------------


def _why(symbol="ANET.XNYS", support=(202.52,), rank=10):
    return {
        "historicalQuotes": [{"symbol": symbol, "date": "2026-09-25", "close": 206.55}],
        "supportAndResistance": {
            "support": [{"value": v, "date": "2026-08-12T00:00:00"} for v in support],
            "resistance": [{"value": 214.89, "date": "2026-08-05T00:00:00"}],
        },
        "technicalRank": rank,
    }


def test_a_complete_chart_capture_passes():
    assert fve.validate_chart_capture(_why(), "ANET") == []
    assert fve.session_of(_why()) == "2026-09-25"


def test_a_capture_for_the_wrong_ticker_fails():
    assert any("not ANET" in p for p in fve.validate_chart_capture(_why(symbol="MSFT.XNAS"), "ANET"))


def test_a_level_that_is_not_a_number_fails():
    assert any("not a number" in p for p in fve.validate_chart_capture(_why(support=("n/a",)), "ANET"))


def test_a_capture_without_a_rank_is_kept_with_a_note():
    """The vendor sends no rank for some names while the rest of the page is complete; the rank is a
    gap to note, never a reason to throw the bars, trends and levels away."""
    assert fve.validate_chart_capture(_why(rank=None), "ANET") == []
    assert fve.chart_capture_notes(_why(rank=None)) == ["no 1-10 technical rank"]
    assert fve.chart_capture_notes(_why()) == []


def test_edition_symbols_skip_the_breadth_table_but_keep_names_discussed_elsewhere():
    link = '<a href="x?symbol={s}">{s}</a>'
    page = (
        link.format(s="AMD")
        + "<th>Sector</th>"
        + link.format(s="AMD")
        + link.format(s="PEP")
        + "The three shades"
        + link.format(s="NVDA")
    )
    assert fve.edition_symbols(page) == ["AMD", "NVDA"]


def _fake_session(monkeypatch, *, logged_out: bool, forbid_after_login: bool = False):
    """Stand in for the browser: the first dashboard load 403s whenever the session has lapsed (what
    the vendor does to an unauthenticated /securities call), and optionally again after login."""
    hits: list[str] = []
    state = {"logged_in": not logged_out}

    def open_dashboard(_page, _url):
        if not state["logged_in"] or forbid_after_login:
            hits.append("403 https://vendor.example/api/v1/securities")

    def auto_login(_page):
        state["logged_in"] = True

    monkeypatch.setattr(fve, "_open_dashboard", open_dashboard)
    monkeypatch.setattr(fve, "_login_needed", lambda _page: not state["logged_in"])
    monkeypatch.setattr(fve, "_auto_login", auto_login)
    return hits


def test_a_403_from_the_logged_out_load_is_not_throttling(monkeypatch):
    """2026-09-30: an expired session's 403, followed by a successful auto-login, was read as the
    vendor throttling -- 24-hour cooldown, exit 1, and the next morning's run skipped as well."""
    hits = _fake_session(monkeypatch, logged_out=True)
    fve._open_authenticated(object(), "https://vendor.example", hits)
    assert hits == []


def test_a_403_after_logging_in_still_counts(monkeypatch):
    hits = _fake_session(monkeypatch, logged_out=True, forbid_after_login=True)
    fve._open_authenticated(object(), "https://vendor.example", hits)
    assert len(hits) == 1


def test_a_403_on_a_live_session_still_counts(monkeypatch):
    hits = _fake_session(monkeypatch, logged_out=False, forbid_after_login=True)
    fve._open_authenticated(object(), "https://vendor.example", hits)
    assert len(hits) == 1


def test_the_user_agent_is_regular_chrome_of_the_installed_version():
    ua = fve.chrome_user_agent("151.0.7922.34", "win32")
    assert "Headless" not in ua
    assert "Chrome/151.0.0.0 " in ua and "Windows NT 10.0; Win64; x64" in ua
