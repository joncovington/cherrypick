"""The hourly suite-status digest: rendering conventions, delta watermarking, and job derivation.

Two conventions here are load-bearing suite-wide and pinned hardest:
- null is never zero — an unmeasured figure renders as an em dash, because the hour an input is
  broken is exactly the hour a $0 would mislead;
- no suite-level net — the review package refuses to sum across modules on purpose, and this card
  must not quietly reinvent the total it refused.
"""

from __future__ import annotations

import json

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import jobspec, status_digest

DASH = "—"


def module_block(**overrides) -> dict:
    base = {
        "ok": True,
        "book": "paper",
        "health": {
            "loop_ticked": True,
            "iterations": 120,
            "entries": 4,
            "entry_attempts": {"filled": 3, "gate_blocked": 7},
        },
        "results": {"closed": 3, "gross": 500.0, "cost": 20.0, "net": 480.0, "wins": 2, "losses": 1},
        "concentration": {"sign_flips_without_largest": False},
        "carried_overnight": {"positions": 0, "capital_at_risk": None},
    }
    base.update(overrides)
    return base


def facts(**modules) -> dict:
    return {
        "session": "2026-09-01",
        "status": "provisional",
        "fact_version": 7,
        "modules": modules or {"meic": module_block()},
    }


def build(facts_doc=None, watchdog=None, morning=None, halted=False, prev=None):
    return status_digest.build_digest("2026-09-01", "13:00", facts_doc, watchdog, morning, halted, prev)


def build_close():
    return status_digest.build_digest("2026-09-01", "16:35", facts(), None, None, False, None, close=True)


def all_text(embed: dict, message: str) -> str:
    return message + " ".join(f["name"] + " " + f["value"] for f in embed["fields"])


# --------------------------------------------------------------------------- rendering conventions
def test_null_renders_as_em_dash_never_zero():
    """An unmeasured net/capital must be visibly unmeasured. Verified to fail: rendering None as 0
    puts '+$0' in the field and the assertion below catches both spellings."""
    doc = facts(
        meic=module_block(
            results={"closed": 2, "net": None, "wins": None, "losses": None},
            carried_overnight={"positions": 1, "capital_at_risk": None},
        )
    )
    doc["modules"]["meic"]["health"].pop("entries")  # an artifact predating fact set v7
    _, message, embed, _ = build(doc)
    field = next(f for f in embed["fields"] if f["name"] == "meic")
    assert f"net {DASH}" in field["value"]
    assert f"at risk {DASH}" in field["value"]
    assert f"entered {DASH}" in field["value"]
    assert "entered 0" not in field["value"]
    assert "$0" not in field["value"] and "$0" not in message


def test_no_suite_level_net_is_ever_printed():
    """Two modules whose nets sum to a round, recognizable figure — if any code path totals across
    modules, that figure appears somewhere in the output and this fails (verified by adding such a
    total on purpose during development)."""
    doc = facts(
        meic=module_block(results={"closed": 1, "net": 700.0, "wins": 1, "losses": 0}),
        flies=module_block(results={"closed": 1, "net": 300.0, "wins": 1, "losses": 0}),
    )
    _, message, embed, _ = build(doc)
    text = all_text(embed, message)
    assert "+$700" in text and "+$300" in text
    assert "1,000" not in text


def test_concentration_sign_flip_gets_a_caveat():
    doc = facts(
        meic=module_block(
            concentration={
                "sign_flips_without_largest": True,
                "largest": {"profile": "control"},
            }
        )
    )
    _, _, embed, _ = build(doc)
    field = next(f for f in embed["fields"] if f["name"] == "meic")
    assert "net sign rests on control" in field["value"]


def test_missing_fact_set_says_so_and_still_reports_health():
    wd = {"overall": "OK", "in_session": True, "findings": []}
    _, message, embed, snapshot = build(None, watchdog=wd)
    header = embed["fields"][0]["value"]
    assert "no fact set for 2026-09-01 yet" in header
    assert "watchdog OK" in header
    assert snapshot["modules"] == {}
    assert "watchdog OK" in message


def test_watchdog_problems_and_halt_flag_surface_in_the_header():
    wd = {
        "overall": "WARN",
        "in_session": True,
        "findings": [
            {"status": "OK", "title": "fine", "message": "x"},
            {"status": "WARN", "title": "Streamer", "message": "stale 12m"},
        ],
    }
    _, message, embed, _ = build(facts(), watchdog=wd, halted=True)
    header = embed["fields"][0]["value"]
    assert "WARN: Streamer — stale 12m" in header
    assert "LIVE HALT FLAG IS SET" in header
    assert "LIVE HALTED" in message
    assert "fine" not in header  # OK findings are noise at this altitude


def test_unreadable_module_is_reported_not_dropped():
    doc = facts(curve={"ok": False, "reason": "ledger locked"})
    _, message, embed, snapshot = build(doc)
    field = next(f for f in embed["fields"] if f["name"] == "curve")
    assert "unreadable: ledger locked" in field["value"]
    assert "curve unreadable" in message
    assert "curve" not in snapshot["modules"]  # a broken read must not become the next delta base


def test_card_color_tracks_the_worst_of_phase_and_watchdog():
    green = {"phase": {"phase": "green", "gates_met": 5, "gates_total": 5}}
    ok = {"overall": "OK", "in_session": True, "findings": []}
    assert build(facts(), ok, green)[2]["color"] == status_digest._COLOR_GREEN
    assert build(facts(), ok, {"phase": {"phase": "red"}})[2]["color"] == status_digest._COLOR_RED
    assert (
        build(facts(), {"overall": "CRITICAL", "findings": []}, green)[2]["color"] == status_digest._COLOR_RED
    )
    assert build(facts(), ok, {"phase": {"phase": "yellow"}})[2]["color"] == status_digest._COLOR_AMBER
    # A missing input can never look green — the morning pack's own missing-data rule.
    assert build(facts(), ok, None)[2]["color"] == status_digest._COLOR_SLATE


# --------------------------------------------------------------------------- deltas
def test_second_post_carries_a_delta_against_the_watermark():
    prev = {"session": "2026-09-01", "modules": {"meic": {"closed": 1, "net": 100.0, "entries": 2}}}
    doc = facts(meic=module_block(results={"closed": 3, "net": 480.0, "wins": 2, "losses": 1}))
    _, _, embed, snapshot = build(doc, prev=prev)
    field = next(f for f in embed["fields"] if f["name"] == "meic")
    assert "entered 4 (Δ +2)" in field["value"]
    assert "Δ +2, +$380" in field["value"]
    assert snapshot["modules"]["meic"] == {"closed": 3, "net": 480.0, "entries": 4, "completions": None}


def test_unchanged_module_shows_no_delta():
    prev = {"session": "2026-09-01", "modules": {"meic": {"closed": 3, "net": 480.0, "entries": 4}}}
    _, _, embed, _ = build(facts(), prev=prev)
    field = next(f for f in embed["fields"] if f["name"] == "meic")
    assert "Δ" not in field["value"]


def test_entries_lead_the_activity_line_even_with_no_closes():
    """'0 closed' undersells a module mid-day: entries are the day's activity number, and for the
    multi-day modules the opened and closed populations differ."""
    doc = facts(
        pmcc=module_block(
            results={"closed": 0, "gross": None, "cost": None, "net": None, "wins": 0, "losses": 0},
            health={"loop_ticked": True, "iterations": 50, "entries": 2},
        )
    )
    _, _, embed, _ = build(doc)
    field = next(f for f in embed["fields"] if f["name"] == "pmcc")
    assert field["value"].startswith("entered 2 · no closes yet")


def test_flies_completions_render_beside_entries_with_their_own_delta():
    """Flies' lifecycle split: entered is one leg of a two-stage structure, completed is the moment
    the floor becomes a guarantee — the card shows both, and only for a module that measures it."""
    prev = {"session": "2026-09-01", "modules": {"flies": {"entries": 3, "completions": 1}}}
    doc = facts(
        flies=module_block(health={"loop_ticked": True, "iterations": 900, "entries": 5, "completions": 4}),
        meic=module_block(),
    )
    _, _, embed, _ = build(doc, prev=prev)
    flies_field = next(f for f in embed["fields"] if f["name"] == "flies")
    assert "entered 5 (Δ +2)" in flies_field["value"]
    assert "completed 4 (Δ +3)" in flies_field["value"]
    # A module that doesn't measure completions never shows the word.
    meic_field = next(f for f in embed["fields"] if f["name"] == "meic")
    assert "completed" not in meic_field["value"]


# --------------------------------------------------------------------------- artifact/session guards
def test_a_stale_artifact_is_refused(tmp_path):
    """Yesterday's fact set presented as today's is worse than none."""
    p = tmp_path / "eod-2026-08-31.json"
    p.write_text(json.dumps({"session": "2026-08-31", "modules": {}}), encoding="utf-8")
    assert status_digest._load_session_artifact(p, "2026-09-01") is None
    assert status_digest._load_session_artifact(p, "2026-08-31") is not None


def test_run_discards_a_prior_days_watermark(isolated_state, tmp_path, monkeypatch):
    """A watermark from a previous session must not produce a delta against today. Exercised through
    run() so the state read/write path is the one under test. Data reads are pointed at an empty tmp
    home so the unit lane never reads the developer's real artifacts."""
    monkeypatch.setattr(status_digest.corehome, "data_dir", lambda pkg=None, **kw: tmp_path / (pkg or "data"))
    (isolated_state / "status_digest.json").write_text(
        json.dumps({"session": "2026-08-31", "modules": {"meic": {"closed": 99, "net": 9.0}}}),
        encoding="utf-8",
    )
    sent = {}

    class Spy:
        def __init__(self, _cfg):
            pass

        def notify(self, level, key, title, message, embed=None):
            sent.update({"message": message, "embed": embed})
            return {"log": {"ok": True}}

    monkeypatch.setattr(status_digest, "Notifier", Spy)
    monkeypatch.setattr(status_digest, "_refresh_facts", lambda: None)
    res = status_digest.run(cfg={"notify": {}}, force=True)
    assert res["ok"]
    assert "Δ" not in json.dumps(sent["embed"])
    # The watermark now names today's session, so the NEXT run deltas correctly.
    saved = json.loads((isolated_state / "status_digest.json").read_text(encoding="utf-8"))
    assert saved["session"] == res["session"]


# --------------------------------------------------------------------------- job derivation
def _derive(cfg):
    from cherrypick.orchestrator import timeutil

    return jobspec.derive_jobs(cfg, pythonw="pythonw", launcher="run.py", now=timeutil.now_et())


def test_status_digest_job_is_off_by_default_with_a_reason():
    jobs, _errors = _derive({})
    job = next(j for j in jobs if j.id == "status-digest")
    assert not job.enabled
    assert "status_digest" in job.enabled_reason


def test_status_digest_job_derives_windowed_hourly_on_trading_days():
    cfg = {"status_digest": {"enabled": True}}
    job = next(j for j in _derive(cfg)[0] if j.id == "status-digest")
    assert job.enabled
    assert job.kind == jobspec.KIND_INTERVAL and job.interval_seconds == 3600
    assert (job.window_start, job.window_end) == ("10:00", "16:10")
    assert job.trading_days_only
    assert job.argv == ("pythonw", "run.py", "notify-status")


def test_the_close_card_is_its_own_daily_job_after_settlement():
    """One CLOSE card per session, after the 0DTE books settle (~16:15) and the official 16:30
    review-provisional build — the day's final intraday word."""
    cfg = {"status_digest": {"enabled": True}}
    job = next(j for j in _derive(cfg)[0] if j.id == "status-digest-close")
    assert job.enabled
    assert job.kind == jobspec.KIND_DAILY and job.at_et == "16:35"
    assert job.trading_days_only
    assert job.argv == ("pythonw", "run.py", "notify-status", "--close")

    off = next(j for j in _derive({})[0] if j.id == "status-digest-close")
    assert not off.enabled and "status_digest" in off.enabled_reason


def test_the_close_card_says_close():
    _, message, embed, _ = build_close()
    assert embed["title"].startswith("CLOSE · SUITE")
    assert message.startswith("Suite close")


def test_settings_reader_defaults():
    sd = cfgmod.status_digest_settings({})
    assert sd == {
        "enabled": False,
        "interval_minutes": 60,
        "start": "10:00",
        "end": "16:10",
        "close_at": "16:35",
        "channels": ["log", "discord"],
    }
