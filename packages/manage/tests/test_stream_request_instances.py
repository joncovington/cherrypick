"""manage declares each account's legs to the producer that quotes it: its login's own instance when
it has one (`stream_instances`), else main -- and main's request file is as it always was."""

from __future__ import annotations

import json

import pytest
from cherrypick.core import streamrequests as _sr

from cherrypick.manage import stream_request


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))


def _acct(login, underlying):
    return {
        "login": login,
        "trades": [{"underlying": underlying, "legs": []}],
        "portfolio": {"benchmark": "SPY"},
    }


def _legs(path):
    return json.loads(path.read_text(encoding="utf-8"))["legs"]


def test_without_instances_main_holds_every_account_as_before():
    result = {"accounts": [_acct("main", "AAA"), _acct("margin", "BBB")]}
    path = stream_request.write(result)
    assert _legs(path) == ["AAA", "BBB", "SPY"]
    assert not _sr.requests_dir("margin").exists()


def test_an_instance_gets_its_login_s_accounts_and_main_the_rest():
    result = {"accounts": [_acct("main", "AAA"), _acct("margin", "BBB")], "stream_instances": ["margin"]}
    main = stream_request.write(result)
    assert _legs(main) == ["AAA", "SPY"]
    assert _legs(_sr.requests_dir("margin") / "manage.json") == ["BBB", "SPY"]


def test_an_instance_whose_accounts_hold_nothing_is_emptied():
    stream_request.write({"accounts": [_acct("margin", "BBB")], "stream_instances": ["margin"]})
    stream_request.write({"accounts": [_acct("main", "AAA")], "stream_instances": ["margin"]})
    assert _legs(_sr.requests_dir("margin") / "manage.json") == []
