"""The suite's normalised readers (`cherrypick.core.ledgers`, schema `ensemble_trade`) against a ledger the
loop itself wrote: a position entered, closed by a touch and another held to the posted close."""

from __future__ import annotations

import pytest
from cherrypick.core import excursions, ledgers
from test_paper_loop import DAY, NEXT, at, tick, world

from cherrypick.ensemble import db, paper_loop


def test_an_open_position_is_read_with_its_capital(tmp_path):
    w = world(tmp_path)
    tick(w, at(10, 0))
    held = ledgers.OPEN_READERS["ensemble_trade"](w.conn)
    assert [(h["arm"], h["symbol"], h["strategy"], h["session"]) for h in held] == [
        ("iron-fly-1000", "SPX", "ensemble_trade", DAY)
    ]
    pos = db.position(w.conn, f"iron-fly-1000:{DAY}")
    assert held[0]["capital"] == pos["capital"] > 0
    assert ledgers.HELD_READERS["ensemble_trade"](w.conn) == held
    assert ledgers.READERS["ensemble_trade"](w.conn) == []


def test_a_settled_position_reads_with_every_cost_in_its_own_column(tmp_path):
    w = world(tmp_path)
    tick(w, at(10, 0))
    w.cache.summary("SPX", NEXT, prev_close=6003.0)
    paper_loop.run_settlements(w.config, w.conn, cache_path=w.cache_path, when=at(9, 31, NEXT))
    pos = db.position(w.conn, f"iron-fly-1000:{DAY}")
    closed = ledgers.READERS["ensemble_trade"](w.conn)
    assert len(closed) == 1
    r = closed[0]
    assert r["net_pnl"] == pos["net_pnl"] and r["gross_pnl"] == pos["gross_pnl"]
    assert r["session"] == DAY and r["entry_session"] == DAY and r["outcome"] == "settlement"
    assert r["slippage_basis"] == "cost" and r["slippage"] == pos["slippage"]
    assert r["settlement"] == pos["settlement"]
    # rows add up: net = gross - fees - settlement - slippage, with the fee column holding fees alone
    assert round(r["gross_pnl"] - r["fees"] - r["settlement"] - r["slippage"], 2) == pytest.approx(
        r["net_pnl"]
    )
    assert r["fees"] == pytest.approx(pos["fees"])
    assert r["exit_spot"] == 6003.0 and r["entry_spot"] == pos["entry_spot"]
    assert r["max_profit"] == pytest.approx(pos["max_profit"] * 100 * pos["quantity"], abs=0.01)
    assert r["r_multiple"] == pytest.approx(r["net_pnl"] / r["capital"], abs=1e-4)
    assert ledgers.READERS["ensemble_trade"](w.conn, DAY, DAY) and not ledgers.READERS["ensemble_trade"](
        w.conn, NEXT, NEXT
    )
    assert ledgers.OPEN_READERS["ensemble_trade"](w.conn) == []
    assert ledgers.ROW_NET["ensemble_trade"](db.position(w.conn, f"iron-fly-1000:{DAY}")) == pytest.approx(
        pos["net_pnl"]
    )


def test_the_mark_path_ends_at_the_recorded_gross(tmp_path):
    w = world(tmp_path, overrides={"iron-fly-1000": {"exit": {"touch": {"pct": -0.5}}}})
    tick(w, at(10, 0))
    tick(w, at(10, 20))
    w.cache.last("SPX", 5960.0)
    tick(w, at(10, 40))
    pos = db.position(w.conn, f"iron-fly-1000:{DAY}")
    assert pos["exit_reason"] == "touch"
    out = excursions.excursion(w.conn, "ensemble_trade", pos["position_id"])
    assert out["recorded"] is True and out["basis"] == "gross_at_mid"
    assert out["path"][0][1] == 0.0
    assert out["endpoint"] == pytest.approx(pos["gross_pnl"])
