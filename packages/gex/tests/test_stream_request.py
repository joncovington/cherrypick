"""gex declares its underlyings (plus the regime sampler's quote-only legs) to the streamer via a
stream-request file (best-effort, never fatal)."""

import json

from cherrypick.gex import regime, stream_request


def test_register_writes_deduped_upper_symbols(managed_home):
    stream_request.register({"symbols": ["spx", "qqq", "spx"]})
    path = managed_home / "state" / "stream_requests" / "gex.json"
    payload = json.loads(path.read_text())
    assert payload["symbols"] == ["QQQ", "SPX"]
    # The regime sampler's readings ride as quote-only legs, each with a bounded history request;
    # the reading-by-reading coverage assertion lives in test_regime_recorder.py.
    assert payload["legs"] == sorted(set(regime.READINGS.values()))
    assert payload["history_days"] == {leg: 270 for leg in payload["legs"]}
    assert payload["leg_sources"] == []
    assert payload["window_hints"] == {}
    assert payload["expirations"] == {}


def test_register_empty_symbols(managed_home):
    stream_request.register({})
    path = managed_home / "state" / "stream_requests" / "gex.json"
    assert json.loads(path.read_text())["symbols"] == []


def test_register_is_best_effort_never_raises(managed_home, monkeypatch):
    def _boom(_symbols):
        raise OSError("disk full")

    monkeypatch.setattr(stream_request, "write", _boom)
    stream_request.register({"symbols": ["SPX"]})  # must not propagate — the read keeps running
