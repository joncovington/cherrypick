"""Options prints (TimeAndSale, 2026-11-01): an opt-in window event, recorded raw to the cache.

What is pinned here:

* the event is OPT-IN -- a silent declarer never pays for it, and naming it alone does not narrow
  the four default events (SPX is read for gamma x OI by modules that never mention prints);
* it rides the nearest window only, never an extra-expiration window;
* an add goes out as a time series from the session's open (so a reconnect fills its gap), through
  the same paced choke point as every other subscription;
* a print is stored raw and keyed by the feed's index, so a correction or a cancellation replaces
  the row it names, and a withdrawn event is kept as REMOVED rather than vanishing.
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

from cherrypick.core import streamcache, streamrequests
from cherrypick.core.streamer import ChainStreamer

# --------------------------------------------------------------------------- the declaration


def test_a_silent_declarer_never_gets_prints(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    streamrequests.write_request("flies", ["SPX"])
    assert streamrequests.union_window_events()["SPX"] == list(streamrequests.WINDOW_EVENTS)


def test_naming_prints_alone_adds_them_and_keeps_the_four(tmp_path, monkeypatch):
    """gex declares only TimeAndSale; a union reading that as 'gex wants only prints' would drop
    open interest from SPX the moment every other declarer opted down."""
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    streamrequests.write_request("gex", ["SPX"], window_events={"SPX": ["TimeAndSale"]})
    assert streamrequests.union_window_events()["SPX"] == [*streamrequests.WINDOW_EVENTS, "TimeAndSale"]


def test_prints_join_a_narrowed_set_without_widening_it(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    streamrequests.write_request("thin", ["SPY"], window_events={"SPY": ["Quote", "TimeAndSale"]})
    assert streamrequests.union_window_events()["SPY"] == ["Quote", "TimeAndSale"]


def test_the_budget_counts_prints_on_the_nearest_window_only(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    streamrequests.write_request(
        "gex",
        ["SPX"],
        window_events={"SPX": ["TimeAndSale"]},
        expirations={"SPX": ["2099-01-02"]},
    )
    est = streamrequests.estimate_subscriptions(default_strike_count=10, today=None)
    options = (10 + 10 + 1) * 2
    # nearest window: five events; the extra-expiration window: the default four
    assert est["by_symbol"]["SPX"]["subscriptions"] == options * 5 + options * 4


# --------------------------------------------------------------------------- the engine's window


def _engine(events, **kw):
    kw.setdefault("subscribe_per_minute", 0)
    kw.setdefault("subscribe_pace_s", 0)
    return ChainStreamer(
        session_factory=lambda: None,
        db_path=":memory:",
        symbols=["SPX"],
        window_events_for=lambda symbol: events,
        **kw,
    )


def test_prints_ride_the_nearest_window_only():
    eng = _engine([*streamrequests.WINDOW_EVENTS, "TimeAndSale"])
    assert eng._window_events("SPX")[-1] == "TimeAndSale"
    assert "TimeAndSale" not in eng._window_events("SPX@2099-01-02")
    assert eng._wants_time_and_sale()


def test_no_declaration_no_prints():
    eng = _engine(list(streamrequests.WINDOW_EVENTS))
    assert "TimeAndSale" not in eng._window_events("SPX")
    assert not eng._wants_time_and_sale()


class TimeAndSale:  # stand-in for the SDK event class
    pass


class Quote:
    pass


class _FakeSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, message):
        self.messages.append(message)


class _FakeStreamer:
    def __init__(self, *, internals: bool = True) -> None:
        self.calls: list[tuple[str, str, int]] = []
        if internals:
            self._channels = {"TimeAndSale": 7}
            self._websocket = _FakeSocket()

    async def subscribe(self, cls, symbols):
        self.calls.append(("sub", cls.__name__, len(list(symbols))))

    async def unsubscribe(self, cls, symbols):
        self.calls.append(("unsub", cls.__name__, len(list(symbols))))


def test_an_add_is_a_time_series_from_the_open():
    eng = _engine([*streamrequests.WINDOW_EVENTS, "TimeAndSale"])
    fake = _FakeStreamer()
    asyncio.run(eng._send_subs(fake, TimeAndSale, ["a", "b"]))
    (msg,) = fake._websocket.messages
    assert msg["channel"] == 7
    assert [a["symbol"] for a in msg["add"]] == ["a", "b"]
    assert {a["fromTime"] for a in msg["add"]} == {eng._session_open_ms()}
    assert fake.calls == [("sub", "TimeAndSale", 0)]  # the channel opened the SDK's way, nothing added


def test_without_sdk_internals_it_degrades_to_a_plain_subscribe():
    eng = _engine([*streamrequests.WINDOW_EVENTS, "TimeAndSale"])
    fake = _FakeStreamer(internals=False)
    asyncio.run(eng._send_subs(fake, TimeAndSale, ["a"]))
    assert fake.calls == [("sub", "TimeAndSale", 0), ("sub", "TimeAndSale", 1)]


def test_a_remove_and_other_events_are_untouched():
    eng = _engine([*streamrequests.WINDOW_EVENTS, "TimeAndSale"])
    fake = _FakeStreamer()
    asyncio.run(eng._send_subs(fake, TimeAndSale, ["a"], remove=True))
    asyncio.run(eng._send_subs(fake, Quote, ["a"]))
    assert fake.calls == [("unsub", "TimeAndSale", 1), ("sub", "Quote", 1)]
    assert fake._websocket.messages == []


def test_a_window_with_no_class_for_prints_skips_them():
    eng = _engine([*streamrequests.WINDOW_EVENTS, "TimeAndSale"])
    fake = _FakeStreamer()
    classes = {name: type(name, (), {}) for name in streamrequests.WINDOW_EVENTS}
    asyncio.run(eng._window_subs(fake, "SPX", ["a"], classes))
    assert [c[1] for c in fake.calls] == list(streamrequests.WINDOW_EVENTS)


# --------------------------------------------------------------------------- the row and the table


def _event(**over):
    base = dict(
        event_symbol=".SPXW261102C7600",
        index=7_000_000_001,
        time=1_793_400_000_000,
        price=4.4,
        size=3,
        bid_price=4.1,
        ask_price=4.4,
        aggressor_side="BUY",
        spread_leg=False,
        extended_trading_hours=False,
        valid_tick=True,
        type="NEW",
        event_flags=0,
        exchange_code="C",
        exchange_sale_conditions="I",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_a_print_is_stored_raw():
    row = ChainStreamer.time_and_sale_row(_event(), 100.0)
    assert row["aggressor"] == "BUY" and row["spread_leg"] == 0 and row["valid_tick"] == 1
    assert (row["bid"], row["ask"], row["price"], row["size"]) == (4.1, 4.4, 4.4, 3)
    assert row["kind"] == "NEW" and row["event_ms"] == 1_793_400_000_000


def test_a_withdrawn_event_is_kept_as_removed_and_codes_are_named():
    assert ChainStreamer.time_and_sale_row(_event(event_flags=0x02), 1.0)["kind"] == "REMOVED"
    assert ChainStreamer.time_and_sale_row(_event(type="2"), 1.0)["kind"] == "CANCEL"
    assert ChainStreamer.time_and_sale_row(_event(index=None), 1.0) is None


def _cache(tmp_path) -> sqlite3.Connection:
    return streamcache.connect(tmp_path / "cache.db")


def test_a_correction_replaces_the_print_it_names(tmp_path):
    conn = _cache(tmp_path)
    streamcache.write_time_and_sale(conn, ChainStreamer.time_and_sale_row(_event(), 100.0))
    streamcache.write_time_and_sale(
        conn, ChainStreamer.time_and_sale_row(_event(type="CORRECTION", price=4.3), 200.0)
    )
    streamcache.write_time_and_sale(conn, ChainStreamer.time_and_sale_row(_event(index=2), 150.0))
    rows = conn.execute(
        "SELECT idx, price, kind, received_at FROM stream_time_and_sale ORDER BY idx"
    ).fetchall()
    assert [tuple(r) for r in rows] == [(2, 4.4, "NEW", 150.0), (7_000_000_001, 4.3, "CORRECTION", 200.0)]


def test_prune_keeps_the_last_days(tmp_path):
    conn = _cache(tmp_path)
    now = 10 * 86_400.0
    for i, age_days in enumerate((0, 2, 4)):
        streamcache.write_time_and_sale(
            conn, ChainStreamer.time_and_sale_row(_event(index=i), now - age_days * 86_400)
        )
    assert streamcache.prune_time_and_sale(conn, now_ts=now) == 1
    assert conn.execute("SELECT COUNT(*) FROM stream_time_and_sale").fetchone()[0] == 2
