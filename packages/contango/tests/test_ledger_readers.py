"""The suite's normalised readers (`cherrypick.core.ledgers`, schema `contango_etf`) against a ledger
this module actually wrote. Core cannot import this package, so this is where a column the reader
names and the schema drops would fail."""

import pytest
from cherrypick.core import ledgers
from test_paper_loop import DAY1, DAY2, _funds, tick

from cherrypick.contango import db


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "paper_trades.db"))


def test_closed_and_open_stints_read_through_the_suite_readers(config, conn, cache, technicals):
    _funds(cache)
    cache.regime(0.85)
    tick(config, conn, cache, technicals, DAY1, "15:51")
    cache.regime(1.02)  # a real inversion: both arms leave SVXY for SHV
    tick(config, conn, cache, technicals, DAY2, "15:51")

    closed = ledgers.READERS["contango_etf"](conn)
    assert {r["arm"] for r in closed} == {"control", "flipexit"}
    for r in closed:
        assert r["symbol"] == "SVXY" and r["session"] == DAY2
        assert r["gross_pnl"] - r["cost"] == pytest.approx(r["net_pnl"])  # cost is fees + slippage
        assert r["capital"] == pytest.approx(199 * 50.0)
        assert r["max_profit"] is None

    held = ledgers.OPEN_READERS["contango_etf"](conn)
    assert {(r["arm"], r["symbol"]) for r in held} == {("control", "SHV"), ("flipexit", "SHV")}
    assert ledgers.READERS["contango_etf"](conn, DAY1, DAY1) == []  # bounded by exit session
