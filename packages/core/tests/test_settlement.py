"""The share-leg arithmetic calendars and pmcc must agree on to the cent."""

from __future__ import annotations

import pytest

from cherrypick.core import settlement


def test_long_shares_earn_the_rise():
    assert settlement.share_pnl("long", 100, 600.0, 610.0) == pytest.approx(1000.0)


def test_long_shares_lose_the_fall():
    assert settlement.share_pnl("long", 100, 600.0, 590.0) == pytest.approx(-1000.0)


def test_short_shares_earn_the_fall():
    assert settlement.share_pnl("short", 100, 600.0, 590.0) == pytest.approx(1000.0)


def test_short_shares_lose_the_rise():
    assert settlement.share_pnl("short", 100, 600.0, 610.0) == pytest.approx(-1000.0)


def test_a_flat_disposal_is_zero_both_ways():
    assert settlement.share_pnl("long", 100, 600.0, 600.0) == 0.0
    assert settlement.share_pnl("short", 100, 600.0, 600.0) == 0.0


def test_it_is_booked_to_the_cent():
    """Rounded because it is booked, not intermediate -- calendars validates its derivation against
    the real books to the cent, so an unrounded value would read there as a validation failure."""
    assert settlement.share_pnl("long", 3, 100.0, 100.3333) == 1.0


def test_the_physical_decomposition_equals_the_raw_cash_flow():
    """Physical settlement is cash settlement PLUS a share leg, which is what lets one derivation
    and one validation serve both styles.

    Short put, strike K, credit E, settles at S_f, shares disposed at S_m:
        option leg  E - (K - S_f)
        share leg   S_m - S_f      (long shares, basis S_f)
        total       E - K + S_m    -- take E, buy at K, sell at S_m
    Basing the shares at K instead would double-count it.
    """
    K, E, S_f, S_m, shares = 600.0, 5.0, 590.0, 595.0, 100
    option_leg = (E - (K - S_f)) * shares
    share_leg = settlement.share_pnl("long", shares, S_f, S_m)
    assert option_leg + share_leg == pytest.approx((E - K + S_m) * shares)
