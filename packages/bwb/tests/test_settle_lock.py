"""A live settlement takes the tick's lock: a tick and a settle writing the same rows is P&L corruption."""

from cherrypick.core import looplock

from cherrypick.bwb import live_loop


def _patch_settles(monkeypatch):
    calls = []
    monkeypatch.setattr(live_loop, "settle_overdue", lambda *a, **k: calls.append("overdue") or {"ok": True})
    monkeypatch.setattr(live_loop, "run_settle_live", lambda *a, **k: calls.append("settle") or {"ok": True})
    return calls


def test_a_settle_refuses_while_a_live_tick_holds_the_lock(monkeypatch, capsys):
    calls = _patch_settles(monkeypatch)
    lock = live_loop._once_lock_path()
    assert looplock.acquire(lock, 180, alive=looplock.pid_alive)
    try:
        assert live_loop.main(["--settle-overdue"]) == 1
        assert live_loop.main(["--settle", "--price", "6000", "--date", "2026-10-23"]) == 1
    finally:
        looplock.release(lock)
    assert calls == []
    assert "a live tick is running" in capsys.readouterr().out


def test_a_settle_runs_and_releases_the_lock_when_it_is_free(monkeypatch):
    calls = _patch_settles(monkeypatch)
    assert live_loop.main(["--settle-overdue"]) == 0
    assert live_loop.main(["--settle", "--price", "6000", "--date", "2026-10-23"]) == 0
    assert calls == ["overdue", "settle"]
    lock = live_loop._once_lock_path()
    assert looplock.acquire(lock, 180, alive=looplock.pid_alive)  # released after each settle
    looplock.release(lock)
