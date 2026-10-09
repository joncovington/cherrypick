"""`run.py settle-overdue-live` (item 4, 2026-10-08): runs each live module's catch-up settlement and
announces every one -- a write to a live ledger never happens quietly."""

from __future__ import annotations

import json
import subprocess

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import livesettle


class Note:
    def __init__(self):
        self.sent = []

    def notify(self, level, key, title, message, **kw):
        self.sent.append((level, key, title, message))


def test_each_settled_session_is_announced_and_a_failure_is_not_ok(monkeypatch, tmp_path):
    cfg = {
        "modules": {
            "flies": {"enabled": True, "path": str(tmp_path)},
            "bwb": {"enabled": True, "path": str(tmp_path)},
        }
    }
    outputs = {
        "cherrypick.flies.live_loop": {
            "ok": True,
            "settled": [{"session": "2026-10-06", "price": 7494.0}],
            "left": [],
        },
        "cherrypick.bwb.live_loop": None,
    }

    def fake_run(root, argv, timeout=30):
        payload = outputs[argv[1]]
        return subprocess.CompletedProcess(
            argv, 0 if payload else 1, json.dumps(payload) if payload else "", "boom"
        )

    monkeypatch.setattr(livesettle.doctor, "_run", fake_run)
    monkeypatch.setattr(cfgmod, "enabled_modules", lambda c: c["modules"])
    note = Note()
    out = livesettle.run(cfg, notifier=note)
    assert out["ok"] is False and out["modules"]["bwb"]["ok"] is False
    ((level, key, title, message),) = note.sent
    assert level == "INFO" and "2026-10-06" in title and "7494.00" in message and "official close" in message
