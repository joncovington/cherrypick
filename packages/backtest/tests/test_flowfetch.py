"""The flow fetch against a fake vendor: what a name-day becomes in `flow.db`, the open interest either
side of an order, and that refusals, the newest session and re-runs are recorded rather than lost."""

from __future__ import annotations

import json

import pytest

from cherrypick.backtest import flowfetch, settings
from cherrypick.backtest.vendor import VendorRefusal

DAY, NEXT = "2026-10-15", "2026-10-16"  # Thursday, Friday
EXP = "2026-11-20"


def _settings():
    return settings.resolve(
        {"enabled": True, "datasets": [{"symbol": "SPX", "root": "SPXW", "dte": 0, "from": "2022-05-16"}]}
    )


def _print(ts_s, size, price, condition=18, right="CALL"):
    return {"expiration": EXP, "strike": 100.0, "right": right, "ts_ms": int(ts_s * 1000), "size": size,
            "price": price, "bid": price - 0.10, "ask": price, "condition": condition}  # fmt: skip


class FakeClient:
    def __init__(self, refuse=(), fatal=False, no_next=False):
        self.refuse, self.fatal, self.no_next, self.calls = set(refuse), fatal, no_next, []

    def trades(self, root, day):
        self.calls.append(("trades", root, day))
        if self.fatal:
            raise VendorRefusal("unauthenticated", "another login")
        if (root, day) in self.refuse:
            raise VendorRefusal("no_data", "nothing")
        return [
            _print(36000, 400, 5.00),  # $200K bought: kept
            _print(36001, 10, 5.00),  # one second later, same side: the same order
            _print(37000, 5, 1.00),  # $500: counted in the histogram, not kept
            _print(38000, 1000, 5.00, condition=131),  # a multi-leg leg: never an order
        ]

    def open_interest_all(self, root, day):
        self.calls.append(("oi", root, day))
        if day == NEXT and self.no_next:
            raise VendorRefusal("no_data", "not yet reported")
        return {(EXP, "CALL", 100.0): 1000 if day == DAY else 1300}


def test_a_name_day_keeps_its_large_orders_with_open_interest_either_side():
    s = _settings()
    out = flowfetch.fetch(s, FakeClient(), ["AAPL"], DAY, DAY, log=lambda m: None)
    assert out["complete"] == 1
    conn = flowfetch.connect(s)
    (o,) = conn.execute("SELECT * FROM flow_orders").fetchall()
    assert (o["side"], o["size"], o["prints"], o["oi_before"], o["oi_after"]) == (
        "bought",
        410,
        2,
        1000,
        1300,
    )
    day = conn.execute("SELECT * FROM flow_days").fetchone()
    assert (day["status"], day["prints"], day["single_leg_prints"], day["orders"], day["kept"]) == (
        "complete", 4, 3, 2, 1)  # fmt: skip
    assert sum(json.loads(day["histogram"])) == 2  # both orders, the small one included


def test_until_the_next_report_the_day_awaits_its_open_interest_and_is_fetched_again():
    s = _settings()
    flowfetch.fetch(s, FakeClient(no_next=True), ["AAPL"], DAY, DAY, log=lambda m: None)
    conn = flowfetch.connect(s)
    assert flowfetch.status(conn, "AAPL", DAY) == "awaiting_oi"
    assert conn.execute("SELECT oi_after FROM flow_orders").fetchone()[0] is None
    client = FakeClient()
    flowfetch.fetch(s, client, ["AAPL"], DAY, DAY, log=lambda m: None)
    assert ("trades", "AAPL", DAY) in client.calls  # not skipped
    assert flowfetch.status(conn, "AAPL", DAY) == "complete"
    assert conn.execute("SELECT COUNT(*) FROM flow_orders").fetchone()[0] == 1  # replaced, not doubled


def test_a_complete_day_is_skipped_unless_refetched():
    s = _settings()
    flowfetch.fetch(s, FakeClient(), ["AAPL"], DAY, DAY, log=lambda m: None)
    again = FakeClient()
    assert flowfetch.fetch(s, again, ["AAPL"], DAY, DAY, log=lambda m: None)["skipped"] == 1
    assert again.calls == []
    flowfetch.fetch(s, again, ["AAPL"], DAY, DAY, refetch=True, log=lambda m: None)
    assert again.calls


def test_a_refusal_is_recorded_and_the_fetch_goes_on():
    s = _settings()
    out = flowfetch.fetch(
        s, FakeClient(refuse={("AAPL", DAY)}), ["AAPL", "MSFT"], DAY, DAY, log=lambda m: None
    )
    assert (out["refused"], out["complete"]) == (1, 1)
    conn = flowfetch.connect(s)
    assert flowfetch.status(conn, "AAPL", DAY) == "refused"
    assert (
        conn.execute("SELECT reason FROM flow_days WHERE symbol='AAPL'").fetchone()[0].startswith("no_data")
    )


def test_a_lost_session_stops_the_fetch():
    with pytest.raises(VendorRefusal):
        flowfetch.fetch(_settings(), FakeClient(fatal=True), ["AAPL", "MSFT"], DAY, DAY, log=lambda m: None)


def test_the_store_sits_in_the_marked_licensed_directory():
    from cherrypick.backtest import paths

    s = _settings()
    flowfetch.connect(s).close()
    assert flowfetch.db_path(s).parent == paths.vendor_dir(s)
    assert (paths.vendor_dir(s) / paths.LICENSED_MARKER).exists()
