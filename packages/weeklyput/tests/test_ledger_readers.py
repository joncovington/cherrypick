"""The suite's normalised readers (`cherrypick.core.ledgers`, schema `weeklyput_put`) against a
ledger this module actually wrote. Core cannot import this package, so this is where a column the
reader names and the schema drops would fail."""

from datetime import date

import pytest
from cherrypick.core import excursions, ledgers
from conftest import black_chain
from test_paper_loop import ENTRY, EXP, YEARS, at, tick

from cherrypick.weeklyput import db

MONDAY = date(2026, 11, 16)


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "ledger.db"))


@pytest.fixture
def stocked(cache):
    cache.last("SPX", 6800.0).last("SPY", 680.0)
    cache.chain("SPX", EXP, "SPXW", black_chain(6800.0, 0.16, YEARS, range(6000, 7001, 5)))
    cache.chain("SPY", EXP, "SPY", black_chain(680.0, 0.16, YEARS, range(600, 701)))
    return cache


def test_open_puts_read_as_held_with_cash_securing_capital(config, conn, stocked):
    tick(config, conn, stocked, at(ENTRY, 15, 46))
    held = ledgers.OPEN_READERS["weeklyput_put"](conn)
    assert {r["symbol"] for r in held} == {"SPX", "SPY"}
    for r in held:
        pos = db.position(conn, f"{r['arm']}:{r['symbol']}:{EXP}")
        assert r["capital"] == pytest.approx(pos["strike"] * 100)
        assert r["session"] == ENTRY.isoformat()
    assert ledgers.HELD_READERS["weeklyput_put"](conn) == held
    assert ledgers.READERS["weeklyput_put"](conn) == []


def test_closed_puts_carry_the_whole_money_layout_and_add_up(config, conn, stocked):
    tick(config, conn, stocked, at(ENTRY, 15, 46))
    spy = db.position(conn, f"unhedged:SPY:{EXP}")
    stocked.summary("SPX", MONDAY.isoformat(), prev_day_close=6900.0)
    stocked.summary("SPY", MONDAY.isoformat(), prev_day_close=spy["strike"] - 10)
    tick(config, conn, stocked, at(MONDAY, 9, 31))

    closed = {r["symbol"]: r for r in ledgers.READERS["weeklyput_put"](conn)}
    assert set(closed) == {"SPX", "SPY"}
    for r in closed.values():
        assert r["session"] == EXP and r["entry_session"] == ENTRY.isoformat()
        assert r["gross_pnl"] - r["fees"] - r["settlement"] - r["slippage"] == pytest.approx(
            r["net_pnl"], abs=0.01
        )
        assert r["cost"] == pytest.approx(r["fees"] + r["settlement"] + r["slippage"], abs=0.01)
        assert r["slippage_basis"] == "cost" and r["entry_credit"] == r["max_profit"] > 0
        assert r["exit_time"] and r["exit_spot"] is not None and r["entry_spot"] is not None
        assert r["r_multiple"] == pytest.approx(r["net_pnl"] / r["capital"], abs=1e-4)
    assert closed["SPX"]["outcome"] == "expired" and closed["SPY"]["outcome"] == "assigned"
    assert closed["SPY"]["settlement"] > 0 and closed["SPX"]["settlement"] == 0
    assert ledgers.READERS["weeklyput_put"](conn, EXP, EXP) and not ledgers.READERS["weeklyput_put"](
        conn, ENTRY.isoformat(), ENTRY.isoformat()
    )
    assert ledgers.OPEN_READERS["weeklyput_put"](conn) == []
    assert ledgers.ROW_NET["weeklyput_put"](db.position(conn, f"unhedged:SPX:{EXP}")) == pytest.approx(
        closed["SPX"]["net_pnl"]
    )


def test_the_excursion_path_runs_from_entry_to_the_recorded_gross(config, conn, stocked):
    tick(config, conn, stocked, at(ENTRY, 15, 46))
    spx = db.position(conn, f"unhedged:SPX:{EXP}")
    stocked.quote(spx["streamer_symbol"], 0.40, 0.50)
    tick(config, conn, stocked, at(date(2026, 11, 10), 11, 0))
    stocked.summary("SPX", MONDAY.isoformat(), prev_day_close=6900.0)
    tick(config, conn, stocked, at(MONDAY, 9, 31))
    out = excursions.excursion(conn, "weeklyput_put", spx["position_id"])
    assert out["recorded"] is True and out["basis"] == "gross_at_mid"
    assert out["path"][0][1] == 0.0
    assert out["endpoint"] == pytest.approx(db.position(conn, spx["position_id"])["gross_pnl"])
