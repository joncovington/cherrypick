"""Opening-range capture — a generic trade hook the streamer wires into the engine.

Records each streamed underlying's 9:30-9:35 ET opening range (high/low) from live Trade ticks and
persists it to the shared cache's ``orb_ranges`` table once the window closes. Nothing here is
MEIC-specific — it watches Trade prices and writes a table any consumer reads (MEIC's ``get_orb_range``
does). It belongs in the always-on streamer, not a consumer's loop, because a consumer's cadence isn't
guaranteed to land inside the five-minute window; the streamer sees every tick.

Lifted from MEIC's streamer ``_OrbTracker`` when the standalone streamer became the suite's sole
market-data producer. The ``orb_ranges`` table already lives in ``cherrypick.core.streamcache``'s schema,
so this writes through the engine's own cache connection with no schema change.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_WINDOW_OPEN_H, _WINDOW_OPEN_M = 9, 30
_WINDOW_CLOSE_H, _WINDOW_CLOSE_M = 9, 35


class OpeningRangeTracker:
    """Engine ``trade_hook``: invoked on every underlying Trade tick as ``(engine, symbol, price, ts)``.

    Holds its own per-symbol high/low for the day and writes each symbol's range once, the first tick at
    or after the window closes. Idempotent within a day (``_captured`` plus ``ON CONFLICT DO NOTHING``).
    """

    def __init__(self) -> None:
        self._high: dict[str, float] = {}
        self._low: dict[str, float] = {}
        self._captured: set[str] = set()  # symbols already persisted to orb_ranges today

    def __call__(self, engine, symbol: str, price: float | None, ts: float) -> None:
        if price is None or symbol not in engine.symbols:
            return
        et_now = datetime.fromtimestamp(ts, tz=_ET)
        window_start = et_now.replace(hour=_WINDOW_OPEN_H, minute=_WINDOW_OPEN_M, second=0, microsecond=0)
        window_end = et_now.replace(hour=_WINDOW_CLOSE_H, minute=_WINDOW_CLOSE_M, second=0, microsecond=0)
        if window_start <= et_now < window_end:
            h, low = self._high.get(symbol), self._low.get(symbol)
            self._high[symbol] = price if h is None else max(h, price)
            self._low[symbol] = price if low is None else min(low, price)
            return
        if et_now >= window_end and symbol not in self._captured:
            self._persist(engine, symbol, et_now, ts)

    def _persist(self, engine, symbol: str, et_now: datetime, ts: float) -> None:
        high, low = self._high.get(symbol), self._low.get(symbol)
        if high is None or low is None:
            return  # the streamer wasn't running during the window today — nothing to persist
        try:
            conn = engine.state.conn
            conn.execute(
                "INSERT INTO orb_ranges (symbol, trade_date, orb_high, orb_low, captured_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(symbol, trade_date) DO NOTHING",
                (symbol, et_now.strftime("%Y-%m-%d"), high, low, ts),
            )
            conn.commit()
            self._captured.add(symbol)
            engine.log.info("[%s] ORB range captured: high=%.2f low=%.2f", symbol, high, low)
        except Exception as exc:  # noqa: BLE001 — ORB is telemetry; a write hiccup must not kill the stream
            engine.log.warning("[%s] ORB persist error: %s", symbol, exc)
