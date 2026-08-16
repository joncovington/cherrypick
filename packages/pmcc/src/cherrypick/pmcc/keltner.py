"""The keltner book's entry filter: daily bars, the channel, and the pullback-and-reversal gate.

The filter as the user specified it (2026-08-16): enter only when spot sits NEAR THE MIDDLE of the
Keltner channel AND shows a sign of turning higher — both of:

- spot within `keltner_mid_band_atr` × ATR of the channel midline (the 20-EMA of daily closes);
- spot above yesterday's close AND recovered at least `keltner_bounce_atr` × ATR off today's
  intraday low.

Each condition is its own refusal reason and its own recorded MEASURE — distance to the midline,
the bounce off the low, the gap to prior close — stored on every book's entries (not just this
one's) so the filter's counterfactual stays readable from the control book.

Daily bars come from the shared cache's `stream_summary` (exchange-official OHLC per (symbol,
trade_date)), MIRRORED into this module's own `pmcc_daily_bars` on every tick: the cache offers no
retention guarantee (the flies 2026-08-05 correction records this), so whatever window it holds at
first run seeds the history and the module accumulates its own from there. Until
`keltner_min_history` completed days exist the book refuses entries (`insufficient_bar_history`) —
an honest cold start of ~21 trading days, by design, not a failure.

Pure math over rows plus one telemetry-class writer; no clock, no network.
"""

from __future__ import annotations

import sqlite3

PARAM_DEFAULTS = {
    "keltner_ema_period": 20,
    "keltner_atr_period": 20,
    "keltner_atr_mult": 1.5,
    "keltner_min_history": 21,
    "keltner_mid_band_atr": 0.5,
    "keltner_bounce_atr": 0.25,
}


def upsert_daily_bars(conn, cache_path, symbols: list[str]) -> int:
    """Mirror every retained `stream_summary` row for `symbols` into `pmcc_daily_bars`. Telemetry
    class: wrapped so a failure can never cost a tick; returns rows touched (0 on any failure)."""
    try:
        src = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
        src.row_factory = sqlite3.Row
    except sqlite3.Error:
        return 0
    touched = 0
    try:
        placeholders = ", ".join("?" * len(symbols))
        rows = src.execute(
            f"SELECT symbol, trade_date, day_open, day_high, day_low, day_close, prev_day_close, "
            f"updated_at FROM stream_summary WHERE symbol IN ({placeholders})",
            [s.strip().upper() for s in symbols],
        ).fetchall()
        for r in rows:
            conn.execute(
                "INSERT INTO pmcc_daily_bars (symbol, trade_date, day_open, day_high, day_low, "
                "day_close, prev_day_close, source, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, "
                "'stream_summary', ?) ON CONFLICT(symbol, trade_date) DO UPDATE SET "
                "day_open=excluded.day_open, day_high=excluded.day_high, day_low=excluded.day_low, "
                "day_close=excluded.day_close, prev_day_close=excluded.prev_day_close, "
                "updated_at=excluded.updated_at",
                (
                    r["symbol"],
                    r["trade_date"],
                    r["day_open"],
                    r["day_high"],
                    r["day_low"],
                    r["day_close"],
                    r["prev_day_close"],
                    r["updated_at"],
                ),
            )
            touched += 1
        conn.commit()
    except Exception:  # noqa: BLE001, S110 — telemetry may never cost a tick
        pass
    finally:
        src.close()
    return touched


def completed_bars(conn, symbol: str, before_date: str) -> list[dict]:
    """Completed daily bars strictly before `before_date`, oldest first, closes present. A row with
    no close is an incomplete or broken day and is dropped rather than guessed at."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM pmcc_daily_bars WHERE symbol = ? AND trade_date < ? "
            "AND day_close IS NOT NULL ORDER BY trade_date",
            (symbol.strip().upper(), before_date),
        )
    ]


def _ema(values: list[float], period: int) -> float:
    """Standard EMA seeded with the SMA of the first `period` values."""
    seed = sum(values[:period]) / period
    k = 2.0 / (period + 1)
    ema = seed
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _wilder_atr(bars: list[dict], period: int) -> float | None:
    """Wilder-smoothed ATR over true ranges. Prefers each bar's own prev close; falls back to the
    prior bar's close when the field is missing."""
    trs = []
    prev_close = None
    for bar in bars:
        high, low = bar.get("day_high"), bar.get("day_low")
        ref = bar.get("prev_day_close") if bar.get("prev_day_close") is not None else prev_close
        if high is None or low is None:
            prev_close = bar.get("day_close")
            continue
        if ref is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - ref), abs(low - ref))
        trs.append(tr)
        prev_close = bar.get("day_close")
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def channel(bars: list[dict], params: dict | None = None) -> dict | None:
    """The Keltner channel over COMPLETED bars, or None when history is short. `days` is what the
    readiness surfaces report; the ATR rides along because both entry conditions are ATR-scaled."""
    p = {**PARAM_DEFAULTS, **(params or {})}
    closes = [b["day_close"] for b in bars if b.get("day_close") is not None]
    if len(closes) < max(p["keltner_min_history"], p["keltner_ema_period"]):
        return None
    mid = _ema(closes, p["keltner_ema_period"])
    atr = _wilder_atr(bars, p["keltner_atr_period"])
    if atr is None:
        return None
    return {
        "mid": round(mid, 4),
        "atr": round(atr, 4),
        "upper": round(mid + p["keltner_atr_mult"] * atr, 4),
        "lower": round(mid - p["keltner_atr_mult"] * atr, 4),
        "days": len(closes),
    }


def entry_ok(
    spot: float,
    chan: dict | None,
    *,
    prev_close: float | None,
    day_low: float | None,
    params: dict | None = None,
) -> dict:
    """The keltner book's gate: `{"ok": True, "measures": ...}` or a refusal naming the ONE failed
    condition. Measures are always returned (whatever passed or failed) so every book's entry row
    can carry them."""
    p = {**PARAM_DEFAULTS, **(params or {})}
    if chan is None:
        return {"ok": False, "reason": "insufficient_bar_history", "measures": {}}
    atr = chan["atr"]
    distance = spot - chan["mid"]
    bounce = (spot - day_low) if day_low is not None else None
    prev_gap = (spot - prev_close) if prev_close is not None else None
    measures = {
        "keltner_mid": chan["mid"],
        "keltner_atr": atr,
        "keltner_days": chan["days"],
        "keltner_distance_atr": round(distance / atr, 4) if atr else None,
        "keltner_bounce_atr": round(bounce / atr, 4) if (bounce is not None and atr) else None,
        "keltner_prev_close_gap": round(prev_gap, 4) if prev_gap is not None else None,
    }
    band = p["keltner_mid_band_atr"] * atr
    if distance > band:
        return {"ok": False, "reason": "keltner_above_band", "measures": measures}
    if distance < -band:
        return {"ok": False, "reason": "keltner_below_band", "measures": measures}
    if prev_close is None:
        return {"ok": False, "reason": "keltner_no_prev_close", "measures": measures}
    if spot <= prev_close:
        return {"ok": False, "reason": "keltner_below_prev_close", "measures": measures}
    if day_low is None:
        return {"ok": False, "reason": "keltner_no_day_low", "measures": measures}
    if bounce < p["keltner_bounce_atr"] * atr:
        return {"ok": False, "reason": "keltner_no_bounce", "measures": measures}
    return {"ok": True, "measures": measures}
