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
    # Normalized to (below, above) on the way to disk, so every consumer reads one shape.
    assert payload["window_hints"] == {"XSP": [90, 90], "QQQ": [30, 30]}


def test_write_request_carries_a_directional_window_hint(tmp_path, monkeypatch):
    """pmcc's deep-ITM long sits below spot and its short sits at it; a symmetric count bought an
    identical depth upward that no module could read."""
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = streamrequests.write_request("demo", ["TQQQ"], window_hints={"tqqq": {"down": 163, "up": 12}})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["window_hints"] == {"TQQQ": [163, 12]}


def test_clean_window_hints_drops_junk_entries():
    assert streamrequests.clean_window_hints(
        {"xsp": 90, "bad": 0, "neg": -5, "float": 1.5, 7: 10, "ok": "40", None: 10}
    ) == {"XSP": (90, 90)}


def test_clean_window_hints_reads_both_declared_forms():
    assert streamrequests.clean_window_hints(
        {"a": 30, "b": {"down": 163, "up": 12}, "c": [40, 5], "d": {"down": 40}, "e": {}}
    ) == {"A": (30, 30), "B": (163, 12), "C": (40, 5), "D": (40, 40)}


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


# --- the subscription budget ---------------------------------------------------------------
# Estimated from the same registry union the producer subscribes from, so the two cannot disagree
# about what was ASKED FOR. It will never equal the producer's own subscribed_symbols -- it cannot
# know how many strikes a chain actually lists -- and its job is a change in order of magnitude.


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return tmp_path


def _write(module, symbols, **payload):
    streamrequests.write_request(module, symbols, **payload)


def test_budget_estimate_scales_with_windows_and_width(home):
    _write("a", ["SPX"])
    one = streamrequests.estimate_subscriptions(default_strike_count=30)
    assert one["windows"] == 1
    assert one["by_symbol"]["SPX"]["subscriptions"] == (2 * 30 + 1) * 2 * 4

    # A second expiration doubles that symbol's cost; a hint widens the window itself.
    _write("a", ["SPX"], expirations={"SPX": ["2099-01-15"]}, window_hints={"SPX": 100})
    two = streamrequests.estimate_subscriptions(default_strike_count=30)
    assert two["by_symbol"]["SPX"]["windows"] == 2
    assert two["by_symbol"]["SPX"]["strike_count"] == 100  # max(default, hint)
    assert two["total"] == (2 * 100 + 1) * 2 * 4 * 2


def test_budget_takes_the_max_of_default_and_hint_never_the_hint_alone(home):
    _write("a", ["SPX"], window_hints={"SPX": 5})
    est = streamrequests.estimate_subscriptions(default_strike_count=30)
    assert est["by_symbol"]["SPX"]["strike_count"] == 30  # a narrower hint never shrinks the base


def test_a_directional_hint_costs_one_side_not_both(home):
    """The waste the 2026-08-24 incident exposed: asking for depth downward bought the mirror
    upward. A directional hint must cost strictly less than the symmetric one it replaces."""
    _write("a", ["TQQQ"], window_hints={"TQQQ": {"down": 163, "up": 12}})
    directional = streamrequests.estimate_subscriptions(default_strike_count=30)
    _write("a", ["TQQQ"], window_hints={"TQQQ": 163})
    symmetric = streamrequests.estimate_subscriptions(default_strike_count=30)

    # 30 is the floor on the shallow side, so the window is 163 below and 30 above.
    assert directional["by_symbol"]["TQQQ"]["span"] == [163, 30]
    assert directional["by_symbol"]["TQQQ"]["subscriptions"] == (163 + 30 + 1) * 2 * 4
    assert directional["total"] < symmetric["total"]


def test_a_directional_hint_is_unioned_per_side(home):
    """Two modules with opposite needs on one symbol must both be served — the wider single number
    winning would buy one of them depth it cannot use and leave the other short."""
    _write("deep", ["TQQQ"], window_hints={"TQQQ": {"down": 163, "up": 5}})
    _write("high", ["TQQQ"], window_hints={"TQQQ": {"down": 5, "up": 80}})
    assert streamrequests.union_window_hints() == {"TQQQ": (163, 80)}


def test_budget_status_flags_the_worst_symbol_not_just_the_total(home):
    """A total alone does not say which declaration to look at — the 2026-08-24 book was dominated
    by one symbol's widened window."""
    _write("a", ["SPX", "TQQQ"], window_hints={"TQQQ": 163})
    status = streamrequests.budget_status(default_strike_count=30, budget=1_000)
    assert status["over"] is True
    assert status["worst"]["symbol"] == "TQQQ"
    assert status["worst"]["hinted"] is True
    assert status["worst"]["subscriptions"] > status["by_symbol"]["SPX"]["subscriptions"]


def test_budget_status_is_quiet_under_the_ceiling(home):
    _write("a", ["SPX"])
    assert streamrequests.budget_status(default_strike_count=30, budget=100_000)["over"] is False


def test_budget_ignores_past_expirations(home):
    """A request nobody rewrote over a weekend must not be costed for dead dates — the same rule
    union_expirations already applies."""
    _write("a", ["SPX"], expirations={"SPX": ["2000-01-01", "2099-01-15"]})
    est = streamrequests.estimate_subscriptions(default_strike_count=30)
    assert est["by_symbol"]["SPX"]["windows"] == 2  # nearest + the live extra, not the dead one
