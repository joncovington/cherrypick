"""The intraday chain fetch (item 31): exactly the requests asked for, each wholly written or not at
all, only the rows inside its window, resumable, a refusal recorded and retried, a fatal one stopping
the run; and the client method that feeds it. Synthetic rows only."""

from __future__ import annotations

import json
import sqlite3
import threading
import types
from datetime import UTC, datetime

import pytest

from cherrypick.backtest import intradayfetch as ix
from cherrypick.backtest import paths, settings, stores
from cherrypick.backtest.vendor import VendorRefusal
from cherrypick.backtest.vendor import thetadata as td

ENTRY = ("15:35", "15:50", "5m")


def _settings(tmp_path):
    return settings.resolve({"store": {"dir": str(tmp_path / "vendor")}})


def _rows(bars=("15:35", "15:40", "15:45", "15:50"), strikes=2):
    return [
        {"bar": b, "strike": 100.0 + k, "right": r, "bid": 1.0, "ask": 1.1, "delta": 0.3, "iv": 0.4,
         "underlying": 100.5}
        for b in bars
        for k in range(strikes)
        for r in ("CALL", "PUT")
    ]  # fmt: skip


def _key(sym="AAPL", day="2024-02-02", exp="2024-02-09", window=ENTRY):
    return (sym, day, exp, *window)


class FakeWindows:
    def __init__(self, empty=(), refuse=(), no_data=(), fatal_after=None, rows=None):
        self.empty, self.refuse, self.no_data = set(empty), set(refuse), set(no_data)
        self.fatal_after, self.rows = fatal_after, rows
        self.calls = []
        self.lock = threading.Lock()

    def chain_window(self, root, day, expiration, start, end, *, interval="5m", strike_range=None):
        with self.lock:
            self.calls.append((root, day, expiration, start, end, interval))
            if self.fatal_after is not None and len(self.calls) > self.fatal_after:
                raise VendorRefusal("unauthenticated", "another login took the session")
        if root in self.refuse:
            raise VendorRefusal("permission_denied", "not on this tier")
        if root in self.no_data:
            raise VendorRefusal("no_data", "No data found")
        return [] if root in self.empty else (self.rows if self.rows is not None else _rows())


def _db(s):
    return sqlite3.connect(ix.db_path(s))


def test_the_store_lives_in_the_marked_licensed_directory_and_is_declared(tmp_path):
    s = _settings(tmp_path)
    ix.fetch_windows(s, FakeWindows(), [_key()], log=lambda m: None)
    assert ix.db_path(s).parent == paths.vendor_dir(s)
    assert (paths.vendor_dir(s) / paths.LICENSED_MARKER).exists()
    (entry,) = [e for e in stores.STORES if e["file"] == ix.DB_NAME]
    assert entry["built_by"].startswith("python -m cherrypick.backtest fetch-intraday-chains")


def test_a_request_records_what_became_of_it_and_the_rows_it_wrote(tmp_path):
    s = _settings(tmp_path)
    reqs = [_key("AAPL"), _key("DAL"), _key("MSFT"), _key("NKE")]
    fake = FakeWindows(empty={"DAL"}, refuse={"MSFT"}, no_data={"NKE"})
    out = ix.fetch_windows(s, fake, reqs, workers=3, log=lambda m: None)
    assert (out["complete"], out["empty"], out["refused"], out["rows"]) == (1, 2, 1, 16)
    with _db(s) as c:
        by = dict(c.execute("SELECT symbol, status FROM intraday_chain_requests"))
        assert by == {"AAPL": "complete", "DAL": "empty", "MSFT": "refused", "NKE": "empty"}
        assert c.execute(
            "SELECT rows, bars FROM intraday_chain_requests WHERE symbol = 'AAPL'"
        ).fetchone() == (16, 4)
        (detail,) = c.execute("SELECT detail FROM intraday_chain_requests WHERE symbol = 'MSFT'").fetchone()
        assert detail.startswith("permission_denied")


def test_only_rows_inside_the_requested_window_are_kept(tmp_path):
    s = _settings(tmp_path)
    rows = _rows(bars=("15:30", "15:35", "15:50", "15:55"))
    ix.fetch_windows(s, FakeWindows(rows=rows), [_key()], log=lambda m: None)
    with _db(s) as c:
        assert [b for (b,) in c.execute("SELECT DISTINCT bar FROM intraday_chain_quotes ORDER BY bar")] == [
            "15:35",
            "15:50",
        ]


def test_a_rerun_skips_complete_and_empty_requests_and_retries_a_refused_one(tmp_path):
    s = _settings(tmp_path)
    reqs = [_key("AAPL"), _key("DAL"), _key("MSFT")]
    ix.fetch_windows(s, FakeWindows(empty={"DAL"}, refuse={"MSFT"}), reqs, log=lambda m: None)
    again = FakeWindows()
    out = ix.fetch_windows(s, again, reqs, log=lambda m: None)
    assert [c[0] for c in again.calls] == ["MSFT"]
    assert (out["already"], out["complete"]) == (2, 1)
    redo = FakeWindows()
    ix.fetch_windows(s, redo, reqs, refetch=True, log=lambda m: None)
    assert len(redo.calls) == 3


def test_a_refetch_replaces_the_rows_of_its_window_and_leaves_other_windows(tmp_path):
    s = _settings(tmp_path)
    later = _key(window=("09:40", "15:55", "5m"))
    ix.fetch_windows(s, FakeWindows(rows=_rows(bars=("09:40", "15:55"))), [later], log=lambda m: None)
    ix.fetch_windows(s, FakeWindows(), [_key()], log=lambda m: None)
    ix.fetch_windows(
        s, FakeWindows(rows=_rows(bars=("15:40",), strikes=1)), [_key()], refetch=True, log=lambda m: None
    )
    with _db(s) as c:
        assert c.execute("SELECT COUNT(*) FROM intraday_chain_quotes WHERE bar = '15:40'").fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM intraday_chain_quotes WHERE bar = '15:35'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM intraday_chain_quotes WHERE bar = '09:40'").fetchone()[0] == 4


def test_a_fatal_refusal_stops_the_run_instead_of_refusing_every_request(tmp_path):
    s = _settings(tmp_path)
    reqs = [_key(d) for d in ("AAPL", "DAL", "MSFT", "NKE")]
    out = ix.fetch_windows(s, FakeWindows(fatal_after=1), reqs, log=lambda m: None)
    assert out["aborted"].startswith("unauthenticated")
    assert out["complete"] == 1 and out["refused"] == 1


def test_stop_is_asked_before_each_new_request_and_a_rerun_resumes(tmp_path):
    s = _settings(tmp_path)
    reqs = [_key(d) for d in ("AAPL", "DAL", "MSFT")]
    asked = []

    def stop():
        asked.append(1)
        return len(asked) > 1

    out = ix.fetch_windows(s, FakeWindows(), reqs, stop=stop, log=lambda m: None)
    assert out["stopped"] and out["complete"] == 1
    rest = FakeWindows()
    ix.fetch_windows(s, rest, reqs, log=lambda m: None)
    assert [c[0] for c in rest.calls] == ["DAL", "MSFT"]


def test_a_share_class_goes_to_the_vendor_without_its_separator_and_is_stored_as_spelled(tmp_path):
    s = _settings(tmp_path)
    fake = FakeWindows()
    ix.fetch_windows(s, fake, [_key("BRK.B")], log=lambda m: None)
    assert fake.calls[0][0] == "BRKB"
    with _db(s) as c:
        assert c.execute("SELECT DISTINCT symbol FROM intraday_chain_quotes").fetchall() == [("BRK.B",)]


def test_the_request_file_reads_once_each_in_order_and_refuses_a_backwards_window(tmp_path):
    f = tmp_path / "r.json"
    item = {
        "symbol": "aapl",
        "session": "2024-02-02T00:00",
        "expiration": "2024-02-09",
        "start": "15:35",
        "end": "15:50",
    }
    f.write_text(
        json.dumps({"requests": [item, item, {**item, "symbol": "DAL", "interval": "1m"}]}), encoding="utf-8"
    )
    assert ix.read_requests(f) == [
        ("AAPL", "2024-02-02", "2024-02-09", "15:35", "15:50", "5m"),
        ("DAL", "2024-02-02", "2024-02-09", "15:35", "15:50", "1m"),
    ]
    f.write_text(json.dumps([{**item, "start": "15:50", "end": "15:35"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="ends before"):
        ix.read_requests(f)
    f.write_text(json.dumps([{**item, "start": "3:35pm"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="HH:MM"):
        ix.read_requests(f)


# ------------------------------------------------------------------------------------------------
# The client method.


class _Frame:  # the slice of a DataFrame the client uses; CI has no pandas
    def __init__(self, rows):
        self.rows = rows

    def itertuples(self, index=False):
        return (types.SimpleNamespace(**r) for r in self.rows)


def _vendor_row(stamp, **over):
    row = {"timestamp": stamp, "strike": 100.0, "right": "call", "bid": 1.0, "ask": 1.2, "delta": 0.4,
           "implied_vol": 0.35, "underlying_price": 101.0}  # fmt: skip
    row.update(over)
    return row


def test_chain_window_asks_for_the_expiry_and_window_and_returns_et_bars_with_none_for_nan(monkeypatch):
    seen = {}

    def call(method, **kw):
        seen.update(method=method, **kw)
        return _Frame(
            [
                _vendor_row(datetime(2024, 2, 2, 15, 40)),
                _vendor_row(datetime(2024, 2, 2, 20, 45, tzinfo=UTC), implied_vol=float("nan"), bid=None),
            ]
        )

    client = td.ThetaDataClient.__new__(td.ThetaDataClient)
    monkeypatch.setattr(client, "_call", call, raising=False)
    rows = client.chain_window("AAPL", "2024-02-02", "2024-02-09", "15:35", "15:50")
    assert seen["method"] == "option_history_greeks_first_order"
    assert (seen["interval"], seen["start_time"], seen["end_time"]) == ("5m", "15:35:00", "15:50:00")
    assert str(seen["expiration"]) == "2024-02-09" and str(seen["date"]) == "2024-02-02"
    assert "strike_range" not in seen
    assert [r["bar"] for r in rows] == ["15:40", "15:45"], "a zone-aware stamp is read as ET"
    assert rows[0]["right"] == "CALL" and rows[0]["iv"] == 0.35 and rows[0]["underlying"] == 101.0
    assert rows[1]["iv"] is None and rows[1]["bid"] is None
    client.chain_window("AAPL", "2024-02-02", "2024-02-09", "15:35", "15:50", strike_range=20)
    assert seen["strike_range"] == 20


# --------------------------------------------------------------------------- strike_range (item 38)


def _write(tmp_path, items, name="req.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"requests": items}), encoding="utf-8")
    return path


def _item(**over):
    base = {"symbol": "SPY", "session": "2024-02-05", "expiration": "2024-02-09", "start": "10:00",
            "end": "10:15", "interval": "15m"}  # fmt: skip
    base.update(over)
    return base


class RecordingWindows(FakeWindows):
    def __init__(self):
        super().__init__()
        self.ranges = []

    def chain_window(self, root, day, expiration, start, end, *, interval="5m", strike_range=None):
        self.ranges.append(strike_range)
        return super().chain_window(root, day, expiration, start, end, interval=interval)


def test_a_request_may_name_a_strike_range_and_the_client_is_asked_for_exactly_that(tmp_path):
    path = _write(tmp_path, [_item(strike_range=50), _item(session="2024-02-06")])
    keys, ranges = ix.read_request_file(path)
    assert len(keys) == 2 and ranges == {keys[0]: 50}
    assert ix.read_requests(path) == keys, "the plain reader is unchanged for item 31's files"
    fake = RecordingWindows()
    ix.fetch_windows(_settings(tmp_path), fake, keys, strike_ranges=ranges, log=lambda m: None)
    assert fake.ranges == [50, None], "one asked for 50 strikes, the other for the whole chain"


def test_a_file_without_ranges_asks_for_the_whole_chain_as_before(tmp_path):
    keys, ranges = ix.read_request_file(_write(tmp_path, [_item()]))
    assert ranges == {}
    fake = RecordingWindows()
    ix.fetch_windows(_settings(tmp_path), fake, keys, strike_ranges=ranges, log=lambda m: None)
    assert fake.ranges == [None]


@pytest.mark.parametrize("bad", [0, -3, 2.5, "50", True])
def test_a_strike_range_that_is_not_a_positive_whole_number_is_refused_before_any_request(tmp_path, bad):
    with pytest.raises(ValueError, match="strike_range"):
        ix.read_request_file(_write(tmp_path, [_item(strike_range=bad)]))


def test_the_same_window_with_two_ranges_is_refused_not_fetched_with_the_first(tmp_path):
    with pytest.raises(ValueError, match="two strike ranges"):
        ix.read_request_file(_write(tmp_path, [_item(strike_range=50), _item(strike_range=10)]))
    # the same range twice is one request
    keys, ranges = ix.read_request_file(_write(tmp_path, [_item(strike_range=50), _item(strike_range=50)]))
    assert len(keys) == 1 and list(ranges.values()) == [50]
