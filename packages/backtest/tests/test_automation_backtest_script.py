"""The automation backtest script (packages/backtest/scripts/automation_backtest.py): which store a run reads,
that the vendor's is refused once its licence ends, and where a result goes. The replay itself is
`cherrypick.ensemble.driver`, held to the paper loop by ensemble's own tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from cherrypick.core import sources as src

from cherrypick.backtest import paths

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("automation_backtest", SCRIPTS / "automation_backtest.py")
ab = importlib.util.module_from_spec(_spec)
sys.modules["automation_backtest"] = ab
_spec.loader.exec_module(ab)

SPAN = {"first": "2022-05-16", "last": "2026-11-13", "sessions": 1131}
INV = {
    "sources": {
        "recorded-0dte": {"symbols": {"SPX": {"first": "2026-10-13", "last": "2026-11-13", "sessions": 24}}},
        "vendor-0dte-spx-60s": {"store": SPAN},
    }
}


def test_the_finest_covering_store_is_chosen_and_the_vendors_only_while_licensed():
    recent = ab.choose_source("SPX", "2026-10-13", "2026-11-13", None, licensed_ok=True, inv=INV)
    assert recent.name == "recorded-0dte", "our recording, the finer cadence, covers it"
    older = ab.choose_source("SPX", "2023-01-03", "2023-12-29", None, licensed_ok=True, inv=INV)
    assert older.name == "vendor-0dte-spx-60s"
    with pytest.raises(ab.Refused, match="no store covers SPX 0DTE chains 2023-01-03..2023-12-29"):
        ab.choose_source("SPX", "2023-01-03", "2023-12-29", None, licensed_ok=False, inv=INV)
    with pytest.raises(ab.Refused, match="licence does not allow new work"):
        ab.choose_source("SPX", "2023-01-03", "2023-12-29", "vendor-0dte-spx-60s", licensed_ok=False, inv=INV)
    with pytest.raises(ab.Refused, match="not a same-day chain store"):
        ab.choose_source("SPX", "2023-01-03", "2023-12-29", "dolt-chains", licensed_ok=True, inv=INV)


def test_a_store_path_follows_its_source_and_a_result_follows_its_licence(managed_home):
    from cherrypick.backtest import settings

    s = settings.resolve({"enabled": True})
    assert ab.store_path(src.by_name("recorded-0dte"), s).as_posix().endswith("data/gex/gex_history.db")
    assert ab.store_path(src.by_name("vendor-0dte-spx-60s"), s) == paths.vendor_dir(s) / "SPX-60s.db"
    assert paths.automations_dir(True) == paths.results_dir() / "automations"
    assert paths.automations_dir(False) == paths.data_dir() / "automations"


EOD_INV = {
    "sources": {
        "dolt-chains": {
            "symbols": {
                "SPY": {
                    "first": "2019-02-09",
                    "last": "2026-11-13",
                    "sessions": 1313,
                    "years": {"2023": 151, "2024": 182, "2025": 258},
                }
            }
        },
        "vendor-eod-chains": {"store": {"first": "2016-01-04", "last": "2026-11-13", "sessions": None}},
    }
}


def test_a_multi_day_arm_reads_end_of_day_chains_chosen_by_coverage():
    assert ab.same_day({"a": {"expiry": {"kind": "dte", "days": 0}}})
    assert not ab.same_day({"a": {"expiry": {"kind": "calendar", "days": 20}}})
    daily = ab.choose_eod_source(
        "SPY", "2025-01-02", "2025-12-31", None, licensed_ok=False, inv=EOD_INV, min_density=0.95
    )
    assert daily.name == "dolt-chains"
    with pytest.raises(ab.Refused, match="no end-of-day chains cover SPY 2023-01-03..2023-12-29"):
        ab.choose_eod_source(
            "SPY", "2023-01-03", "2023-12-29", None, licensed_ok=False, inv=EOD_INV, min_density=0.95
        )
    thin = ab.choose_eod_source(
        "SPY", "2023-01-03", "2023-12-29", None, licensed_ok=False, inv=EOD_INV, min_density=0.5
    )
    assert thin.name == "dolt-chains", "a weekly entry may accept the thinner years knowingly"
    with pytest.raises(ab.Refused, match="not an end-of-day chain source"):
        ab.choose_eod_source(
            "SPY",
            "2025-01-02",
            "2025-12-31",
            "recorded-0dte",
            licensed_ok=True,
            inv=EOD_INV,
            min_density=0.95,
        )
    with pytest.raises(ab.Refused, match="licence does not allow new work"):
        ab.choose_eod_source(
            "SPY",
            "2025-01-02",
            "2025-12-31",
            "vendor-eod-chains",
            licensed_ok=False,
            inv=EOD_INV,
            min_density=0.95,
        )


def test_the_vendor_eod_loader_spells_rights_ours_and_computes_delta_from_mids(tmp_path):
    import sqlite3

    from cherrypick.core import bs

    db = tmp_path / "eod_chains.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE eod_chain_quotes (symbol TEXT, session TEXT, expiration TEXT, strike REAL, right TEXT, "
        "bid REAL, ask REAL, close REAL, volume REAL, bid_size REAL, ask_size REAL)"
    )
    years = 20 / 365
    for k in (290.0, 300.0, 310.0):
        for right in ("CALL", "PUT"):
            px = bs.price(300.0, k, years, 0.2, right[0], 0.0)
            conn.execute(
                "INSERT INTO eod_chain_quotes VALUES "
                "('GLD', '2026-03-02', '2026-03-22', ?, ?, ?, ?, 0, 0, 0, 0)",
                (k, right, round(px - 0.05, 2), round(px + 0.05, 2)),
            )
    conn.commit()
    conn.close()
    got = ab.vendor_eod_loader(db, "GLD")("2026-03-02")
    rows = got["2026-03-22"]
    assert {r["right"] for r in rows} == {"C", "P"}
    atm_call = next(r for r in rows if r["strike"] == 300.0 and r["right"] == "C")
    assert atm_call["delta"] is not None and 0.45 < atm_call["delta"] < 0.6


def test_declared_volume_is_read_from_the_recorded_bars_and_refused_where_they_do_not_reach(tmp_path):
    import sqlite3

    db = tmp_path / "profile_bars.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE profile_bars (symbol TEXT, ts_ms INTEGER, contract TEXT, et_day TEXT, et_time TEXT, "
        "open REAL, high REAL, low REAL, close REAL, volume REAL, fetched_at REAL)"
    )
    for hhmm, vol in (("09:29", 5.0), ("09:30", 100.0), ("09:31", 120.0), ("16:00", 9.0)):
        conn.execute(
            "INSERT INTO profile_bars VALUES ('SPY', 0, 'SPY', '2026-11-13', ?, 1, 1, 1, 1, ?, 0)",
            (hhmm, vol),
        )
    conn.commit()
    conn.close()
    assert ab.profile_volumes("SPY", db) == {"2026-11-13": {570: 100.0, 571: 120.0}}
    inv = {"sources": {"profile-bars": {"symbols": {"SPY": {
        "first": "2026-11-05", "last": "2026-11-13", "sessions": 7, "years": {"2026": 7}
    }}}}}  # fmt: skip
    ab.check_volume({"SPY"}, "2026-11-05", "2026-11-13", inv=inv, min_density=0.95)
    with pytest.raises(ab.Refused, match="no one-minute volume covers SPY 2026-10-01..2026-11-13"):
        ab.check_volume({"SPY"}, "2026-10-01", "2026-11-13", inv=inv, min_density=0.95)
    with pytest.raises(ab.Refused, match="no one-minute volume covers QQQ"):
        ab.check_volume({"QQQ"}, "2026-11-05", "2026-11-13", inv=inv, min_density=0.95)


FORTY_FIVE = {"symbol": "SPY", "expiry": {"kind": "between", "min": 40, "max": 50},
              "entry": {"kind": "time", "at": "15:59", "window": 5}}  # fmt: skip


def _full_set_inv(**extra):
    span = {
        "first": "2020-01-02",
        "last": "2026-11-13",
        "sessions": src.trading_days("2020-01-02", "2026-11-13"),
    }
    names = ("vendor-mid-spy", "vendor-near-spy", "vendor-0dte-spy-60s-full")
    return {"sources": {**{n: {"store": span} for n in names}, **extra}}


def test_a_multi_day_run_takes_our_chains_then_the_full_set_intraday_then_the_vendors_close():
    arms = {"a": FORTY_FIVE}

    def choose(start, end, inv, named=None, licensed_ok=True):
        return ab.choose_multi_day("SPY", start, end, named, arms, licensed_ok=licensed_ok, inv=inv,
                                   min_density=0.95)  # fmt: skip

    both = {"sources": {**EOD_INV["sources"], **_full_set_inv()["sources"]}}
    kind, parts = choose("2025-01-02", "2025-12-31", both)
    assert (kind, [p.name for p in parts]) == ("eod", ["dolt-chains"]), "our own chains first"
    kind, parts = choose("2023-01-03", "2023-12-29", both)
    assert kind == "intraday"
    assert [p.name for p in parts] == ["vendor-mid-spy", "vendor-near-spy", "vendor-0dte-spy-60s-full"]
    held = src.trading_days("2016-01-04", "2026-11-13")
    spy = {"first": "2016-01-04", "last": "2026-11-13", "sessions": held}
    vendor_eod = {"sources": {"vendor-eod-chains": {"symbols": {"SPY": spy}}}}
    kind, parts = choose("2023-01-03", "2023-12-29", vendor_eod)
    assert (kind, parts[0].name) == ("eod", "vendor-eod-chains"), "no full set: the vendor's close"
    assert choose("2023-01-03", "2023-12-29", {}, "vendor-mid-spy")[0] == "intraday"
    with pytest.raises(ab.Refused, match="licence does not allow new work"):
        choose("2023-01-03", "2023-12-29", {}, "vendor-mid-spy", licensed_ok=False)
    with pytest.raises(ab.Refused, match="not the intraday replay"):
        choose("2023-01-03", "2023-12-29", {}, "vendor-near-spy")


def _intraday_store(path, rows, sessions):
    import sqlite3

    from cherrypick.backtest import intradayfetch

    conn = sqlite3.connect(path)
    conn.executescript(intradayfetch.SCHEMA)
    conn.executemany(
        "INSERT INTO intraday_chain_quotes (symbol, session, expiration, bar, strike, right, bid, ask, "
        "delta, iv, underlying) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    for s in sessions:
        conn.execute(
            "INSERT INTO intraday_chain_requests VALUES ('SPY', ?, '2026-04-17', '09:30', '16:00', '5m', "
            "'complete', 1, 1, NULL, 0)",
            (s,),
        )
    conn.commit()
    conn.close()


def test_the_intraday_loader_reads_the_finer_store_first_and_states_where_the_spot_came_from(tmp_path):
    from cherrypick.core import bs

    def chain(session, expiry, bar, spot, *, underlying=False):
        years = 30 / 365
        out = []
        for k in (595.0, 600.0, 605.0):
            for right in ("CALL", "PUT"):
                px = bs.price(spot, k, years, 0.2, right[0], 0.0)
                bid, ask = round(px - 0.02, 2), round(px + 0.02, 2)
                out.append(("SPY", session, expiry, bar, k, right, bid, ask, None, None,
                            spot if underlying else None))  # fmt: skip
        return out

    day, exp, week = "2026-03-02", "2026-04-17", "2026-03-05"
    near, mid = tmp_path / "SPY-near-1m.db", tmp_path / "SPY-mid-5m.db"
    _intraday_store(near, chain(day, week, "09:30", 600.0), [day])
    mid_rows = (
        chain(day, exp, "09:30", 600.0) + chain(day, exp, "09:35", 601.0) + chain(day, week, "09:30", 999.0)
    )
    _intraday_store(mid, mid_rows, ["2026-03-03"])
    expiries, quotes, spot, held, closes = ab.intraday_loader([near, mid], None, "SPY", "SPY")
    assert expiries(day) == ["2026-03-05", exp] and held == [day, "2026-03-03"] and closes == {}
    assert sorted(quotes(day, exp)) == [570, 575]
    assert {r["right"] for r in quotes(day, exp)[570]} == {"C", "P"}
    one_min = quotes(day, week)
    assert one_min[570][0]["bid"] < 100, "an expiry both stores hold is read from the finer"
    trail, source = spot(day)
    assert source == "parity forward" and abs(trail[570] - 600.0) < 0.1
    # A store that kept the underlying (a Standard-tier fetch) gives that price instead.
    std = tmp_path / "standard.db"
    _intraday_store(std, chain(day, "2026-03-05", "09:30", 600.0, underlying=True), [day])
    trail, source = ab.intraday_loader([std], None, "SPY", "SPY")[2](day)
    assert (source, trail) == ("row underlying", {570: 600.0})


def test_close_only_bars_fill_the_sessions_the_stock_history_lacks():
    bars = [{"session": "2026-03-03", "open": 1, "high": 1, "low": 1, "close": 1}]
    got = ab.with_closes(bars, {"2026-03-02": 5.0, "2026-03-03": 9.0})
    assert [(b["session"], b["close"]) for b in got] == [("2026-03-02", 5.0), ("2026-03-03", 1)]
