"""The loop end to end, against a real-DDL stream cache: the window, the switch, the money, the miss."""

from datetime import datetime

import pytest
from conftest import add_dividend

from cherrypick.contango import clock, db, paper_loop

DAY1, DAY2 = "2026-10-06", "2026-10-07"


def at(day: str, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    y, mo, d = (int(x) for x in day.split("-"))
    return datetime(y, mo, d, h, m, tzinfo=clock.ET)


def tick(config, conn, cache, technicals, day, hhmm):
    return paper_loop.run_once(
        config, conn, cache_path=cache.path, technicals_path=str(technicals), when=at(day, hhmm)
    )


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "paper_trades.db"))


def _funds(cache, svxy=50.0, shv=110.40):
    cache.quote("SVXY", svxy - 0.01, svxy + 0.01).quote("SHV", shv - 0.005, shv + 0.005)


def _identity(conn, arm):
    acct = db.account(conn, arm)
    pos = db.open_position(conn, arm)
    closed = [p for p in db.positions(conn, arm) if p["status"] == "closed"]
    return acct, pos, closed


def test_contango_buys_the_risk_fund_inside_the_window_and_not_before(config, conn, cache, technicals):
    _funds(cache)
    cache.regime(0.85)
    assert tick(config, conn, cache, technicals, DAY1, "15:30")["phase"] == "wait"
    assert db.open_position(conn, "control") is None
    out = tick(config, conn, cache, technicals, DAY1, "15:51")
    assert out["decisions"] == {"control": "switch", "flipexit": "switch"}
    pos = db.open_position(conn, "control")
    assert (pos["symbol"], pos["role"], pos["shares"]) == ("SVXY", "risk", 199)
    session = db.session_for(conn, DAY1, "control")
    assert session["nav"] == pytest.approx(session["cash"] + 199 * 50.0)
    # decided once: a later tick in the window does nothing
    assert tick(config, conn, cache, technicals, DAY1, "15:53")["decisions"] == {}


def test_a_flip_sells_into_cash_and_the_closed_stint_adds_up(config, conn, cache, technicals):
    _funds(cache)
    cache.regime(0.85)
    tick(config, conn, cache, technicals, DAY1, "15:51")
    _funds(cache, svxy=46.0)
    cache.regime(0.98)  # control leaves; flipexit holds through the band
    out = tick(config, conn, cache, technicals, DAY2, "15:51")
    assert out["decisions"] == {"control": "switch", "flipexit": "hold"}
    acct, pos, closed = _identity(conn, "control")
    assert pos["symbol"] == "SHV"
    (stint,) = closed
    assert stint["entry_value"] + stint["exit_value"] + stint["distributions"] == pytest.approx(
        stint["gross_pnl"]
    )
    assert stint["gross_pnl"] - stint["fees"] - stint["slippage"] == pytest.approx(stint["net_pnl"])
    # the account is its starting capital plus every closed net, less what the open stint cost to buy
    open_cost = -pos["entry_value"] + pos["entry_fees"] + pos["entry_slippage"]
    assert acct["cash"] == pytest.approx(10_000 + stint["net_pnl"] - open_cost, abs=0.02)
    assert db.open_position(conn, "flipexit")["symbol"] == "SVXY"


def test_after_the_window_an_undecided_arm_is_missed_and_never_trades(config, conn, cache, technicals):
    """The guard on "nothing fills after the window": a perfectly good regime and quotes at 15:59 must
    record a miss, not a late switch the replay never measured."""
    _funds(cache)
    cache.regime(0.85)
    out = tick(config, conn, cache, technicals, DAY1, "15:59")
    assert out["decisions"] == {"control": "missed", "flipexit": "missed"}
    assert db.open_position(conn, "control") is None
    assert db.session_for(conn, DAY1, "control")["refusal"] == "no_tick_in_window"


def test_a_stale_quote_waits_for_the_next_tick_then_misses(config, conn, cache, technicals):
    cache.quote("SVXY", 49.99, 50.01, age=600).quote("SHV", 110.40, 110.41)
    cache.regime(0.85)
    assert tick(config, conn, cache, technicals, DAY1, "15:51")["decisions"]["control"] == "pending"
    out = tick(config, conn, cache, technicals, DAY1, "15:59")
    assert out["decisions"]["control"] == "missed"
    assert db.session_for(conn, DAY1, "control")["refusal"] == "no_quote_SVXY"


def test_an_unmeasured_regime_never_switches(config, conn, cache, technicals):
    _funds(cache)
    cache.regime(0.85, age=900)
    assert tick(config, conn, cache, technicals, DAY1, "15:51")["decisions"]["control"] == "pending"
    assert db.regime_for(conn, DAY1)["refusal"] == "stale_vix"


def test_early_close_moves_the_window(config, conn, cache, technicals):
    _funds(cache)
    cache.regime(0.85)
    day = "2026-11-27"  # the day after Thanksgiving closes at 13:00
    assert tick(config, conn, cache, technicals, day, "12:51")["decisions"]["control"] == "switch"


def test_a_distribution_is_credited_once_and_restates_a_closed_stint(config, conn, cache, technicals):
    _funds(cache)
    cache.regime(0.99)  # both arms start in cash
    tick(config, conn, cache, technicals, DAY1, "15:51")
    cache.regime(0.85)
    tick(config, conn, cache, technicals, DAY2, "15:51")  # both sell SHV on DAY2, ex-date DAY2
    acct, _, (stint,) = _identity(conn, "control")
    before_cash, before_net = acct["cash"], stint["net_pnl"]
    add_dividend(technicals, "SHV", DAY2, 0.34)  # the row lands after the stint closed
    tick(config, conn, cache, technicals, "2026-10-08", "10:00")
    tick(config, conn, cache, technicals, "2026-10-08", "10:01")
    acct, _, (stint,) = _identity(conn, "control")
    paid = round(0.34 * stint["shares"], 2)
    assert acct["cash"] == pytest.approx(before_cash + paid)
    assert stint["net_pnl"] == pytest.approx(before_net + paid)
    assert stint["entry_value"] + stint["exit_value"] + stint["distributions"] == pytest.approx(
        stint["gross_pnl"]
    )
