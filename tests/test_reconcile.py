"""Tests for the paper↔live isolation guard (orchestrator.reconcile).

Unit lane: open-position readers vs temp paper DBs, and the verdict logic (flat / drift / unknown) with
the broker subprocess stubbed so no real module checkout or broker is needed. Also asserts the account
number is masked everywhere it surfaces.
"""

import sqlite3

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import reconcile

pytestmark = pytest.mark.unit


def _meic_db(path, rows):  # (symbol, risk_profile, exit_time)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ic_trades (id INTEGER PRIMARY KEY, symbol TEXT, risk_profile TEXT, "
        "pnl REAL, fees REAL, exit_time TEXT)"
    )
    conn.executemany("INSERT INTO ic_trades (symbol, risk_profile, exit_time) VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "meic").mkdir()
    # two open (exit_time NULL) + one closed
    _meic_db(
        tmp_path / "meic" / "paper.db",
        [("SPX", "conservative", None), ("XSP", "aggressive", None), ("SPX", "conservative", "2026-07-10")],
    )
    monkeypatch.setattr(cfgmod, "ROOT", tmp_path)
    cfg = {
        "modules": {
            "meic": {
                "enabled": True,
                "path": str(tmp_path / "meic"),
                "paper": {"paper_db": "paper.db", "trade_schema": "meic_ic"},
            }
        }
    }
    return tmp_path, cfg


def test_paper_open_positions_counts_only_unclosed(env):
    _, cfg = env
    paper = reconcile._paper_open_positions(cfg)
    assert paper["meic"]["ok"] is True
    assert paper["meic"]["open_count"] == 2
    assert paper["meic"]["symbols"] == ["SPX", "XSP"]


def test_verdict_flat_when_broker_account_empty(env, monkeypatch):
    _, cfg = env
    monkeypatch.setattr(
        reconcile,
        "_query_broker",
        lambda cfg, forced: {"reachable": True, "account": "****1234", "open_positions": [], "balances": {}},
    )
    out = reconcile.run(cfg)
    assert out["verdict"] == reconcile.FLAT and out["ok"] is True
    assert out["paper"]["meic"]["open_count"] == 2  # paper ledger still reported for context


def test_verdict_drift_when_real_account_has_positions(env, monkeypatch):
    _, cfg = env
    monkeypatch.setattr(
        reconcile,
        "_query_broker",
        lambda cfg, forced: {
            "reachable": True,
            "account": "****1234",
            "open_positions": [{"symbol": "AAPL", "quantity": 1}],
            "balances": {},
        },
    )
    out = reconcile.run(cfg)
    assert out["verdict"] == reconcile.DRIFT
    text, worst = reconcile.format_report(out)
    assert "DRIFT" in text and "1 OPEN position" in text and worst == 2


def test_verdict_unknown_when_broker_unreachable(env, monkeypatch):
    _, cfg = env
    monkeypatch.setattr(
        reconcile, "_query_broker", lambda cfg, forced: {"reachable": False, "detail": "no session"}
    )
    out = reconcile.run(cfg)
    assert out["verdict"] == reconcile.UNKNOWN and out["ok"] is False
    text, worst = reconcile.format_report(out)
    assert "could not be checked" in text and worst == 1


def test_query_broker_masks_account_and_uses_get_positions(env, monkeypatch):
    tmp_path, cfg = env

    class _Proc:
        def __init__(self, stdout):
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0

    calls = []

    def fake_run(root, argv, timeout=30):
        calls.append(argv[-1])
        if argv[-1] == "get_positions":
            return _Proc('{"ok": true, "account_number": "5WU987654321", "positions": []}')
        return _Proc('{"ok": true, "balances": {"buying-power": "1000"}}')

    monkeypatch.setattr(reconcile.doctor, "_run", fake_run)
    broker = reconcile._query_broker(cfg, None)
    assert broker["reachable"] is True
    assert broker["account"] == "****4321"  # masked, never the full number
    assert "987654321" not in str(broker)
    assert broker["balances"] == {"buying-power": "1000"}
    assert "get_positions" in calls and "get_account_info" in calls
