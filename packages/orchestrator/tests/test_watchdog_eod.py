"""The event-driven EOD trigger: the watchdog fires the digest + insight once all installed modules have
written their paper-eod-<day>.md (or at the deadline backstop), exactly once per trading day, launched
detached. Replaces the old fixed-clock scheduled tasks."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cherrypick.orchestrator import watchdog as wd

pytestmark = pytest.mark.unit

_ET = ZoneInfo("America/New_York")


@pytest.fixture
def eod_env(tmp_path, monkeypatch):
    monkeypatch.setattr(wd, "_STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(wd.cfgmod, "ensure_dirs", lambda: None)
    logs = tmp_path / "logs"
    monkeypatch.setattr(wd.cfgmod, "module_logs_dir", lambda name: logs / name)
    monkeypatch.setattr(wd.cfgmod, "enabled_modules", lambda cfg: {"meic": {}, "flies": {}})
    launched: list[str] = []
    monkeypatch.setattr(wd, "_eod_launch", lambda verb: (launched.append(verb), True)[1])
    return {"tmp": tmp_path, "logs": logs, "launched": launched}


def _write_eod(logs, name, day="2026-07-21"):
    d = logs / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"paper-eod-{day}.md").write_text("x", encoding="utf-8")


def _cfg(digest=True, insight=False, deadline="16:45"):
    return {"eod_digest": {"enabled": digest, "deadline": deadline},
            "eod_insight": {"enabled": insight}, "modules": {}}


def _now(h, m, day=21):
    return datetime(2026, 7, day, h, m, tzinfo=_ET)


def _fired_day(env):
    f = env["tmp"] / "state.json"
    return json.loads(f.read_text())[wd._EOD_FIRED_KEY] if f.exists() else None


def test_before_close_does_not_fire(eod_env):
    _write_eod(eod_env["logs"], "meic"), _write_eod(eod_env["logs"], "flies")
    wd._check_eod(_cfg(), _now(15, 30), is_trading=True)
    assert eod_env["launched"] == [] and _fired_day(eod_env) is None


def test_not_trading_day_does_not_fire(eod_env):
    _write_eod(eod_env["logs"], "meic"), _write_eod(eod_env["logs"], "flies")
    wd._check_eod(_cfg(), _now(16, 30), is_trading=False)
    assert eod_env["launched"] == []


def test_fires_when_all_modules_settled(eod_env):
    _write_eod(eod_env["logs"], "meic"), _write_eod(eod_env["logs"], "flies")
    wd._check_eod(_cfg(), _now(16, 30), is_trading=True)
    assert eod_env["launched"] == ["notify-eod"]
    assert _fired_day(eod_env) == "2026-07-21"


def test_waits_for_straggler_before_deadline(eod_env):
    _write_eod(eod_env["logs"], "meic")  # flies not yet settled
    wd._check_eod(_cfg(), _now(16, 30), is_trading=True)  # 16:30 < 16:45 backstop
    assert eod_env["launched"] == [] and _fired_day(eod_env) is None


def test_fires_at_deadline_despite_missing(eod_env):
    _write_eod(eod_env["logs"], "meic")  # flies flat day — never writes
    wd._check_eod(_cfg(), _now(16, 50), is_trading=True)  # past the 16:45 backstop
    assert eod_env["launched"] == ["notify-eod"]
    assert _fired_day(eod_env) == "2026-07-21"


def test_fires_once_per_day(eod_env):
    _write_eod(eod_env["logs"], "meic"), _write_eod(eod_env["logs"], "flies")
    wd._check_eod(_cfg(), _now(16, 30), is_trading=True)
    wd._check_eod(_cfg(), _now(16, 40), is_trading=True)  # already fired today
    assert eod_env["launched"] == ["notify-eod"]


def test_insight_enabled_launches_both_detached(eod_env):
    _write_eod(eod_env["logs"], "meic"), _write_eod(eod_env["logs"], "flies")
    wd._check_eod(_cfg(insight=True), _now(16, 30), is_trading=True)
    assert eod_env["launched"] == ["notify-eod", "eod-insight"]


def test_both_disabled_does_nothing(eod_env):
    _write_eod(eod_env["logs"], "meic"), _write_eod(eod_env["logs"], "flies")
    wd._check_eod(_cfg(digest=False, insight=False), _now(16, 30), is_trading=True)
    assert eod_env["launched"] == []
