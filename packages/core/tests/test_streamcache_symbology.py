"""Tests for the stream cache's symbology + quote-mid readers.

The converter's whole job is to produce the key the *streamer* wrote, and the streamer got that key from
the tastytrade SDK. So the SDK is the oracle here rather than a hand-written expectation table: if the
SDK ever changes how it formats a strike, a test asserting our own literals would keep passing while
every fractional-strike lookup silently started missing.
"""

import sqlite3
import time

import pytest

from cherrypick.core import streamcache

OCC_CASES = [
    "SPY   260918P00750000",  # whole-dollar strike
    "KR    260918P00052500",  # .5 strike — the trailing-zero trim case
    "APO   260918C00120000",
    "EQX   270115C00005000",  # single-digit strike
    "SPXW  260918C01234500",  # 4-digit strike, 6-char root with padding
    "F     260918C00013000",  # short root, heavy padding
    "UVXY  260918C00027000",  # 4-char root
    "BRK/B 260918C00500000",  # root containing a non-alnum character
]


def test_occ_conversion_matches_the_sdk_exactly():
    """Pinned to `Option.occ_to_streamer_symbol`, because the cache keys came from the SDK."""
    Option = pytest.importorskip("tastytrade.instruments").Option
    for occ in OCC_CASES:
        assert streamcache.occ_to_streamer_symbol(occ) == Option.occ_to_streamer_symbol(occ), occ


# (streamer, OCC) pairs exactly as the streamer recorded them in `stream_chain` (the SDK's
# `streamer_symbol` and `symbol` on one chain row), read from the live cache on 2026-10-12.
CACHE_PAIRS = [
    (".XSP261009P460", "XSP   261009P00460000"),
    (".XSP261009C460", "XSP   261009C00460000"),
    (".SPXW261009P3000", "SPXW  261009P03000000"),
    (".SPX261016C200", "SPX   261016C00200000"),
    (".SPY261009P500", "SPY   261009P00500000"),
    (".TQQQ261009C40", "TQQQ  261009C00040000"),
    (".VXX261016P5", "VXX   261016P00005000"),
    (".SLV261009C15", "SLV   261009C00015000"),
]


def test_streamer_to_occ_matches_the_pairs_the_streamer_recorded():
    for streamer, occ in CACHE_PAIRS:
        assert streamcache.streamer_to_occ_symbol(streamer) == occ, streamer
        assert streamcache.occ_to_streamer_symbol(occ) == streamer, occ


def test_streamer_to_occ_inverts_the_forward_converter_on_every_case():
    """Fractional strikes (52.5, 52.25), padded and non-alphanumeric roots, and an adjusted root
    with a digit -- the cases the SDK's own `streamer_symbol_to_occ` gets wrong."""
    for occ in [*OCC_CASES, "KR    260918P00052250", "TNA1  261016C00050000", "XSP   261012P00580000"]:
        assert streamcache.streamer_to_occ_symbol(streamcache.occ_to_streamer_symbol(occ)) == occ, occ


def test_streamer_to_occ_refuses_what_has_no_occ_form():
    for sym in (
        "",
        "XSP",
        ".XSP",
        "XSP   261012P00580000",
        ".TOOLONGR261012P5",
        ".XSP261012P123456",
        ".XSP261012P5.1234",
        ".XSP261012P52.50",
        ".XSP261012X5",
        ".XSP26101P5",
    ):
        assert streamcache.streamer_to_occ_symbol(sym) == "", sym


def test_non_option_symbols_convert_to_empty_string():
    """An equity holding has no OCC form. "" lets a caller iterating a mixed position list say "this one
    is already its own streamer symbol" instead of having to pre-classify."""
    for sym in ("SCHD", "SPY", "", "NOTANOPTION"):
        assert streamcache.occ_to_streamer_symbol(sym) == ""


def _cache_with_quote(path, symbol, *, mid, age_seconds, bid=None, ask=None):
    conn = streamcache.connect(path)
    conn.execute(
        "INSERT INTO stream_quotes (symbol, bid, ask, mid, updated_at) VALUES (?, ?, ?, ?, ?)",
        (symbol, bid, ask, mid, time.time() - age_seconds),
    )
    conn.commit()
    return conn


def test_quote_mids_returns_fresh_rows_with_their_age(tmp_path):
    conn = _cache_with_quote(tmp_path / "sc.db", ".SPY260918P750", mid=4.5, bid=4.4, ask=4.6, age_seconds=2)
    out = streamcache.quote_mids(conn, [".SPY260918P750"])
    assert out[".SPY260918P750"]["mid"] == 4.5
    assert out[".SPY260918P750"]["bid"] == 4.4
    assert out[".SPY260918P750"]["source"] == "stream_cache"
    assert 0 <= out[".SPY260918P750"]["age_seconds"] <= 5


def test_a_stale_row_is_withheld_rather_than_returned(tmp_path):
    """The failure this exists to prevent: on 2026-08-18 four SPY legs sat in the cache 21 hours old,
    from expirations no longer subscribed. Returned as marks they read as live and mispriced the book by
    a full session's move. Absent is the honest answer — the caller refetches."""
    conn = _cache_with_quote(tmp_path / "sc.db", ".SPY260821C780", mid=0.655, age_seconds=75_149)
    assert streamcache.quote_mids(conn, [".SPY260821C780"]) == {}
    # ...and opting in explicitly still works, for a caller that owns saying the mark is old.
    assert ".SPY260821C780" in streamcache.quote_mids(conn, [".SPY260821C780"], max_age_seconds=None)


def test_a_row_without_a_mid_is_not_a_mark(tmp_path):
    """A quote row can exist with no mid (one side of the book missing). Treating that as a price would
    report the position as worthless."""
    conn = _cache_with_quote(tmp_path / "sc.db", ".SPY260918P750", mid=None, age_seconds=1)
    assert streamcache.quote_mids(conn, [".SPY260918P750"]) == {}


def test_absent_symbols_are_simply_missing(tmp_path):
    """`set(asked) - set(returned)` is the documented way to get the still-needs-a-live-fetch list."""
    conn = _cache_with_quote(tmp_path / "sc.db", ".SPY260918P750", mid=4.5, age_seconds=1)
    asked = [".SPY260918P750", ".NOPE260918C1"]
    out = streamcache.quote_mids(conn, asked)
    assert set(asked) - set(out) == {".NOPE260918C1"}


def test_an_unreadable_quotes_table_does_not_raise(tmp_path):
    """A reader sharing a file with a live daemon must degrade to "fetch it live", never to a traceback."""
    path = tmp_path / "sc.db"
    streamcache.connect(path).close()
    conn = sqlite3.connect(str(path))
    conn.execute("DROP TABLE stream_quotes")
    conn.commit()
    assert streamcache.quote_mids(conn, [".SPY260918P750"]) == {}
