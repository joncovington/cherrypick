"""account-alerts on an extra broker login: its own inbox, status and heartbeat, one account resolved
from the login's own list, and never a guess when that is not exactly one account."""

from __future__ import annotations

import json
import os

import pytest

from cherrypick.core.live import account_alerts as aa


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))


def test_main_keeps_its_paths_and_an_extra_login_gets_its_own():
    assert os.path.basename(aa.inbox_path()) == "account_alerts.db"
    assert os.path.basename(aa.inbox_path("main")) == "account_alerts.db"
    assert os.path.basename(aa.status_path()) == "account_alerts.status.json"
    assert os.path.basename(aa.inbox_path("margin")) == "account_alerts.margin.db"
    assert os.path.basename(aa.status_path("margin")) == "account_alerts.margin.status.json"
    assert aa.package_name() == "account-alerts" and aa.package_name("margin") == "account-alerts.margin"


def test_status_files_never_mix():
    aa._write_status({"events": 1})
    aa._write_status({"events": 9}, "margin")
    assert aa.read_status()["events"] == 1
    assert aa.read_status("margin")["events"] == 9


def test_status_prints_one_logins_file(capsys):
    aa._write_status({"events": 9}, "margin")
    assert aa.main(["--status", "--login", "margin"]) == 0
    assert json.loads(capsys.readouterr().out)["events"] == 9


@pytest.mark.parametrize(
    "accounts,last4,expected",
    [
        (["9ZZ000001234"], None, "9ZZ000001234"),  # the only account
        (["9ZZ000001234", "9ZZ000005678"], None, None),  # several, none named: refuse
        (["9ZZ000001234", "9ZZ000005678"], "5678", "9ZZ000005678"),
        (["9ZZ000001234"], "9999", None),  # named but absent
        ([], None, None),
    ],
)
def test_the_watched_account_is_exactly_one_or_none(accounts, last4, expected):
    assert aa.resolve_login_account(accounts, last4) == expected


def test_a_run_on_an_extra_login_writes_its_own_inbox_and_heartbeat(tmp_path, monkeypatch):
    beats = []
    monkeypatch.setattr(aa._home, "beat", beats.append)

    class _Broker:
        def wait_for_order_alerts(self, ids, timeout):
            clock["t"] += timeout
            return [{"order_id": 7, "status": "Filled"}]

        def orders_today(self):
            return []

    clock = {"t": 0.0}
    out = aa.run(_Broker(), clock=lambda: clock["t"], max_seconds=30.0, log=lambda *_: None, login="margin")
    assert out["events"] == 1 and beats == ["account-alerts.margin"]
    assert [e["order_id"] for e in aa.events_after(aa.inbox_path("margin"), None)] == ["7"]
    assert aa.events_after(aa.inbox_path(), None) == []
    assert aa.read_status("margin")["login"] == "margin"


def test_the_login_broker_is_read_only():
    broker = aa.make_login_broker("margin")
    assert broker._live_gates() == ["account-alerts is read-only"]
