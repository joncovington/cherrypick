"""Each account's legs are read from the cache of the producer that quotes it (`stream_instances`):
its login's own instance when it has one, else main's -- and a dead instance leaves its legs unpriced
rather than borrowing main's."""

from __future__ import annotations

import pytest

from cherrypick.orchestrator import managesnap, positions

pytestmark = pytest.mark.unit


def _snapshot(instances):
    return {
        "metrics": {"SPY": {}},
        "stream_instances": instances,
        "positions": {
            "accounts": [
                {"login": "main", "underlyings": [{"underlying": "AAA", "legs": [{"symbol": "AAA"}]}]},
                {"login": "margin", "underlyings": [{"underlying": "BBB", "legs": [{"symbol": "BBB"}]}]},
            ]
        },
    }


def test_without_instances_everything_reads_main():
    assert managesnap.overlay_routes(_snapshot([])) == {"main": ["AAA", "BBB", "SPY"]}


def test_an_instance_reads_its_own_login_s_legs():
    assert managesnap.overlay_routes(_snapshot(["margin"])) == {"main": ["AAA", "SPY"], "margin": ["BBB"]}
    assert managesnap.overlay_symbols(_snapshot(["margin"])) == ["AAA", "BBB", "SPY"]


def test_positions_marks_an_instance_account_from_its_own_cache_first(monkeypatch):
    calls = []

    def cached(symbols, instance=None):
        calls.append((instance, list(symbols)))
        return {s: {"mid": 1.0} for s in symbols if instance == "margin"}

    entries = [
        {"login": "main", "account": "****1111", "open_positions": [{"symbol": "AAA", "quantity": 1}]},
        {"login": "margin", "account": "****2222", "open_positions": [{"symbol": "BBB", "quantity": 1}]},
    ]
    monkeypatch.setattr(
        positions.reconcile,
        "_query_broker",
        lambda cfg, forced: {"reachable": True, "module": "meic", "accounts": entries},
    )
    monkeypatch.setattr(positions.cfgmod, "enabled_modules", lambda cfg: {"meic": {}})
    monkeypatch.setattr(positions.cfgmod, "module_root", lambda mcfg, name: ".")
    monkeypatch.setattr(positions.cfgmod, "streamer_instances", lambda cfg: ["margin"])
    monkeypatch.setattr(positions, "_cached_marks", cached)
    monkeypatch.setattr(positions, "_live_marks", lambda root, syms, tool: {})
    positions.run({})
    assert calls == [("margin", ["BBB"]), (None, ["AAA"])]
