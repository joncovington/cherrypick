"""Session-wide test setup: a managed home that is never real."""

import pytest


@pytest.fixture(autouse=True)
def managed_home(tmp_path, monkeypatch):
    """Point `CHERRYPICK_HOME` at a temporary directory for every test in the suite.

    Autouse, and deliberately not something a test opts into. This was a per-test `home` fixture, and
    on 2026-07-20 three settlement tests that did not request it ran while the live loop was in
    session: they wrote a real `paper-eod-2026-07-20.md` into `~/.cherrypick/logs/flies`, the loop
    read the day as already finished, and the session never settled. Eleven open positions sat under
    a report describing a fixture at spot 5998.

    The lesson is not "remember the fixture" — it is that opting in to isolation puts the real
    trading home one forgotten argument away. `FLIES_DB_PATH` and `FLIES_CONFIG` are cleared for the
    same reason: an operator's shell may carry them, and they resolve to real files.
    """
    home = tmp_path / "cherrypick-home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(home))
    monkeypatch.delenv("FLIES_DB_PATH", raising=False)
    monkeypatch.delenv("FLIES_CONFIG", raising=False)
    return home


def _pin_today_off_early_closes() -> None:
    """The live tests date their fixtures to the real today (`DAY` in test_live_scaffold, and
    run_watch and run_settle_live resolve "today" themselves through `provider.now_et`), and the entry
    gate refuses an NYSE early close outright. So on a real half day (2026-11-27) every live test
    opened nothing and failed, and so did each guard mutant whose clean run uses them. When the real
    today is an early close, "today" is the trading day before it, for the whole run. This runs when
    the conftest is imported, before a test module reads `DAY`; a test that dates its snapshot to an
    early close on purpose still meets the gate exactly, since the gate is not touched."""
    from cherrypick.core import calendar as cal

    from cherrypick.flies import provider

    real = provider.now_et
    if not cal.is_early_close(real().date()):
        return

    def now_et():
        now = real()
        day = now.date()
        while cal.is_early_close(day):
            day = cal.previous_trading_day(day)
        return now.replace(year=day.year, month=day.month, day=day.day)

    provider.now_et = now_et


_pin_today_off_early_closes()


@pytest.fixture(autouse=True)
def todays_date_is_not_a_quarter_end(monkeypatch):
    """The live tests date their fixtures to the real today (run_watch and run_settle_live resolve
    "today" themselves), so on a real quarter end the live gate refused every one of them -- the
    suite went red on 2026-09-30, the day the gate landed. The real today is pinned off; a test that
    dates its snapshot to a quarter end still meets the gate exactly."""
    from cherrypick.flies import live_loop, provider

    today = provider.now_et().date().isoformat()
    real = live_loop._quarter_end
    monkeypatch.setattr(live_loop, "_quarter_end", lambda day: str(day) != today and real(day))
