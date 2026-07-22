"""The suite EOD tasks (notify-eod / eod-insight) fire every calendar day, so they must skip
non-trading days rather than emit a flat weekend digest, push a "0 trades" notification, or burn a
paid Claude call. These assert the guard skips a weekend, runs a weekday, and honors --force.
"""

import types

import pytest

from cherrypick import cli

pytestmark = pytest.mark.unit

# 2026-07-18 is a Saturday, 2026-07-20 a Monday (see test_report / earnings fixtures).
SATURDAY = "2026-07-18"
MONDAY = "2026-07-20"


def test_skip_helper_blocks_weekend_runs_weekday_and_honors_force():
    assert cli._non_trading_day_skip(SATURDAY, force=False)["skipped"] == "not_a_trading_day"
    assert cli._non_trading_day_skip(MONDAY, force=False) is None  # a real session runs
    assert cli._non_trading_day_skip(SATURDAY, force=True) is None  # explicit override runs
    assert cli._non_trading_day_skip("not-a-date", force=False) is None  # unparseable: let cmd report


def _run(monkeypatch, cmd, day, force=False):
    """Invoke a cmd_* with a stub cfg/args, recording whether the real work ran and what was emitted."""
    called = {"work": False}
    emitted = {}
    monkeypatch.setattr(cli, "_emit", lambda obj: emitted.update(obj))
    # Both underlying entry points get stubbed so a weekday test can't do real I/O or a Claude call.
    monkeypatch.setattr(cli.eod_digest, "run",
                        lambda cfg, day: called.__setitem__("work", True) or {"digest": "x", "suite": {}})
    monkeypatch.setattr(cli.eod_insight, "run",
                        lambda cfg, day: called.__setitem__("work", True) or {"ok": True})
    monkeypatch.setattr(cli, "Notifier", lambda _n: types.SimpleNamespace(notify=lambda *a, **k: {}))
    args = types.SimpleNamespace(date=day, force=force)
    cmd({"notify": {}}, args)
    return called["work"], emitted


def test_notify_eod_skips_on_a_weekend(monkeypatch):
    worked, emitted = _run(monkeypatch, cli.cmd_notify_eod, SATURDAY)
    assert worked is False
    assert emitted["skipped"] == "not_a_trading_day"


def test_eod_insight_skips_on_a_weekend(monkeypatch):
    worked, emitted = _run(monkeypatch, cli.cmd_eod_insight, SATURDAY)
    assert worked is False
    assert emitted["skipped"] == "not_a_trading_day"


def test_notify_eod_runs_on_a_weekday(monkeypatch):
    worked, _ = _run(monkeypatch, cli.cmd_notify_eod, MONDAY)
    assert worked is True


def test_weekend_force_runs_anyway(monkeypatch):
    worked, _ = _run(monkeypatch, cli.cmd_eod_insight, SATURDAY, force=True)
    assert worked is True
