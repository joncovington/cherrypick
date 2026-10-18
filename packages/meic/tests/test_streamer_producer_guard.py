"""Unit tests for the one-producer runtime guard in streamer.py's full-streamer mode.

Two writers into the shared cache means two DXLink connections into one account -- these tests cover
`_standalone_producer_pid` (the liveness check against the standalone producer's colocated PID file)
and that `main()` refuses to start full-streamer mode while it reports a live pid. Nothing here touches
a real tastytrade session or DXLink connection.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import streamer


def test_standalone_producer_pid_none_when_no_pid_file(tmp_path, monkeypatch):
    monkeypatch.setattr(streamer, "_CACHE_DB", tmp_path / "stream_cache.db")
    assert streamer._standalone_producer_pid() is None


def test_standalone_producer_pid_reads_sibling_pid_file(tmp_path, monkeypatch):
    """The standalone producer's PID file lives at cache_path().parent / 'streamer.pid'
    (cherrypick.streamer.config.pid_path) -- the sibling of the shared cache this module points at."""
    cache = tmp_path / "stream_cache.db"
    monkeypatch.setattr(streamer, "_CACHE_DB", cache)
    (tmp_path / "streamer.pid").write_text(str(__import__("os").getpid()))
    assert streamer._standalone_producer_pid() == __import__("os").getpid()


def test_standalone_producer_pid_clears_stale_file_for_dead_pid(tmp_path, monkeypatch):
    cache = tmp_path / "stream_cache.db"
    monkeypatch.setattr(streamer, "_CACHE_DB", cache)
    pid_file = tmp_path / "streamer.pid"
    # A PID astronomically unlikely to be alive.
    pid_file.write_text("999999999")
    assert streamer._standalone_producer_pid() is None
    assert not pid_file.exists()


def test_main_refuses_full_streamer_mode_when_standalone_producer_is_live(monkeypatch, capsys):
    monkeypatch.setattr(streamer, "_running_pid", lambda: None)
    monkeypatch.setattr(streamer, "_standalone_producer_pid", lambda: 12345)
    monkeypatch.setattr(sys, "argv", ["streamer.py"])
    with pytest.raises(SystemExit) as exc_info:
        streamer.main()
    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Standalone streamer producer already running" in out
    assert "12345" in out


def test_the_sidecar_mode_is_gone(monkeypatch):
    """The REST/7699 sidecar was removed on 2026-10-18: `--sidecar` is no longer an option, so a
    service entry still naming it fails loudly rather than starting something half-there."""
    import pytest

    monkeypatch.setattr(sys, "argv", ["streamer.py", "--sidecar", "--status"])
    with pytest.raises(SystemExit) as exc_info:
        streamer.main()
    assert exc_info.value.code == 2  # argparse: unrecognised argument
    assert not hasattr(streamer, "_ApiHandler") and not hasattr(streamer, "_rest_poller")
