"""The consumer-side stream-request writer: path convention, cleaning, atomicity, payload shape."""

from __future__ import annotations

import json
from datetime import date

import pytest

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
        "history_days": {},
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


def test_write_request_carries_history_days(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = streamrequests.write_request("demo", ["TNA"], history_days={"tna": 42, "bad": 0, 7: 5})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["history_days"] == {"TNA": 42}


def test_union_history_days_takes_max_per_symbol(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    streamrequests.write_request("pmcc", ["TNA"], history_days={"TNA": 42})
    streamrequests.write_request("other", ["TNA", "QQQ"], history_days={"TNA": 30, "QQQ": 20})
    assert streamrequests.union_history_days() == {"TNA": 42, "QQQ": 20}


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


# --------------------------------------------------------------------------- leg_source / validation


def test_leg_source_builds_the_producer_contract():
    src = streamrequests.leg_source("/tmp/x.db", "SELECT streamer_symbol FROM legs")
    assert src == {"db": "/tmp/x.db", "query": "SELECT streamer_symbol FROM legs"}


def test_leg_source_coerces_a_path_to_str():
    from pathlib import Path

    src = streamrequests.leg_source(Path("/tmp/x.db"), "SELECT 1")
    assert isinstance(src["db"], str)


@pytest.mark.parametrize(
    "db, query",
    [("", "SELECT 1"), ("/tmp/x.db", ""), ("/tmp/x.db", "   "), ("/tmp/x.db", None)],
)
def test_leg_source_refuses_empty_parts(db, query):
    with pytest.raises(ValueError):
        streamrequests.leg_source(db, query)


def test_a_mistyped_leg_source_key_raises_instead_of_subscribing_nothing(tmp_path, monkeypatch):
    """The silent failure this validation exists to prevent.

    The producer skips any spec without string db/query keys, so {"database":..., "sql":...} used to
    write a request file that looked entirely healthy and subscribed NO legs -- every open position
    quietly stops being quoted, with no error anywhere. Failing on the write side puts the error at
    the mistake.
    """
    monkeypatch.setattr(streamrequests, "requests_dir", lambda: tmp_path)
    with pytest.raises(ValueError) as excinfo:
        streamrequests.write_request("mod", ["SPX"], leg_sources=[{"database": "x", "sql": "SELECT 1"}])
    assert "db" in str(excinfo.value) and "query" in str(excinfo.value)
    assert not (tmp_path / "mod.json").exists(), "no request file should be written"


def test_write_request_accepts_specs_built_by_leg_source(tmp_path, monkeypatch):
    monkeypatch.setattr(streamrequests, "requests_dir", lambda: tmp_path)
    path = streamrequests.write_request(
        "mod", ["SPX"], leg_sources=[streamrequests.leg_source("/tmp/x.db", "SELECT 1")]
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["leg_sources"] == [{"db": "/tmp/x.db", "query": "SELECT 1"}]


# --------------------------------------------------------------------------- register_best_effort


def test_register_best_effort_returns_the_write_result():
    assert streamrequests.register_best_effort(lambda a, b=None: (a, b), 1, b=2) == (1, 2)


def test_register_best_effort_swallows_and_logs(caplog):
    """The contract seven modules each stated in prose: a failed registration must never be fatal.

    A loop that refused to run because it could not write a request file would trade a
    data-quality problem for an outage.
    """
    def boom():
        raise OSError("disk gone")

    with caplog.at_level("WARNING"):
        assert streamrequests.register_best_effort(boom) is None
    assert "disk gone" in caplog.text


def test_register_best_effort_swallows_even_a_bare_exception():
    def boom():
        raise RuntimeError("anything at all")

    assert streamrequests.register_best_effort(boom) is None
