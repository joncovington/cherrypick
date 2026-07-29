"""The consumer-side stream-request writer: path convention, cleaning, atomicity, payload shape."""

from __future__ import annotations

import json

from cherrypick.core import streamrequests


def test_write_request_shape_and_cleaning(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = streamrequests.write_request("demo", ["xsp", " qqq ", "xsp", "", None, 7])
    assert path == tmp_path / "state" / "stream_requests" / "demo.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "symbols": ["QQQ", "XSP"],
        "legs": [],
        "leg_sources": [],
    }


def test_write_request_carries_legs_and_leg_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    source = {"db": "some/paper.db", "query": "SELECT put_symbol FROM t"}
    path = streamrequests.write_request("demo", ["SPX"], legs=[".SPX260630C7500"], leg_sources=[source])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["legs"] == [".SPX260630C7500"]
    assert payload["leg_sources"] == [source]


def test_write_is_atomic_no_tmp_residue(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    streamrequests.write_request("demo", ["SPX"])
    directory = tmp_path / "state" / "stream_requests"
    assert [p.name for p in directory.iterdir()] == ["demo.json"]


def test_overwrite_replaces_whole_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    streamrequests.write_request("demo", ["SPX", "NDX"])
    streamrequests.write_request("demo", ["XSP"])
    payload = json.loads(streamrequests.request_path("demo").read_text(encoding="utf-8"))
    assert payload["symbols"] == ["XSP"]
