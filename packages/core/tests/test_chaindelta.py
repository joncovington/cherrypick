"""core.chaindelta: delta from a chain's own mids, against a chain priced from known inputs."""

from __future__ import annotations

from datetime import datetime

import pytest

from cherrypick.core import bs, chaindelta

SPOT, VOL, YEARS = 6000.0, 0.15, 3 * 3600 / chaindelta.YEAR_SECONDS  # three hours to the close


def chain(spot=SPOT, vol=VOL, years=YEARS, strikes=range(5900, 6105, 5), spread=0.10):
    rows = []
    for k in strikes:
        for right in ("C", "P"):
            px = bs.price(spot, float(k), years, vol, right, 0.0)
            rows.append(
                {
                    "strike_price": float(k),
                    "option_type": right,
                    "bid": max(0.0, px - spread / 2),
                    "ask": px + spread / 2,
                    "delta": 0.5,
                }
            )
    return rows


def test_the_forward_comes_from_put_call_parity():
    assert chaindelta.forward_from_parity(chain()) == pytest.approx(SPOT, abs=0.01)


def test_computed_deltas_match_the_model_that_priced_the_chain():
    rows = chain()
    deltas = chaindelta.computed_deltas(rows, years=YEARS)
    for r, d in zip(rows, deltas, strict=True):
        truth = bs.delta(SPOT, r["strike_price"], YEARS, VOL, r["option_type"], 0.0)
        if 0.02 <= abs(truth) <= 0.98:  # deep wings carry the spread, and the spread is the error
            assert d == pytest.approx(truth, abs=0.01), r


def test_the_feed_value_is_kept_beside_the_computed_one():
    out = chaindelta.with_computed_delta(chain(), years=YEARS)
    assert all(r["delta_stream"] == 0.5 for r in out)
    assert all(r["delta"] != 0.5 for r in out if r["delta"] is not None)


def test_no_parity_pair_or_no_time_left_computes_nothing_never_the_feed():
    calls_only = [r for r in chain() if r["option_type"] == "C"]
    assert set(chaindelta.computed_deltas(calls_only, years=YEARS)) == {None}
    assert set(chaindelta.computed_deltas(chain(), years=1.0 / chaindelta.YEAR_SECONDS)) == {None}


def test_a_row_without_a_usable_quote_gets_none():
    rows = chain()
    rows[0] = {**rows[0], "bid": None}
    assert chaindelta.computed_deltas(rows, years=YEARS)[0] is None


def test_years_to_the_close_is_calendar_time_in_et():
    noon = datetime(2026, 10, 27, 12, 0, tzinfo=chaindelta.ET)
    assert chaindelta.years_to_close(noon) * chaindelta.YEAR_SECONDS == pytest.approx(4 * 3600)
    late = datetime(2026, 10, 27, 16, 30, tzinfo=chaindelta.ET)
    assert chaindelta.years_to_close(late) == 0.0
