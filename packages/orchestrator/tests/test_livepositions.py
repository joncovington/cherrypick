"""Broker positions vs the live ledgers (orchestrator/livepositions.py), safeguard 2(c), 2026-10-08."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from cherrypick.orchestrator import livepositions as lp
from cherrypick.orchestrator import watchdog

ACCT = "5WT12345678"
SHORT_5800 = {"underlying": "SPX", "expiry": "2026-10-08", "right": "P", "strike": 5800.0, "qty": -1}


@pytest.fixture(autouse=True)
def midday(monkeypatch):
    # The legs below expire 2026-10-08; pin the clock inside that session.
    monkeypatch.setattr(lp.timeutil, "now_et", lambda *a, **k: datetime(2026, 10, 8, 12, 0))


def module(legs=(), armed=True, pending=0, underlyings=("SPX",)):
    return {
        "ok": True,
        "legs": list(legs),
        "armed_today": armed,
        "pending": pending,
        "underlyings": list(underlyings),
    }


def pos(symbol, qty, direction, under="SPX"):
    return {
        "symbol": symbol,
        "quantity": str(qty),
        "quantity_direction": direction,
        "underlying_symbol": under,
    }


def broker(*rows):
    calls = []

    def fetch(number):
        calls.append(number)
        return {"ok": True, "positions": list(rows)}

    fetch.calls = calls
    return fetch


def test_nothing_armed_or_open_never_asks_the_broker():
    fetch = broker()
    out = lp.evaluate(
        {"flies": module(armed=False), "bwb": module(armed=False)}, {"flies": ACCT}, fetch, None
    )
    assert out["verdict"] == lp.IDLE and fetch.calls == []


def test_a_matching_account_is_match_and_numbers_are_masked():
    fetch = broker(pos("SPXW  261008P05800000", 1, "Short"), pos("AAPL  261016C00250000", 1, "Long", "AAPL"))
    out = lp.evaluate({"flies": module([SHORT_5800])}, {"flies": ACCT}, fetch, None)
    assert out["verdict"] == lp.MATCH
    assert out["accounts"][0]["account"].startswith("****") and ACCT not in json.dumps(out)


def test_an_unrecorded_fill_settles_once_then_mismatches_on_the_second_check():
    fetch = broker(pos("SPXW  261008P05800000", 1, "Short"), pos("SPXW  261008P05790000", 1, "Long"))
    first = lp.evaluate({"flies": module([SHORT_5800])}, {"flies": ACCT}, fetch, None)
    assert first["verdict"] == lp.SETTLING
    second = lp.evaluate({"flies": module([SHORT_5800])}, {"flies": ACCT}, fetch, first)
    assert second["verdict"] == lp.MISMATCH
    (diff,) = second["accounts"][0]["confirmed"]
    assert diff["kind"] == "unrecorded" and diff["strike"] == 5790.0


def test_two_modules_on_one_account_are_compared_together():
    bwb_leg = dict(SHORT_5800, qty=-2)
    fetch = broker(pos("SPXW  261008P05800000", 3, "Short"))
    out = lp.evaluate(
        {"flies": module([SHORT_5800]), "bwb": module([bwb_leg])}, {"flies": ACCT, "bwb": ACCT}, fetch, None
    )
    assert out["verdict"] == lp.MATCH and fetch.calls == [ACCT]
    assert sorted(out["accounts"][0]["modules"]) == ["bwb", "flies"]


def test_an_unreadable_broker_or_module_is_unknown_not_match():
    def down(number):
        return {"ok": False, "error": "401"}

    out = lp.evaluate({"flies": module([SHORT_5800])}, {"flies": ACCT}, down, None)
    assert out["verdict"] == lp.UNKNOWN
    out = lp.evaluate({"flies": {"ok": False, "error": "boom"}}, {}, broker(), None)
    assert out["verdict"] == lp.UNKNOWN
    out = lp.evaluate({"flies": module([SHORT_5800])}, {"flies": None}, broker(), None)
    assert out["verdict"] == lp.UNKNOWN


@pytest.fixture
def state(tmp_path, monkeypatch):
    path = tmp_path / "live_positions.last.json"
    monkeypatch.setattr(lp, "state_path", lambda: path)

    def write(payload, age_minutes=1):
        at = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
        path.write_text(json.dumps({**payload, "generated_at": at.isoformat()}), encoding="utf-8")

    return write


def test_watchdog_raises_critical_on_a_confirmed_mismatch(state):
    diff = {
        "underlying": "SPX",
        "expiry": "2026-10-08",
        "right": "P",
        "strike": 5790.0,
        "expected": 0,
        "broker": 1,
        "kind": "unrecorded",
    }
    state(
        {
            "verdict": lp.MISMATCH,
            "accounts": [{"account": "****5678", "modules": ["flies"], "confirmed": [diff]}],
        }
    )
    (f,) = watchdog._check_live_positions(in_session=True)
    assert f.status == watchdog.CRITICAL and "5790P: broker +1, ledger +0 (unrecorded)" in f.message


def test_watchdog_is_quiet_when_stale_out_of_session_or_settling(state):
    state({"verdict": lp.MISMATCH, "accounts": []}, age_minutes=60)
    assert watchdog._check_live_positions(in_session=True) == []
    state({"verdict": lp.MISMATCH, "accounts": []})
    assert watchdog._check_live_positions(in_session=False) == []
    state({"verdict": lp.SETTLING, "accounts": []})
    (f,) = watchdog._check_live_positions(in_session=True)
    assert f.status == watchdog.OK
    state({"verdict": lp.UNKNOWN, "unknown": ["flies: no designated account"]})
    (f,) = watchdog._check_live_positions(in_session=True)
    assert f.status == watchdog.WARN


def test_only_a_recent_verdict_counts_as_the_previous_check(state):
    # Yesterday's last difference must not confirm today's first one.
    state({"verdict": lp.SETTLING, "accounts": []}, age_minutes=5)
    assert lp.recent_state() is not None
    state({"verdict": lp.SETTLING, "accounts": []}, age_minutes=60)
    assert lp.recent_state() is None


def test_after_the_close_todays_expired_contracts_are_left_out_of_both_sides(monkeypatch):
    # First real run, 2026-10-08 evening: the ledger had settled the day's two flies, the broker still
    # listed them until its overnight processing -- six "unrecorded" legs that were nothing of the sort.
    monkeypatch.setattr(lp.timeutil, "now_et", lambda *a, **k: datetime(2026, 10, 8, 16, 30))
    fetch = broker(pos("SPXW  261008P05800000", 1, "Short"), pos("SPXW  261009P05800000", 1, "Short"))
    out = lp.evaluate({"bwb": module()}, {"bwb": ACCT}, fetch, None)
    (acct,) = out["accounts"]
    assert acct["expired_unprocessed"] == 1
    assert [(d["expiry"], d["kind"]) for d in acct["diffs"]] == [("2026-10-09", "unrecorded")]


# --------------------------------------------------------------------------- broker outage (item 3)
def test_broker_failing_for_three_minutes_is_critical_and_two_is_not():
    now = datetime(2026, 10, 8, 11, 0, tzinfo=timezone.utc)
    health = {
        "failing_since": (now - timedelta(minutes=4)).isoformat(),
        "failures": 9,
        "last_error": "ConnectError: Temporary failure in name resolution",
    }
    f = watchdog._broker_outage_finding("flies", "Flies", health, now, 3)
    assert f.status == watchdog.CRITICAL and "4 min" in f.message and "name resolution" in f.message
    young = dict(health, failing_since=(now - timedelta(minutes=2)).isoformat())
    assert watchdog._broker_outage_finding("flies", "Flies", young, now, 3) is None
    assert watchdog._broker_outage_finding("flies", "Flies", {"failing_since": None}, now, 3) is None
    assert watchdog._broker_outage_finding("flies", "Flies", {}, now, 3) is None
