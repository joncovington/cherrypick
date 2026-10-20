"""The dollar-volume fetch (scripts/fetch_dollar_volume.py): what it asks the feed for, and the file.

The extended-hours flag is the whole correctness of the figure: the regular-hours candle (the SDK's
default) carries a median 0.40 of the consolidated volume, which halved the security list's
rank-1-or-2 set when it slipped through on 2026-10-19.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_dollar_volume.py"


def _module():
    spec = importlib.util.spec_from_file_location("fetch_dollar_volume", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fdv = _module()


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


class FakeStreamer:
    calls: list[dict] = []

    def __init__(self, session):
        self.events = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def subscribe_candle(self, symbols, **kwargs):
        FakeStreamer.calls.append({"symbols": list(symbols), **kwargs})
        for sym in symbols:
            self.events += [
                SimpleNamespace(
                    event_symbol=f"{sym}{{=d}}", event_flags=0, time=_ms("2026-10-15"), close=10, volume=5
                ),
                SimpleNamespace(
                    event_symbol=f"{sym}{{=d}}", event_flags=0, time=_ms("2026-10-16"), close=10, volume=7
                ),
                SimpleNamespace(
                    event_symbol=f"{sym}{{=d}}", event_flags=0x0A, time=0, close=None, volume=None
                ),
            ]

    async def get_event(self, _kind):
        if not self.events:
            await asyncio.sleep(3600)
        return self.events.pop(0)


def test_the_fetch_asks_for_extended_hours_and_reads_the_broker_spelling(monkeypatch):
    import tastytrade

    FakeStreamer.calls = []
    monkeypatch.setattr(tastytrade, "DXLinkStreamer", FakeStreamer)
    bars, ended = asyncio.run(fdv.fetch_batch(None, ["BRK.B", "AAPL"], datetime.now(UTC)))
    (call,) = FakeStreamer.calls
    assert call["extended_trading_hours"] is True and call["interval"] == "1d"
    assert call["symbols"] == ["BRK/B", "AAPL"]
    assert ended == {"BRK.B", "AAPL"}
    assert bars["BRK.B"] == [("2026-10-15", 10.0, 5.0), ("2026-10-16", 10.0, 7.0)]


def test_batches_stay_under_the_feeds_candle_subscription_limit(monkeypatch):
    import tastytrade

    FakeStreamer.calls = []
    monkeypatch.setattr(tastytrade, "DXLinkStreamer", FakeStreamer)
    names = [f"S{i:03d}" for i in range(250)]
    asyncio.run(fdv.fetch(None, names, datetime.now(UTC)))
    assert [len(c["symbols"]) for c in FakeStreamer.calls] == [100, 100, 50]


def test_the_file_reaches_the_previous_session_and_skips_names_with_nothing():
    bars = {"A": [("2026-10-16", 10.0, 5.0), ("2026-10-19", 10.0, 1e9)], "EMPTY": []}
    doc = fdv.build_doc(bars, {"A", "EMPTY"}, today="2026-10-19")
    assert doc["through"] == "2026-10-16" and doc["requested"] == 2
    assert doc["names"] == {"A": {"median": 50, "sessions": 1}}
