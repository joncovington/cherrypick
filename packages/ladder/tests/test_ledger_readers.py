"""The suite's normalised readers (`cherrypick.core.ledgers`, schema `ladder_lot`) against a ledger the loop
itself wrote: a lot bought, and a lot bought and sold."""

from __future__ import annotations

import pytest
from cherrypick.core import excursions, ledgers, trace
from test_paper_loop import DAY, NEXT, _only, _tick

from cherrypick.ladder import db


def _buy_and_sell(config, ledger, cache):
    cfg = _only(config, "once")
    cache.summary("TQQQ", DAY, prev_day_close=100.0).quote("TQQQ", 92.90, 92.95)
    _tick(cfg, ledger, cache)
    cache.quote("TQQQ", 96.80, 96.85)
    _tick(cfg, ledger, cache, day=NEXT, hhmm="11:00")
    return db.positions(ledger, status="closed")[0]


def test_an_unsold_lot_is_read_with_its_purchase_as_capital(config, ledger, cache):
    cache.summary("TQQQ", DAY, prev_day_close=100.0).quote("TQQQ", 92.90, 92.95)
    _tick(_only(config, "once"), ledger, cache)
    held = ledgers.OPEN_READERS["ladder_lot"](ledger)
    assert [(h["arm"], h["symbol"], h["strategy"], h["session"]) for h in held] == [
        ("once", "TQQQ", "ladder_lot", DAY)
    ]
    assert held[0]["capital"] == 930.0
    assert ledgers.HELD_READERS["ladder_lot"](ledger) == held
    assert ledgers.READERS["ladder_lot"](ledger) == []


def test_a_sold_lot_reads_with_every_cost_in_its_own_column(config, ledger, cache):
    lot = _buy_and_sell(config, ledger, cache)
    closed = ledgers.READERS["ladder_lot"](ledger)
    assert len(closed) == 1
    r = closed[0]
    assert r["net_pnl"] == lot["net_pnl"] and r["gross_pnl"] == lot["gross_pnl"]
    assert r["session"] == NEXT and r["entry_session"] == DAY and r["outcome"] == "target_gain"
    assert r["capital"] == 930.0 and r["slippage"] == 0.0 and r["settlement"] == 0.0
    assert r["slippage_basis"] == "cost"
    # rows add up: net = gross - fees - settlement - slippage, with the fee column holding fees alone
    assert round(r["gross_pnl"] - r["fees"] - r["settlement"] - r["slippage"], 2) == pytest.approx(
        r["net_pnl"]
    )
    assert r["fees"] == pytest.approx(lot["fees"]) and r["fees"] > 0
    assert (r["entry_spot"], r["exit_spot"]) == (lot["buy_bid"], lot["sell_bid"])
    assert r["entry_time"] == lot["buy_time"] and r["exit_time"] == lot["sell_time"]
    assert "r_multiple" in r and r["r_multiple"] is None  # a fund has no defined maximum loss
    assert ledgers.READERS["ladder_lot"](ledger, NEXT, NEXT) and not ledgers.READERS["ladder_lot"](
        ledger, DAY, DAY
    )
    assert ledgers.OPEN_READERS["ladder_lot"](ledger) == []
    assert ledgers.ROW_NET["ladder_lot"](lot) == pytest.approx(lot["net_pnl"])


def test_the_schema_has_a_trace_table_and_says_it_keeps_no_mark_path(config, ledger, cache):
    lot = _buy_and_sell(config, ledger, cache)
    assert trace.POSITIONS["ladder_lot"] == ("ladder_positions", "position_id")
    out = excursions.excursion(ledger, "ladder_lot", lot["position_id"])
    assert out["recorded"] is False and "mark path" in out["reason"]
