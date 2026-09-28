"""The earnings-moves fetch: which expiration prices an event, and the row it writes.

Here beside overview's tests because the morning pack is what reads the file; the script's module
level needs only the standard library, and `move_row` only cherrypick-core.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_earnings_moves.py"


def _module():
    spec = importlib.util.spec_from_file_location("fetch_earnings_moves", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fem = _module()
EXPS = [date(2026, 9, 30), date(2026, 10, 2), date(2026, 10, 9)]


def test_an_after_close_print_is_priced_on_an_expiration_after_the_report_day():
    """A same-day expiration expires before the market can trade the print."""
    assert fem.front_expiration(EXPS, date(2026, 9, 30), "After market close") == date(2026, 10, 2)


def test_a_before_open_print_is_priced_on_the_report_days_own_expiration():
    assert fem.front_expiration(EXPS, date(2026, 9, 30), "Before market open") == date(2026, 9, 30)


def test_no_expiration_after_the_event_is_none():
    assert fem.front_expiration(EXPS, date(2026, 10, 9), "After market close") is None


def test_the_row_uses_the_suites_one_expected_move_definition():
    row = fem.move_row(
        "MU", date(2026, 9, 30), "After market close", date(2026, 10, 2), 1082.28, 1082.5,
        {"bid": 46.0, "ask": 47.0}, {"bid": 46.5, "ask": 47.5},
    )  # fmt: skip
    assert row["straddle"] == 93.5 and row["expected_move"] == round(0.85 * 93.5, 2)
    assert row["expected_move_pct"] == round(100 * 0.85 * 93.5 / 1082.28, 2)


def test_an_unpriceable_name_is_listed_with_its_reason_never_dropped():
    row = fem.move_row(
        "X", date(2026, 9, 30), None, date(2026, 10, 2), 10.0, 10.0, {"bid": 0, "ask": 0.5}, None
    )
    assert row["expected_move"] is None and row["reason"] == "straddle not two-sided"


def test_the_copy_matches_the_earnings_modules_own_rule():
    """Copied because the overview CI job does not install the earnings package; pinned equal here
    wherever it is installed."""
    scanner = pytest.importorskip("cherrypick.earnings.scanner")
    for when in ("After market close", "Before market open", None):
        for d in (date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 2)):
            theirs, _ = scanner.select_front_expiration(EXPS, d, when)
            assert fem.front_expiration(EXPS, d, when) == theirs, (d, when)
