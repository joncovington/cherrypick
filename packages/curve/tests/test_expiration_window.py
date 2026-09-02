"""The DTE band must not leave the module with nothing to trade.

`target_expiration` selects a MONTHLY expiration, and consecutive monthlies sit 28-35 days apart. So
a band narrower than that gap has holes: just after one expiry rolls off, the next is under the
floor while the one behind it is over the ceiling, and nothing qualifies at all.

That is not hypothetical. The shipped 25-50 band left 42 of 251 trading days in 2026 with no
eligible expiration -- 17%, recurring every single month, worst run seven consecutive sessions --
and this module was registered on 2026-08-24 straight into one of those runs, which is why it had
never opened a position across its first sessions.

Driven off the DEPLOYED example rather than a literal, so a future narrowing of the band fails here
instead of silently costing sessions.
"""

import datetime as dt
import json
import pathlib

import pytest
from cherrypick.core import calendar as cal

from cherrypick.curve import clock

YEAR = 2026
# A whole month of consecutive dead sessions would be indefensible; the point of the guard is that
# the band covers the monthly gap, and anything above zero means it does not.
MAX_TOLERATED_DEAD_DAYS = 0


def _example_defaults() -> dict:
    path = pathlib.Path(__file__).resolve().parents[1] / "config.example.json"
    return json.loads(path.read_text(encoding="utf-8"))["defaults"]


def _dead_days(defaults: dict) -> list[dt.date]:
    out = []
    day = dt.date(YEAR, 1, 1)
    while day.year == YEAR:
        if cal.is_trading_day(day) and clock.target_expiration(day, defaults) is None:
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def test_the_shipped_band_leaves_no_session_without_an_expiration():
    dead = _dead_days(_example_defaults())
    assert len(dead) <= MAX_TOLERATED_DEAD_DAYS, (
        f"{len(dead)} trading days in {YEAR} have no eligible expiration "
        f"(first: {dead[0] if dead else '-'}) — the band does not cover the monthly gap"
    )


def test_the_guard_catches_a_band_narrower_than_the_monthly_gap():
    """The guard has to be able to fail, so prove it on the band that actually shipped."""
    narrow = {**_example_defaults(), "dte_min": 25, "dte_max": 50}
    dead = _dead_days(narrow)
    assert len(dead) > 30, "the 25-50 band left 42 dead days in 2026; the guard must see that"


def test_a_chosen_expiration_always_sits_inside_the_band():
    defaults = _example_defaults()
    for offset in range(0, 365, 7):
        day = dt.date(YEAR, 1, 1) + dt.timedelta(days=offset)
        if not cal.is_trading_day(day):
            continue
        plan = clock.target_expiration(day, defaults)
        assert plan is not None
        assert defaults["dte_min"] <= plan["dte"] <= defaults["dte_max"], (
            f"{day}: chose {plan} outside [{defaults['dte_min']}, {defaults['dte_max']}]"
        )


def test_the_deployed_config_matches_the_example_band():
    """A fresh install and this machine must agree about the band, or the guard above protects a
    file nobody runs."""
    deployed = pathlib.Path.home() / ".cherrypick/config/curve.json"
    if not deployed.exists():
        pytest.skip("no deployed curve config on this machine")
    live = json.loads(deployed.read_text(encoding="utf-8"))["defaults"]
    ex = _example_defaults()
    assert (live["dte_min"], live["dte_max"]) == (ex["dte_min"], ex["dte_max"])
