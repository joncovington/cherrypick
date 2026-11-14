"""Hypothesis 75's driver on the fetched-chain paths: the GLD spreads and the SPX day-before-expiry fly read
`intraday_chains.db` (and `open_interest.db`); invented rows, the stores' own DDL."""

from __future__ import annotations

import sqlite3

import pytest
from cherrypick.core import bs
from cherrypick.ensemble.events import EventCalendar
from test_ensemble_item75_script import QUIET, ctx, m, spx

from cherrypick.backtest import intradayfetch, oifetch

MON, TUE = "2026-11-16", "2026-11-17"


def chain_db(entries: list[tuple], vol=0.5) -> sqlite3.Connection:
    """entries: (symbol, session, expiry, bar, spot, step, width, years)."""
    db = sqlite3.connect(":memory:")
    db.executescript(intradayfetch.SCHEMA)
    for sym, session, exp, bar, spot, step, width, years in entries:
        k = round((spot - width) / step) * step
        while k <= spot + width:
            for right, word in (("C", "CALL"), ("P", "PUT")):
                p = bs.price(spot, k, years, vol, right, 0.0)
                d = bs.delta(spot, k, years, vol, right, 0.0)
                db.execute(
                    "INSERT INTO intraday_chain_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        sym,
                        session,
                        exp,
                        bar,
                        float(k),
                        word,
                        round(max(p - 0.02, 0), 2),
                        round(p + 0.02, 2),
                        d,
                        vol,
                        spot,
                    ),
                )
            k += step
        db.execute(
            "INSERT INTO intraday_chain_requests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sym, session, exp, bar, bar, "5m", "complete", 1, 1, None, 0.0),
        )
    return db


def test_the_one_day_gld_spread_enters_on_the_fetched_bar_and_exits_on_the_expiry_bar():
    db = chain_db(
        [
            ("GLD", MON, TUE, "12:20", 250.0, 1.0, 40.0, 1.1 / 365),
            ("GLD", TUE, TUE, "15:45", 251.0, 1.0, 40.0, 0.01 / 365),
        ]
    )
    intra = m.Intraday(db, None)
    arm = m.arm_mod.load_arms()["gld-call-spread-1dte"]
    closes = {"2026-11-13": 249.0, MON: 250.0, TUE: 251.0}
    trades, why = m.run_gld(arm, intra, closes, ctx(), [MON, TUE])
    assert len(trades) == 1, dict(why)
    t = trades[0]
    assert t["exit_kind"] == "time" and t["exit_minute"] == 945 and t["quantity"] == 3
    assert t["session"] == MON and t["expiry"] == TUE
    for k in m.KINDS:
        assert t["net"][k] == pytest.approx(
            t["gross"] - t["fees"] - t["settlement"] - t["slippage"][k], abs=0.01
        )
    # a spread whose exit bar was never fetched is settled at the expiry's close and counted
    only_entry = m.Intraday(chain_db([("GLD", MON, TUE, "12:20", 250.0, 1.0, 40.0, 1.1 / 365)]), None)
    trades, why = m.run_gld(arm, only_entry, closes, ctx(), [MON, TUE])
    assert trades[0]["exit_kind"] == "settlement" and why["exit_unpriced"] == 1


def test_a_gld_session_without_a_fetched_chain_is_not_entered():
    arm = m.arm_mod.load_arms()["gld-call-spread-1dte"]
    trades, why = m.run_gld(
        arm, m.Intraday(chain_db([]), None), {"2026-11-13": 249.0, MON: 250.0}, ctx(), [MON]
    )
    assert trades == [] and why["no_chain"] == 1


def test_the_spx_one_day_fly_enters_from_the_fetched_chain_and_runs_through_the_store():
    # session one: a flat 6000 day in the 60 s store; session two the same, the fly's expiry
    history = {f"2026-09-{d:02d}": 5900.0 + d for d in range(1, 29)} | {
        f"2026-10-{d:02d}": 5930.0 for d in range(1, 15)
    }
    s = spx(
        {MON: 6000.0, TUE: 6000.0, "2026-11-13": 6000.0},
        history | {"2026-11-13": 6000.0, MON: 6000.0, TUE: 6000.0},
    )
    db = chain_db(
        [("SPXW", MON, TUE, "13:30", 6000.0, 5.0, 300.0, 1.15 / 365)],
        vol=0.12,
    )
    oi = sqlite3.connect(":memory:")
    for stmt in oifetch.DDL:
        oi.execute(stmt)
    for k in range(5700, 6301, 5):
        oi.execute("INSERT INTO oi_contracts VALUES ('SPXW-1D', ?, ?, 'C', ?, 2000)", (MON, TUE, float(k)))
        oi.execute("INSERT INTO oi_contracts VALUES ('SPXW-1D', ?, ?, 'P', ?, 1000)", (MON, TUE, float(k)))
    intra = m.Intraday(db, oi)
    arm = m.arm_mod.load_arms()["iron-fly-1dte-1330"]
    trades, why = m.run_one_dte_spx(arm, s, intra, ctx(), [MON])
    assert len(trades) == 1, dict(why)
    t = trades[0]
    assert t["session"] == MON and t["expiry"] == TUE
    assert {g["strike"] for g in t["legs"] if g["name"].startswith("short")} == {6000.0}
    assert t["exit_session"] in (MON, TUE)
    # nothing fetched for the day: no entry, and the reason says so
    trades, why = m.run_one_dte_spx(arm, s, m.Intraday(chain_db([]), None), ctx(), [MON])
    assert trades == [] and why["no_chain"] == 1


def test_event_data_missing_does_not_block_an_entry_and_is_counted():
    db = chain_db([("GLD", MON, TUE, "12:20", 250.0, 1.0, 40.0, 1.1 / 365)])
    arm = m.arm_mod.load_arms()["gld-call-spread-1dte"]
    closes = {"2026-11-13": 249.0, MON: 250.0}
    _, quiet = m.run_gld(arm, m.Intraday(db, None), closes, ctx(QUIET), [MON])
    assert quiet["event_data_unknown"] == 0
    _, blind = m.run_gld(arm, m.Intraday(db, None), closes, ctx(EventCalendar()), [MON])
    assert blind["event_data_unknown"] == 1
