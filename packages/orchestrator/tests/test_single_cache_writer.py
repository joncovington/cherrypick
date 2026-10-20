"""Exactly one process may write the shared stream cache: the standalone producer.

Two producers on one SQLite cache is not a slow system -- it is two writers interleaving quotes into
the file every module trades off, which is the failure the 2026-07-21 cutover existed to end.

MEIC's in-module streamer survived that cutover as a rollback path until 2026-10-18, when it was
removed. Install and the watchdog no longer start a module-owned streamer, and doctor warns on an
enabled `modules.<m>.streamer` block left in a kept config. These tests hold the shipped example and
this machine's config to the one producer, and show the check can still fail.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cherrypick.orchestrator import doctor

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "packages" / "orchestrator" / "config.example.json"


def producers(cfg: dict) -> list[str]:
    """Every stream-cache producer this config declares enabled, module blocks included."""
    on: list[str] = []
    if (cfg.get("streamer") or {}).get("enabled"):
        on.append("streamer")
    for name, mcfg in (cfg.get("modules") or {}).items():
        if ((mcfg or {}).get("streamer") or {}).get("enabled"):
            on.append(f"modules.{name}.streamer")
    return on


def test_the_shipped_example_starts_exactly_one_producer():
    cfg = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert producers(cfg) == ["streamer"]


def test_the_shipped_example_has_no_module_streamer_block():
    """Not even a disabled one: a block in the template is an invitation to turn it on."""
    cfg = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    carrying = [name for name, mcfg in (cfg.get("modules") or {}).items() if "streamer" in (mcfg or {})]
    assert carrying == []


def test_a_module_block_is_counted_as_a_second_producer():
    """The check has to be able to FAIL, or it guards nothing."""
    both = {"streamer": {"enabled": True}, "modules": {"meic": {"streamer": {"enabled": True}}}}
    assert producers(both) == ["streamer", "modules.meic.streamer"]


def test_no_producer_is_not_treated_as_a_producer():
    assert producers({}) == []


def test_the_engine_refuses_the_shared_cache_to_anyone_but_the_producer(tmp_path, monkeypatch):
    """The config checks above see only what the orchestrator starts. A process started by hand
    (gex's `run.py stream`, which took the shared path by default until 2026-10-19) never passes
    through them, so the engine itself refuses the shared cache unless told it is the producer."""
    from cherrypick.core import streamcache
    from cherrypick.core.streamer import ChainStreamer

    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    shared = streamcache.cache_path()
    with pytest.raises(ValueError, match="only packages/streamer writes"):
        ChainStreamer(session_factory=lambda: None, db_path=shared, symbols=["SPX"])
    ChainStreamer(session_factory=lambda: None, db_path=shared, symbols=["SPX"], producer=True)
    ChainStreamer(session_factory=lambda: None, db_path=tmp_path / "own.db", symbols=["SPX"])


def test_the_producer_says_it_is_the_producer():
    """The daemon is the one constructor of the engine that claims the shared cache."""
    hits = []
    for p in (REPO / "packages").rglob("*.py"):
        if {"tests", "node_modules", "build"} & set(p.parts):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "ChainStreamer(" in text and "producer=True" in text:
            hits.append(p.relative_to(REPO).as_posix())
    assert hits == ["packages/streamer/src/cherrypick/streamer/daemon.py"]


def test_doctor_warns_on_an_enabled_module_block_and_ignores_a_disabled_one():
    """The old rollback recipe (module block on, standalone off) would now start nothing at all."""
    warned = doctor.module_streamer_checks("meic", {"streamer": {"enabled": True}})
    assert [c.status for c in warned] == [doctor.WARN]
    assert "modules.meic.streamer" in warned[0].detail
    assert doctor.module_streamer_checks("meic", {"streamer": {"enabled": False}}) == []
    assert doctor.module_streamer_checks("meic", {}) == []


@pytest.mark.skipif(
    not (Path(os.path.expanduser("~")) / ".cherrypick" / "config.json").exists(),
    reason="no deployed config on this machine",
)
def test_the_deployed_config_starts_exactly_one_producer():
    """The invariant that actually matters is the one on this machine right now."""
    cfg = json.loads(
        (Path(os.path.expanduser("~")) / ".cherrypick" / "config.json").read_text(encoding="utf-8")
    )
    assert producers(cfg) == ["streamer"], f"expected only the standalone producer, found {producers(cfg)}"
