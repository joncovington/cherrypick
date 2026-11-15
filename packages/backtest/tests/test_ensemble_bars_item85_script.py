"""Item 85's script (ensemble_bars_item85.py): invented trades and an in-memory store, never the real one."""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as kit_rule

_spec = importlib.util.spec_from_file_location(
    "ensemble_bars_item85", Path(__file__).resolve().parents[1] / "scripts" / "ensemble_bars_item85.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

CONDOR = {
    "session": "2024-06-03", "exit_session": "2024-06-03", "expiry": "2024-06-03", "entry_minute": 825,
    "quantity": 2, "entry_mid": 3.3, "net": {"verdict": 378.23},
    "legs": [{"right": "P", "side": -1, "strike": 4285.0}, {"right": "P", "side": 1, "strike": 4280.0},
             {"right": "C", "side": -1, "strike": 4295.0}, {"right": "C", "side": 1, "strike": 4300.0}],
}  # fmt: skip
PUT = {
    "session": "2024-06-03", "exit_session": "2024-06-03", "expiry": "2024-06-03", "entry_minute": 630,
    "quantity": 1, "entry_mid": -2.05, "net": {"verdict": -207.97},
    "legs": [{"right": "P", "side": 1, "strike": 3970.0}],
}  # fmt: skip


def test_the_declared_rule_is_complete():
    kit_rule.check_rule(m.RULE)
    assert [s["step"] for s in m.CHAIN] == ["study"]


def test_capital_is_the_structures_maximum_loss_at_its_entry_price():
    assert m.capital(CONDOR) == pytest.approx((5.0 - 3.3) * 100 * 2)  # width less credit
    assert m.capital(PUT) == pytest.approx(205.0)  # the debit
    # A credit wider than its wing (a mispriced row) holds no capital: never a negative one.
    assert m.capital({**CONDOR, "entry_mid": 6.0}) == 0.0


def test_the_position_delta_signs_each_leg_and_a_missing_leg_is_none():
    deltas = {(4285.0, "P"): -0.30, (4280.0, "P"): -0.25, (4295.0, "C"): 0.28, (4300.0, "C"): 0.22}
    # short put +30, long put -25, short call -28, long call +22 (per contract x 100), two contracts.
    assert m.position_delta(CONDOR, deltas) == pytest.approx((30 - 25 - 28 + 22) * 2)
    assert m.position_delta(PUT, {(3970.0, "P"): -0.2}) == pytest.approx(-20.0)
    assert m.position_delta(PUT, {}) is None


def test_a_row_is_the_return_on_capital_with_its_delta_financed_on_it():
    r = m.row(PUT, (4000.0, 3950.0, {(3970.0, "P"): -0.2}), 0.05)
    assert r["net"] == pytest.approx(-207.97 / 205.0)
    assert r["financed"] == pytest.approx(ck.financed_delta(-20.0, 4000.0, 3950.0, 205.0, 0.05, 1) / 205.0)
    assert r["days"] == 1 and r["date"] == "2024-06-03"
    assert m.row(PUT, (None, 3950.0, {(3970.0, "P"): -0.2}), 0.05) is None


def _ts(day: str, hhmm: str) -> float:
    return datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=ZoneInfo("America/New_York")).timestamp()


def test_the_store_reads_the_entry_minutes_spot_and_deltas_and_the_last_spot():
    c = sqlite3.connect(":memory:")
    c.executescript(
        "CREATE TABLE gex_spot_history (symbol TEXT, trade_date TEXT, ts REAL, spot REAL);"
        "CREATE TABLE gex_quote_samples (id INTEGER, symbol TEXT, trade_date TEXT, ts REAL, expiration TEXT);"
        "CREATE TABLE gex_quotes (sample_id INTEGER, side INTEGER, strike REAL, bid REAL, ask REAL, mid REAL,"
        " age_s REAL, delta REAL);"
    )
    day = "2024-06-03"
    for hhmm, spot in (("10:29", 4001.0), ("10:30", 4000.0), ("10:31", 3999.0), ("15:59", 3950.0)):
        c.execute("INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, ?)", (day, _ts(day, hhmm), spot))
    c.execute("INSERT INTO gex_quote_samples VALUES (1, 'SPX', ?, ?, ?)", (day, _ts(day, "10:30"), day))
    c.execute("INSERT INTO gex_quote_samples VALUES (2, 'SPX', ?, ?, ?)", (day, _ts(day, "10:31"), day))
    c.execute("INSERT INTO gex_quotes VALUES (1, 0, 3970.0, 1, 1.1, 1.05, NULL, -0.2)")
    c.execute("INSERT INTO gex_quotes VALUES (2, 0, 3970.0, 1, 1.1, 1.05, NULL, -0.9)")  # after entry: unread
    f0, f1, deltas = m.Store(c).entry(PUT)
    assert (f0, f1) == (4000.0, 3950.0) and deltas == {(3970.0, "P"): -0.2}
