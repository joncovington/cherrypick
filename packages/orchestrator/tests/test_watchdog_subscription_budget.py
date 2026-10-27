"""The subscription-budget check reads its ceiling where the producer's window width is read.

On 2026-10-26 the budget was raised to 20,000 in `config/streamer.json` while the watchdog read only
the suite config's 14,000, so the warning kept firing on an estimate the raised ceiling cleared.
"""

from __future__ import annotations

import json

import pytest
from cherrypick.core import streamrequests

from cherrypick.orchestrator import watchdog


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    # One symbol, one nearest window: (30 + 30 + 1) strikes x 2 rights x 4 events = 488.
    streamrequests.write_request("demo", ["SPY"])
    return tmp_path


def _streamer_config(home, **streamer):
    path = home / "config" / "streamer.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"streamer": {"window_strike_count": 30, **streamer}}), encoding="utf-8")


def _status(cfg):
    (finding,) = watchdog._check_subscription_budget(cfg)
    assert finding.key == "streamer.budget"
    return finding.status


def test_the_producers_own_budget_outranks_the_suite_configs(home):
    _streamer_config(home, subscription_budget=1_000)
    assert _status({"streamer": {"subscription_budget": 400}}) == watchdog.OK


def test_a_low_budget_in_the_producers_own_config_still_warns(home):
    _streamer_config(home, subscription_budget=400)
    assert _status({"streamer": {"subscription_budget": 100_000}}) == watchdog.WARN


def test_the_suite_config_applies_when_the_producers_file_sets_no_budget(home):
    _streamer_config(home)
    assert _status({"streamer": {"subscription_budget": 400}}) == watchdog.WARN
    assert _status({"streamer": {"subscription_budget": 1_000}}) == watchdog.OK


def test_the_default_applies_when_neither_sets_one(home):
    _streamer_config(home)
    assert watchdog._streamer_subscription_budget({}) == streamrequests.DEFAULT_SUBSCRIPTION_BUDGET
    assert _status({}) == watchdog.OK
