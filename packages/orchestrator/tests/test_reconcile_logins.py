"""reconcile and positions across broker logins (`broker.logins`).

- Every login's accounts are read, an extra login through `tt --login NAME`.
- An account on a login flagged `manage` is `managed`: positions expected, never DRIFT.
- An extra login that cannot be read is one erroring entry, never the whole check.
- With no `broker.logins` the argv and result are what they were before named logins.
"""

import json

import pytest

from cherrypick.orchestrator import config as cfgmod
from cherrypick.orchestrator import positions, reconcile

pytestmark = pytest.mark.unit


class _Proc:
    def __init__(self, payload):
        self.stdout = json.dumps(payload)
        self.stderr = ""
        self.returncode = 0


# Two logins, both holding an account ending 1234 (the collision case), each with one position.
_ACCOUNTS = {"main": ["5WU000001234"], "margin": ["9ZZ000001234"]}


def _fake_run(calls, broken=()):
    def run(root, argv, timeout=30):
        argv = list(argv[2:])  # drop ["-m", "cherrypick.meic.tt"]
        login = "main"
        if argv[:1] == ["--login"]:
            login, argv = argv[1], argv[2:]
        calls.append((login, argv[0]))
        if login in broken:
            return _Proc({"ok": False, "error": "token revoked"})
        cmd = argv[0]
        if cmd == "list_accounts":
            return _Proc({"ok": True, "accounts": [{"account_number": n} for n in _ACCOUNTS[login]]})
        num = argv[argv.index("--account_number") + 1]
        if cmd == "get_positions":
            return _Proc(
                {"ok": True, "account_number": num, "positions": [{"symbol": "AAPL", "quantity": 1}]}
            )
        return _Proc(
            {"ok": True, "balances": {}, "margin_or_cash": "Margin" if login == "margin" else "Cash"}
        )

    return run


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    (tmp_path / "meic").mkdir()
    monkeypatch.setattr(cfgmod, "ROOT", tmp_path)
    monkeypatch.setattr(reconcile, "_designated_numbers", lambda cfg: {"5WU000001234"})
    return {"modules": {"meic": {"enabled": True, "path": str(tmp_path / "meic")}}}


def test_no_logins_key_is_main_only_and_argv_unchanged(cfg, monkeypatch):
    calls = []
    monkeypatch.setattr(reconcile.doctor, "_run", _fake_run(calls))
    broker = reconcile._query_broker(cfg, None)
    assert broker["logins"] == ["main"]
    assert {login for login, _ in calls} == {"main"}
    [entry] = broker["accounts"]
    assert entry["role"] == reconcile.LIVE and entry["account_key"] == "main-1234"


def test_every_login_is_read_and_a_managed_account_is_not_drift(cfg, monkeypatch):
    cfg["broker"] = {"logins": [{"name": "margin", "label": "Reg T", "manage": True}]}
    calls = []
    monkeypatch.setattr(reconcile.doctor, "_run", _fake_run(calls))
    monkeypatch.setattr(reconcile, "_paper_open_positions", lambda cfg: {})
    result = reconcile.run(cfg)
    by_key = {a["account_key"]: a for a in result["broker"]["accounts"]}
    assert set(by_key) == {"main-1234", "margin-1234"}
    assert by_key["margin-1234"]["role"] == reconcile.MANAGED
    assert by_key["margin-1234"]["login_label"] == "Reg T"
    assert by_key["margin-1234"]["designated"] is False
    assert result["verdict"] == reconcile.FLAT
    text, worst = reconcile.format_report(result)
    assert "margin ****1234 (managed - expected)" in text and worst == 0
    assert "000001234" not in json.dumps(result)


def test_an_unflagged_extra_login_with_positions_is_drift(cfg, monkeypatch):
    cfg["broker"] = {"logins": [{"name": "margin"}]}
    monkeypatch.setattr(reconcile.doctor, "_run", _fake_run([]))
    monkeypatch.setattr(reconcile, "_paper_open_positions", lambda cfg: {})
    result = reconcile.run(cfg)
    assert result["verdict"] == reconcile.DRIFT
    assert "margin ****1234: 1 open position(s) in a paper-only account" in reconcile.summary(result)


def test_an_extra_login_that_cannot_be_read_is_one_entry_not_the_whole_check(cfg, monkeypatch):
    cfg["broker"] = {"logins": [{"name": "margin", "manage": True}]}
    monkeypatch.setattr(reconcile.doctor, "_run", _fake_run([], broken={"margin"}))
    broker = reconcile._query_broker(cfg, None)
    assert broker["reachable"] is True
    errored = [a for a in broker["accounts"] if a.get("error")]
    assert len(errored) == 1 and errored[0]["login"] == "margin"
    assert "token revoked" in errored[0]["error"]


def test_designation_never_reaches_an_extra_login(cfg, monkeypatch):
    # Even a designated number that happens to sit on another login is not "live" there.
    monkeypatch.setattr(reconcile, "_designated_numbers", lambda cfg: {"5WU000001234", "9ZZ000001234"})
    cfg["broker"] = {"logins": [{"name": "margin"}]}
    monkeypatch.setattr(reconcile.doctor, "_run", _fake_run([]))
    by_key = {a["account_key"]: a for a in reconcile._query_broker(cfg, None)["accounts"]}
    assert by_key["margin-1234"]["role"] == reconcile.PAPER_ONLY


def test_positions_filters_by_account_key(cfg, monkeypatch):
    cfg["broker"] = {"logins": [{"name": "margin", "manage": True}]}
    monkeypatch.setattr(reconcile.doctor, "_run", _fake_run([]))
    monkeypatch.setattr(positions, "_cached_marks", lambda syms: {})
    monkeypatch.setattr(positions, "_live_marks", lambda root, syms, tool: {})
    both = positions.run(cfg, account="1234")
    assert [a["account_key"] for a in both["accounts"]] == ["main-1234", "margin-1234"]
    one = positions.run(cfg, account="margin-1234")
    assert [(a["login"], a["role"]) for a in one["accounts"]] == [("margin", reconcile.MANAGED)]


def test_broker_logins_skips_bad_and_duplicate_names():
    cfg = {
        "broker": {
            "logins": [
                {"name": "margin"},
                {"name": "margin"},
                {"name": "Bad"},
                "x",
                {"name": "main", "manage": True},
            ]
        }
    }
    assert cfgmod.broker_logins(cfg) == [
        {"name": "main", "label": None, "manage": True},
        {"name": "margin", "label": None, "manage": False},
    ]
    assert cfgmod.broker_logins({}) == [{"name": "main", "label": None, "manage": False}]
    assert cfgmod.login_argv("main") == [] and cfgmod.login_argv("margin") == ["--login", "margin"]
