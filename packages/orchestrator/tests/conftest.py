"""Pytest fixtures + path setup for cherrypick tests.

Unit/reliability lane only: no broker, no network, no OS scheduler. Live/Windows-integration tests
belong behind the `live` / `windows` markers (see pytest.ini).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the src-layout `cherrypick` package importable regardless of pytest's cwd (no install needed).
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402


class FakeNotifier:
    """Records notifications instead of sending them."""

    def __init__(self):
        self.sent: list[dict] = []

    def notify(self, level, key, title, message):
        self.sent.append({"level": level, "key": key, "title": title, "message": message})
        return {"log": {"ok": True}}


@pytest.fixture
def fake_notifier():
    return FakeNotifier()


@pytest.fixture(autouse=True)
def isolated_state(request, tmp_path, monkeypatch):
    """Point every test's state writes at a temp dir instead of the developer's `~/.cherrypick/state`.

    The unit lane is supposed to touch nothing real, but nothing enforced it for state: any test that
    reached a code path which *writes* state wrote it into the running developer's live suite. That
    stopped being theoretical with the service launch stamps — a `_check_streamer_health` test left a
    `service-streamer.launch.json` behind holding the hash of a tmp_path fixture, which a subsequent
    real watchdog tick would have read as "config changed" and recycled the live streamer over.

    Autouse, because the hazard is the tests that DON'T know they write state; a test that wants a
    specific state dir still overrides these itself. `@pytest.mark.real_state` opts out — for the one
    test that asserts where the real paths resolve to, which is the thing this fixture moves.

    Logs are moved for the same reason. Until 2026-09-13 only state was isolated, and every run of
    the supervisor tests appended three lines to the developer's live `logs/supervisor.log` — a
    config reload with a tmp_path mtime and a "registry row dropped" for a fixture job — which,
    interleaved with the real daemon's lines, read as the daemon reloading a config nobody had
    edited and cost a diagnosis on the night it mattered.
    """
    from cherrypick.orchestrator import config as cfgmod

    if request.node.get_closest_marker("real_state"):
        return cfgmod.STATE_DIR

    state = tmp_path / "cherrypick-state"
    state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cfgmod, "STATE_DIR", state, raising=False)
    monkeypatch.setattr(cfgmod, "state_file", lambda name: state / name)
    logs = tmp_path / "cherrypick-logs"
    logs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cfgmod, "LOGS_DIR", logs, raising=False)
    monkeypatch.setattr(cfgmod, "log_file", lambda name: logs / name)
    return state
