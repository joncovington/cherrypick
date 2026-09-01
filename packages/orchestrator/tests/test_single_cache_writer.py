"""Exactly one process may write the shared stream cache.

The watchdog states this ("Exactly one producer is ever enabled at a time") and the config's own
`_note` states it, but nothing enforced it. Two producers on one SQLite cache is not a slow
system -- it is two writers interleaving quotes into the file every module trades off, which is the
failure the 2026-07-21 cutover existed to end.

MEIC's in-module ChainStreamer survives as the ROLLBACK path and must stay disabled while the
standalone producer is enabled. This turns that from a convention into something that fails.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "packages" / "orchestrator" / "config.example.json"


def producers(cfg: dict) -> list[str]:
    """Every stream-cache producer this config would start."""
    on: list[str] = []
    if (cfg.get("streamer") or {}).get("enabled"):
        on.append("streamer")
    meic = ((cfg.get("modules") or {}).get("meic") or {}).get("streamer") or {}
    if meic.get("enabled"):
        on.append("modules.meic.streamer")
    return on


def test_the_shipped_example_starts_exactly_one_producer():
    cfg = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert producers(cfg) == ["streamer"], (
        "the example must ship the standalone producer alone — MEIC's ChainStreamer is the "
        "rollback path and stays disabled"
    )


def test_two_enabled_producers_are_detected():
    """The check has to be able to FAIL, or it guards nothing."""
    both = {"streamer": {"enabled": True}, "modules": {"meic": {"streamer": {"enabled": True}}}}
    assert len(producers(both)) == 2


def test_the_rollback_path_alone_is_still_a_valid_config():
    """Rollback must remain reachable: one producer, just the other one."""
    rolled_back = {"streamer": {"enabled": False}, "modules": {"meic": {"streamer": {"enabled": True}}}}
    assert producers(rolled_back) == ["modules.meic.streamer"]


def test_no_producer_is_not_treated_as_a_producer():
    assert producers({}) == []


@pytest.mark.skipif(
    not (Path(os.path.expanduser("~")) / ".cherrypick" / "config.json").exists(),
    reason="no deployed config on this machine",
)
def test_the_deployed_config_starts_exactly_one_producer():
    """The invariant that actually matters is the one on this machine right now."""
    cfg = json.loads(
        (Path(os.path.expanduser("~")) / ".cherrypick" / "config.json").read_text(encoding="utf-8")
    )
    on = producers(cfg)
    assert len(on) == 1, f"expected exactly one stream-cache producer, found {on}"
