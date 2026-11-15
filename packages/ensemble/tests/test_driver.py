"""The automation driver against the paper loop: the same synthetic day, written once as a stream cache
for the real paper loop and once as a recorder-schema store for the driver, gives the same entry and the
same trade to the cent. The driver also ticks only stored minutes, refuses a tick it cannot build, and
runs an exit and a signal the way paper does."""

from __future__ import annotations

import json

from cachebuilder import sessions_before
from market import CALENDAR
from market import rows as market_rows
from storebuilder import StoreBuilder
from test_paper_loop import DAY, DOWN_EXIT, ZERO_DTE_YEARS, at, tick, world

from cherrypick.ensemble import db, driver, history, paper_loop

CLOSES = [5800.0 + i for i in range(39)] + [6000.0]  # the paper world's daily history


def store(tmp_path, *, spot=6000.0, minutes=range(570, 611), settle=6003.0, trail=None):
    sb = StoreBuilder(tmp_path / "store.db")
    for d, c in zip(sessions_before(DAY, len(CLOSES)), CLOSES, strict=True):
        sb.close("SPX", d, c)
    rows = market_rows(spot, ZERO_DTE_YEARS, 0.2)
    for m in minutes:
        sb.minute("SPX", DAY, m, DAY, (trail or {}).get(m, spot), rows, vix=16.0)
    sb.close("SPX", DAY, settle)
    return history.StoreHistory(tmp_path / "store.db", "SPX")


VIX_DAILY = {sessions_before(DAY, 1)[0]: 16.0}


def test_the_driver_and_the_paper_loop_make_the_same_trade(tmp_path):
    w = world(tmp_path)
    assert tick(w, at(10, 0))["entries"] == {"iron-fly-1000": "entered"}
    w.cache.summary("SPX", "2026-11-17", prev_close=6003.0)
    paper_loop.run_settlements(w.config, w.conn, cache_path=w.cache_path, when=at(9, 31, "2026-11-17"))
    paper = db.position(w.conn, f"iron-fly-1000:{DAY}")
    entry = json.loads(paper["entry_json"])

    book = driver.run(
        {"iron-fly-1000": w.arms["iron-fly-1000"]}, store(tmp_path), [DAY], events=CALENDAR,
        vix_daily=VIX_DAILY, costs=w.config,
    )  # fmt: skip
    assert book.why["iron-fly-1000"] == {"entered": 1} and not book.open
    (trade,) = book.trades
    assert trade["entry_minute"] == entry["minute"] == 600
    assert trade["legs"] == [{k: g[k] for k in ("name", "right", "side", "strike")} for g in entry["legs"]]
    assert trade["quantity"] == paper["quantity"] and trade["entry_mid"] == entry["prices"]["mid"]
    assert trade["exit_kind"] == paper["exit_reason"] == "settlement"
    assert trade["gross"] == paper["gross_pnl"] and trade["fees"] == paper["fees"]
    assert trade["slippage"]["verdict"] == paper["slippage"] and trade["net"]["verdict"] == paper["net_pnl"]


def test_a_signal_exit_closes_in_the_driver_on_the_minute_paper_closes(tmp_path):
    w = world(tmp_path, overrides={"iron-fly-1000": {"exit": DOWN_EXIT}})
    tick(w, at(10, 0))
    for i, px in enumerate([5999.0, 5997.0, 5993.0, 5990.0]):
        w.cache.last("SPX", px)
        tick(w, at(10, 1 + i))
    paper = db.position(w.conn, f"iron-fly-1000:{DAY}")
    assert paper["exit_reason"] == "signal"

    trail = {601: 5999.0, 602: 5997.0, 603: 5993.0, 604: 5990.0}
    book = driver.run(
        {"iron-fly-1000": w.arms["iron-fly-1000"]},
        store(tmp_path, minutes=range(600, 610), trail=trail),
        [DAY],
        events=CALENDAR, vix_daily=VIX_DAILY, costs=w.config,
    )  # fmt: skip
    (trade,) = book.trades
    assert trade["exit_kind"] == "signal" and trade["exit_minute"] == 604


def test_a_session_with_no_stored_minute_in_the_window_is_counted_not_traded(tmp_path):
    w = world(tmp_path)
    book = driver.run(
        {"iron-fly-1000": w.arms["iron-fly-1000"]}, store(tmp_path, minutes=range(570, 590)), [DAY],
        events=CALENDAR, vix_daily=VIX_DAILY, costs=w.config,
    )  # fmt: skip
    assert book.trades == [] and book.why["iron-fly-1000"] == {"no_tick_in_window": 1}
