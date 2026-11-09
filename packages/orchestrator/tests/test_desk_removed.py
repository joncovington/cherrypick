"""The manual desk was removed in v0.12.0. A machine upgraded from an older version can still carry
its configuration: a `desk_notify` block in config.json (enabled, on the machines that used it) and
`~/.cherrypick/config/desk.json`. Neither may start anything, and doctor says each can go."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from cherrypick.cli import build_parser
from cherrypick.orchestrator import doctor, jobspec

LEFTOVER = {
    "desk_notify": {
        "enabled": True,
        "task_name": "cherrypick-desk-notify",
        "interval_minutes": 1,
        "channels": ["log", "discord"],
    }
}


def _derive(cfg):
    base = {"timezone": "America/New_York", "modules": {}}
    return jobspec.derive_jobs(
        {**base, **cfg},
        pythonw="pythonw",
        launcher="run.py",
        now=datetime(2026, 11, 9, 12, 0, tzinfo=ZoneInfo("America/New_York")),
        include_retired=True,
    )


def test_an_enabled_leftover_block_derives_no_job():
    jobs, errors = _derive(LEFTOVER)
    assert errors == {}
    assert not [j for j in jobs if "desk" in j.id]
    assert not [j for j in jobs if "notify-desk" in j.argv]


def test_the_notify_desk_verb_is_gone():
    parser = build_parser()
    verbs = next(a for a in parser._actions if a.dest == "command").choices
    assert "notify-desk" not in verbs


def test_doctor_warns_about_each_leftover(tmp_path):
    desk_json = tmp_path / "desk.json"
    desk_json.write_text("{}", encoding="utf-8")
    out = doctor.removed_config_checks(LEFTOVER, desk_config=desk_json)
    assert [c.name for c in out] == ["config.desk_notify", "config.desk"]
    assert all(c.status == doctor.WARN and "removed in v0.12.0" in c.detail for c in out)


def test_doctor_is_silent_on_a_clean_config(tmp_path):
    assert doctor.removed_config_checks({"modules": {}}, desk_config=tmp_path / "desk.json") == []
