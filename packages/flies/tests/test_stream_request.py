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
    assert json.loads(path.read_text()) == {
        "symbols": ["SPX", "XSP"],
        "legs": [],
        "leg_sources": [],
        "window_hints": {},
    }


def test_register_carries_window_hints(managed_home):
    stream_request.register({"symbols": ["XSP"]}, window_hints={"XSP": 90})
    path = managed_home / "state" / "stream_requests" / "flies.json"
    assert json.loads(path.read_text())["window_hints"] == {"XSP": 90}


def test_live_register_writes_a_separate_file(managed_home):
    stream_request.register({"symbols": ["XSP"]}, window_hints={"XSP": 40})
    stream_request.register({"symbols": ["XSP"]}, window_hints={"XSP": 90}, live=True)

    paper = json.loads((managed_home / "state" / "stream_requests" / "flies.json").read_text())
    live = json.loads((managed_home / "state" / "stream_requests" / "flies-live.json").read_text())
    assert paper["window_hints"] == {"XSP": 40}
    assert live["window_hints"] == {"XSP": 90}


def test_register_empty_symbols(managed_home):
    stream_request.register({})
    path = managed_home / "state" / "stream_requests" / "flies.json"
    assert json.loads(path.read_text())["symbols"] == []


def test_register_is_best_effort_never_raises(managed_home, monkeypatch):
    def _boom(_symbols, window_hints=None, live=False):
        raise OSError("disk full")

    monkeypatch.setattr(stream_request, "write", _boom)
    stream_request.register({"symbols": ["SPX"]})  # must not propagate — the loop keeps running
