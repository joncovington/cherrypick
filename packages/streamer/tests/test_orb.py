"""Opening-range capture: the generic trade hook records each symbol's 9:30-9:35 ET range and persists
it to the shared cache's orb_ranges table (which MEIC's get_orb_range reads). No broker required."""

import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_CORE = _SRC / "_core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from cherrypick.core import streamcache  # noqa: E402

import orb as _orb  # noqa: E402

_ET = ZoneInfo("America/New_York")


class _FakeState:
    def __init__(self, conn):
        self.conn = conn


class _FakeEngine:
    """Minimal stand-in for ChainStreamer: the tracker only touches .symbols, .state.conn, .log."""

    def __init__(self, symbols, conn):
        self.symbols = symbols
        self.state = _FakeState(conn)
        self.log = logging.getLogger("test-orb")


def _ts(h, m):
    # A deterministic epoch ts for a given ET wall-clock on a fixed date (no wall-clock reads in tests).
    return datetime(2026, 7, 21, h, m, tzinfo=_ET).timestamp()


@pytest.fixture()
def engine(tmp_path):
    conn = streamcache.connect(tmp_path / "stream_cache.db")  # creates orb_ranges
    return _FakeEngine(["SPX"], conn)


def _row(engine, symbol="SPX", day="2026-07-21"):
    return engine.state.conn.execute(
        "SELECT orb_high, orb_low FROM orb_ranges WHERE symbol = ? AND trade_date = ?", (symbol, day)
    ).fetchone()


def test_captures_high_low_after_window(engine):
    orb = _orb.OpeningRangeTracker()
    orb(engine, "SPX", 100.0, _ts(9, 31))
    orb(engine, "SPX", 102.0, _ts(9, 33))  # high
    orb(engine, "SPX", 99.0, _ts(9, 32))   # low
    assert _row(engine) is None            # not persisted until the window closes
    orb(engine, "SPX", 101.0, _ts(9, 36))  # first tick past 9:35 -> persist
    assert tuple(_row(engine)) == (102.0, 99.0)


def test_no_persist_if_streamer_missed_the_window(engine):
    orb = _orb.OpeningRangeTracker()
    # First tick arrives after the window (streamer started late) — nothing to persist.
    orb(engine, "SPX", 100.0, _ts(9, 36))
    assert _row(engine) is None


def test_ignores_unregistered_symbol(engine):
    orb = _orb.OpeningRangeTracker()
    orb(engine, "QQQ", 100.0, _ts(9, 31))  # not in engine.symbols
    orb(engine, "QQQ", 100.0, _ts(9, 36))
    assert _row(engine, symbol="QQQ") is None


def test_ignores_none_price(engine):
    orb = _orb.OpeningRangeTracker()
    orb(engine, "SPX", None, _ts(9, 31))
    orb(engine, "SPX", 100.0, _ts(9, 32))
    orb(engine, "SPX", 100.0, _ts(9, 36))
    assert tuple(_row(engine)) == (100.0, 100.0)


def test_capture_is_once_per_day(engine):
    orb = _orb.OpeningRangeTracker()
    orb(engine, "SPX", 100.0, _ts(9, 31))
    orb(engine, "SPX", 100.0, _ts(9, 36))  # persist
    # A later tick must neither error nor overwrite (ON CONFLICT DO NOTHING + _captured guard).
    orb(engine, "SPX", 200.0, _ts(9, 40))
    assert tuple(_row(engine)) == (100.0, 100.0)
