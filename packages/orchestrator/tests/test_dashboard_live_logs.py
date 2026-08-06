"""The log card carries a paper tail and a separate live tail, and the footer says which.

Paper and live are deliberately two blocks rather than one merged feed: interleaved, a live fill and
a paper fill end up one indistinguishable scroll apart, told apart only by squinting at a module
name. That is the confusion the suite's paper/live separation exists to prevent, so the separation is
pinned here rather than left to whoever edits the renderer next.

Reading a live *log* is read-only and touches no broker — the same footing as `report --live`. What
must not drift is the labelling: a live tail on the page while the footer still claims "paper".
"""

from __future__ import annotations

import pytest

from cherrypick.orchestrator import dashboard

pytestmark = pytest.mark.unit


def _entry(source: str, text: str, level: str = "INFO") -> dict:
    return {"ts": "2026-07-31T14:20:01", "level": level, "source": source, "text": text}


PAPER = [_entry("flies", "paper: settled at 748.97")]
LIVE = [_entry("flies", "live: order filled")]


def _render(model_extra: dict) -> str:
    model = {"generated_at": "2026-08-02T20:00:00+00:00", "suite": {}, "modules": [], "logs": PAPER}
    model.update(model_extra)
    return dashboard._render_html(model, serve=False)


def test_live_lines_render_in_their_own_tail_not_merged_into_paper():
    html = _render({"live_logs": LIVE, "live_logs_configured": True})
    assert "live logs" in html
    assert html.index("recent logs") < html.index("live logs"), "paper tail comes first"
    # Two independent tails, each with its own filter bar bound to its own box.
    assert html.count('class="logbar"') == 2
    assert 'class="logs logs-live"' in html


def test_footer_admits_when_live_content_is_on_the_page():
    """The mode tag is a safety label; it must describe what is actually rendered."""
    assert "read-only · paper + live logs ·" in _render({"live_logs": LIVE, "live_logs_configured": True})


def test_footer_stays_paper_only_when_no_live_lines_are_shown():
    assert "read-only · paper ·" in _render({"live_logs": [], "live_logs_configured": False})


def test_a_configured_but_silent_live_loop_says_so_rather_than_showing_nothing():
    """Absent output next to a LIVE badge would read as "nothing is happening live", which this card
    must never imply by accident — flies arms per-day, so silence is the normal state."""
    html = _render({"live_logs": [], "live_logs_configured": True})
    assert "live logs" in html
    assert "no live log lines yet" in html


def test_no_live_section_at_all_when_no_module_declares_one():
    """A suite with no live loop should not grow an empty LIVE-badged panel implying it has one."""
    html = _render({"live_logs": [], "live_logs_configured": False})
    assert "live logs" not in html
    assert "badge-live" not in html.split("_CSS")[-1] or 'class="badge-live"' not in html


def test_the_two_tails_filter_independently():
    """`flt` used to query the whole document, so hiding WARN in one tail also hid it in the other
    while that tail's buttons still read as ON. It now scopes to its own adjacent .logs box."""
    html = _render({"live_logs": LIVE, "live_logs_configured": True})
    assert "document.querySelectorAll('.logline" not in html, "filter must not be document-wide"
    assert "scope.querySelectorAll('.logline" in html
