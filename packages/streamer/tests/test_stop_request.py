"""`--stop` asks first and kills second, and says which one ended the producer.

The kill was the whole mechanism and it records nothing on this platform: win32
`os.kill(pid, SIGTERM)` is `TerminateProcess`, so the engine's handler and `run_daemon`'s `finally`
both go unrun. The stop request is a file the engine polls, so the process logs its own exit -- and
"asked and it complied" versus "asked and had to kill it" are different facts about the producer's
health, which is why the result names the path taken.
"""

import json

from cherrypick.streamer import config as _config
from cherrypick.streamer import daemon


def _cfg(tmp_path):
    return {"streamer": {"cache_db": str(tmp_path / "stream_cache.db")}}


def test_a_compliant_daemon_is_stopped_by_the_file_and_never_signalled(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    pids = iter([4242, None])  # alive on the first check, gone on the second
    monkeypatch.setattr(daemon, "running_pid", lambda _c: next(pids))
    killed = []
    monkeypatch.setattr(daemon.os, "kill", lambda *a: killed.append(a))

    result = daemon.stop(cfg, reason="test", wait_s=5)

    assert result["ok"] and result["how"] == "stop_file"
    assert killed == [], "a daemon that complied must never be terminated on top"
    written = json.loads((_config.stop_path(cfg)).read_text(encoding="utf-8"))
    assert written["reason"] == "test" and "requested_at" in written


def test_a_wedged_daemon_still_gets_killed(tmp_path, monkeypatch):
    """The file is the polite path, not the only one. A process too stuck to poll must still die,
    or a stalled producer would become unkillable by its own tooling."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(daemon, "running_pid", lambda _c: 4242)  # never exits
    killed = []
    monkeypatch.setattr(daemon.os, "kill", lambda *a: killed.append(a))

    result = daemon.stop(cfg, reason="test", wait_s=0.2)

    assert result["ok"] and result["how"] == "signal"
    assert len(killed) == 1
    assert not _config.stop_path(cfg).exists(), (
        "the request is cleared after a kill -- otherwise the NEXT process starts up, reads it, "
        "and shuts itself down as though it had been asked"
    )


def test_stopping_a_daemon_that_is_not_running_writes_no_request(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(daemon, "running_pid", lambda _c: None)

    result = daemon.stop(cfg)

    assert result["ok"] is False
    assert not _config.stop_path(cfg).exists()


def test_the_stop_file_sits_beside_the_pid_file(tmp_path):
    """Same reasoning the PID file already carries: one canonical producer, one cache, one place to
    look for both."""
    cfg = _cfg(tmp_path)
    assert _config.stop_path(cfg).parent == _config.pid_path(cfg).parent
    assert _config.stop_path(cfg) != _config.pid_path(cfg)
