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


def _arm(expiry, entry):
    import copy

    from cherrypick.automations import arms

    a = copy.deepcopy(arms.load_arms()["gld-call-spread-20d"])
    a.update(expiry=expiry, entry=entry)
    return a


def test_an_entry_inside_the_session_on_a_later_expiry_reads_the_full_sets_intraday_stores():
    later = {"kind": "calendar", "days": 20}
    assert ab.intraday({"a": _arm(later, {"kind": "time", "at": "12:30", "window": 5})})
    assert not ab.intraday({"a": _arm(later, {"kind": "time", "at": "15:55", "window": 5})}), (
        "at the close: EOD"
    )
    assert not ab.intraday(
        {"a": _arm({"kind": "dte", "days": 0}, {"kind": "time", "at": "12:30", "window": 5})}
    )
    span = {
        "first": "2020-01-02",
        "last": "2026-11-13",
        "sessions": src.trading_days("2020-01-02", "2026-11-13"),
    }
    inv = {"sources": {"vendor-mid-gld": {"store": span}, "vendor-eod-chains": {"symbols": {"GLD": span}}}}
    got = ab.choose_intraday_source(
        "GLD", "2024-01-02", "2024-12-31", None, licensed_ok=True, inv=inv, min_density=0.95
    )
    assert got.name == "vendor-mid-gld"
    with pytest.raises(ab.Refused, match="no intraday chain store covers GLD"):
        ab.choose_intraday_source(
            "GLD", "2024-01-02", "2024-12-31", None, licensed_ok=False, inv=inv, min_density=0.95
        )
    with pytest.raises(ab.Refused, match="not one of the full set's intraday chain stores"):
        ab.choose_intraday_source("GLD", "2024-01-02", "2024-12-31", "vendor-eod-chains", licensed_ok=True,
                                  inv=inv,
                                  min_density=0.95)  # fmt: skip
    with pytest.raises(ab.Refused, match="licence does not allow new work"):
        ab.choose_intraday_source("GLD", "2024-01-02", "2024-12-31", "vendor-near-gld", licensed_ok=False,
                                  inv=inv,
                                  min_density=0.95)  # fmt: skip


def test_the_intraday_history_reads_the_symbols_three_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "vendor_dir", lambda s: tmp_path)
    h = ab.intraday_history("spx", {}, [], {})
    assert [p.name for p, _ in h.stores] == ["SPX-near-1m.db", "SPX-mid-5m.db"]
    assert [step for _, step in h.stores] == [1, 5] and h.root == "SPXW" and h.zero is None
