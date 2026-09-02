"""report.live_run — the phase-5 live-tagged view, isolated from promotion by construction.

Live P&L is a separate function over a separate config key (`live_db`); calibrate reads
`report.run` (paper) and can never see a live ledger.
"""

import inspect
import sqlite3

import pytest

from cherrypick.orchestrator import calibrate, report

pytestmark = pytest.mark.unit


def _meic_db(path, rows):  # (symbol, risk_profile, pnl, fees, exit_time)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, symbol TEXT, risk_profile TEXT, "
        "pnl REAL, fees REAL, exit_time TEXT)"
    )
    conn.executemany(
        "INSERT INTO ic_trades (symbol, risk_profile, pnl, fees, exit_time) VALUES (?, ?, ?, ?, ?)", rows
    )
    conn.commit()
    conn.close()


@pytest.fixture
def env(tmp_path):
    (tmp_path / "meic").mkdir()
    _meic_db(tmp_path / "meic" / "paper.db", [("SPX", "conservative", 100.0, 5.0, "2026-07-10T15:45")])
    _meic_db(
        tmp_path / "meic" / "live.db",
        [
            ("SPX", "conservative", -20.0, 6.0, "2026-07-10T15:50"),
            ("SPX", "conservative", 40.0, 6.0, "2026-07-11T15:50"),
        ],
    )
    cfg = {
        "modules": {
            "meic": {
                "enabled": True,
                "path": str(tmp_path / "meic"),
                "live_db": str(tmp_path / "meic" / "live.db"),
                "paper": {"paper_db": "paper.db", "trade_schema": "meic_ic"},
            },
            "flies": {  # paper-only module: no live_db, ever
                "enabled": True,
                "path": str(tmp_path / "meic"),
                "paper": {"paper_db": "paper.db", "trade_schema": "meic_ic"},
            },
        }
    }
    return cfg


def test_live_run_reads_the_live_ledger_and_tags_everything(env):
    out = report.live_run(env)
    assert out["live"] is True
    m = out["modules"]["meic"]
    assert m["ok"] is True and m["live"] is True
    # net = (-20-6) + (40-6) = 8 — the live ledger, not the paper one (which holds +95).
    assert m["net_pnl"] == pytest.approx(8.0)
    assert out["suite"]["net_pnl"] == pytest.approx(8.0)
    # No data_epoch: that is a paper-measurement concept.
    assert "data_epoch" not in out


def test_module_without_live_db_is_expected_not_an_error(env):
    out = report.live_run(env)
    f = out["modules"]["flies"]
    assert f["ok"] is False and f["live"] is True and "no live_db" in f["reason"]


def test_missing_live_ledger_reads_as_not_yet(env, tmp_path):
    env["modules"]["meic"]["live_db"] = str(tmp_path / "meic" / "nope.db")
    out = report.live_run(env)
    assert out["modules"]["meic"]["reason"] == "no live ledger yet"


def test_session_filter(env):
    out = report.live_run(env, session="2026-07-11")
    assert out["modules"]["meic"]["net_pnl"] == pytest.approx(34.0)


def test_paper_run_never_touches_the_live_ledger(env):
    out = report.run(env)
    m = out["modules"]["meic"]
    assert "live" not in m
    assert m["net_pnl"] == pytest.approx(95.0)  # the paper number, unmoved by live_db's presence


def test_calibrate_cannot_see_live():
    """The isolation invariant, stated as code: promotion readings go through report.run only —
    calibrate must never grow a reference to the live reader or the live_db key."""
    src = inspect.getsource(calibrate)
    assert "live_run" not in src and "live_db" not in src
