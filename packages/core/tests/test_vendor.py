"""The vendor switch (core.vendor): on only when switched on with both generic keys set."""

from __future__ import annotations

import json

from cherrypick.core import home, vendor

KEYS = {"vendor_dashboard_url": "https://example.invalid", "vendor_edition_title": "Report"}


def test_on_needs_the_switch_and_both_keys():
    assert vendor.collector_on({"market_report": {"collector": True, **KEYS}})
    assert not vendor.collector_on({"market_report": {"collector": False, **KEYS}})
    assert not vendor.collector_on({"market_report": {"collector": True, "vendor_edition_title": "Report"}})
    assert not vendor.collector_on(
        {"market_report": {"collector": True, **KEYS, "vendor_dashboard_url": " "}}
    )
    assert not vendor.collector_on({})


def test_missing_names_what_is_absent():
    assert vendor.collector_missing({"vendor_edition_title": "Report"}) == ["vendor_dashboard_url"]
    assert vendor.collector_missing(None) == list(vendor.VENDOR_COLLECTOR_KEYS)


def test_the_suite_config_on_disk_decides(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    assert vendor.suite_collector_on() is False  # no config: off
    home.config_path().write_text(
        json.dumps({"market_report": {"collector": True, **KEYS}}), encoding="utf-8"
    )
    assert vendor.suite_collector_on() is True
    home.config_path().write_text("{not json", encoding="utf-8")
    assert vendor.suite_collector_on() is False
