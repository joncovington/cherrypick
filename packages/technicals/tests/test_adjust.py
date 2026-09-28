"""The adjustment, on bars small enough to check by hand.

The method was settled against the vendor's own MSFT bars (753 of 753 closes to the cent); these
tests pin each piece of it so a change that breaks the match fails here, not in a chart.
"""

from __future__ import annotations

import math

from cherrypick.technicals.adjust import Bar, Dividend, Split, adjust


def _bars(*closes, start=1):
    return [Bar(f"2026-09-{start + i:02d}", c, c, c, c, 1000.0) for i, c in enumerate(closes)]


def test_no_events_is_the_raw_series():
    out = adjust(_bars(10.0, 11.0, 12.0))
    assert [b.close for b in out] == [10.0, 11.0, 12.0] and all(b.price_factor == 1.0 for b in out)


def test_a_two_for_one_split_halves_every_earlier_price_and_doubles_its_volume():
    out = adjust(_bars(100.0, 102.0, 51.0), splits=[Split("2026-09-03", 2, 1)])
    assert [b.close for b in out] == [50.0, 51.0, 51.0]
    assert [b.volume for b in out] == [2000.0, 2000.0, 1000.0]


def test_a_reverse_split_raises_earlier_prices():
    """Dolt's split table: KITT 2026-09-25, to 1 for 6."""
    out = adjust(_bars(1.0, 6.2), splits=[Split("2026-09-02", 1, 6)])
    assert out[0].close == 6.0 and out[1].close == 6.2


def test_a_dividend_scales_earlier_bars_by_one_minus_d_over_the_prior_close():
    out = adjust(_bars(100.0, 99.0), dividends=[Dividend("2026-09-02", 1.0)])
    assert math.isclose(out[0].close, 99.0) and out[1].close == 99.0
    assert math.isclose(out[0].price_factor, 0.99)


def test_events_compound_backwards():
    out = adjust(
        _bars(200.0, 200.0, 100.0, 100.0),
        splits=[Split("2026-09-03", 2, 1)],
        dividends=[Dividend("2026-09-04", 1.0)],
    )
    assert math.isclose(out[0].price_factor, 0.5 * 0.99) and math.isclose(out[2].price_factor, 0.99)


def test_an_ex_date_on_a_day_with_no_bar_still_applies_before_it():
    """A weekend or holiday ex-date sits between two sessions and applies to the earlier one."""
    bars = [Bar("2026-09-25", 100, 100, 100, 100, 1), Bar("2026-09-28", 99, 99, 99, 99, 1)]
    out = adjust(bars, dividends=[Dividend("2026-09-26", 1.0)])
    assert math.isclose(out[0].close, 99.0)


def test_events_outside_the_bars_and_bad_dividends_change_nothing():
    out = adjust(
        _bars(100.0, 100.0, start=10),
        splits=[Split("2026-09-01", 2, 1), Split("2026-09-30", 2, 1), Split("2026-09-11", 0, 1)],
        dividends=[Dividend("2026-09-11", 150.0), Dividend("2026-09-11", -1.0)],
    )
    assert [b.close for b in out] == [100.0, 100.0]


def test_prices_are_returned_unrounded():
    """Rounding is a display decision: a pre-split division can land on a half cent."""
    out = adjust(_bars(292.66, 73.0), splits=[Split("2026-09-02", 4, 1)])
    assert out[0].close == 292.66 / 4
