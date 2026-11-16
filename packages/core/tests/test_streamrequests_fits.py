"""A request priced before it is written (`fits`), and the one ceiling every reader resolves."""

from __future__ import annotations

import json

import pytest

from cherrypick.core import home, streamrequests


@pytest.fixture
def cphome(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return tmp_path


def streamer_config(block: dict) -> None:
    path = home.config_path("streamer")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"streamer": block}), encoding="utf-8")


def test_the_producers_own_config_outranks_the_suite_config(cphome):
    streamer_config({"subscription_budget": 9_000, "window_strike_count": 40})
    suite = {"streamer": {"subscription_budget": 20_000, "window_strike_count": 80}}
    assert streamrequests.configured_budget(suite) == 9_000
    assert streamrequests.configured_strike_count(suite) == 40


def test_the_suite_config_then_the_default(cphome):
    assert streamrequests.configured_budget({"streamer": {"subscription_budget": 20_000}}) == 20_000
    assert streamrequests.configured_budget({}) == streamrequests.DEFAULT_SUBSCRIPTION_BUDGET
    assert streamrequests.configured_strike_count({}) == streamrequests.DEFAULT_WINDOW_STRIKE_COUNT
    (cphome / "config.json").write_text(
        json.dumps({"streamer": {"subscription_budget": 15_000}}), encoding="utf-8"
    )
    assert streamrequests.configured_budget() == 15_000  # read from disk when none is passed


def test_the_candidate_replaces_its_modules_file_and_nothing_is_written(cphome):
    streamer_config({"subscription_budget": 1_000, "window_strike_count": 10})
    big = streamrequests.build_payload(
        ["SPX"], expirations={"SPX": ["2099-01-02", "2099-01-09", "2099-01-16"]}
    )
    streamrequests.write_payload("demo", big)
    small = streamrequests.build_payload(
        ["SPX"], nearest_window={"SPX": False}, window_events={"SPX": ["Quote"]}
    )
    before = streamrequests.request_path("demo").read_text(encoding="utf-8")
    assert streamrequests.fits("demo", small)["total"] == 0  # no window at all
    assert streamrequests.request_path("demo").read_text(encoding="utf-8") == before
    assert streamrequests.budget_status(default_strike_count=10, budget=1_000)["total"] > 0  # the file again


def test_another_modules_file_still_counts(cphome):
    streamer_config({"subscription_budget": 100, "window_strike_count": 10})
    streamrequests.write_payload("other", streamrequests.build_payload(["SPX"]))
    status = streamrequests.fits("demo", streamrequests.build_payload(["QQQ"]))
    assert status["over"] is True and set(status["by_symbol"]) == {"SPX", "QQQ"}
