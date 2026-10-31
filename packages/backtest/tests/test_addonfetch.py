"""The add-on weeklies fetch (item 35): exactly the spans asked for, each wholly written or not at all,
a span replacing only its own sessions' rows, an old per-expiry row covering what it covered, a refusal
recorded and retried, a fatal one stopping the run; and the SPXW weekly filter of the open-interest
keep. Synthetic rows only."""

from __future__ import annotations

import json
import sqlite3

import pytest

from cherrypick.backtest import addonfetch as af
from cherrypick.backtest import oifetch, paths, settings, stores
from cherrypick.backtest.vendor import VendorRefusal
from cherrypick.backtest.vendor import thetadata as td

EXP = "2025-04-17"  # a Thursday standing in for Good Friday


def _settings(tmp_path):
    return settings.resolve({"store": {"dir": str(tmp_path / "vendor")}})


def _vendor_rows(right, first, last, spot=5000.0, strikes=(4990.0, 5000.0, 5060.0)):
    days = sorted({first, last})
    return [
        {"day": d, "ts": 1_700_000_000 + i, "strike": k, "right": right, "bid": 1.0, "ask": 1.1,
         "delta": -0.3, "iv": 0.2, "underlying": spot}
        for i, d in enumerate(days)
        for k in strikes
    ]  # fmt: skip


class FakeClient:
    def __init__(self, refuse=(), fatal_after=None):
        self.refuse, self.fatal_after, self.calls = set(refuse), fatal_after, []

    def minute_span(self, root, expiration, first, last, *, right, start_time, end_time, strike_range):
        self.calls.append((root, expiration, first, last, right, start_time, end_time, strike_range))
        if self.fatal_after is not None and len(self.calls) > self.fatal_after:
            raise VendorRefusal("unauthenticated", "another login took the session")
        if expiration in self.refuse:
            raise VendorRefusal("vendor_error", "dropped")
        return _vendor_rows(right[0].upper(), first, last)


def _count(s, where="1=1"):
    conn = sqlite3.connect(af.db_path(s))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM q WHERE {where}").fetchone()[0]
    finally:
        conn.close()


def test_a_span_asks_for_puts_across_the_session_and_calls_at_ten_and_keeps_what_is_near_the_index(tmp_path):
    s = _settings(tmp_path)
    client = FakeClient()
    out = af.fetch_spans(s, client, [(EXP, "2025-04-11", EXP)], log=lambda m: None)
    assert (out["complete"], out["refused"]) == (1, 0)
    put_call, call_call = client.calls
    assert put_call == ("SPXW", EXP, "2025-04-11", EXP, "put", "09:30:00", "16:15:00", 60)
    assert call_call == ("SPXW", EXP, "2025-04-11", EXP, "call", "10:00:00", "10:01:00", 3)
    # the put at 5060 is more than 50 above the index and is dropped; the calls are all kept
    assert _count(s, "right = 'P'") == 4 and _count(s, "right = 'C'") == 6
    assert af.db_path(s).parent == paths.vendor_dir(s)
    assert (paths.vendor_dir(s) / paths.LICENSED_MARKER).exists()
    (entry,) = [e for e in stores.STORES if e["file"] == af.DB_NAME]
    assert "fetch-addon-weeklies" in entry["built_by"]


def test_a_span_replaces_only_its_own_sessions(tmp_path):
    s = _settings(tmp_path)
    af.fetch_spans(s, FakeClient(), [(EXP, "2025-04-11", EXP)], log=lambda m: None)
    before = _count(s)
    af.fetch_spans(s, FakeClient(), [(EXP, "2025-04-11", "2025-04-11")], refetch=True, log=lambda m: None)
    assert _count(s) == before  # the one session was replaced, the other kept
    assert _count(s, f"day = '{EXP}'") > 0


def test_a_complete_span_is_skipped_and_a_refused_one_retried(tmp_path):
    s = _settings(tmp_path)
    span = (EXP, "2025-04-11", EXP)
    bad = af.fetch_spans(s, FakeClient(refuse={EXP}), [span], log=lambda m: None)
    assert bad["refused"] == 1 and _count(s) == 0
    again = FakeClient()
    ok = af.fetch_spans(s, again, [span], log=lambda m: None)
    assert ok["complete"] == 1 and len(again.calls) == 2
    third = FakeClient()
    done = af.fetch_spans(s, third, [span], log=lambda m: None)
    assert done["already"] == 1 and done["todo"] == 0 and third.calls == []


def test_an_old_per_expiry_row_covers_a_span_that_starts_no_earlier(tmp_path):
    s = _settings(tmp_path)
    conn = af.connect(s)
    conn.execute("INSERT INTO fetched VALUES ('2025-04-25', 'complete', 10, NULL, 0)")
    conn.commit()
    conn.close()
    client = FakeClient()
    out = af.fetch_spans(
        s,
        client,
        [("2025-04-25", "2025-04-18", "2025-04-25"), ("2025-04-25", "2025-04-17", "2025-04-17")],
        log=lambda m: None,
    )
    assert out["already"] == 1 and out["todo"] == 1  # the Thursday before the old start is not covered
    assert {c[2] for c in client.calls} == {"2025-04-17"}


def test_a_fatal_refusal_stops_the_run_and_the_limit_and_stop_bound_it(tmp_path):
    s = _settings(tmp_path)
    spans = [(f"2025-05-{d:02d}", f"2025-05-{d - 6:02d}", f"2025-05-{d:02d}") for d in (9, 16, 23, 30)]
    out = af.fetch_spans(s, FakeClient(fatal_after=2), spans, log=lambda m: None)
    assert "aborted" in out and out["complete"] == 1
    s2 = _settings(tmp_path / "two")
    assert af.fetch_spans(s2, FakeClient(), spans, limit=2, log=lambda m: None)["complete"] == 2
    s3 = _settings(tmp_path / "three")
    stopped = af.fetch_spans(s3, FakeClient(), spans, stop=lambda: True, log=lambda m: None)
    assert stopped["stopped"] and stopped["complete"] == 0


def test_a_request_file_reads_in_order_once_and_refuses_a_span_outside_its_expiry(tmp_path):
    f = tmp_path / "r.json"
    one = {"expiration": EXP, "first": "2025-04-11", "last": EXP}
    f.write_text(json.dumps({"requests": [one, one, {"expiration": EXP, "start": EXP, "end": EXP}]}))
    assert af.read_requests(f) == [(EXP, "2025-04-11", EXP), (EXP, EXP, EXP)]
    f.write_text(json.dumps([{"expiration": EXP, "first": "2025-04-11", "last": "2025-04-18"}]))
    with pytest.raises(ValueError):
        af.read_requests(f)
    f.write_text(json.dumps([{"expiration": EXP, "first": EXP, "last": "2025-04-11"}]))
    with pytest.raises(ValueError):
        af.read_requests(f)


def test_the_vendor_rows_leave_as_the_stores_columns():
    puts = _vendor_rows("P", "2025-04-11", EXP)
    rows = af.rows_from_vendor(puts, _vendor_rows("C", EXP, EXP), EXP)
    assert all(len(r) == 10 and r[0] == EXP for r in rows)
    assert not any(r[3] == "P" and r[4] == 5060.0 for r in rows)  # above the index plus 50


class _Frame:
    def __init__(self, rows):
        self._rows = rows

    def itertuples(self, index=False):
        return iter(self._rows)


def test_the_client_method_reads_the_session_day_in_new_york_and_turns_nan_into_none():
    import datetime as dt
    from zoneinfo import ZoneInfo

    nan = float("nan")
    row = type("R", (), {})()
    row.timestamp = dt.datetime(2025, 4, 17, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    row.strike, row.bid, row.ask, row.delta, row.implied_vol, row.underlying_price = (
        5000,
        nan,
        1.0,
        -0.3,
        0.2,
        5001,
    )
    seen = {}

    class Client(td.ThetaDataClient):
        def __init__(self):
            pass

        def _call(self, method, **kw):
            seen.update(method=method, **kw)
            return _Frame([row])

    (out,) = Client().minute_span(
        "SPXW",
        EXP,
        "2025-04-11",
        EXP,
        right="put",
        start_time="09:30:00",
        end_time="16:15:00",
        strike_range=60,
    )
    assert seen["interval"] == "1m" and seen["right"] == "put" and seen["strike_range"] == 60
    assert (
        out["day"] == EXP
        and out["right"] == "P"
        and out["bid"] is None
        and out["ts"] == int(row.timestamp.timestamp())
    )


def test_spxw_open_interest_keeps_only_its_weeklies_within_ten_days():
    report = {
        ("2025-04-21", "CALL", 5000.0): 1,  # a Monday daily: dropped
        ("2025-04-17", "PUT", 5000.0): 2,  # the Thursday before Good Friday: kept
        ("2025-04-25", "CALL", 5000.0): 3,  # a Friday inside ten days: kept
        ("2025-05-02", "PUT", 5000.0): 4,  # a Friday, 18 days on: dropped
    }
    kept = oifetch.keep(report, "2025-04-16", "SPXW")
    assert [(e, r) for e, r, _, _ in kept] == [("2025-04-17", "P"), ("2025-04-25", "C")]
    # any other root keeps every expiry within MAX_DAYS, as before
    assert len(oifetch.keep(report, "2025-04-16", "AAPL")) == 4
    assert len(oifetch.keep(report, "2025-04-16")) == 4
