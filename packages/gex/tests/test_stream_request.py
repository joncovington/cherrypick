"""gex declares its underlyings to the streamer via a stream-request file (best-effort, never fatal)."""

import json

from cherrypick.gex import stream_request


def test_register_writes_deduped_upper_symbols(managed_home):
    stream_request.register({"symbols": ["spx", "qqq", "spx"]})
    path = managed_home / "state" / "stream_requests" / "gex.json"
    assert json.loads(path.read_text()) == {
        "symbols": ["QQQ", "SPX"],
        "legs": [],
        "leg_sources": [],
        "window_hints": {},
        "expirations": {},
        "history_days": {},
    }


def test_register_empty_symbols(managed_home):
    stream_request.register({})
    path = managed_home / "state" / "stream_requests" / "gex.json"
    assert json.loads(path.read_text())["symbols"] == []


def test_register_is_best_effort_never_raises(managed_home, monkeypatch):
    def _boom(_symbols):
        raise OSError("disk full")

    monkeypatch.setattr(stream_request, "write", _boom)
    stream_request.register({"symbols": ["SPX"]})  # must not propagate — the read keeps running
