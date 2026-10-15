"""`run.py live-fill-alerts` (2026-10-15): one LIVE alert per filled order, from the alert inboxes."""

from __future__ import annotations

import json

from cherrypick.core.live import alerts as _alerts

from cherrypick.orchestrator import livefills


class _Recorder:
    def __init__(self):
        self.sent = []

    def notify(self, level, key, title, message, embed=None):
        self.sent.append({"level": level, "key": key, "title": title, "message": message})


def _setup(tmp_path, monkeypatch, rows_by_module):
    state = tmp_path / "state"
    (state / "live_known_orders").mkdir(parents=True)
    (state / "live_known_orders" / "meic.json").write_text(
        json.dumps({"order_ids": ["M1"]}), encoding="utf-8"
    )
    monkeypatch.setattr(livefills._home, "state_dir", lambda: state)
    paths = {}
    for module, rows in rows_by_module.items():
        path = str(tmp_path / module / "live_alerts.db")
        conn = _alerts.connect(path)
        for oid, status, at in rows:
            _alerts.record_alert(conn, {"order_id": oid, "status": status, "price": "0.31"}, at)
        conn.close()
        paths[module] = path
    monkeypatch.setattr(livefills, "inboxes", lambda cfg: paths)
    return paths


def test_one_alert_per_fill_attributed_to_its_module(tmp_path, monkeypatch):
    _setup(
        tmp_path,
        monkeypatch,
        {
            "meic": [("M1", "Filled", "2026-10-15T12:30:05"), ("M2", "Live", "2026-10-15T12:30:06")],
            "flies": [("M1", "Filled", "2026-10-15T12:30:05")],  # the same account seen twice
        },
    )
    rec = _Recorder()
    out = livefills.run({}, notifier=rec, today="2026-10-15")
    assert [s["order_id"] for s in out["sent"]] == ["M1"]
    assert rec.sent[0]["title"] == "LIVE fill: meic"
    # a second pass sends nothing new
    assert livefills.run({}, notifier=rec, today="2026-10-15")["sent"] == []


def test_a_fill_no_ledger_knows_is_announced_as_such(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"meic": [("X9", "Filled", "2026-10-15T13:00:00")]})
    rec = _Recorder()
    livefills.run({}, notifier=rec, today="2026-10-15")
    assert rec.sent[0]["title"] == "LIVE fill: an order no live ledger knows"


def test_a_new_day_starts_clean(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"meic": [("M1", "Filled", "2026-10-15T12:30:05")]})
    rec = _Recorder()
    livefills.run({}, notifier=rec, today="2026-10-15")
    # yesterday's fill is not re-announced on a new day, and the state resets
    assert livefills.run({}, notifier=rec, today="2026-10-16")["sent"] == []
    assert json.loads(livefills.state_path().read_text(encoding="utf-8"))["day"] == "2026-10-16"
