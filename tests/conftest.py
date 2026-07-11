"""Pytest fixtures + path setup for Cherrypick tests.

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
