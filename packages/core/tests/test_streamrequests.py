"""The consumer-side stream-request writer: path convention, cleaning, atomicity, payload shape."""

from __future__ import annotations

import json
from datetime import date

from cherrypick.core import streamrequests


def test_write_request_shape_and_cleaning(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = streamrequests.write_request("demo", ["xsp", " qqq ", "xsp", "", None, 7])
    assert path == tmp_path / "state" / "stream_requests" / "demo.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "symbols": ["QQQ", "XSP"],
        "legs": [],
        "leg_sources": [],
        "window_hints": {},
        "expirations": {},
    }


def test_write_request_carries_window_hints(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = streamrequests.write_request("demo", ["XSP"], window_hints={"xsp": 90, " qqq ": 30})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["window_hints"] == {"XSP": 90, "QQQ": 30}


def test_clean_window_hints_drops_junk_entries():
    assert streamrequests.clean_window_hints(
        {"xsp": 90, "bad": 0, "neg": -5, "float": 1.5, 7: 10, "ok": "40", None: 10}
    ) == {"XSP": 90}


def test_clean_window_hints_handles_none():
    assert streamrequests.clean_window_hints(None) == {}


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


def test_write_request_carries_expirations_normalized(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = streamrequests.write_request(
        "demo", ["SPX"], expirations={" spx ": ["2026-08-24", "2026-08-21", "2026-08-24"]}
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["expirations"] == {"SPX": ["2026-08-21", "2026-08-24"]}


def test_clean_expirations_drops_junk():
    assert streamrequests.clean_expirations(
        {
            "spx": ["2026-08-21", "not-a-date", "", None, 7],
            "": ["2026-08-21"],
            None: ["2026-08-21"],
            "qqq": "2026-08-21",  # a bare string is junk — the contract is a list
            "ndx": [],
        }
    ) == {"SPX": ["2026-08-21"]}


def test_clean_expirations_handles_none():
    assert streamrequests.clean_expirations(None) == {}


def test_union_expirations_unions_per_symbol_and_drops_past_dates(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    streamrequests.write_request("a", ["SPX"], expirations={"SPX": ["2026-08-21", "2026-08-14"]})
    streamrequests.write_request("b", ["SPX"], expirations={"SPX": ["2026-08-24"], "QQQ": ["2026-08-14"]})
    union = streamrequests.union_expirations(today=date(2026, 8, 17))
    # 2026-08-14 is past on the 17th for BOTH symbols; QQQ ends up with nothing and is omitted.
    assert union == {"SPX": ["2026-08-21", "2026-08-24"]}


def test_union_expirations_keeps_today_a_date_is_its_own_last_valid_day(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    streamrequests.write_request("a", ["SPX"], expirations={"SPX": ["2026-08-21"]})
    assert streamrequests.union_expirations(today=date(2026, 8, 21)) == {"SPX": ["2026-08-21"]}


def test_union_expirations_absent_field_means_none(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    # A pre-extension writer's file has no `expirations` key at all — the union must treat that as
    # "asked for nothing extra", not crash or invent a value.
    streamrequests.request_path("legacy").write_text(json.dumps({"symbols": ["SPX"]}), encoding="utf-8")
    assert streamrequests.union_expirations(today=date(2026, 8, 17)) == {}


def test_subscription_snapshot_excludes_expirations(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    streamrequests.write_request("a", ["SPX"], expirations={"SPX": ["2099-01-15"]})
    # Expirations are served dynamically (re-read every window pass) and roll forward weekly by
    # design — like legs/leg_sources they must never look like a reason to recycle the producer.
    assert streamrequests.subscription_snapshot() == {"symbols": ["SPX"], "window_hints": {}}
