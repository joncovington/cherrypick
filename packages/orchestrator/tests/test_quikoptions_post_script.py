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
    return {"quikoptions": {"enabled": True, "post": True, **q}}


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
        "urls": {"discord": "https://d/notify", "discord_quikoptions": "https://d/own"},
    }

    def post(url, payload, files):
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
    assert embed["title"] == "@everyone flow — Fri 2 Oct 2026 (stocks)"
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
    cards = qp.plan_messages(CAPTURE, "Hot options", "cards", ["Birdseye", "Top sweeps", "Vol / OI"], None)
    assert [m["cards"] for m in cards] == [[], ["Birdseye", "Top sweeps"], ["Vol / OI"]]
    singles = qp.plan_messages(CAPTURE, "Hot options", "singles", ["Birdseye", "Top sweeps"], None)
    assert [m["cards"] for m in singles] == [[], ["Birdseye"], ["Top sweeps"]]
    embed = qp.plan_messages(CAPTURE, "Hot options", "embed", SECTIONS, CALENDAR)
    assert len(embed) == 1 and embed[0]["cards"] == []
    names = [f["name"] for f in embed[0]["payload"]["embeds"][0]["fields"]]
    assert names[-3:] == SECTIONS
    text = qp.plan_messages(CAPTURE, "Hot options", "text", SECTIONS, CALENDAR)
    body = "\n".join(m["payload"]["content"] for m in text)
    assert body.startswith("**Hot options — Fri 2 Oct 2026 (stocks)**")
    assert "Captured 16:52 ET." in body and "QuikOptions" not in body  # no source attribution
    assert all(len(m["payload"]["content"]) <= 2000 for m in text)


def test_calendar_shows_the_days_releases_and_the_next_high_impact_ones():
    out = qp.calendar_text(SESSION, CALENDAR)
    assert "08:30  Non Farm Payrolls (Sep): 29K vs 90K est" in out
    assert "Factory Orders" not in out  # medium impact
    assert "Wed  7 14:00  FOMC Minutes" in out and "Fri  9 10:00  Michigan Consumer Sentiment (Oct)" in out
    assert "Too far ahead" not in out
    assert qp.calendar_text(SESSION, None) == "(no calendar captured with this session)"
    # In the image styles Events has no card: it is a header field instead.
    header = qp.plan_messages(CAPTURE, "Hot options", "cards", SECTIONS, CALENDAR)
    assert header[0]["payload"]["embeds"][0]["fields"][-1]["name"] == "Events"
    assert [m["cards"] for m in header[1:]] == [["Largest by contracts", "Top sweeps"]]


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


def test_default_series_is_the_header_and_one_pair(harness):
    assert _run() == "posted"
    assert harness["captured"] == ["Largest by contracts", "Top sweeps"]
    assert [len(p["files"]) for p in harness["posted"]] == [0, 2]
    assert all(p["url"] == "https://d/own" for p in harness["posted"])  # dedicated by default
    assert _run() == "skipped"  # once per session
    assert len(harness["posted"]) == 2


def test_the_webhook_is_the_one_chosen_and_never_the_other(harness):
    assert _run(cfg=_cfg(post_webhook="notify")) == "posted"
    assert {p["url"] for p in harness["posted"]} == {"https://d/notify"}
    # The dedicated one is chosen but not stored: nothing is posted, the notify one is not used.
    harness["posted"].clear()
    harness["urls"].pop("discord_quikoptions")
    assert _run(force=True) == "failed"
    assert harness["posted"] == []


def test_a_run_may_override_the_webhook_for_a_test_post(harness):
    assert _run(cfg=_cfg(post_webhook="dedicated"), webhook="notify") == "posted"
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
    harness["fail_at"] = 1  # the header lands, the pictures do not
    assert _run(cfg=_cfg(post_title="Flow", post_cards=["Birdseye", "Top spreads"])) == "failed"
    assert len(harness["posted"]) == 1
    harness["fail_at"] = None
    # The config changed meanwhile; the day's series finishes as it started.
    assert _run(cfg=_cfg(post_title="Something else", post_cards=["Top sweeps"])) == "posted"
    assert len(harness["posted"]) == 2
    assert harness["captured"][-2:] == ["Birdseye", "Top spreads"]


def test_bad_settings_and_a_failed_capture_post_nothing(harness, monkeypatch):
    assert _run(cfg=_cfg(post_title="x" * 81)) == "failed"
    assert _run(cfg=_cfg(post_cards=["Top trades"])) == "failed"
    assert _run(cfg=_cfg(post_webhook="slack")) == "failed"
    assert _run(cfg=_cfg(post_style="carousel")) == "failed"
    assert _run(cfg={"quikoptions": {"enabled": True, "post": False}}) == "skipped"
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
