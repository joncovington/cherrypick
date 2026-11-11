"""Order alerts across broker logins.

- live-fill-alerts reads every login's inbox, a cursor each; a state file from before named logins
  (one `after`) keeps main's cursor.
- An order on an extra login is announced as manual there and never wakes a live loop -- no module
  trades that login. (The guard was shown to fail by dropping the login test on `woken`.)
- managerefresh takes the newest fill from any inbox.
- One `account-alerts.<login>` resident job per extra login; the watchdog's findings carry the login.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cherrypick.core.live import account_alerts as _account

from cherrypick.orchestrator import jobspec, livefills, managerefresh, watchdog

DAY = "2026-11-02"
CFG = {"broker": {"logins": [{"name": "margin", "manage": True}]}}


class _Recorder:
    def __init__(self):
        self.sent = []

    def notify(self, level, key, title, message, embed=None):
        self.sent.append({"level": level, "key": key, "title": title, "message": message})


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))
    state = tmp_path / "state"
    (state / "live_known_orders").mkdir(parents=True)
    # meic's ledger publishes M1 -- and, to prove the login rule, the same id is used on margin below.
    (state / "live_known_orders" / "meic.json").write_text(
        json.dumps({"order_ids": ["M1"]}), encoding="utf-8"
    )
    monkeypatch.setattr(livefills._home, "state_dir", lambda: state)
    woken = []
    monkeypatch.setattr(livefills._supervisor, "request_run", lambda job, by: woken.append(job))
    return {"state": state, "woken": woken}


def _add(path, events, minute=0):
    conn = _account.connect(path)
    for i, (oid, status) in enumerate(events):
        _account.record(
            conn,
            {"order_id": oid, "status": status, "price": "1.00"},
            "stream",
            f"{DAY}T10:{minute + i:02d}:00-05:00",
        )
    conn.close()


def _cfg():
    return {**CFG, "modules": {"meic": {"enabled": True, "live": {"task_name": "meic-live"}}}}


def test_both_inboxes_are_read_and_an_extra_login_never_wakes_a_loop(home):
    _add(_account.inbox_path(), [("M1", "Filled")])
    _add(_account.inbox_path("margin"), [("M1", "Filled"), ("H9", "Filled")])
    rec = _Recorder()
    out = livefills.run(_cfg(), notifier=rec, today=DAY)
    # M1 filled on main once (the notified set is keyed by order id, and broker ids are unique).
    assert [(s["order_id"], s["login"], s["module"]) for s in out["sent"]] == [
        ("M1", "main", "meic"),
        ("H9", "margin", "manual (margin)"),
    ]
    assert home["woken"] == ["meic-live"]
    assert "on the margin login" in rec.sent[1]["message"]


def test_an_extra_logins_fill_alone_wakes_nothing(home):
    _add(_account.inbox_path("margin"), [("M1", "Filled")])
    out = livefills.run(_cfg(), notifier=_Recorder(), today=DAY)
    assert out["woken"] == [] and home["woken"] == []


def test_cursors_per_login_and_the_old_state_shape_keeps_mains(home):
    _add(_account.inbox_path(), [("A", "Filled")])
    _add(_account.inbox_path("margin"), [("B", "Filled")])
    # A state file written before named logins: one `after`, no `after_by_login`.
    livefills._save_state({"day": DAY, "after": 1, "notified": ["A:filled"]})
    out = livefills.run(_cfg(), notifier=_Recorder(), today=DAY)
    assert [s["order_id"] for s in out["sent"]] == ["B"]
    state = livefills._load_state()
    assert state["after"] == 1 and state["after_by_login"] == {"margin": 1}
    _add(_account.inbox_path("margin"), [("C", "Filled")], minute=5)
    assert [s["order_id"] for s in livefills.run(_cfg(), notifier=_Recorder(), today=DAY)["sent"]] == ["C"]


def test_no_logins_reads_only_main(home):
    _add(_account.inbox_path("margin"), [("B", "Filled")])
    out = livefills.run({"modules": {}}, notifier=_Recorder(), today=DAY)
    assert out["sent"] == []


def test_latest_fill_any_takes_the_newest(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    _add(str(a), [("X", "Filled")], minute=1)
    _add(str(b), [("Y", "Filled")], minute=7)
    at = managerefresh.latest_fill_any([a, b, tmp_path / "missing.db"])
    assert at is not None and at.minute == 7
    assert managerefresh.latest_fill_any([tmp_path / "missing.db"]) is None


def _jobs(cfg):
    jobs, _errors = jobspec.derive_jobs(
        cfg,
        pythonw="pythonw",
        launcher="run.py",
        now=datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc),
        pipelines=False,
    )
    return {j.id: j for j in jobs}


def test_one_resident_alert_job_per_extra_login():
    cfg = {
        "broker": {
            "logins": [{"name": "margin", "alerts_account": "x1234"}, {"name": "ira2", "alerts": False}]
        }
    }
    jobs = _jobs(cfg)
    margin = jobs["account-alerts.margin"]
    assert margin.argv[-4:] == ("--login", "margin", "--account", "1234")
    assert margin.enabled and margin.kind == jobspec.KIND_RESIDENT
    assert Path(margin.silence_file).name == "account-alerts.margin.heartbeat"
    assert jobs["account-alerts.ira2"].enabled is False
    assert "account-alerts.margin" not in _jobs({})


def test_watchdog_findings_carry_the_login(monkeypatch):
    now = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
    status = {
        "day": now.astimezone().date().isoformat(),
        "stream_missed_fills": [],
        "rest_failures": 3,
        "last_rest_ok_at": None,
        "last_rest_error": "401 Unauthorized",
    }
    monkeypatch.setattr(_account, "read_status", lambda login=None: status if login == "margin" else {})
    findings = watchdog._check_account_alerts(True, now=now, login="margin")
    ids = {f.key: f for f in findings}
    assert set(ids) == {"account_alerts.margin.stream", "account_alerts.margin.rest"}
    assert "(margin)" in ids["account_alerts.margin.rest"].title
    assert watchdog._check_account_alerts(True, now=now) == []  # main has no status today
