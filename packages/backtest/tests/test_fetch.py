"""The fetch, against a fake vendor: what a session becomes in the store, that it reads back through
the suite's own quote-history reader, and that refusals and re-runs are recorded rather than lost."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest
from cherrypick.core import quotehistory
from cherrypick.core.clock import ET

from cherrypick.backtest import fetch, settings, store
from cherrypick.backtest.vendor import VendorRefusal

DAY = "2026-10-16"  # a Friday, full session
EARLY = "2026-11-27"  # the day after Thanksgiving: 13:00 close
STRIKES = [6000.0 + 5 * i for i in range(-60, 61)]


def _ts(day, hh, mm, ss=0):
    return datetime.fromisoformat(day).replace(hour=hh, minute=mm, second=ss, tzinfo=ET).timestamp()


def _rows(day, *, spot_at=lambda i: 6000.0 + 0.1 * i, close=(16, 0), crossed_at=None, cadence=15):
    """A synthetic vendor session: every strike, both rights, every `cadence` seconds."""
    open_ts, close_ts = _ts(day, 9, 30), _ts(day, *close)
    rows, i, ts = [], 0, open_ts
    while ts <= close_ts:  # includes the close instant, which the build must drop
        spot = None if i == 0 else spot_at(i)  # the vendor's first row has no underlying yet
        for k in STRIKES:
            for right in ("CALL", "PUT"):
                bid, ask = 2.0, 2.2
                if crossed_at == (i, k, right):
                    bid, ask = 2.5, 2.4
                rows.append(
                    {
                        "strike": k,
                        "right": right,
                        "ts": ts,
                        "bid": bid,
                        "ask": ask,
                        "delta": 0.5 if spot else 0.0,
                        "iv": 0.2 if spot else 0.0,
                        "underlying": spot,
                    }
                )
        i += 1
        ts += cadence
    return rows


class FakeClient:
    name = "fake"

    def __init__(self, sessions, refuse=()):
        self.sessions, self.refuse, self.calls = sessions, set(refuse), []

    def expirations(self, root):
        return sorted(self.sessions)

    def greeks(self, root, day, **kw):
        self.calls.append((day, kw))
        if day in self.refuse:
            raise VendorRefusal("permission_denied", "requiring a professional subscription")
        return self.sessions[day]

    def open_interest(self, root, day):
        return {(r, k): 100 for k in STRIKES for r in ("CALL", "PUT")}

    def index_closes(self, symbol, start, end):
        return {"2026-10-15": 6010.0, DAY: 6020.0}


def _settings():
    return settings.resolve(
        {"enabled": True, "datasets": [{"symbol": "SPX", "root": "SPXW", "dte": 0, "from": "2022-05-16"}]}
    )


def test_a_session_is_built_the_recorder_way():
    built = fetch.build_session(_rows(DAY), {}, day=DAY, each_side=20)
    open_ts, close_ts = _ts(DAY, 9, 30), _ts(DAY, 16, 0)
    stamps = [ts for ts, _ in built["samples"]]
    assert stamps[0] == open_ts + 15, "the first row has no underlying, so no sample"
    assert stamps[-1] == close_ts - 15, "the close instant itself is outside the session"
    assert all(len(q) == 80 for _, q in built["samples"]), "20 strikes each side, both rights"
    assert len(built["spots"]) == len(stamps)
    assert len(built["profiles"]) == 77, "every 5 minutes from the open, except the spotless first"
    _, quotes = built["samples"][0]
    assert all(q[5] is None for q in quotes), "age is unknown, never invented"


def test_the_kept_strikes_follow_the_spot_through_the_day():
    built = fetch.build_session(_rows(DAY, spot_at=lambda i: 5900.0 + 0.25 * i), {}, day=DAY, each_side=20)
    first, last = built["samples"][0][1], built["samples"][-1][1]
    assert min(q[1] for q in first) < 5900 < max(q[1] for q in first)
    assert min(q[1] for q in last) > 5900, "a trending spot carries the window with it"


def test_a_crossed_quote_is_dropped_and_gamma_needs_an_iv():
    built = fetch.build_session(_rows(DAY, crossed_at=(5, 6000.0, "CALL")), {}, day=DAY, each_side=20)
    sample = dict(built["samples"])[_ts(DAY, 9, 30) + 5 * 15]
    assert (store.SIDES["CALL"], 6000.0) not in {(q[0], q[1]) for q in sample}
    _, profile = built["profiles"][0]
    assert profile[6000.0]["call_gamma"] > 0
    assert profile[6000.0]["call_iv"] == 20.0, "IV is stored in percent, the recorder's unit"


def test_an_early_close_ends_the_session_at_one():
    built = fetch.build_session(_rows(EARLY, close=(16, 0)), {}, day=EARLY, each_side=20)
    assert built["samples"][-1][0] == _ts(EARLY, 13, 0) - 15


def test_fetch_writes_sessions_that_the_suites_reader_reads_back():
    s = _settings()
    client = FakeClient({DAY: _rows(DAY)})
    report = fetch.fetch(s, client, "SPX", "2026-10-16", "2026-10-16", log=lambda _m: None)
    assert report["fetched"] == 1 and report["coverage"]["complete"] == 1
    samples = quotehistory.session("SPX", DAY, history_db=store.db_path(s, "SPX"))
    assert len(samples) > 1500
    first = samples[0]
    assert first["underlying_price"] == pytest.approx(6000.1)
    assert len(first["calls"]) == 40 and len(first["puts"]) == 40
    assert first["calls"][6000.0]["age_seconds"] is None
    conn = sqlite3.connect(store.db_path(s, "SPX"))
    assert (
        conn.execute("SELECT close FROM daily_closes WHERE trade_date = '2026-10-15'").fetchone()[0] == 6010.0
    )


def test_a_refusal_is_recorded_with_its_reason_and_writes_no_rows():
    s = _settings()
    client = FakeClient({DAY: _rows(DAY)}, refuse={DAY})
    report = fetch.fetch(s, client, "SPX", DAY, DAY, log=lambda _m: None)
    assert report["refused"] == 1
    assert report["coverage"]["refused"] == [{"date": DAY, "reason": "permission_denied"}]
    conn = sqlite3.connect(store.db_path(s, "SPX"))
    assert conn.execute("SELECT COUNT(*) FROM gex_quote_samples").fetchone()[0] == 0


def test_a_complete_session_is_skipped_unless_refetched_and_a_refetch_never_doubles():
    s = _settings()
    client = FakeClient({DAY: _rows(DAY)})
    fetch.fetch(s, client, "SPX", DAY, DAY, log=lambda _m: None)
    again = fetch.fetch(s, client, "SPX", DAY, DAY, log=lambda _m: None)
    assert again["skipped"] == 1 and len(client.calls) == 1
    conn = sqlite3.connect(store.db_path(s, "SPX"))
    before = conn.execute("SELECT COUNT(*) FROM gex_quotes").fetchone()[0]
    fetch.fetch(s, client, "SPX", DAY, DAY, refetch=True, log=lambda _m: None)
    assert conn.execute("SELECT COUNT(*) FROM gex_quotes").fetchone()[0] == before


def test_only_days_with_a_same_day_expiration_are_sessions():
    days = fetch.session_days(
        ["2022-05-13", "2022-05-16", "2022-05-18", "2022-05-21"], "2022-05-13", "2022-05-21"
    )
    assert days == ["2022-05-13", "2022-05-16", "2022-05-18"], "a Saturday listing is not a session"


def test_the_request_asks_for_the_session_s_own_close():
    s = _settings()
    client = FakeClient({EARLY: _rows(EARLY)})
    fetch.fetch(s, client, "SPX", EARLY, EARLY, log=lambda _m: None)
    ((_day, kw),) = client.calls
    assert kw["end_time"] == "13:00:00" and kw["interval"] == "15s" and kw["strike_range"] == 50
