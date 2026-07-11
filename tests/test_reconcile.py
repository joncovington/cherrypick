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


def _flat_acct(account, designated=False):
    return {
        "account": account,
        "open_positions": [],
        "open_count": 0,
        "balances": {},
        "designated": designated,
    }


def _pos_acct(account, n=1, designated=False):
    return {
        "account": account,
        "open_positions": [{"symbol": "AAPL", "quantity": 1}] * n,
        "open_count": n,
        "balances": {},
        "designated": designated,
    }


def test_verdict_flat_when_all_accounts_empty(env, monkeypatch):
    _, cfg = env
    monkeypatch.setattr(
        reconcile,
        "_query_broker",
        lambda cfg, forced: {
            "reachable": True,
            "accounts": [_flat_acct("****4222"), _flat_acct("****8569")],
            "total_open": 0,
            "undesignated_open": 0,
        },
    )
    out = reconcile.run(cfg)
    assert out["verdict"] == reconcile.FLAT and out["ok"] is True
    assert out["paper"]["meic"]["open_count"] == 2  # paper ledger still reported for context


def test_verdict_drift_when_any_account_has_positions(env, monkeypatch):
    _, cfg = env
    # first account flat, SECOND account carries a position -> DRIFT (the single-account bug this guards)
    monkeypatch.setattr(
        reconcile,
        "_query_broker",
        lambda cfg, forced: {
            "reachable": True,
            "accounts": [_flat_acct("****4222"), _pos_acct("****8569")],
            "total_open": 1,
            "undesignated_open": 1,
        },
    )
    out = reconcile.run(cfg)
    assert out["verdict"] == reconcile.DRIFT
    text, worst = reconcile.format_report(out)
    assert "DRIFT" in text and "1 OPEN position" in text and "****8569" in text and worst == 2
    assert "checked 2 real account(s)" in text


def test_designated_live_account_with_positions_is_expected_not_drift(env, monkeypatch):
    _, cfg = env
    # the DESIGNATED live account holds a position (expected); no other account does -> FLAT
    monkeypatch.setattr(
        reconcile,
        "_query_broker",
        lambda cfg, forced: {
            "reachable": True,
            "accounts": [_flat_acct("****4222"), _pos_acct("****8569", designated=True)],
            "total_open": 1,
            "undesignated_open": 0,
        },
    )
    out = reconcile.run(cfg)
    assert out["verdict"] == reconcile.FLAT and out["ok"] is True
    text, _ = reconcile.format_report(out)
    assert "live - expected" in text and "expected)" in text and "DRIFT" not in text


def test_verdict_unknown_when_broker_unreachable(env, monkeypatch):
    _, cfg = env
    monkeypatch.setattr(
        reconcile, "_query_broker", lambda cfg, forced: {"reachable": False, "detail": "no session"}
    )
    out = reconcile.run(cfg)
    assert out["verdict"] == reconcile.UNKNOWN and out["ok"] is False
    text, worst = reconcile.format_report(out)
    assert "could not be checked" in text and worst == 1


def test_query_broker_checks_every_account_and_masks(env, monkeypatch):
    _, cfg = env

    class _Proc:
        def __init__(self, stdout):
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0

    seen_position_accounts = []

    def fake_run(root, argv, timeout=30):
        cmd = argv[1]  # ["src/tt.py", <cmd>, ...]
        if cmd == "list_accounts":
            return _Proc(
                '{"ok": true, "accounts": ['
                '{"account_number": "5WU111114222"}, {"account_number": "5WU222228569"}]}'
            )
        num = argv[argv.index("--account_number") + 1]
        if cmd == "get_positions":
            seen_position_accounts.append(num)
            # the SECOND account carries a position — must not be missed
            positions = [] if num == "5WU111114222" else [{"symbol": "AAPL", "quantity": 1}]
            import json as _json

            return _Proc(_json.dumps({"ok": True, "account_number": num, "positions": positions}))
        return _Proc('{"ok": true, "balances": {"buying-power": "1000"}}')

    monkeypatch.setattr(reconcile.doctor, "_run", fake_run)
    broker = reconcile._query_broker(cfg, None)
    assert broker["reachable"] is True
    # BOTH accounts were queried by their full number...
    assert seen_position_accounts == ["5WU111114222", "5WU222228569"]
    # ...and the drift in the second account is captured
    assert broker["total_open"] == 1
    accounts = {a["account"]: a for a in broker["accounts"]}
    assert set(accounts) == {"****4222", "****8569"}
    assert accounts["****8569"]["open_count"] == 1
    # never leak a full account number anywhere in the returned structure
    assert "111114222" not in str(broker) and "222228569" not in str(broker)
