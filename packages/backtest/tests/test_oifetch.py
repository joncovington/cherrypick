"""The open-interest fetch against a fake vendor: what a name-day becomes in `open_interest.db`, which
expiries are kept, and that refusals, re-runs and a lost session are recorded rather than lost."""

from __future__ import annotations

import pytest

from cherrypick.backtest import oifetch, paths, settings
from cherrypick.backtest.vendor import VendorRefusal

DAY = "2026-10-15"


def _settings():
    return settings.resolve(
        {
            "enabled": True,
            "datasets": [{"symbol": "SPX", "root": "SPXW", "dte": 0, "from": "2022-05-16"}],
        }
    )


class FakeClient:
    def __init__(self, refuse=(), fatal=False, nothing=False):
        self.refuse, self.fatal, self.nothing, self.calls = set(refuse), fatal, nothing, []

    def open_interest_all(self, root, day):
        self.calls.append((root, day))
        if self.fatal:
            raise VendorRefusal("unauthenticated", "another login")
        if (root, day) in self.refuse:
            raise VendorRefusal("no_data", "nothing")
        if self.nothing:
            return {}
        return {
            ("2026-10-14", "CALL", 100.0): 5,  # expired before the session: dropped
            ("2026-10-15", "PUT", 95.0): 10,  # expires that day: kept
            ("2026-11-20", "CALL", 105.0): 2000,
            ("2026-11-20", "PUT", 95.0): 3000,
            ("2027-01-22", "CALL", 110.0): 7,  # 99 days on: kept
            ("2027-01-29", "CALL", 110.0): 9,  # 106 days on: past MAX_DAYS, dropped
        }


def test_a_name_day_keeps_each_contract_within_max_days_with_rights_as_one_letter():
    s = _settings()
    out = oifetch.fetch_plan(s, FakeClient(), [(DAY, "AAPL")], log=lambda m: None)
    assert out["complete"] == 1
    conn = oifetch.connect(s)
    rows = [
        tuple(r) for r in conn.execute("SELECT expiration, right, strike, open_interest FROM oi_contracts")
    ]
    assert sorted(rows) == [
        ("2026-10-15", "P", 95.0, 10),
        ("2026-11-20", "C", 105.0, 2000),
        ("2026-11-20", "P", 95.0, 3000),
        ("2027-01-22", "C", 110.0, 7),
    ]
    day = conn.execute("SELECT status, contracts, expirations FROM oi_days").fetchone()
    assert tuple(day) == ("complete", 4, 3)


def test_a_day_with_no_contracts_is_empty_and_fetched_again():
    s = _settings()
    oifetch.fetch_plan(s, FakeClient(nothing=True), [(DAY, "AAPL")], log=lambda m: None)
    conn = oifetch.connect(s)
    assert oifetch.status(conn, "AAPL", DAY) == "empty"
    again = FakeClient()
    oifetch.fetch_plan(s, again, [(DAY, "AAPL")], log=lambda m: None)
    assert again.calls == [("AAPL", DAY)]
    assert oifetch.status(conn, "AAPL", DAY) == "complete"


def test_a_complete_day_is_skipped_unless_refetched_and_a_refetch_replaces_it():
    s = _settings()
    oifetch.fetch_plan(s, FakeClient(), [(DAY, "AAPL")], log=lambda m: None)
    again = FakeClient()
    assert oifetch.fetch_plan(s, again, [(DAY, "AAPL")], log=lambda m: None)["skipped"] == 1
    assert again.calls == []
    oifetch.fetch_plan(s, again, [(DAY, "AAPL")], refetch=True, log=lambda m: None)
    assert oifetch.connect(s).execute("SELECT COUNT(*) FROM oi_contracts").fetchone()[0] == 4  # not doubled


def test_a_refusal_is_recorded_and_the_fetch_goes_on():
    s = _settings()
    out = oifetch.fetch_plan(
        s, FakeClient(refuse={("AAPL", DAY)}), [(DAY, "AAPL"), (DAY, "SPY")], log=lambda m: None
    )
    assert (out["refused"], out["complete"]) == (1, 1)
    conn = oifetch.connect(s)
    assert conn.execute("SELECT reason FROM oi_days WHERE symbol='AAPL'").fetchone()[0].startswith("no_data")


def test_a_lost_session_stops_the_fetch():
    with pytest.raises(VendorRefusal):
        oifetch.fetch_plan(
            _settings(),
            FakeClient(fatal=True),
            [(DAY, "AAPL"), (DAY, "SPY")],
            workers=2,
            log=lambda m: None,
        )


def test_a_stop_starts_no_new_request():
    client = FakeClient()
    out = oifetch.fetch_plan(_settings(), client, [(DAY, "AAPL")], stop=lambda: True, log=lambda m: None)
    assert out["stopped"] and client.calls == []


def test_the_store_sits_in_the_marked_licensed_directory():
    s = _settings()
    oifetch.connect(s).close()
    assert oifetch.db_path(s).parent == paths.vendor_dir(s)
    assert (paths.vendor_dir(s) / paths.LICENSED_MARKER).exists()
