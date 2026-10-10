"""The QuikOptions Discord series (`scripts/quikoptions_post.py`), shown to fail where it must.

What it guards: a header that carries its date and capture time whatever the title, mentions switched
off on every message, the webhook the config chose and never the other as a fallback, a series that
resumes rather than repeats and finishes under the title and cards it started with, a capture
failure that posts nothing, and a 429 waited out once. No network, no browser: the capture and the
post are replaced, the marker lives in a temporary directory.
"""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "quikoptions_post.py"


def _module():
    spec = importlib.util.spec_from_file_location("quikoptions_post", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


qp = _module()
SESSION = "2026-10-02"

CAPTURE = {
    "session": SESSION,
    "saved_at": "2026-10-02T20:52:00+00:00",
    "problems": [],
    "tables": {
        "birdseye": [{"symbol": "TSLA", "shown": {"total": "762.5K"}}],
        "outrights": [{"symbol": "VST", "premium": 92_952_000, "side": {"sentiment": "Neutral"}}],
    },
    "derived": {
        "names": [{"symbol": "SPCX"}, {"symbol": "TSLA"}],
        "premium_by_side": {"Bullish": 3_512_505, "Bearish": 3_376_755, "Neutral": 96_423_450},
        "trades_by_side": {"Bullish": 5, "Bearish": 11, "Neutral": 4},
        "largest_trade": {"table": "outrights", "symbol": "VST", "premium": 92_952_000},
    },
}


def _cfg(**q):
    # The image path is what most of these exercise, so `cards` unless a test says otherwise; the
    # default style (text) has its own test.
    return {"quikoptions": {"enabled": True, "post": True, "post_style": "cards", **q}}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """A capture on disk, a marker in tmp, captures that write a file, and a post that records."""
    cap = tmp_path / f"{SESSION}.json"
    cap.write_text(json.dumps(CAPTURE), encoding="utf-8")
    monkeypatch.setattr(qp, "capture_path", lambda session: tmp_path / f"{session}.json")
    monkeypatch.setattr(qp, "_marker_path", lambda: tmp_path / "state" / "quikoptions-post.json")
    monkeypatch.setattr(qp, "_log", lambda line: None)
    monkeypatch.setattr(qp.time, "sleep", lambda s: None)
    captured: list[str] = []

    def capture_card(session, name, out):
        captured.append(name)
        out.write_bytes(b"png")
        return None

    monkeypatch.setattr(qp, "capture_card", capture_card)
    state = {
        "posted": [],
        "fail_at": None,
        "urls": {"discord": "https://d/notify", "discord_reporting": "https://d/own"},
    }

    def post(url, payload, files, **record):
        if state["fail_at"] is not None and len(state["posted"]) == state["fail_at"]:
            return "discord HTTP 500"
        state["posted"].append({"url": url, "payload": payload, "files": [f.name for f in files]})
        return None

    monkeypatch.setattr(qp, "post", post)
    from cherrypick.notify import secrets

    monkeypatch.setattr(secrets, "read_entry", lambda name: state["urls"].get(name))
    state["captured"] = captured
    return state


def _run(**kw):
    kw.setdefault("cfg", _cfg())
    return qp.run(SESSION, dry_run=kw.pop("dry_run", False), force=kw.pop("force", False), keep=None, **kw)


# ------------------------------------------------------------------------------------------------


def test_the_header_carries_date_and_capture_time_whatever_the_title_and_no_source():
    embed = qp.header_embed(CAPTURE, "@everyone flow")
    assert embed["title"] == "@everyone flow — Fri 2 Oct 2026"
    assert embed["footer"]["text"] == "Captured 16:52 ET."
    assert "QuikOptions" not in json.dumps(embed)  # no source attribution (2026-10-03)
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields["Most traded"] == "TSLA · 762.5K trades"
    assert fields["Largest trade"] == "VST $92.95M outright (neutral)"
    assert fields["Bullish / bearish"] == "$3.51M (5) / $3.38M (11)"
    assert fields["In two or more tables"] == "SPCX · TSLA"
    assert len(embed["fields"]) == 4  # a short header: the day does not need every figure


CALENDAR = {
    "captured": SESSION,
    "events": [
        {
            "date": SESSION,
            "time_et": "08:30",
            "impact": "H",
            "event": "Non Farm Payrolls (Sep)",
            "actual": "29K",
            "estimate": "90K",
        },
        {
            "date": SESSION,
            "time_et": "10:00",
            "impact": "M",
            "event": "Factory Orders MoM (Aug)",
            "actual": "0.1%",
            "estimate": "0.1%",
        },
        {
            "date": "2026-10-07",
            "time_et": "14:00",
            "impact": "H",
            "event": "FOMC Minutes",
            "actual": None,
            "estimate": None,
        },
        {
            "date": "2026-10-09",
            "time_et": "10:00",
            "impact": "H",
            "event": "Michigan Consumer Sentiment (Oct)",
            "actual": None,
        },
        {"date": "2026-10-20", "time_et": "08:30", "impact": "H", "event": "Too far ahead", "actual": None},
    ],
}
SECTIONS = ["Largest by contracts", "Top sweeps", "Events"]


def test_each_style_plans_its_own_messages():
    cards = qp.plan_messages(CAPTURE, "Hot options", "cards", ["Trades", "Top sweeps", "Vol / OI"], None)
    assert [m["cards"] for m in cards] == [["Trades", "Top sweeps"], ["Vol / OI"]]
    singles = qp.plan_messages(CAPTURE, "Hot options", "singles", ["Trades", "Top sweeps"], None)
    assert [m["cards"] for m in singles] == [["Trades"], ["Top sweeps"]]
    # No header message: the title line rides on the first picture, above its own title.
    assert singles[0]["payload"]["content"] == "## Hot options — Fri 2 Oct 2026\n\n**Trades**"
    assert singles[1]["payload"]["content"] == "**Top sweeps**"
    embed = qp.plan_messages(CAPTURE, "Hot options", "embed", SECTIONS, CALENDAR)
    assert len(embed) == 1 and embed[0]["cards"] == []
    names = [f["name"] for f in embed[0]["payload"]["embeds"][0]["fields"]]
    assert names[-3:] == SECTIONS
    text = qp.plan_messages(CAPTURE, "Hot options", "text", SECTIONS, CALENDAR)
    body = "\n".join(m["payload"]["content"] for m in text)
    assert body.startswith("**Hot options — Fri 2 Oct 2026**")
    assert "Captured 16:52 ET." in body and "QuikOptions" not in body  # no source attribution
    assert all(len(m["payload"]["content"]) <= 2000 for m in text)


def test_calendar_shows_the_days_releases_and_the_next_high_impact_ones():
    out = qp.calendar_text(SESSION, CALENDAR)
    assert "08:30  Non Farm Payrolls (Sep): 29K vs 90K est" in out
    assert "Factory Orders" not in out  # medium impact
    assert "Wed  7 14:00  FOMC Minutes" in out and "Fri  9 10:00  Michigan Consumer Sentiment (Oct)" in out
    assert "Too far ahead" not in out
    assert qp.calendar_text(SESSION, None) == "(no calendar captured with this session)"
    # In the image styles Events is a capture of the post page's table — and only when a calendar
    # was captured with the session, so a missing calendar costs that picture, not the series.
    planned = qp.plan_messages(CAPTURE, "Hot options", "singles", SECTIONS, CALENDAR)
    assert [m["cards"] for m in planned] == [["Largest by contracts"], ["Top sweeps"], ["Events"]]
    planned = qp.plan_messages(CAPTURE, "Hot options", "singles", SECTIONS, None)
    assert [m["cards"] for m in planned] == [["Largest by contracts"], ["Top sweeps"]]


def test_every_message_pings_no_one():
    for style in ("cards", "singles", "embed", "text"):
        for m in qp.plan_messages(CAPTURE, "@everyone", style, SECTIONS, CALENDAR):
            assert qp.with_files(m["payload"], [])["allowed_mentions"] == {"parse": []}
    sent = qp.with_files({"content": ""}, [Path("a.png"), Path("b.png")])
    assert sent["attachments"] == [{"id": 0, "filename": "a.png"}, {"id": 1, "filename": "b.png"}]


def test_text_longer_than_a_message_is_split_at_a_section():
    many = {
        **CAPTURE,
        "derived": {
            **CAPTURE["derived"],
            "names": [{"symbol": f"N{i}", "tables": ["birdseye"] * 6} for i in range(40)],
        },
    }
    text = qp.plan_messages(
        many, "Hot options", "text", ["Names across tables", "Names across tables", "Events"], CALENDAR
    )
    assert len(text) > 1 and all(len(m["payload"]["content"]) <= 2000 for m in text)


def test_the_default_is_one_titled_capture_a_message(harness):
    """Screen captures, one a message, so each reads full width on a phone (2026-10-03): Derived
    flow, Trades and the Events table."""
    qp_load = qp.load_calendar
    qp.load_calendar = lambda session: CALENDAR
    try:
        assert _run(cfg={"quikoptions": {"enabled": True, "post": True}}) == "posted"
    finally:
        qp.load_calendar = qp_load
    assert harness["captured"] == ["Derived flow", "Trades", "Top spreads", "Events"]
    assert [len(p["files"]) for p in harness["posted"]] == [1, 1, 1, 1]
    assert [p["payload"].get("content") for p in harness["posted"]] == [
        "## Hot options — Fri 2 Oct 2026\n\n**Derived flow**",
        "**Trades**",
        "**Top spreads**",
        "**Events**",
    ]
    assert _run(cfg={"quikoptions": {"enabled": True, "post": True}}) == "skipped"  # once per session


def test_cards_style_pairs_the_captures(harness):
    assert _run(cfg=_cfg(post_cards=["Derived flow", "Largest by contracts", "Top sweeps"])) == "posted"
    assert [len(p["files"]) for p in harness["posted"]] == [2, 1]
    assert all(p["url"] == "https://d/own" for p in harness["posted"])  # the reporting channel by default


def test_the_header_leads_with_the_derived_flow_and_yesterdays_calls():
    flow = {
        "flows": [{"symbol": "PCG", "what": "15 Jan 27 16C", "direction": "bought", "score": 46.0}],
        "unread": [{"symbol": "VST", "what": "17 Dec 27 195P", "delta_dollars": 1.16e8}],
        "names": [{"symbol": "PCG", "net": 1.1e7}, {"symbol": "SMCI", "net": -1.33e7}],
    }
    prev = {
        "flows": [{"symbol": "MU", "score": -39.0}, {"symbol": "F", "score": 45.0, "confirmed_score": 32.0}],
        "outcomes": {"1d": {"returns": {"MU": -0.012, "F": -0.004}}},
    }
    embed = qp.header_embed({**CAPTURE, "_flow": flow}, "Hot options", prev=prev)
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields["Bullish"] == "PCG +$11.00M" and fields["Bearish"] == "SMCI −$13.30M"
    assert fields["Top flow"] == "+46 PCG 15 Jan 27 16C bought"
    assert fields["Unread, largest"] == "VST 17 Dec 27 195P ($116.00M)"
    assert fields["Yesterday's calls, a day on"] == "MU -39 -1.2% ✓ · F +32 -0.4% ✗"
    assert qp.yesterday_line({"flows": [], "outcomes": {}}) is None  # no outcome yet: no line


def test_the_morning_post_waits_for_the_confirmation_and_the_week_is_descriptive():
    flow = {
        "session": SESSION,
        "flows": [{"symbol": "PCG", "what": "15 Jan 27 16C", "score": 46.0}],
        "unread": [],
    }
    assert qp.morning_text(flow, "Hot options") is None  # not confirmed yet: nothing to say
    flow["confirmed_at"] = "2026-10-05T12:31:00+00:00"
    flow["flows"][0].update(confirmed="opened", confirmed_score=76.0)
    text = qp.morning_text(flow, "Hot options")
    assert text.startswith("**Hot options, confirmed — Fri 2 Oct**")
    assert "Opened 1 · closed 0 · mixed 0 · not checked 0" in text and "+46 → +76" in text
    week = qp.weekly_text(
        [
            {
                **flow,
                "checks": {
                    "site_vote": {"agrees": 16, "neutral": 0, "opposite": 0},
                    "delta": {"broker": 20, "singles": 20, "off": ["x"]},
                },
            }
        ],
        {"checked": 4, "agree": 3},
        {"sessions": 1, "needed": 40},
        "Hot options",
    )
    assert "16 of 16 agree" in week and "model off by >0.10 on 1" in week and "3 of 4 agree" in week
    assert "1 of 40 sessions" in week and week.endswith("the fixed 40-session test.")
    assert qp.weekly_text([], None, None, "Hot options") is None


def test_the_derived_section_shows_top_flows_and_net_names():
    flow = {
        "flows": [
            {
                "symbol": "PCG",
                "what": "15 Jan 27 16C",
                "direction": "bought",
                "view": "bullish",
                "delta_dollars": 11e6,
                "score": 46.0,
            },
            {
                "symbol": "SMCI",
                "what": "261009 43.5/45.5 CS",
                "direction": "sold",
                "view": "bearish",
                "delta_dollars": 9.6e6,
                "score": -24.0,
                "confirmed_score": -40.0,
            },
        ],
        "names": [
            {"symbol": "PCG", "net": 11e6},
            {"symbol": "SMCI", "net": -13.3e6},
            {"symbol": "AI", "net": 0},
        ],
    }
    text = qp.derived_text(flow)
    assert "+46  PCG" in text and "-40  SMCI" in text  # the confirmed score once there is one
    assert text.splitlines()[-1] == "net Δ$: PCG +$11.00M · SMCI −$13.30M"
    assert qp.derived_text(None) == "(not scored yet)"


def test_the_webhook_is_the_one_chosen_and_never_the_other(harness):
    assert _run(cfg=_cfg(post_webhook="notify")) == "posted"
    assert {p["url"] for p in harness["posted"]} == {"https://d/notify"}
    # The reporting one is chosen but not stored: nothing is posted, the notify one is not used.
    harness["posted"].clear()
    harness["urls"].pop("discord_reporting")
    assert _run(force=True) == "failed"
    assert harness["posted"] == []


def test_a_run_may_override_the_webhook_for_a_test_post(harness):
    assert _run(cfg=_cfg(post_webhook="reporting"), webhook="notify") == "posted"
    assert {p["url"] for p in harness["posted"]} == {"https://d/notify"}


def test_a_forced_test_post_in_another_style_starts_the_day_over(harness):
    assert _run() == "posted"
    assert _run(force=True, overrides={"post_style": "text", "post_cards": ["Events"]}) == "posted"
    assert harness["posted"][-1]["payload"]["content"].startswith("**Hot options")
    assert harness["posted"][-1]["files"] == []
    marker = qp.markers()[SESSION]
    assert (marker["style"], marker["cards"], marker["sent"]) == ("text", ["Events"], [0])
    # An override is checked by the same rules as the config.
    assert _run(force=True, overrides={"post_cards": ["Top trades"]}) == "failed"


def test_a_failure_resumes_under_the_title_and_cards_it_started_with(harness):
    harness["fail_at"] = 1  # the first picture lands, the second does not
    first = _cfg(post_title="Flow", post_style="singles", post_cards=["Trades", "Top spreads"])
    assert _run(cfg=first) == "failed"
    assert len(harness["posted"]) == 1
    harness["fail_at"] = None
    # The config changed meanwhile; the day's series finishes as it started.
    assert _run(cfg=_cfg(post_title="Something else", post_cards=["Top sweeps"])) == "posted"
    assert len(harness["posted"]) == 2
    assert harness["captured"][-2:] == ["Trades", "Top spreads"]


def test_bad_settings_and_a_failed_capture_post_nothing(harness, monkeypatch):
    assert _run(cfg=_cfg(post_title="x" * 81)) == "failed"
    assert _run(cfg=_cfg(post_cards=["Top trades"])) == "failed"
    assert _run(cfg=_cfg(post_webhook="slack")) == "failed"
    assert _run(cfg=_cfg(post_style="carousel")) == "failed"
    assert _run(cfg={"quikoptions": {"enabled": True, "post": False, "post_style": "cards"}}) == "skipped"
    monkeypatch.setattr(qp, "capture_card", lambda session, name, out: "ui-check exit 1: no card")
    assert _run() == "failed"
    assert harness["posted"] == []  # never a header without its pictures


def test_a_rejected_or_missing_capture_is_never_posted(harness, tmp_path):
    (tmp_path / f"{SESSION}.json").write_text(json.dumps({**CAPTURE, "problems": ["x"]}), encoding="utf-8")
    assert _run() == "skipped"
    (tmp_path / f"{SESSION}.json").unlink()
    assert _run() == "skipped"
    assert harness["posted"] == []


def test_a_429_is_waited_out_once_then_retried(monkeypatch, tmp_path):
    monkeypatch.setattr(qp.time, "sleep", lambda s: None)
    calls = []

    class _Resp:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        calls.append(req)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                req.full_url, 429, "slow down", {}, io.BytesIO(b'{"retry_after": 1.5}')
            )
        return _Resp()

    assert qp.post("https://d/x", {"content": ""}, [], opener=opener) is None
    assert len(calls) == 2

    def always_429(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "slow down", {}, io.BytesIO(b"{}"))

    assert qp.post("https://d/x", {"content": ""}, [], opener=always_429) == "discord HTTP 429"


def test_the_weekly_scorecard_posts_on_the_weeks_last_trading_day_only(harness, tmp_path, monkeypatch):
    from datetime import date, datetime

    flow = {
        "session": SESSION,
        "flows": [{"symbol": "PCG", "what": "15 Jan 27 16C", "score": 46.0}],
        "unread": [],
        "checks": {"site_vote": {"agrees": 1, "neutral": 0, "opposite": 0}},
    }
    (tmp_path / f"{SESSION}.flow.json").write_text(json.dumps(flow), encoding="utf-8")
    from cherrypick.core import home

    monkeypatch.setattr(home, "data_dir", lambda name: tmp_path)  # no audit or review yet

    assert qp.last_session_of_week(date(2026, 10, 2))  # a Friday
    assert not qp.last_session_of_week(date(2026, 10, 1))
    assert qp.last_session_of_week(date(2027, 3, 25))  # the Thursday before Good Friday
    monkeypatch.setattr(qp, "_now_et", lambda: datetime(2026, 9, 30, 17, 15, tzinfo=qp.ET))
    assert qp.run_weekly(None, dry_run=False, force=False, cfg=_cfg(), webhook=None) == "skipped"
    assert harness["posted"] == []
    # A named session posts any day: the week has a scorecard to hold back.
    assert qp.run_weekly(SESSION, dry_run=False, force=False, cfg=_cfg(), webhook=None) == "posted"


def test_the_post_waits_for_the_days_scoring_and_the_morning_for_its_confirmation(
    harness, tmp_path, monkeypatch
):
    clock = {"t": 0.0}
    monkeypatch.setattr(qp.time, "monotonic", lambda: clock["t"])
    flow = tmp_path / f"{SESSION}.flow.json"

    def sleep(s):
        clock["t"] += s
        if clock["t"] >= 90:  # the score lands a minute and a half in
            flow.write_text(json.dumps({"session": SESSION, "flows": [], "unread": []}), encoding="utf-8")

    monkeypatch.setattr(qp.time, "sleep", sleep)
    assert qp.wait_for_inputs("daily", SESSION, 45) is True and clock["t"] == 90
    # Scored but not yet confirmed: the morning post waits to its bound, then goes ahead (and skips).
    clock["t"] = 0.0
    assert qp.wait_for_inputs("morning", None, 2) is False
    flow.write_text(
        json.dumps({"session": SESSION, "confirmed_at": "x", "flows": [], "unread": []}), encoding="utf-8"
    )
    assert qp.wait_for_inputs("morning", None, 2) is True


def test_the_weekly_scorecard_goes_to_the_notify_channel_only(harness, tmp_path, monkeypatch):
    """The scorecard is the suite's own measurement, never part of the options report's channel."""
    from cherrypick.core import home

    flow = {
        "session": SESSION,
        "flows": [{"symbol": "PCG", "what": "15 Jan 27 16C", "score": 46.0}],
        "unread": [],
        "checks": {"site_vote": {"agrees": 1, "neutral": 0, "opposite": 0}},
    }
    (tmp_path / f"{SESSION}.flow.json").write_text(json.dumps(flow), encoding="utf-8")
    monkeypatch.setattr(home, "data_dir", lambda name: tmp_path)
    cfg = _cfg(post_webhook="reporting")
    assert qp.run_weekly(SESSION, dry_run=False, force=False, cfg=cfg, webhook="reporting") == "failed"
    assert harness["posted"] == []
    assert qp.run_weekly(SESSION, dry_run=False, force=False, cfg=cfg, webhook=None) == "posted"
    assert [p["url"] for p in harness["posted"]] == ["https://d/notify"]


def test_the_webhook_choices_earlier_name_still_resolves(harness):
    """`dedicated` was the reporting channel's first name; a config that kept it posts there."""
    from cherrypick.orchestrator import config as c

    assert c.quikoptions_post_problem({"post_webhook": "dedicated"}) is None
    assert (
        c.quikoptions_settings({"quikoptions": {"post_webhook": "dedicated"}})["post_webhook"] == "reporting"
    )
    assert c.quikoptions_settings({})["post_webhook"] == "reporting"
    assert _run(cfg=_cfg(post_webhook="dedicated")) == "posted"
    assert {p["url"] for p in harness["posted"]} == {"https://d/own"}


def test_an_unverified_spread_premium_is_labelled_in_the_post():
    row = {
        "symbol": "VALE",
        "spread": "270115 10/17 RR",
        "size": 10_000,
        "direction": None,
        "premium": 10_000,
    }
    assert qp._spread_cells({**row, "premium_unverified": True})[-1].endswith(" unverified")
    assert not qp._spread_cells(row)[-1].endswith(" unverified")


def test_a_panel_the_site_did_not_draw_says_why_rather_than_none():
    doc = {"tables": {}, "unavailable": {"voloi": "the site drew the Openings list in this panel"}}
    assert (
        qp.section_text("Vol / OI", doc, None) == "(not shown: the site drew the Openings list in this panel)"
    )
    assert qp.section_text("Vol / OI", {"tables": {}}, None) == "(none)"
