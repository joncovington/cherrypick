"""Live completed-bar stabilization oracle for the entry-gate trigger arms.

`stabilization_entry` profiles need a verdict AT ENTRY TIME. This module computes it — the
completed-bar form of the Thinkorswim "Universal Adaptive Market Stabilization Indicator"
(docs/theta-profits-be-condor.md, gap 2: the strategy's "wait until I see two or three five-minute
candles on more or less the same level", made deterministic):

  - 5-min bars from the recorded spot trail (`gex_spot_history` in the GEX history store),
    session minutes only (09:30-15:59), fully bucketed high/low/close;
  - COMPLETED bars only: a bar contributes from its scheduled close on, never while forming
    (a forming bar would let the verdict flicker mid-bar);
  - Wilder ATR(5) (seed = mean of the first 5 true ranges, then (atr*4 + tr)/5) and its
    20-bar trailing average;
  - STABLE = the last `consecutive_bars` (2) completed bars' total range <= 1.25 x ATR, and
    ATR <= its 20-bar average. Missing ATR or average fails closed (not stable);
  - two warmup modes: `perday` (state resets at each session start) and `multi` (the ATR/average
    roll carries across every prior recorded session -- what a chart of the indicator shows).
    Both are computed every call; the arm reads the one `stabilization_oracle` names.

The prior-session warmup is cached per (symbol, trade date) -- one trail walk per session per
symbol, minutes of state -- and every tick re-reads only that day's rows. Strictly read-only:
the trail is opened ``mode=ro``, nothing here writes. A missing trail, a symbol with no recorded
rows, or too few bars yields ``None`` -- fail closed, so a trigger arm without a verified read
waits instead of firing on a guess. Wired into both loops: the paper loop computes the verdicts
into the snapshot before entries (paper_loop), and the live loop does the same in run_once
(live_loop) -- open-position management never reads them; they gate ENTRIES only.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# The recorded spot trail. Overridable per call for tests.
DEFAULT_TRAIL = Path.home() / ".cherrypick/data/gex/gex_history.db"

DEFAULTS = {
    "consecutive_bars": 2,
    "atr_multiplier": 1.25,
    "atr_period": 5,
    "avg_atr_period": 20,
}

ORACLE_MODES = ("multi", "perday")
THRESHOLD_MODES = ("atr", "iv")

# The ToS script's SPX IV sensitivity (its "IV sensitivity" input for the SPX symbol class);
# XSP/SPY take a tenth of it, exactly as the script scales them.
IV_SENSITIVITY = 0.006


def _is_xsp_spy(symbol: str) -> bool:
    return (symbol or "").strip().upper() in ("XSP", "$XSP", "SPY", "$SPY")


def _auto_iv_sensitivity(symbol: str, base: float = IV_SENSITIVITY) -> float:
    return base / 10.0 if _is_xsp_spy(symbol) else base


def _iv_at(con: sqlite3.Connection, symbol: str, trade_date: str, ts: float) -> float | None:
    """The recorded atm_iv reading at or before `ts` for `symbol` on `trade_date` -- the same
    trail the recorder keeps for the regime engine. No table, no rows, no reading yet: None
    (the caller fails closed)."""
    try:
        rows = con.execute(
            "SELECT ts, value FROM market_regime_history WHERE trade_date=? AND reading='atm_iv' "
            "AND symbol=? AND value IS NOT NULL ORDER BY ts",
            (trade_date, symbol.upper()),
        ).fetchall()
    except sqlite3.Error:
        return None
    lo, hi, ans = 0, len(rows) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if float(rows[mid][0]) <= ts:
            ans = float(rows[mid][1])
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def _bars_from_rows(rows: list, last_ts: float) -> list[dict]:
    """5-min bars from one session's (ts, spot) rows: session minutes 09:30..15:59 only,
    scheduled-close keyed, COMPLETE bars only (a bar appears once `last_ts` passes its close)."""
    if not rows:
        return []
    first_ts = float(rows[0][0])
    midnight = (
        datetime.fromtimestamp(first_ts, ET).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )
    buckets: dict[int, dict] = {}
    for ts, spot in rows:
        if spot is None or float(spot) <= 0:
            continue
        dt = datetime.fromtimestamp(float(ts), ET)
        mod = dt.hour * 60 + dt.minute
        if not (570 <= mod <= 959):
            continue
        b = (mod - 570) // 5
        bar = buckets.get(b)
        if bar is None:
            buckets[b] = {
                "high": float(spot),
                "low": float(spot),
                "close": float(spot),
                "end_ts": midnight + (570 + (b + 1) * 5) * 60,
            }
        else:
            bar["high"] = max(bar["high"], float(spot))
            bar["low"] = min(bar["low"], float(spot))
            bar["close"] = float(spot)
    return [buckets[b] for b in sorted(buckets) if last_ts >= buckets[b]["end_ts"] - 0.5]


def _wilder_state(bars_list: list[dict], period: int):
    """Wilder ATR recursion, per bar: (atrs, avgs) lists (None until warm)."""
    atrs: list[float | None] = []
    avgs: list[float | None] = []
    atr: float | None = None
    seed: list[float] = []
    hist: list[float] = []
    carry: float | None = None
    for bar in bars_list:
        hi, lo, cl = bar["high"], bar["low"], bar["close"]
        tr = hi - lo if carry is None else max(hi - lo, abs(hi - carry), abs(lo - carry))
        carry = cl
        if atr is None:
            seed.append(tr)
            if len(seed) >= period:
                atr = sum(seed) / period
                hist.append(atr)
        else:
            atr = (atr * (period - 1) + tr) / period
            hist.append(atr)
        atrs.append(atr)
        avgs.append(sum(hist[-20:]) / len(hist[-20:]) if len(hist) >= 20 else None)
    return atrs, avgs


def _stable(win: list[dict], atr: float | None, avg: float | None, thr: float | None) -> bool:
    """The stability predicate over the last `consecutive_bars` completed bars against an
    already-computed threshold. A missing ATR or threshold reads not-stable (a cold read),
    never stable."""
    if atr is None or atr <= 0 or thr is None or thr <= 0:
        return False
    total = max(b["high"] for b in win) - min(b["low"] for b in win)
    if total > thr + 1e-12:
        return False
    if avg is None or atr > avg + 1e-12:
        return False
    return True


# Warmup state for `multi`, cached per (symbol, trade date): the roll walks EVERY prior
# recorded session in date order (sessions with no in-window bars are no-ops), seeding the
# cross-day ATR roll that today's bars extend. One walk per (symbol, session), never per tick.
_WARMUP: dict[tuple[str, str], tuple[float | None, float | None, list, list]] = {}
_WARMUP_MAX = 8


def _multi_warmup(con: sqlite3.Connection, symbol: str, trade_date: str, period: int):
    key = (symbol.upper(), trade_date)
    if key in _WARMUP:
        return _WARMUP[key]
    dates = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT trade_date FROM gex_spot_history WHERE symbol=? AND trade_date < ? "
            "ORDER BY trade_date",
            (symbol, trade_date),
        )
    ]
    carry_close: float | None = None
    atr: float | None = None
    seed: list[float] = []
    hist: list[float] = []
    for day in dates:
        rows = con.execute(
            "SELECT ts, spot FROM gex_spot_history WHERE symbol=? AND trade_date=? ORDER BY ts",
            (symbol, day),
        ).fetchall()
        bars = _bars_from_rows(rows, float(rows[-1][0]) if rows else 0.0)
        for bar in bars:
            hi, lo, cl = bar["high"], bar["low"], bar["close"]
            tr = (
                hi - lo if carry_close is None else max(hi - lo, abs(hi - carry_close), abs(lo - carry_close))
            )
            carry_close = cl
            if atr is None:
                seed.append(tr)
                if len(seed) >= period:
                    atr = sum(seed) / period
                    hist.append(atr)
            else:
                atr = (atr * (period - 1) + tr) / period
                hist.append(atr)
    if len(_WARMUP) >= _WARMUP_MAX:
        _WARMUP.clear()  # cheap; a session change rebuilds in one walk
    _WARMUP[key] = (carry_close, atr, seed, hist)
    return _WARMUP[key]


def snapshot_verdicts(
    symbol: str,
    trade_date: str,
    now: datetime | None = None,
    trail_path: Path | None = None,
    threshold_mode: str = "atr",
) -> dict[str, bool | None]:
    """Both oracles' verdict for `symbol` at `now` (last COMPLETED bar close <= now).

    `threshold_mode` picks the adaptive threshold's basis: "atr" (default, 1.25 x ATR) or "iv"
    (close x IV x sensitivity, the script's alternate mode). An unknown mode returns all-None:
    fail closed. Returns {"multi": bool|None, "perday": bool|None}; None = no verified read (no
    trail, no rows, no completed bars, or -- in "iv" mode -- no recorded IV); callers fail
    closed on None. Decisions keyed by bar CLOSE: a stable bar at close C covers C-5..C."""
    now_dt = now or datetime.now(ET)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=ET)
    now_ts = now_dt.timestamp()
    symbol = str(symbol).upper()
    mode = str(threshold_mode or "atr").lower().strip()
    if mode not in THRESHOLD_MODES:
        return {m: None for m in ORACLE_MODES}
    suffix = "" if mode == "atr" else "-iv"
    path = Path(trail_path) if trail_path else DEFAULT_TRAIL
    out: dict[str, bool | None] = {f"{m}{suffix}": None for m in ORACLE_MODES}
    if not path.exists():
        return out
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return out
    try:
        rows = con.execute(
            "SELECT ts, spot FROM gex_spot_history WHERE symbol=? AND trade_date=? ORDER BY ts",
            (symbol, trade_date),
        ).fetchall()
        bars = _bars_from_rows(rows, now_ts)
        if len(bars) < DEFAULTS["consecutive_bars"]:
            return out
        win = bars[-DEFAULTS["consecutive_bars"] :]
        last_close = win[-1]["end_ts"]
        # The threshold. "atr": 1.25 x Wilder ATR(5). "iv": close x IV x sensitivity, the IV
        # read at-or-before the last completed bar's close (the same as-of rule the spot rows
        # get); no reading, no threshold. Fallback thresholds are a binding-path hazard: a gate
        # that silently substitutes ATR for a missing IV would bless ticks on a basis the arm
        # never chose.
        thr_iv: float | None = None
        if mode == "iv":
            iv = _iv_at(con, symbol, trade_date, last_close)
            if iv is not None and iv > 0:
                thr_iv = float(win[-1]["close"]) * (iv / 100.0) * _auto_iv_sensitivity(symbol)
            if thr_iv is None:
                # No recorded IV: no verified read in this mode -- None for both oracles (the
                # arm waits). Distinct from a verified not-stable: there is no ATR fallback on
                # a binding path, a gate that silently substitutes ATR for a missing IV would
                # bless ticks on a basis the arm never chose.
                return out
        # multi: seeded by the cached cross-session warmup. The cached state is IMMUTABLE input:
        # the day's recursion works on copies, so no call can contaminate another's warmup (a
        # mutation here silently shifted every later call's index window).
        carry_close, atr, seed, hist = _multi_warmup(con, symbol, trade_date, DEFAULTS["atr_period"])
        seed, hist = list(seed), list(hist)
        cum0 = len(hist)
        per = DEFAULTS["atr_period"]
        for bar in bars:
            hi, lo, cl = bar["high"], bar["low"], bar["close"]
            tr = (
                hi - lo if carry_close is None else max(hi - lo, abs(hi - carry_close), abs(lo - carry_close))
            )
            carry_close = cl
            if atr is None:
                seed.append(tr)
                if len(seed) >= per:
                    atr = sum(seed) / per
                    hist.append(atr)
            else:
                atr = (atr * (per - 1) + tr) / per
                hist.append(atr)
        idx = cum0 + len(bars) - 1
        if idx < len(hist):
            a_multi = hist[idx]
            window = hist[max(0, idx + 1 - DEFAULTS["avg_atr_period"]) : idx + 1]
            avg_multi = sum(window) / len(window) if len(window) >= DEFAULTS["avg_atr_period"] else None
        else:
            a_multi, avg_multi = None, None
        # perday: today's bars only (state resets at each session start).
        atrs, avgs = _wilder_state(bars, DEFAULTS["atr_period"])
        a_per, avg_per = atrs[-1], avgs[-1]
        suffix = "" if mode == "atr" else "-iv"
        thr = a_multi * DEFAULTS["atr_multiplier"] if a_multi else None
        thr_per = a_per * DEFAULTS["atr_multiplier"] if a_per else None
        out[f"multi{suffix}"] = _stable(win, a_multi, avg_multi, thr if mode == "atr" else thr_iv)
        out[f"perday{suffix}"] = _stable(win, a_per, avg_per, thr_per if mode == "atr" else thr_iv)
        return out
    except sqlite3.Error:
        return {f"{m}{suffix}": None for m in ORACLE_MODES}
    finally:
        con.close()
