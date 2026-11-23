"""The writers that moved onto `jsonio` write the same bytes they wrote before, and they retry.

Each expected string is the serialization the writer's own hand-rolled copy produced, so a change
of indent, separators, key order or encoding at one call site fails here. The two live-adjacent
files (known orders, open legs) are compared byte for byte.
"""

from __future__ import annotations

import json

import pytest

from cherrypick.core import execution, jsonio, livepositions, symbolevents
from cherrypick.core.live import account_alerts
from cherrypick.core.sources import inventory


def _broker(**kw):
    return execution.Broker(get_session=lambda: object(), designated_account=lambda: "****0000", **kw)


def _text(path) -> str:
    return path.read_text(encoding="utf-8")


def _no_tmp_left(directory) -> None:
    assert [p.name for p in directory.iterdir() if p.name.endswith(".tmp")] == []


# --------------------------------------------------------------------------- the helper


def test_write_text_atomic_writes_creates_the_directory_and_leaves_no_tmp(tmp_path):
    target = jsonio.write_text_atomic(tmp_path / "a" / "b.txt", "héllo\n")
    assert _text(target) == "héllo\n"
    _no_tmp_left(target.parent)


def test_write_text_atomic_retries_a_held_replace_and_raises_when_held_for_good(monkeypatch, tmp_path):
    calls = {"n": 0}
    real = jsonio.os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied")
        real(src, dst)

    monkeypatch.setattr(jsonio, "_REPLACE_PAUSE_S", 0)
    monkeypatch.setattr(jsonio.os, "replace", flaky)
    target = jsonio.write_text_atomic(tmp_path / "x.txt", "ok")
    assert calls["n"] == 3 and _text(target) == "ok"

    def held(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(jsonio.os, "replace", held)
    with pytest.raises(PermissionError):
        jsonio.write_text_atomic(tmp_path / "y.txt", "no")


def test_json_separators_and_sort_keys_pass_through(tmp_path):
    target = jsonio.write_json_atomic(
        tmp_path / "x.json", {"b": 1, "a": [1, 2]}, indent=None, separators=(",", ":"), sort_keys=True
    )
    assert _text(target) == '{"a":[1,2],"b":1}'


# --------------------------------------------------------------------------- core call sites


def test_known_orders_bytes_are_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(execution, "_known_orders_dir", lambda: tmp_path / "known")
    execution.publish_known_orders("meic", ["b2", "a1", None, "a1"], now_iso="2026-11-23T10:00:00")
    expected = json.dumps({"module": "meic", "at": "2026-11-23T10:00:00", "order_ids": ["a1", "b2"]})
    assert _text(tmp_path / "known" / "meic.json") == expected
    _no_tmp_left(tmp_path / "known")


def test_open_legs_bytes_are_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(livepositions, "_open_legs_dir", lambda: tmp_path / "legs")
    legs = [{"underlying": "SPX", "expiry": "2026-11-23", "right": "P", "strike": 5000.0, "qty": -1}]
    livepositions.publish_open_legs("meic", legs, now_iso="2026-11-23T10:00:00")
    rows = [
        {"underlying": k[0], "expiry": k[1], "right": k[2], "strike": k[3], "qty": q}
        for k, q in sorted(livepositions.expected_legs(legs).items())
    ]
    expected = json.dumps({"module": "meic", "at": "2026-11-23T10:00:00", "legs": rows})
    assert _text(tmp_path / "legs" / "meic.json") == expected
    _no_tmp_left(tmp_path / "legs")


def test_hold_file_bytes_are_unchanged_and_clearing_removes_it(tmp_path):
    hold = tmp_path / "state" / "hold.json"
    guard = _broker(hold_path=hold, health_path=tmp_path / "state" / "health.json")
    guard._unresolved = {"ext-1": "submit raised"}
    guard._unrecorded = {"o9": {"price": 1.5}}
    guard._save_hold()
    doc = {"unresolved": guard._unresolved, "unrecorded": guard._unrecorded}
    assert _text(hold) == json.dumps(doc, default=str)
    guard._unresolved, guard._unrecorded = {}, {}
    guard._save_hold()
    assert not hold.exists()


def test_health_note_is_compact_json_and_a_write_failure_is_swallowed(monkeypatch, tmp_path):
    health = tmp_path / "state" / "health.json"
    guard = _broker(hold_path=tmp_path / "state" / "hold.json", health_path=health)
    guard._note_contact(RuntimeError("boom"))
    text = _text(health)
    assert text == json.dumps(json.loads(text))
    assert json.loads(text)["failures"] == 1

    def refuse(*_a, **_k):
        raise PermissionError(5, "held")

    monkeypatch.setattr(jsonio, "_REPLACE_PAUSE_S", 0)
    monkeypatch.setattr(jsonio, "_REPLACE_TRIES", 2)
    monkeypatch.setattr(jsonio.os, "replace", refuse)
    guard._note_contact(RuntimeError("boom"))  # PermissionError is an OSError: a note never breaks a call


def test_account_alert_status_bytes_are_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(account_alerts, "data_dir", lambda: str(tmp_path / "live"))
    fields = {"ok": True, "n": 3, "at": "2026-11-23T10:00:00"}
    account_alerts._write_status(fields)
    assert _text(tmp_path / "live" / account_alerts.STATUS_FILENAME) == json.dumps(fields)


def test_inventory_bytes_are_unchanged(tmp_path):
    doc = {"sources": {"b": {"n": 1}, "a": {"n": 2}}}
    target = inventory.write(doc, tmp_path / "state" / "inv.json")
    assert _text(target) == json.dumps(doc, indent=1, sort_keys=True)


def test_symbol_events_bytes_are_unchanged(monkeypatch, tmp_path):
    out = tmp_path / "cal" / "symbol_events.json"
    doc = symbolevents.write([], connect=None, out=out)
    assert _text(out) == json.dumps(doc, separators=(",", ":"))
    _no_tmp_left(out.parent)
