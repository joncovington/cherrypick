"""The shared atomic JSON writer: the two formatting knobs its callers depend on."""

import json

import pytest

from cherrypick.core import jsonio, regimecuts


def test_compact_form_is_one_line(tmp_path):
    target = jsonio.write_json_atomic(tmp_path / "req.json", {"a": [1, 2]}, indent=None)
    assert target.read_text(encoding="utf-8") == '{"a": [1, 2]}'


def test_strict_default_refuses_and_leaves_nothing_behind(tmp_path):
    """`default=None` is advice's and the stream requests' posture: a non-JSON value is an error,
    and because the text is encoded before the tmp file opens, the refusal leaves no file."""
    target = tmp_path / "sub" / "advice.json"
    with pytest.raises(TypeError):
        jsonio.write_json_atomic(target, {"when": {1, 2}}, default=None)
    assert list(target.parent.iterdir()) == []


def test_default_writes_a_non_json_value_as_its_str(tmp_path):
    target = jsonio.write_json_atomic(tmp_path / "x.json", {"s": {"only"}})
    assert json.loads(target.read_text(encoding="utf-8")) == {"s": "{'only'}"}


def test_a_replace_held_for_a_moment_is_retried_and_one_held_for_good_raises(monkeypatch, tmp_path):
    """Windows refuses a replace onto a file another process holds open for a moment; the
    supervisor died seven times on that before the retry existed (2026-09-21 to 10-08)."""
    calls = {"n": 0}
    real = jsonio.os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied")
        real(src, dst)

    monkeypatch.setattr(jsonio, "_REPLACE_PAUSE_S", 0)
    monkeypatch.setattr(jsonio.os, "replace", flaky)
    target = jsonio.write_json_atomic(tmp_path / "x.json", {"a": 1})
    assert calls["n"] == 3 and json.loads(target.read_text(encoding="utf-8")) == {"a": 1}

    def held(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(jsonio.os, "replace", held)
    with pytest.raises(PermissionError):
        jsonio.write_json_atomic(tmp_path / "y.json", {"a": 1})


def test_regimecuts_still_exports_the_same_function():
    assert regimecuts.write_json_atomic is jsonio.write_json_atomic
