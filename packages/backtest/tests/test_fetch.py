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


def test_refused_index_closes_fall_back_to_the_last_underlying_and_never_stop_the_fetch():
    """The index tier is separate from the options tier; a free one refuses old closes. The fetch
    carries on, and the session's last underlying price stands in, under its own source label."""

    class NoIndex(FakeClient):
        def index_closes(self, symbol, start, end):
            raise VendorRefusal("permission_denied", "index history requiring a STANDARD subscription")

    s = _settings()
    report = fetch.fetch(s, NoIndex({DAY: _rows(DAY)}), "SPX", DAY, DAY, log=lambda _m: None)
    assert report["fetched"] == 1
    conn = sqlite3.connect(store.db_path(s, "SPX"))
    close, source = conn.execute(
        "SELECT close, source FROM daily_closes WHERE trade_date = ?", (DAY,)
    ).fetchone()
    assert source == "thetadata:last_underlying" and close == pytest.approx(6000.0 + 0.1 * 1559)


def test_a_lost_session_stops_the_fetch_instead_of_refusing_every_remaining_day():
    """One session per account: another login with the same key ends this one, and every later
    request fails. Those failures say nothing about the sessions asked for, so none is recorded."""

    class Kicked(FakeClient):
        def greeks(self, root, day, **kw):
            raise VendorRefusal("unauthenticated", "StatusCode.UNAUTHENTICATED")

    s = _settings()
    days = {"2026-10-14": _rows("2026-10-14"), DAY: _rows(DAY)}
    report = fetch.fetch(s, Kicked(days), "SPX", "2026-10-14", DAY, log=lambda _m: None)
    assert report["aborted"]["reason"] == "unauthenticated"
    assert report["coverage"]["refused"] == [] and report["refused"] == 0


def test_a_minute_cadence_asks_for_the_vendor_s_minute_interval_in_its_own_file():
    s = _settings()
    client = FakeClient({DAY: _rows(DAY, cadence=60)})
    fetch.fetch(s, client, "SPX", DAY, DAY, cadence=60, log=lambda _m: None)
    ((_day, kw),) = client.calls
    assert kw["interval"] == "1m"
    assert store.db_path(s, "SPX", 60).exists() and not store.db_path(s, "SPX").exists()


def test_a_nan_quote_is_no_quote():
    """NaN passes every comparison usable_quote makes; 2022 sessions carry NaN bids."""
    rows = _rows(DAY)
    for r in rows:
        if r["strike"] == 6000.0 and r["right"] == "PUT":
            r["bid"] = float("nan")
    built = fetch.build_session(rows, {}, day=DAY, each_side=20)
    assert all((q[0], q[1]) != (store.SIDES["PUT"], 6000.0) for _, qs in built["samples"] for q in qs)


def test_a_session_that_cannot_be_written_is_recorded_and_the_fetch_goes_on(monkeypatch):
    """An error writing one session once left a fetch pulling all of history and writing none."""
    s = _settings()
    days = {"2026-10-14": _rows("2026-10-14"), DAY: _rows(DAY)}
    real = fetch.write_session

    def flaky(conn, built, *, day, **kw):
        if day == "2026-10-14":
            raise sqlite3.IntegrityError("NOT NULL constraint failed: gex_quotes.bid")
        return real(conn, built, day=day, **kw)

    monkeypatch.setattr(fetch, "write_session", flaky)
    report = fetch.fetch(s, FakeClient(days), "SPX", "2026-10-14", DAY, log=lambda _m: None)
    assert report["fetched"] == 1 and report["refused"] == 1
    (refusal,) = report["coverage"]["refused"]
    assert refusal == {"date": "2026-10-14", "reason": "build_error"}


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


def _kept_each_side(s, cadence=60):
    conn = sqlite3.connect(store.db_path(s, "SPX", cadence))
    try:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM gex_quotes WHERE sample_id = (SELECT MIN(sample_id) FROM gex_quotes) "
            "AND side = ?",
            (store.SIDES["CALL"],),
        ).fetchone()
    finally:
        conn.close()
    return n // 2


def test_the_wide_tag_keeps_forty_strikes_each_side_in_its_own_file():
    plain, wide = _settings(), store.tagged(_settings(), "wide")
    fetch.fetch(
        plain, FakeClient({DAY: _rows(DAY, cadence=60)}), "SPX", DAY, DAY, cadence=60, log=lambda _m: None
    )
    fetch.fetch(
        wide, FakeClient({DAY: _rows(DAY, cadence=60)}), "SPX", DAY, DAY, cadence=60, log=lambda _m: None
    )
    assert store.db_path(wide, "SPX", 60).name == "SPX-60s-wide.db"
    assert store.db_path(plain, "SPX", 60).name == "SPX-60s.db"
    assert (_kept_each_side(plain), _kept_each_side(wide)) == (20, 40)


def test_an_undeclared_tag_or_one_wider_than_the_request_refuses():
    with pytest.raises(ValueError, match="unknown store tag"):
        store.tagged(_settings(), "huge")
    narrow = settings.resolve({**_settings(), "store": {**_settings()["store"], "request_strike_range": 30}})
    with pytest.raises(ValueError, match="request_strike_range"):
        store.tagged(narrow, "wide")


def test_a_stop_by_cancels_the_sessions_not_yet_pulled_and_says_stopped():
    s = _settings()
    days = {d: _rows(d, cadence=60) for d in ("2026-10-13", "2026-10-14", "2026-10-15", DAY)}
    report = fetch.fetch(
        s, FakeClient(days), "SPX", "2026-10-13", DAY, cadence=60, stop=lambda: True, log=lambda _m: None
    )
    assert report["stopped"] and report["fetched"] == 1, "the session in flight is kept, the rest wait"
    again = fetch.fetch(s, FakeClient(days), "SPX", "2026-10-13", DAY, cadence=60, log=lambda _m: None)
    assert again["skipped"] == 1 and again["fetched"] == 3 and not again.get("stopped")


def test_workers_up_to_the_concurrent_limit_pull_that_many_sessions_at_once():
    """A session's two requests run one after the other, so four workers is four requests in flight,
    Standard's limit; more workers than the limit are held to it."""
    import threading

    days = {f"2026-10-{d:02d}": _rows(f"2026-10-{d:02d}", cadence=60) for d in (5, 6, 7, 8, 9, 13)}

    class Counting(FakeClient):
        def __init__(self, sessions):
            super().__init__(sessions)
            self.lock, self.now, self.most = threading.Lock(), 0, 0
            self.gate = threading.Barrier(4, timeout=5)

        def greeks(self, root, day, **kw):
            with self.lock:
                self.now += 1
                self.most = max(self.most, self.now)
            try:
                if self.most <= 4 and len(self.calls) < 4:
                    self.gate.wait()  # the first four only finish once all four are in flight
            except threading.BrokenBarrierError:
                pass
            finally:
                rows = super().greeks(root, day, **kw)
                with self.lock:
                    self.now -= 1
            return rows

    client = Counting(days)
    fetch.fetch(
        _settings(), client, "SPX", "2026-10-05", "2026-10-13", cadence=60, workers=8, log=lambda _m: None
    )
    assert client.most == 4
