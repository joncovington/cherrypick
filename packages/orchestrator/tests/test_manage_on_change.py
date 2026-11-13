"""`manage.on_change`: the evaluation follows the account. A fill is coalesced and never starts a run
within a minimum gap of the last snapshot; a change no order announces (an assignment, an exercise, a
transfer) starts one through the positions diff; a run posts only when the trades needing action
changed; and every run records why it happened."""

from __future__ import annotations

import json
import types
from datetime import datetime, timedelta, timezone

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import managepost, managerefresh

ET = timezone(timedelta(hours=-5))
NOW = datetime(2026, 11, 5, 11, 0, 0, tzinfo=ET)
TODAY = "2026-11-05"


def _ago(seconds):
    return NOW - timedelta(seconds=seconds)


# ------------------------------------------------------------------ settings
def test_the_settings_default_on_with_manage_and_read_the_config():
    assert cfgmod.manage_settings({"manage": {"enabled": True}})["on_change"] == {
        "enabled": True,
        "settle_seconds": 60,
        "min_gap_seconds": 120,
        "positions_check_seconds": 300,
    }
    assert not cfgmod.manage_settings({})["on_change"]["enabled"]  # manage off: nothing runs
    cfg = {"manage": {"enabled": True, "on_change": {"enabled": False, "settle_seconds": 10}}}
    got = cfgmod.manage_settings(cfg)["on_change"]
    assert got["enabled"] is False and got["settle_seconds"] == 10
    assert (
        cfgmod.manage_settings({"manage": {"enabled": True, "on_change": {"positions_check_seconds": 5}}})[
            "on_change"
        ]["positions_check_seconds"]
        == 60
    )  # never faster than a minute


# ------------------------------------------------------------------ fills: coalescing and the gap
def test_a_burst_of_fills_gives_one_run():
    snap = _ago(600)
    kw = {"settle_seconds": 60, "min_gap_seconds": 120}
    # A roll's legs fill ten seconds apart: each new fill restarts the wait.
    assert managerefresh.why_full(NOW, TODAY, TODAY, snap, _ago(50), **kw) is None
    assert managerefresh.why_full(NOW, TODAY, TODAY, snap, _ago(10), **kw) is None  # the last leg
    assert managerefresh.why_full(NOW, TODAY, TODAY, snap, _ago(61), **kw).startswith("a fill at")
    # The run's snapshot starts after the last fill, so the same burst asks for nothing more.
    assert managerefresh.why_full(NOW, TODAY, TODAY, _ago(30), _ago(61), **kw) is None


def test_no_run_starts_within_the_minimum_gap_but_the_fill_is_kept_for_after_it():
    kw = {"settle_seconds": 60, "min_gap_seconds": 120}
    fill = _ago(90)
    assert managerefresh.why_full(NOW, TODAY, TODAY, _ago(100), fill - timedelta(seconds=1), **kw) is None
    assert managerefresh.why_full(NOW, TODAY, TODAY, _ago(100), _ago(90), **kw) is None  # snapshot 100 s old
    later = NOW + timedelta(seconds=40)  # the snapshot is now 140 s old, the fill unchanged
    assert managerefresh.why_full(later, TODAY, TODAY, _ago(100), fill, **kw).startswith("a fill at")
    # A day with no snapshot yet is never made to wait.
    assert managerefresh.why_full(NOW, TODAY, "2026-11-04", _ago(5), None, **kw) == "no snapshot today yet"


def test_a_fill_that_lands_while_a_snapshot_is_being_read_gets_one_more_run(tmp_path):
    path = tmp_path / "snap.json"
    path.write_text(
        json.dumps(
            {
                "session": TODAY,
                "started_at": _ago(300).strftime("%Y-%m-%dT%H:%M:%S%z"),
                "generated_at": _ago(240).strftime("%Y-%m-%dT%H:%M:%S%z"),  # the reads took a minute
            }
        )
    )
    session, taken = managerefresh.stored_snapshot(path)
    fill = _ago(270)  # after the reads began, before they ended
    reason = managerefresh.why_full(NOW, TODAY, session, taken, fill, settle_seconds=60, min_gap_seconds=120)
    assert reason is not None and reason.startswith("a fill at")
    # An older file with only generated_at is read as before.
    path.write_text(json.dumps({"session": TODAY, "generated_at": _ago(240).strftime("%Y-%m-%dT%H:%M:%S%z")}))
    assert managerefresh.stored_snapshot(path)[1] == _ago(240)


# ------------------------------------------------------------------ positions that changed with no order
def _positions(*legs, ok=True):
    return {
        "ok": ok,
        "accounts": [
            {
                "account": "****1234",
                "underlyings": [{"underlying": "AAA", "legs": [dict(symbol=s, quantity=q) for s, q in legs]}],
            }
        ],
    }


def test_the_positions_diff_names_what_changed_and_stays_silent_otherwise():
    held = managerefresh.held_legs(_positions(("AAA  261218C00100000", -1), ("AAA", 100)))
    same = managerefresh.held_legs(_positions(("AAA", 100), ("AAA  261218C00100000", -1)))
    assert managerefresh.leg_changes(held, same) is None
    assigned = managerefresh.held_legs(_positions(("AAA", 0)))  # the short call gone, the shares called away
    got = managerefresh.leg_changes(held, assigned)
    assert got and got.startswith("positions differ from the snapshot") and "AAA" in got
    more = managerefresh.held_legs(_positions(("AAA  261218C00100000", -2), ("AAA", 100)))
    assert managerefresh.leg_changes(held, more)


def test_a_missing_snapshot_or_a_failed_read_is_not_a_difference(tmp_path):
    held = managerefresh.held_legs(_positions(("AAA", 100)))
    assert managerefresh.leg_changes(None, held) is None
    assert managerefresh.leg_changes(held, None) is None
    assert managerefresh.held_legs(_positions(("AAA", 100), ok=False)) is None
    assert managerefresh.held_legs({"error": "x"}) is None
    assert managerefresh.held_legs(None) is None
    assert managerefresh.stored_legs(tmp_path / "none.json", TODAY) is None
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"session": "2026-11-04", "positions": _positions(("AAA", 100))}))
    assert managerefresh.stored_legs(path, TODAY) is None  # yesterday's is not today's
    path.write_text(json.dumps({"session": TODAY, "positions": _positions(("AAA", 100))}))
    assert managerefresh.stored_legs(path, TODAY) == held


def test_a_positions_change_is_a_reason_after_the_gap_and_a_fill_comes_first():
    change = "positions differ from the snapshot (AAA)"
    kw = {"min_gap_seconds": 120}
    assert managerefresh.why_full(NOW, TODAY, TODAY, _ago(600), None, positions_change=change, **kw) == change
    assert managerefresh.why_full(NOW, TODAY, TODAY, _ago(60), None, positions_change=change, **kw) is None
    assert managerefresh.why_full(NOW, TODAY, TODAY, _ago(600), None, **kw) is None
    fill = managerefresh.why_full(
        NOW, TODAY, TODAY, _ago(600), _ago(90), positions_change=change, settle_seconds=60, **kw
    )
    assert fill.startswith("a fill at")


def _marks_job(monkeypatch, tmp_path, *, read, snap_age=600, fill=None, on_change=None):
    from cherrypick import cli
    from cherrypick.orchestrator import managesnap
    from cherrypick.orchestrator import positions as positions_mod

    monkeypatch.setattr(cli.cfgmod, "state_file", lambda name: tmp_path / name)
    monkeypatch.setattr(cli.timeutil, "now_et", lambda tz=None: NOW)
    calls = []
    monkeypatch.setattr(
        cli,
        "_manage_full",
        lambda cfg, reason="x": calls.append(("full", reason)) or {"ok": True, "session": TODAY},
    )
    monkeypatch.setattr(managesnap, "marks_overlay", lambda *a, **k: calls.append(("remark",)) or None)
    monkeypatch.setattr(cli, "_emit", lambda rec: None)
    monkeypatch.setattr(managerefresh, "latest_fill", lambda inbox: fill)
    reads = []
    monkeypatch.setattr(positions_mod, "run", lambda cfg: reads.append(1) or read)
    (tmp_path / "manage.snapshot.json").write_text(
        json.dumps(
            {
                "session": TODAY,
                "generated_at": _ago(snap_age).strftime("%Y-%m-%dT%H:%M:%S%z"),
                "positions": _positions(("AAA", 100)),
            }
        )
    )
    cfg = {"manage": {"enabled": True, **({"on_change": on_change} if on_change else {})}}
    return cli, cfg, calls, reads


def test_the_minute_job_runs_on_a_positions_change_and_records_why(monkeypatch, tmp_path):
    cli, cfg, calls, reads = _marks_job(monkeypatch, tmp_path, read=_positions(("AAA", 0)))
    cli._run_manage_marks(cfg)
    assert calls and calls[0][0] == "full" and calls[0][1].startswith("positions differ")
    hb = json.loads((tmp_path / "manage.marks.last.json").read_text())
    assert hb["full_snapshot"].startswith("positions differ") and len(reads) == 1


def test_the_minute_job_re_marks_when_nothing_changed_and_reads_the_broker_only_every_so_often(
    monkeypatch, tmp_path
):
    cli, cfg, calls, reads = _marks_job(monkeypatch, tmp_path, read=_positions(("AAA", 100)))
    cli._run_manage_marks(cfg)
    cli._run_manage_marks(cfg)  # the same minute again: inside positions_check_seconds
    assert calls == [("remark",), ("remark",)] and len(reads) == 1


def test_the_minute_job_does_nothing_new_with_a_failed_read_or_no_snapshot_or_the_switch_off(
    monkeypatch, tmp_path
):
    cli, cfg, calls, reads = _marks_job(monkeypatch, tmp_path, read={"ok": False, "accounts": []})
    cli._run_manage_marks(cfg)
    assert calls == [("remark",)]
    cli, cfg, calls, reads = _marks_job(
        monkeypatch, tmp_path, read=_positions(("AAA", 0)), on_change={"enabled": False}
    )
    cli._run_manage_marks(cfg)
    assert calls == [("remark",)] and reads == []  # off: no broker read, as before
    (tmp_path / "manage.snapshot.json").unlink()
    cli, cfg, calls, reads = _marks_job(monkeypatch, tmp_path, read=_positions(("AAA", 0)))
    (tmp_path / "manage.snapshot.json").unlink()
    cli._run_manage_marks(cfg)
    assert calls[0][1] == "no snapshot today yet" and reads == []


def test_a_fill_still_settling_blocks_the_positions_read(monkeypatch, tmp_path):
    cli, cfg, calls, reads = _marks_job(monkeypatch, tmp_path, read=_positions(("AAA", 0)), fill=_ago(20))
    cli._run_manage_marks(cfg)
    assert calls == [("remark",)] and reads == []  # the fill's own run takes the whole order in one


# ------------------------------------------------------------------ the post
def _doc(*pairs):
    trades = [
        {"trade_id": tid, "recommendation": {"action": action, "evidence": "practice"}}
        for tid, action in pairs
    ]
    return {"session": TODAY, "accounts": [{"account": "****1234", "trades": trades}]}


def test_the_signature_is_the_trades_needing_action_and_nothing_else():
    a = managepost.signature(_doc(("A", "CLOSE"), ("B", "HOLD"), ("C", "REVIEW"), ("D", "SETTLES")))
    assert a == ["****1234|A|CLOSE", "****1234|C|REVIEW"]
    assert managepost.signature(_doc(("C", "REVIEW"), ("A", "CLOSE"))) == a  # order does not matter
    assert managepost.signature(_doc(("A", "HOLD"))) == []


def test_a_run_posts_only_when_the_set_changed_and_the_days_first_always_does():
    sig = ["x|A|CLOSE"]
    last = {"session": TODAY, "at": _ago(3600).isoformat(), "signature": sig}
    assert not managepost.post_due_on_change(last, NOW, TODAY, 0, sig)  # the same set: silent
    assert managepost.post_due_on_change(last, NOW, TODAY, 0, sig + ["x|B|REVIEW"])  # a trade joined
    assert managepost.post_due_on_change(last, NOW, TODAY, 0, [])  # the set emptied: say so
    assert managepost.post_due_on_change(None, NOW, TODAY, 0, sig)  # the day's first run
    assert managepost.post_due_on_change({**last, "session": "2026-11-04"}, NOW, TODAY, 0, sig)
    assert managepost.post_due_on_change(
        {"session": TODAY, "at": last["at"]}, NOW, TODAY, 0, sig
    )  # no signature


def test_notify_every_minutes_is_a_floor_against_flapping():
    sig = ["x|A|CLOSE"]
    recent = {"session": TODAY, "at": _ago(600).isoformat(), "signature": sig}
    assert not managepost.post_due_on_change(recent, NOW, TODAY, 30, [])  # changed, but 10 min after a post
    old = {**recent, "at": _ago(3600).isoformat()}
    assert managepost.post_due_on_change(old, NOW, TODAY, 30, [])
    assert not managepost.post_due_on_change(old, NOW, TODAY, 30, sig)  # unchanged stays silent


def test_the_post_names_what_started_the_run():
    _, message, embed = managepost.build(
        _doc(("A", "CLOSE")), "11:00", "a fill at 10:58:00 since the snapshot"
    )
    assert "Run because: a fill at 10:58:00 since the snapshot." in message
    assert embed["description"] == message
    assert "Run because" not in managepost.build(_doc(("A", "CLOSE")), "11:00")[1]


# ------------------------------------------------------------------ the run record
def _full(monkeypatch, tmp_path, doc, cfg):
    from cherrypick import cli
    from cherrypick.orchestrator import managesnap

    monkeypatch.setattr(cli.cfgmod, "state_file", lambda name: tmp_path / name)
    monkeypatch.setattr(cli.timeutil, "now_et", lambda tz=None: NOW)
    monkeypatch.setattr(managesnap, "build", lambda c: {"positions": {"ok": True}})
    written = tmp_path / "eval.json"
    written.write_text(json.dumps(doc))
    out = json.dumps({"ok": True, "session": TODAY, "counts": {}, "written": str(written)})
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=out, stderr="")
    )
    sent = []

    class Fake:
        def __init__(self, *a, **k): ...
        def notify(self, level, source, title, message, embed=None, **k):
            sent.append((title, message))

    monkeypatch.setattr(cli, "Notifier", Fake)
    monkeypatch.setattr(cli, "_manage_attention", lambda *a, **k: None)
    return cli, sent


def test_every_run_records_why_and_a_second_unchanged_run_does_not_post(monkeypatch, tmp_path):
    cfg = {"manage": {"enabled": True}}
    cli, sent = _full(monkeypatch, tmp_path, _doc(("A", "CLOSE")), cfg)
    rec = cli._manage_full(cfg, "a fill at 10:58:00 since the snapshot")
    assert rec["reason"] == "a fill at 10:58:00 since the snapshot"
    assert json.loads((tmp_path / "manage.last.json").read_text())["reason"].startswith("a fill at")
    assert len(sent) == 1 and "Run because: a fill at" in sent[0][1]  # the day's first run posts
    cli._manage_full(cfg, "scheduled run")  # the same trades needing action
    assert len(sent) == 1


def test_a_changed_set_posts_and_with_on_change_off_the_timer_decides(monkeypatch, tmp_path):
    cfg = {"manage": {"enabled": True}}
    cli, sent = _full(monkeypatch, tmp_path, _doc(("A", "CLOSE")), cfg)
    cli._manage_full(cfg, "scheduled run")
    (tmp_path / "eval.json").write_text(json.dumps(_doc(("A", "CLOSE"), ("B", "REVIEW"))))
    cli._manage_full(cfg, "scheduled run")
    assert len(sent) == 2
    off = {"manage": {"enabled": True, "on_change": {"enabled": False}}}
    cli._manage_full(off, "scheduled run")  # notify_every_minutes 0: every run posts, as before
    cli._manage_full(off, "scheduled run")
    assert len(sent) == 4
