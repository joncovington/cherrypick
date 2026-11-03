"""`run.py live-fill-alerts`: LIVE order notices -- placed, filled, ended unfilled -- from the
account's order events (2026-11-02; fills only, from per-module inboxes, from 2026-10-15)."""

from __future__ import annotations

import json

from cherrypick.core.live import account_alerts as _account

from cherrypick.orchestrator import livefills

DAY = "2026-11-02"


class _Recorder:
    def __init__(self):
        self.sent = []

    def notify(self, level, key, title, message, embed=None):
        self.sent.append({"level": level, "key": key, "title": title, "message": message})


def _setup(tmp_path, monkeypatch, events):
    state = tmp_path / "state"
    (state / "live_known_orders").mkdir(parents=True)
    (state / "live_known_orders" / "meic.json").write_text(
        json.dumps({"order_ids": ["M1"]}), encoding="utf-8"
    )
    monkeypatch.setattr(livefills._home, "state_dir", lambda: state)
    inbox = str(tmp_path / "account_alerts.db")
    add(inbox, events)
    return inbox


def add(inbox, events):
    conn = _account.connect(inbox)
    for i, (oid, status, extra) in enumerate(events):
        event = {"order_id": oid, "status": status, "price": "-1.40", "underlying_symbol": "SPX", **extra}
        _account.record(conn, event, "stream", f"{DAY}T10:{i:02d}:00-05:00")
    conn.close()


def _run(inbox, rec, today=DAY):
    return livefills.run({}, notifier=rec, today=today, inbox=inbox)


def test_placed_filled_and_ended_each_notify_once(tmp_path, monkeypatch):
    """Shown to fail with fills alone: the placement and the removal were never announced."""
    inbox = _setup(
        tmp_path,
        monkeypatch,
        [("M1", "Received", {}), ("M1", "Live", {}), ("M2", "Live", {"external_identifier": "cp-1"})],
    )
    rec = _Recorder()
    out = _run(inbox, rec)
    assert [(s["order_id"], s["kind"], s["module"]) for s in out["sent"]] == [
        ("M1", "placed", "meic"),
        ("M2", "placed", "a live loop"),
    ]
    add(inbox, [("M1", "Filled", {}), ("M2", "Removed", {"external_identifier": "cp-1"})])
    out = _run(inbox, rec)
    assert [(s["order_id"], s["kind"]) for s in out["sent"]] == [("M1", "filled"), ("M2", "ended")]
    assert rec.sent[2]["level"] == "WARNING" and rec.sent[2]["title"] == "LIVE fill: meic"
    assert "on SPX FILLED at -1.40" in rec.sent[2]["message"]
    assert rec.sent[3]["title"] == "LIVE order removed: a live loop"
    assert _run(inbox, rec)["sent"] == []  # nothing new, nothing sent


def test_an_order_placed_and_filled_in_one_pass_is_announced_once(tmp_path, monkeypatch):
    inbox = _setup(tmp_path, monkeypatch, [("M1", "Received", {}), ("M1", "Filled", {})])
    rec = _Recorder()
    assert [s["kind"] for s in _run(inbox, rec)["sent"]] == ["filled"]


def test_an_order_with_no_ledger_and_no_suite_identifier_is_manual(tmp_path, monkeypatch):
    inbox = _setup(tmp_path, monkeypatch, [("X9", "Live", {"underlying_symbol": "AAPL"})])
    rec = _Recorder()
    _run(inbox, rec)
    assert rec.sent[0]["title"] == "LIVE order placed: manual"
    assert "a manual order" in rec.sent[0]["message"] and "on AAPL" in rec.sent[0]["message"]


def test_a_fill_or_a_death_asks_the_owning_loop_for_an_early_tick(tmp_path, monkeypatch):
    """Shown to fail without the request: the loop confirmed the fill up to a minute later."""
    asked = []
    monkeypatch.setattr(livefills._supervisor, "request_run", lambda job, by: asked.append((job, by)))
    cfg = {"modules": {"meic": {"enabled": True, "live": {"task_name": "cherrypick-meic-live-loop"}}}}
    inbox = _setup(tmp_path, monkeypatch, [("M1", "Live", {}), ("X9", "Filled", {})])  # X9: manual
    out = livefills.run(cfg, notifier=_Recorder(), today=DAY, inbox=inbox)
    assert asked == [] and out["woken"] == []  # a placement, and a manual fill, wake nothing
    add(inbox, [("M1", "Filled", {})])
    out = livefills.run(cfg, notifier=_Recorder(), today=DAY, inbox=inbox)
    assert asked == [("meic-live", "live-fill-alerts")] and out["woken"] == ["meic"]


def test_a_status_worth_no_notice_sends_nothing(tmp_path, monkeypatch):
    inbox = _setup(tmp_path, monkeypatch, [("M1", "Cancel Requested", {})])
    assert _run(inbox, _Recorder())["sent"] == []


def test_a_new_day_starts_clean(tmp_path, monkeypatch):
    inbox = _setup(tmp_path, monkeypatch, [("M1", "Filled", {})])
    rec = _Recorder()
    _run(inbox, rec)
    # yesterday's events are not re-announced on a new day, and the state resets
    assert _run(inbox, rec, today="2026-11-03")["sent"] == []
    assert json.loads(livefills.state_path().read_text(encoding="utf-8"))["day"] == "2026-11-03"


def test_the_old_state_shape_is_dropped_not_misread(tmp_path, monkeypatch):
    inbox = _setup(tmp_path, monkeypatch, [("M1", "Filled", {})])
    livefills.state_path().write_text(
        json.dumps({"day": DAY, "after": {"meic": "2026-11-02T10:00:00"}, "notified": ["M1"]}),
        encoding="utf-8",
    )
    assert [s["kind"] for s in _run(inbox, _Recorder())["sent"]] == ["filled"]
