"""`features.backtest` fails CLOSED: shown only when config, licence, key and data all hold, and
anything missing or unreadable keeps it hidden. Each condition is shown to fail on its own."""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from cherrypick.orchestrator import backtestgate, capabilities

TODAY = date(2026, 10, 17)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("CHERRYPICK_HOME", str(h))
    (h / "config").mkdir(parents=True)
    return h


def _config(home, **over):
    cfg = {"enabled": True, **over}
    (home / "config" / "backtest.json").write_text(json.dumps(cfg), encoding="utf-8")


def _store(home, status="complete"):
    d = home / "data" / "backtest" / "vendor" / "thetadata"
    d.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(d / "SPX-60s.db")
    conn.execute("CREATE TABLE IF NOT EXISTS vendor_sessions (symbol TEXT, trade_date TEXT, status TEXT)")
    conn.execute("INSERT INTO vendor_sessions VALUES ('SPX', '2026-10-16', ?)", (status,))
    conn.commit()
    conn.close()


def _gate(present=True):
    seen = []

    def key_present(service, key):
        seen.append((service, key))
        return present

    return backtestgate.backtest_feature(today=TODAY, key_present=key_present), seen


def test_everything_present_shows_it(home):
    _config(home)
    _store(home)
    out, seen = _gate()
    assert out == {"available": True, "reasons": []}
    assert seen == [("cherrypick-thetadata", "api_key")]


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda h: _store(h), "no backtest config"),
        (lambda h: (_config(h, enabled=False), _store(h)), "backtest disabled"),
        (
            lambda h: (_config(h, vendor={"license": {"terminated_on": "2026-10-01"}}), _store(h)),
            "vendor licence ended",
        ),
        (lambda h: _config(h), "no vendor data"),
        (lambda h: (_config(h), _store(h, status="refused")), "no vendor data"),
    ],
)
def test_each_missing_condition_hides_it(home, setup, reason):
    setup(home)
    out, _ = _gate()
    assert out["available"] is False and reason in out["reasons"]


def test_no_key_hides_it_and_a_config_less_machine_never_asks_the_keyring(home):
    _config(home)
    _store(home)
    out, _ = _gate(present=False)
    assert out["available"] is False and "no vendor key in the keyring" in out["reasons"]
    (home / "config" / "backtest.json").unlink()
    out, seen = _gate()
    assert out["available"] is False and seen == [], "no config, no secret read"


def test_an_unreadable_config_or_keyring_fails_closed(home, monkeypatch):
    (home / "config" / "backtest.json").write_text("{not json", encoding="utf-8")
    _store(home)
    assert _gate()[0]["available"] is False
    _config(home)
    import keyring

    def boom(*_a):
        raise RuntimeError("no backend")

    monkeypatch.setattr(keyring, "get_password", boom)
    assert backtestgate.backtest_feature(today=TODAY)["available"] is False


def test_the_suite_features_carry_it(home, monkeypatch):
    monkeypatch.setattr(backtestgate, "backtest_feature", lambda **_k: {"available": False, "reasons": ["x"]})
    assert capabilities.gated_features({})["features"]["backtest"] is False
