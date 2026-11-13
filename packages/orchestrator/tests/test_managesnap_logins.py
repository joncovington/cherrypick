"""The manage snapshot across broker logins (snapshot v19).

- `history_account` names the account the top-level history belongs to, by the old rule.
- Each `managed` account reads its own history and orders from its own login, by its last 4.
- With no extra login the broker-tool argv are exactly what they were.
"""

import json
import subprocess
from pathlib import Path

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import managesnap

pytestmark = pytest.mark.unit


def _entry(key, login, designated=False, role=None, underlyings=("XYZ",), error=None):
    return {
        "account": f"****{key[-4:]}",
        "account_key": key,
        "login": login,
        "designated": designated,
        "role": role or ("live" if designated else "paper-only"),
        "error": error,
        "underlyings": [{"underlying": u, "legs": []} for u in underlyings],
    }


@pytest.mark.parametrize(
    "entries,expected",
    [
        ([_entry("main-1234", "main", designated=True), _entry("main-5678", "main")], "main-1234"),
        ([_entry("main-1234", "main"), _entry("margin-9999", "margin", role="managed")], "main-1234"),
        ([_entry("main-1234", "main"), _entry("main-5678", "main")], None),
        ([], None),
    ],
)
def test_history_account_follows_the_old_rule(entries, expected):
    assert managesnap.history_account_key(entries) == expected


def test_only_managed_accounts_read_without_error_are_evaluated_elsewhere():
    entries = [
        _entry("main-1234", "main", designated=True),
        _entry("main-5678", "main"),
        _entry("margin-1111", "margin", role="managed"),
        _entry("margin-2222", "margin", role="managed", error="could not read"),
    ]
    assert [e["account_key"] for e in managesnap.evaluated_elsewhere(entries, "main-1234")] == ["margin-1111"]


def _fake(monkeypatch, positions):
    calls = []

    def run(root, argv, timeout=30):
        argv = list(argv)
        calls.append(argv)
        verb = next(a for a in argv if a.startswith("get_"))
        if verb == "get_transactions" and "--account_wide" in argv:
            answer = {"ok": True, "account": [{"id": "|".join(argv[2:4])}]}
        elif verb == "get_transactions":
            answer = {"ok": True, "transactions": {"XYZ": [{"order_id": "|".join(argv[2:4])}]}}
        elif verb == "get_working_orders":
            answer = {"ok": True, "orders": [{"id": 1, "status": "Live"}]}
        else:
            answer = {"ok": False, "error": f"{verb} not faked"}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(answer), stderr="")

    monkeypatch.setattr(managesnap.positions_mod, "run", lambda cfg: positions)
    monkeypatch.setattr(managesnap.doctor, "_run", run)
    for name in ("income_dir", "earnings_times_dir", "skate_dir"):
        monkeypatch.setattr(managesnap, name, lambda: Path("no-such-dir"))
    monkeypatch.setattr(managesnap, "history_db", lambda: Path("no-such-history.db"))
    monkeypatch.setattr(managesnap._execution, "_known_orders_dir", lambda: Path("no-such-known-orders"))
    monkeypatch.setattr(managesnap, "_cached_greeks", lambda syms: {})
    monkeypatch.setattr(managesnap, "_cached_listing", lambda name: [])
    monkeypatch.setattr(cfgmod, "enabled_modules", lambda cfg: {"meic": {"path": "."}})
    monkeypatch.setattr(cfgmod, "module_root", lambda mcfg, name: ".")
    monkeypatch.setattr(cfgmod, "broker_tool", lambda mcfg, name: ["-m", "cherrypick.meic.tt"])
    return calls


def test_a_managed_account_reads_its_own_login_by_its_last_4(monkeypatch):
    positions = {
        "ok": True,
        "broker_module": "meic",
        "accounts": [
            _entry("main-1234", "main", designated=True),
            _entry("margin-1234", "margin", role="managed"),
        ],
    }
    calls = _fake(monkeypatch, positions)
    snap = managesnap.build({})
    assert snap["history_account"] == "main-1234"
    own = snap["accounts_history"]["margin-1234"]
    assert own["transactions"] == {"XYZ": [{"order_id": "--login|margin"}]}
    assert own["working_orders"] and own["working_orders_error"] is None
    margin_calls = [c for c in calls if "--login" in c]
    assert margin_calls and all(
        c[2:4] == ["--login", "margin"] and "--account_suffix" in c for c in margin_calls
    )
    assert all(c[c.index("--account_suffix") + 1] == "1234" for c in margin_calls)
    # The top level stays the designated account's, read with no login and no suffix.
    assert snap["transactions"] == {"XYZ": [{"order_id": "get_transactions|--underlyings"}]}


def test_no_extra_login_means_the_old_argv_and_no_extra_reads(monkeypatch):
    positions = {
        "ok": True,
        "broker_module": "meic",
        "accounts": [_entry("main-1234", "main", designated=True)],
    }
    calls = _fake(monkeypatch, positions)
    snap = managesnap.build({})
    assert snap["accounts_history"] == {}
    assert not any("--login" in c or "--account_suffix" in c for c in calls)
