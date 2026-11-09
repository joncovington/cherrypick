"""When the minute `manage-marks` job takes a full snapshot (managerefresh.py, 2026-11-04): with no
snapshot today, and after a fill heard since the stored snapshot once it has settled; never for an older
fill, a fill still settling, or a quiet minute. And the job itself takes the full path on those minutes
and the re-mark otherwise, with the broker steps faked."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from cherrypick.orchestrator import managerefresh

ET = timezone(timedelta(hours=-5))
NOW = datetime(2026, 11, 5, 11, 0, 0, tzinfo=ET)
SNAP = NOW - timedelta(minutes=20)


def test_a_new_day_takes_a_full_snapshot():
    assert managerefresh.why_full(NOW, "2026-11-05", "2026-11-04", SNAP, None) == "no snapshot today yet"
    assert managerefresh.why_full(NOW, "2026-11-05", None, None, None) == "no snapshot today yet"


def test_a_settled_fill_since_the_snapshot_takes_one_and_nothing_else_does():
    settled = NOW - timedelta(seconds=45)
    assert managerefresh.why_full(NOW, "2026-11-05", "2026-11-05", SNAP, settled).startswith(
        "a fill at 10:59:15"
    )
    # Still settling: the rest of the order may be landing; the next minute takes it.
    assert managerefresh.why_full(NOW, "2026-11-05", "2026-11-05", SNAP, NOW - timedelta(seconds=10)) is None
    # A fill before the snapshot is in it.
    assert managerefresh.why_full(NOW, "2026-11-05", "2026-11-05", SNAP, SNAP - timedelta(minutes=1)) is None
    assert managerefresh.why_full(NOW, "2026-11-05", "2026-11-05", SNAP, None) is None


def test_the_readers_read_the_inbox_and_the_snapshot(tmp_path):
    inbox = tmp_path / "account_alerts.db"
    conn = sqlite3.connect(inbox)
    conn.execute(
        "CREATE TABLE order_events (order_id TEXT, status TEXT, price TEXT, underlying_symbol TEXT,"
        " external_identifier TEXT, source TEXT, received_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO order_events VALUES (?, ?, NULL, 'AAA', NULL, 'stream', ?)",
        [("1", "filled", "2026-11-05T10:40:00-05:00"), ("2", "live", "2026-11-05T10:55:00-05:00")],
    )
    conn.commit()
    conn.close()
    assert managerefresh.latest_fill(inbox) == datetime(2026, 11, 5, 10, 40, tzinfo=ET)
    assert managerefresh.latest_fill(tmp_path / "missing.db") is None
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"session": "2026-11-05", "generated_at": "2026-11-05T07:40:00-0800"}))
    assert managerefresh.stored_snapshot(snap) == ("2026-11-05", datetime(2026, 11, 5, 10, 40, tzinfo=ET))
    assert managerefresh.stored_snapshot(tmp_path / "none.json") == (None, None)


def test_the_minute_job_takes_the_full_path_on_a_fill_and_the_re_mark_otherwise(tmp_path, monkeypatch):
    from cherrypick import cli
    from cherrypick.orchestrator import managesnap

    monkeypatch.setattr(cli.cfgmod, "state_file", lambda name: tmp_path / name)
    monkeypatch.setattr(cli.timeutil, "now_et", lambda tz=None: NOW)
    calls = []
    monkeypatch.setattr(
        cli,
        "_manage_full",
        lambda cfg, reason="x": calls.append("full") or {"ok": True, "session": "2026-11-05"},
    )
    monkeypatch.setattr(managesnap, "marks_overlay", lambda *a, **k: calls.append("remark") or None)
    monkeypatch.setattr(cli, "_emit", lambda rec: None)
    fill = {"at": None}
    monkeypatch.setattr(managerefresh, "latest_fill", lambda inbox: fill["at"])
    (tmp_path / "manage.snapshot.json").write_text(
        json.dumps({"session": "2026-11-05", "generated_at": SNAP.strftime("%Y-%m-%dT%H:%M:%S%z")})
    )

    cli._run_manage_marks({})
    assert calls == ["remark"]
    fill["at"] = NOW - timedelta(seconds=40)
    cli._run_manage_marks({})
    assert calls == ["remark", "full"]
    hb = json.loads((tmp_path / "manage.marks.last.json").read_text())
    assert hb["full_snapshot"].startswith("a fill at") and hb["ok"] is True
