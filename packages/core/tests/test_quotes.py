"""core.quotes: one-off snapshots through a streamer instance's own request file and cache."""

from __future__ import annotations

import pytest

from cherrypick.core import quotes, streamcache
from cherrypick.core import streamrequests as sr


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))
    yield
    sr.set_instance(None)


class _Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _fill(instance, rows, *, greeks=True, at=1_000_000.0):
    """Stand in for the producer: write quote (and greek) rows into the instance's cache."""
    conn = streamcache.connect(streamcache.instance_cache_path(instance))
    for sym, bid, ask in rows:
        if sym.startswith(".") or bid is not None:
            conn.execute(
                "INSERT OR REPLACE INTO stream_quotes(symbol, bid, ask, mid, updated_at) VALUES (?,?,?,?,?)",
                (sym, bid, ask, (bid + ask) / 2, at),
            )
        if sym.startswith(".") and greeks:
            conn.execute(
                "INSERT OR REPLACE INTO stream_greeks(symbol, delta, gamma, theta, vega, iv, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (sym, -0.25, 0.01, -0.05, 0.9, 0.31, at),
            )
    conn.commit()
    conn.close()


def _trade(instance, sym, last, at=1_000_000.0):
    conn = streamcache.connect(streamcache.instance_cache_path(instance))
    conn.execute(
        "INSERT OR REPLACE INTO stream_trades(symbol, last, updated_at) VALUES (?,?,?)", (sym, last, at)
    )
    conn.commit()
    conn.close()


def test_symbols_normalize_to_cache_keys():
    assert quotes.to_streamer("SPY   270219P00580000") == ".SPY270219P580"
    assert quotes.to_streamer(".SPY270219P580") == ".SPY270219P580"
    assert quotes.to_streamer(" spy ") == "SPY"
    assert quotes.grid("spy", ["270115", "270219"], "p", [580, 582.5]) == [
        ".SPY270115P580",
        ".SPY270115P582.5",
        ".SPY270219P580",
        ".SPY270219P582.5",
    ]
    assert quotes.strike_range("540:550:5") == [540, 545, 550]
    with pytest.raises(ValueError):
        quotes.grid("SPY", ["2702"], "P", [580])


@pytest.mark.parametrize(
    "bid,ask,usable,reason",
    [
        (1.00, 1.10, True, ""),
        (0.00, 0.10, False, "zero_bid"),
        (1.20, 1.10, False, "crossed"),
        (1.00, 1.40, False, "too_wide"),  # 0.40 > 15% of 1.20
        (0.05, 0.10, True, ""),  # under the $0.06 floor
        (None, 1.00, False, "no_quote"),
    ],
)
def test_the_quote_filter(bid, ask, usable, reason):
    assert quotes.judge(bid, ask) == (usable, reason)


def test_snapshot_waits_for_every_leg_reads_it_and_removes_its_request():
    clock = _Clock()
    seen = {}

    def sleep(_s):
        # The producer picks the request up between polls.
        files = list(sr.requests_dir("margin").glob("adhoc-quotes-*.json"))
        seen["files"] = [f.name for f in files]
        seen["main"] = list(sr.requests_dir("main").glob("adhoc-quotes-*.json"))
        _fill(
            "margin",
            [(".SPY270219P580", 7.80, 8.75), (".SPY270219P560", 6.30, 7.18), ("SPY", 690.80, 690.83)],
        )
        clock.t += 5

    out = quotes.snapshot(
        ["SPY", "SPY   270219P00580000", ".SPY270219P560"], clock=clock, sleep=sleep, wait_seconds=60
    )
    assert seen["files"] and not seen["main"]  # asked the margin instance, never main
    assert out["missing"] == []
    assert out["request_removed"] and not list(sr.requests_dir("margin").glob("*.json"))
    row = out["rows"][".SPY270219P580"]
    assert row["mid"] == pytest.approx(8.275)
    assert row["delta"] == -0.25 and row["iv"] == 0.31
    assert row["occ"] == "SPY   270219P00580000"
    assert row["usable"] is True
    assert out["rows"]["SPY"]["mid"] == pytest.approx(690.815)


def test_an_index_counts_as_filled_by_its_trade_and_a_leg_without_greeks_waits():
    clock = _Clock()
    _trade("margin", "XSP", 691.04)
    _fill("margin", [(".XSP270219P580", 7.81, 8.74)], greeks=False)

    def sleep(_s):
        clock.t += 30

    out = quotes.snapshot(["XSP", ".XSP270219P580"], clock=clock, sleep=sleep, wait_seconds=60)
    assert out["missing"] == [".XSP270219P580"]
    assert out["rows"]["XSP"]["last"] == 691.04
    assert ".XSP270219P580" not in out["rows"]
    assert out["request_removed"]


def test_the_request_is_removed_when_the_read_raises(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("cache unreadable")

    monkeypatch.setattr(quotes, "_read", boom)
    with pytest.raises(RuntimeError):
        quotes.snapshot(["SPY"], clock=_Clock(), sleep=lambda _s: None)
    assert not list(sr.requests_dir("margin").glob("*.json"))


def test_keep_leaves_the_request_for_the_caller():
    _fill("margin", [("SPY", 690.80, 690.83)])
    out = quotes.snapshot(["SPY"], clock=_Clock(), sleep=lambda _s: None, keep_request=True)
    assert out["missing"] == [] and not out["request_removed"]
    assert len(list(sr.requests_dir("margin").glob("adhoc-quotes-*.json"))) == 1
