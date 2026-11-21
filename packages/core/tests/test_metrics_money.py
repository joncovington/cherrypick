"""`core.metrics.money`: a group's money laid out to add up, read from `core.ledgers`' split of each
record's total cost."""

from __future__ import annotations

from cherrypick.core.metrics import money


def _r(gross, fees, settlement, slippage, basis="cost", net=None):
    charged = slippage if basis == "cost" and slippage is not None else 0.0
    return {
        "gross_pnl": gross,
        "fees": fees,
        "settlement": settlement,
        "slippage": slippage,
        "slippage_basis": basis,
        "net_pnl": net if net is not None else gross - (fees or 0) - (settlement or 0) - charged,
    }


def test_the_columns_add_up_to_the_net():
    m = money([_r(100.0, 3.0, 1.0, 2.0), _r(-50.0, 3.0, 0.0, 4.0)])
    assert (m["trades"], m["unrecorded"]) == (2, 0)
    assert (m["gross"], m["fees"], m["settlement"], m["slippage"], m["net"]) == (50.0, 6.0, 1.0, 6.0, 37.0)
    assert round(m["gross"] - m["fees"] - m["settlement"] - m["slippage"] - m["net"], 2) == 0


def test_slippage_inside_the_fill_is_no_cost_column():
    # MEIC and flies concede slippage in the fill price: it is in gross, never a zero cost here.
    m = money([_r(100.0, 3.0, 0.0, 2.0, basis="in_gross")])
    assert m["slippage"] is None and m["slippage_inside_fill"] is True
    assert m["net"] == 97.0 and round(m["gross"] - m["fees"] - m["settlement"] - m["net"], 2) == 0


def test_a_trade_whose_cost_was_never_recorded_is_counted_and_left_out_of_the_sums():
    m = money(
        [
            _r(100.0, 3.0, 0.0, 2.0),
            {"gross_pnl": 40.0, "fees": None, "net_pnl": 40.0, "slippage_basis": "cost"},
        ]
    )
    assert (m["trades"], m["unrecorded"]) == (2, 1)
    assert (m["gross"], m["net"]) == (100.0, 95.0)


def test_an_open_record_is_no_trade():
    assert money([{"gross_pnl": None, "fees": None, "net_pnl": None}])["trades"] == 0
