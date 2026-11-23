"""The orchestrator's state writers moved onto `core.jsonio`: same bytes, no tmp file left behind."""

from __future__ import annotations

import json

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import configedit, external_reports, livefills, trade_notifier


def _text(path) -> str:
    return path.read_text(encoding="utf-8")


def _no_tmp_left(directory) -> None:
    assert [p.name for p in directory.iterdir() if p.name.endswith(".tmp")] == []


def test_live_fill_state_bytes_are_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(livefills._home, "state_dir", lambda: tmp_path / "state")
    state = {"seen": ["A1", "B2"], "day": "2026-11-23"}
    livefills._save_state(state)
    assert _text(tmp_path / "state" / "live_fill_alerts.json") == json.dumps(state)
    _no_tmp_left(tmp_path / "state")


def test_posted_external_reports_are_indented_and_keep_their_order(monkeypatch, tmp_path):
    monkeypatch.setattr(external_reports._home, "state_dir", lambda: tmp_path / "state")
    external_reports._remember("zeta", "2026-11-23")
    external_reports._remember("alpha", "2026-11-22")
    expected = json.dumps({"zeta": "2026-11-23", "alpha": "2026-11-22"}, indent=2)
    assert _text(tmp_path / "state" / "external_posts.json") == expected
    _no_tmp_left(tmp_path / "state")


def test_config_edit_writes_the_text_it_was_given_and_backs_up_first(monkeypatch, tmp_path):
    monkeypatch.setattr(configedit._home, "state_dir", lambda: tmp_path / "state")
    target = tmp_path / "meic.json"
    target.write_text('{"old": true}\n', encoding="utf-8")
    out = configedit.backup_and_write(target, '{\n  "new": true\n}\n', "meic")
    assert _text(target) == '{\n  "new": true\n}\n'
    backups = list((tmp_path / "state" / "config-backups").iterdir())
    assert len(backups) == 1 and _text(backups[0]) == '{"old": true}\n' and out["backup"]
    _no_tmp_left(tmp_path)


def test_last_good_copy_is_written_verbatim_and_clears_the_broken_marker(monkeypatch, tmp_path):
    good = tmp_path / "cfg" / "config.last-good.json"
    marker = tmp_path / "cfg" / "config.broken.json"
    marker.parent.mkdir()
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "last_good_path", lambda: good)
    monkeypatch.setattr(cfgmod, "broken_marker_path", lambda: marker)
    cfgmod._remember_good('{"a": 1}\n')
    assert _text(good) == '{"a": 1}\n' and not marker.exists()
    _no_tmp_left(good.parent)


def test_trade_notify_state_is_compact_sorted_text_and_unchanged_only_touches(monkeypatch, tmp_path):
    state_file = tmp_path / "trade_notify.json"
    monkeypatch.setattr(trade_notifier, "_STATE", state_file)
    monkeypatch.setattr(trade_notifier.cfgmod, "ensure_dirs", lambda: None)
    state = {"b": 1, "a": {"y": 2, "x": 1}}
    assert trade_notifier._save_state(state) is True
    text = _text(state_file)
    assert text == '{"a":{"x":1,"y":2},"b":1}'
    assert trade_notifier._save_state(state, unchanged_from=text) is False
    _no_tmp_left(tmp_path)
