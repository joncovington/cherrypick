"""The position evaluation's snapshot: positions, market metrics and trade history, all reads."""

import json
import subprocess

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import managesnap

POSITIONS = {
    "ok": True,
    "broker_module": "meic",
    "accounts": [{"account": "****1234", "underlyings": [{"underlying": "GLD", "legs": []}]}],
}


def _fake(monkeypatch, answers):
    calls = []

    def run(root, argv, timeout=30):
        calls.append((list(argv), timeout))
        verb = next(a for a in argv if a.startswith("get_"))
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(answers[verb]), stderr="")

    monkeypatch.setattr(managesnap.positions_mod, "run", lambda cfg: POSITIONS)
    monkeypatch.setattr(managesnap.doctor, "_run", run)
    monkeypatch.setattr(cfgmod, "enabled_modules", lambda cfg: {"meic": {"path": "."}})
    monkeypatch.setattr(cfgmod, "module_root", lambda mcfg, name: ".")
    monkeypatch.setattr(cfgmod, "broker_tool", lambda mcfg, name: ["-m", "cherrypick.meic.tt"])
    return calls


def test_the_snapshot_carries_each_underlyings_trade_history(monkeypatch):
    calls = _fake(
        monkeypatch,
        {
            "get_market_overview": {"ok": True, "metrics": [{"symbol": "GLD", "last": 381.65}]},
            "get_transactions": {"ok": True, "transactions": {"GLD": [{"order_id": 1}]}},
        },
    )
    snap = managesnap.build({})
    assert snap["snapshot_version"] == 3
    assert snap["transactions"] == {"GLD": [{"order_id": 1}]}
    assert snap["transactions_error"] is None
    (tx_argv, tx_timeout) = next(c for c in calls if "get_transactions" in c[0])
    assert tx_argv[tx_argv.index("--underlyings") + 1] == "GLD"
    assert "--since" in tx_argv and tx_timeout > 35  # a year of history outlasts the quote timeout


def test_a_failed_history_read_is_recorded_not_fatal(monkeypatch):
    _fake(
        monkeypatch,
        {
            "get_market_overview": {"ok": True, "metrics": []},
            "get_transactions": {"ok": False, "error": "HTTP 503"},
        },
    )
    snap = managesnap.build({})
    assert snap["positions"]["ok"]
    assert snap["transactions"] == {}
    assert "HTTP 503" in snap["transactions_error"]


def test_the_benchmark_is_quoted_with_each_beta_but_its_history_is_not_read(monkeypatch):
    calls = _fake(
        monkeypatch,
        {
            "get_market_overview": {
                "ok": True,
                "metrics": [
                    {"symbol": "GLD", "last": 381.65, "beta": "0.12", "corr_spy_3month": "0.2"},
                    {"symbol": "SPY", "last": 667.1, "beta": "1.0"},
                ],
            },
            "get_transactions": {"ok": True, "transactions": {}},
        },
    )
    snap = managesnap.build({})
    assert snap["benchmark"] == "SPY"
    assert snap["metrics"]["GLD"]["beta"] == "0.12"
    assert snap["metrics"]["GLD"]["corr_spy_3month"] == "0.2"
    assert snap["metrics"]["SPY"]["last"] == 667.1
    (mo_argv, _) = next(c for c in calls if "get_market_overview" in c[0])
    assert {"GLD", "SPY"} <= set(mo_argv)  # SPY is not held, and is quoted anyway
    (tx_argv, _) = next(c for c in calls if "get_transactions" in c[0])
    assert "SPY" not in tx_argv


def test_a_missing_beta_is_null_never_zero(monkeypatch):
    _fake(
        monkeypatch,
        {
            "get_market_overview": {"ok": True, "metrics": [{"symbol": "GLD", "last": 381.65}]},
            "get_transactions": {"ok": True, "transactions": {}},
        },
    )
    assert managesnap.build({})["metrics"]["GLD"]["beta"] is None
