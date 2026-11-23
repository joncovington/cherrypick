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


def test_a_leg_the_cache_never_fills_comes_from_rest_and_says_so():
    """A weekend: the cache replays the underlying but never fills the options (2026-11-22)."""
    _fill("margin", [("KO", 96.81, 97.86), (".KO270115P95", 2.40, 2.70)])
    asked = []

    def rest(occs):
        asked.append(sorted(occs))
        return {
            "KO    270219P00095000": {"bid": 3.45, "ask": 3.65, "mid": 3.55, "age_seconds": 64_800.0},
            "KO    999999P00001000": {"bid": 1.0, "ask": 1.1, "mid": 1.05, "age_seconds": 1.0},
        }

    out = quotes.snapshot(
        ["KO", ".KO270115P95", ".KO270219P95", ".KO270219P90"],
        clock=_Clock(),
        sleep=lambda _s: None,
        wait_seconds=0,
        rest=rest,
    )
    # Only the unfilled options were asked, never the underlying or a filled leg.
    assert asked == [["KO    270219P00090000", "KO    270219P00095000"]]
    row = out["rows"][".KO270219P95"]
    assert row["source"] == "rest" and row["mid"] == 3.55 and row["age_seconds"] == 64_800.0
    assert row["delta"] is None and row["iv"] is None and row["usable"] is True
    assert row["occ"] == "KO    270219P00095000"
    assert out["rows"][".KO270115P95"]["source"] == "stream"
    assert out["rows"]["KO"]["source"] == "stream"
    # REST had nothing for the 90: still missing. A symbol nobody asked for is ignored.
    assert out["missing"] == [".KO270219P90"]
    assert set(out["rows"]) == {"KO", ".KO270115P95", ".KO270219P95"}


def test_rest_rows_go_through_the_same_quote_filter():
    def rest(occs):
        return {occs[0]: {"bid": 0.0, "ask": 0.40, "mid": 0.20, "age_seconds": 5.0}}

    out = quotes.snapshot([".KO270219P80"], clock=_Clock(), sleep=lambda _s: None, wait_seconds=0, rest=rest)
    row = out["rows"][".KO270219P80"]
    assert (row["usable"], row["reason"]) == (False, "zero_bid")


def test_no_rest_and_a_full_cache_never_call_it():
    out = quotes.snapshot([".KO270219P90"], clock=_Clock(), sleep=lambda _s: None, wait_seconds=0)
    assert out["missing"] == [".KO270219P90"] and out["rows"] == {}

    def rest(_occs):
        raise AssertionError("asked REST with nothing missing")

    _fill("margin", [(".KO270219P90", 1.68, 1.99)])
    out = quotes.snapshot([".KO270219P90"], clock=_Clock(), sleep=lambda _s: None, wait_seconds=0, rest=rest)
    assert out["rows"][".KO270219P90"]["source"] == "stream"


def test_cli_waits_less_outside_regular_hours():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from cherrypick.core.quotes import __main__ as cli

    ET = ZoneInfo("America/New_York")

    assert cli.default_wait(datetime(2026, 11, 22, 12, 0, tzinfo=ET)) == cli.OFF_HOURS_WAIT_SECONDS  # Sunday
    assert cli.default_wait(datetime(2026, 11, 23, 12, 0, tzinfo=ET)) == quotes.DEFAULT_WAIT_SECONDS


def test_the_table_marks_a_rest_row():
    from cherrypick.core.quotes import __main__ as cli

    row = {
        "bid": 3.45,
        "ask": 3.65,
        "mid": 3.55,
        "width": 0.2,
        "delta": None,
        "iv": None,
        "age_seconds": 64_800.0,
        "reason": "",
        "usable": True,
        "source": "rest",
        "last": None,
    }
    text = cli.table({"instance": "margin", "requested": 1, "missing": [], "rows": {".KO270219P95": row}})
    header, line = text.splitlines()[1:]
    assert line.endswith("rest")
    # The blank delta and iv keep their columns: the age lines up under its header.
    assert len(line.split("  rest")[0]) == len(header.split("  note")[0])
    assert "0.20      -      -" in line
