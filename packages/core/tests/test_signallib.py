"""The signal library: versions are content hashes, an edit adds one, a reference names its version and
resolves to a copy, and a library whose versions do not match their specs is refused."""

from __future__ import annotations

import json

import pytest

from cherrypick.core import signallib

CROSS = {"kind": "cross", "a": {"name": "close"}, "b": 101, "direction": "above", "seed": True}


def test_a_version_is_the_hash_of_the_settings_whatever_their_order():
    reordered = dict(reversed(list(CROSS.items())))
    assert signallib.content_hash(CROSS) == signallib.content_hash(reordered)
    assert len(signallib.content_hash(CROSS)) == signallib.VERSION_LEN
    assert signallib.content_hash({**CROSS, "b": 102}) != signallib.content_hash(CROSS)


def test_an_edit_adds_a_version_and_the_same_settings_add_none():
    lib = signallib.empty()
    v1, added = signallib.add(lib, "close-over-101", CROSS, today="2026-11-15")
    assert added and v1 == signallib.content_hash(CROSS)
    assert signallib.add(lib, "close-over-101", dict(CROSS), today="2026-11-16") == (v1, False)
    v2, added = signallib.add(lib, "close-over-101", {**CROSS, "b": 102}, today="2026-11-16")
    assert added and v2 != v1
    assert [v["version"] for v in lib["signals"]["close-over-101"]["versions"]] == [v1, v2]
    assert signallib.latest(lib, "close-over-101")["version"] == v2
    # The old version stays readable: a finished run names it.
    assert signallib.get(lib, "close-over-101", v1)["b"] == 101
    # Going back to an old version's settings is naming that version, not a new one.
    with pytest.raises(ValueError, match="older version"):
        signallib.add(lib, "close-over-101", CROSS)
    # A copy filed under another name is the same version, and shows as such.
    signallib.add(lib, "renamed-copy", CROSS)
    assert signallib.same_as(lib, v1) == ["close-over-101", "renamed-copy"]


def test_a_spec_that_does_not_validate_is_not_filed():
    lib = signallib.empty()
    with pytest.raises(ValueError):
        signallib.add(lib, "bad", {"kind": "cross", "a": {"name": "close"}})
    with pytest.raises(ValueError, match="bad name"):
        signallib.add(lib, " padded ", CROSS)
    assert lib == signallib.empty()


def test_a_reference_resolves_to_a_copy_of_its_version_and_lists_what_it_used():
    lib = signallib.empty()
    v1, _ = signallib.add(lib, "s", CROSS)
    v2, _ = signallib.add(lib, "s", {**CROSS, "b": 102})
    trigger = {
        "signal": {"library": "s", "version": v1},
        "window": {"open": "09:45", "close": "11:00"},
        "nested": [{"library": "s", "version": v2}, {"library": "s", "version": v1}],
    }
    out, used = signallib.resolve(trigger, lib)
    assert out["signal"] == CROSS and out["nested"][0]["b"] == 102
    assert used == [("s", v1), ("s", v2)]
    assert signallib.refs(trigger) == [("s", v1), ("s", v2)]
    out["signal"]["b"] = 999  # a copy: the library is untouched
    assert signallib.get(lib, "s", v1)["b"] == 101
    assert trigger["signal"] == {"library": "s", "version": v1}


def test_a_reference_names_a_version_and_nothing_else():
    lib = signallib.empty()
    v1, _ = signallib.add(lib, "s", CROSS)
    with pytest.raises(ValueError, match="names its version"):
        signallib.resolve({"signal": {"library": "s"}}, lib)
    with pytest.raises(ValueError, match="takes only"):
        signallib.resolve({"signal": {"library": "s", "version": v1, "b": 5}}, lib)
    with pytest.raises(ValueError, match="no version"):
        signallib.resolve({"signal": {"library": "s", "version": "0" * 12}}, lib)
    with pytest.raises(ValueError, match="no signal"):
        signallib.resolve({"signal": {"library": "t", "version": v1}}, lib)


def test_the_file_round_trips_and_a_tampered_version_is_refused(tmp_path):
    at = tmp_path / "signals.json"
    assert signallib.load(at) == signallib.empty()
    lib = signallib.empty()
    v1, _ = signallib.add(lib, "s", CROSS, today="2026-11-15")
    signallib.save(lib, at)
    assert signallib.load(at) == lib
    raw = json.loads(at.read_text(encoding="utf-8"))
    raw["signals"]["s"]["versions"][0]["spec"]["b"] = 150  # edited by hand, version left alone
    at.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        signallib.load(at)
    raw["signals"]["s"]["versions"][0]["spec"]["b"] = 101
    raw["signals"]["s"]["versions"].append(raw["signals"]["s"]["versions"][0])
    at.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="twice"):
        signallib.load(at)


def test_the_command_files_a_signal_and_prints_its_reference(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(CROSS), encoding="utf-8")
    assert signallib.main(["add", "s", str(spec)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reference"] == {"library": "s", "version": signallib.content_hash(CROSS)} and out["added"]
    assert signallib.path().exists()
    assert signallib.main(["show", "s"]) == 0
    assert json.loads(capsys.readouterr().out)["versions"][0]["spec"] == CROSS
    assert signallib.main(["show", "nope"]) == 1
