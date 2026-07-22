"""flies declares its underlyings to the streamer via a stream-request file (best-effort, never fatal)."""

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import stream_request  # noqa: E402


def test_register_writes_deduped_upper_symbols(managed_home):
    stream_request.register({"symbols": ["spx", " xsp ", "spx"]})
    path = managed_home / "state" / "stream_requests" / "flies.json"
    assert json.loads(path.read_text()) == {"symbols": ["SPX", "XSP"], "legs": [], "leg_sources": []}


def test_register_empty_symbols(managed_home):
    stream_request.register({})
    path = managed_home / "state" / "stream_requests" / "flies.json"
    assert json.loads(path.read_text())["symbols"] == []


def test_register_is_best_effort_never_raises(managed_home, monkeypatch):
    def _boom(_symbols):
        raise OSError("disk full")

    monkeypatch.setattr(stream_request, "write", _boom)
    stream_request.register({"symbols": ["SPX"]})  # must not propagate — the loop keeps running
