"""Live completed-bar stabilization oracle for the entry-gate trigger arms.

`stabilization_entry` profiles need a verdict AT ENTRY TIME. This module computes it — the
completed-bar form of the Thinkorswim "Universal Adaptive Market Stabilization Indicator"
(docs/theta-profits-be-condor.md, gap 2: the strategy's "wait until I see two or three five-minute
candles on more or less the same level", made deterministic):

  - 5-min bars from the recorded spot trail (`gex_spot_history` in the GEX history store),
    session minutes only (09:30-15:59), fully bucketed high/low/close;
  - COMPLETED bars only: a bar contributes from its scheduled close on, never while forming
    (a forming bar would let the verdict flicker mid-bar);
  - CURRENT bars only: the window must end on the latest scheduled close at or before `now` and
    its bars must be adjacent. A stalled recorder or a gap in the trail would otherwise leave the
    verdict reading bars from half an hour ago; that is no verified read, so it is None;
  - Wilder ATR(5) (seed = mean of the first 5 true ranges, then (atr*4 + tr)/5) and its
    20-bar trailing average;
  - STABLE = the last `consecutive_bars` (2) completed bars' total range <= the threshold, and
    ATR <= its 20-bar average. Missing ATR or average fails closed (not stable);
  - two warmup modes: `perday` (state resets at each session start) and `multi` (the ATR/average
    roll carries across prior sessions -- what a chart of the indicator shows). Both are computed
    every call; the arm reads the one `stabilization_oracle` names.

The `multi` warmup walks the last WARMUP_SESSIONS trading days before today, not the whole trail.
Wilder's weight on a bar 78 bars back (one session) is 0.8**78 ~ 3e-8, so the cap changes the ATR
by nothing measurable, and it keeps the cost flat: the live loop is a fresh process every tick
(`--once`), so an in-process cache does not survive between its ticks. Only NYSE trading days
count; the trail has weekend dates with in-window rows from before the recorder's age gate, and
their flat bars would drag the roll down.

Strictly read-only: the trail is opened ``mode=ro``, nothing here writes. A missing trail, a symbol
with no recorded rows, too few or stale bars yields ``None`` -- fail closed, so a trigger arm
without a verified read waits instead of firing on a guess. Wired into both loops and the replay:
the paper loop computes the verdicts into the snapshot before entries (paper_loop), the live loop
does the same in run_once (live_loop), and the replay passes its own history store and tick time
(replay). Open-position management never reads them; they gate ENTRIES only.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import calendar as _cal
from cherrypick.core import quotehistory as _quotehistory

ET = ZoneInfo("America/New_York")

DEFAULTS = {
    "consecutive_bars": 2,
    "atr_multiplier": 1.25,
    "atr_period": 5,
    "avg_atr_period": 20,
}

ORACLE_MODES = ("multi", "perday")
THRESHOLD_MODES = ("atr", "iv")

BAR_SECONDS = 300
SESSION_OPEN_MIN = 570  # 09:30 ET
SESSION_CLOSE_MIN = 960  # 16:00 ET

# Prior trading sessions the `multi` warmup walks (see the module docstring for why a cap is exact).
WARMUP_SESSIONS = 5

# The ToS script's IV sensitivity: threshold = close x IV x this. The script divides it by ten
# for XSP/SPY; that is not copied. `close` is already in the symbol's own units, so the extra
# tenth made an XSP threshold a tenth of the equivalent SPX one and the gate all but never fired.
IV_SENSITIVITY = 0.006


def default_trail() -> Path:
    """The recorded spot trail: the same store `core.quotehistory` reads."""
    return _quotehistory.default_history_db()


def _session_open_ts(trade_date: str) -> float:
    d = date.fromisoformat(trade_date)
    return datetime.combine(d, time(9, 30), tzinfo=ET).timestamp()


def _iv_at(con: sqlite3.Connection, symbol: str, trade_date: str, ts: float) -> float | None:
    """The recorded atm_iv reading (percent) at or before `ts` for `symbol` on `trade_date` -- the
    same trail the recorder keeps for the regime engine. No table, no rows, no reading yet: None
    (the caller fails closed)."""
    try:
        row = con.execute(
            "SELECT value FROM market_regime_history WHERE trade_date=? AND reading='atm_iv' "
            "AND symbol=? AND value IS NOT NULL AND ts <= ? ORDER BY ts DESC LIMIT 1",
            (trade_date, symbol.upper(), ts),
        ).fetchone()
    except sqlite3.Error:
        return None
    return float(row[0]) if row else None


def _bars_from_rows(rows: list, trade_date: str, last_ts: float) -> list[dict]:
    """5-min bars from one session's (ts, spot) rows: session minutes 09:30..15:59 only,
    scheduled-close keyed, COMPLETE bars only (a bar appears once `last_ts` passes its close)."""
    if not rows:
        return []
    open_ts = _session_open_ts(trade_date)
    buckets: dict[int, dict] = {}
    for ts, spot in rows:
        if spot is None or float(spot) <= 0:
            continue
        dt = datetime.fromtimestamp(float(ts), ET)
        mod = dt.hour * 60 + dt.minute
        if not (SESSION_OPEN_MIN <= mod < SESSION_CLOSE_MIN):
            continue
        b = (mod - SESSION_OPEN_MIN) // 5
        bar = buckets.get(b)
        if bar is None:
            buckets[b] = {
                "high": float(spot),
                "low": float(spot),
                "close": float(spot),
                "end_ts": open_ts + (b + 1) * BAR_SECONDS,
            }
        else:
            bar["high"] = max(bar["high"], float(spot))
            bar["low"] = min(bar["low"], float(spot))
            bar["close"] = float(spot)
    return [buckets[b] for b in sorted(buckets) if last_ts >= buckets[b]["end_ts"] - 0.5]


def _latest_close_ts(trade_date: str, now_ts: float) -> float:
    """The scheduled close of the newest bar that has completed by `now_ts` (the 16:00 bar after
    the session), with the same half-second allowance `_bars_from_rows` gives."""
    open_ts = _session_open_ts(trade_date)
    capped = min(now_ts + 0.5, open_ts + (SESSION_CLOSE_MIN - SESSION_OPEN_MIN) * 60)
    return open_ts + ((capped - open_ts) // BAR_SECONDS) * BAR_SECONDS


class _Roll:
    """Wilder ATR recursion with its trailing history; `hist[i]` is the ATR after the i-th warm bar."""

    def __init__(self, period: int):
        self.period = period
        self.carry: float | None = None
        self.atr: float | None = None
        self.seed: list[float] = []
        self.hist: list[float] = []

    def push(self, bar: dict) -> None:
        hi, lo, cl = bar["high"], bar["low"], bar["close"]
        c = self.carry
        tr = hi - lo if c is None else max(hi - lo, abs(hi - c), abs(lo - c))
        self.carry = cl
        if self.atr is None:
            self.seed.append(tr)
            if len(self.seed) >= self.period:
                self.atr = sum(self.seed) / self.period
                self.hist.append(self.atr)
        else:
            self.atr = (self.atr * (self.period - 1) + tr) / self.period
            self.hist.append(self.atr)

    def avg(self, n: int) -> float | None:
        return sum(self.hist[-n:]) / n if len(self.hist) >= n else None

    def copy(self) -> _Roll:
        out = _Roll(self.period)
        out.carry, out.atr, out.seed, out.hist = self.carry, self.atr, list(self.seed), list(self.hist)
        return out


# Warmup rolls per (trail, symbol, trade date), for the processes that live across ticks (the
# paper loop, a replay). Callers get a copy: today's bars must never extend the cached state.
_WARMUP: dict[tuple[str, str, str], _Roll] = {}
_WARMUP_MAX = 8


def _multi_roll(con: sqlite3.Connection, path: Path, symbol: str, trade_date: str) -> _Roll:
    key = (str(path), symbol, trade_date)
    if key not in _WARMUP:
        if len(_WARMUP) >= _WARMUP_MAX:
            _WARMUP.clear()
        _WARMUP[key] = _warmup(con, symbol, trade_date)
    return _WARMUP[key].copy()


def _warmup(con: sqlite3.Connection, symbol: str, trade_date: str) -> _Roll:
    """The cross-session roll seeded by the last WARMUP_SESSIONS trading days before `trade_date`
    (sessions with no in-window bars are no-ops)."""
    recorded = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT trade_date FROM gex_spot_history WHERE symbol=? AND trade_date < ? "
            "ORDER BY trade_date DESC",
            (symbol, trade_date),
        )
    ]
    days = [d for d in recorded if _cal.is_trading_day(date.fromisoformat(d))][:WARMUP_SESSIONS]
    roll = _Roll(DEFAULTS["atr_period"])
    for day in reversed(days):
        rows = con.execute(
            "SELECT ts, spot FROM gex_spot_history WHERE symbol=? AND trade_date=? ORDER BY ts",
            (symbol, day),
        ).fetchall()
        for bar in _bars_from_rows(rows, day, float("inf")):
            roll.push(bar)
    return roll


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


def snapshot_verdicts(
    symbol: str,
    trade_date: str,
    now: datetime | None = None,
    trail_path: Path | None = None,
    threshold_mode: str = "atr",
) -> dict[str, bool | None]:
    """Both oracles' verdict for `symbol` at `now` (last COMPLETED bar close <= now).

    `threshold_mode` picks the adaptive threshold's basis: "atr" (default, 1.25 x ATR) or "iv"
    (close x IV x 0.006, the script's alternate mode). Returns {"multi": bool|None, "perday":
    bool|None} for "atr" and the same under "-iv" keys for "iv"; an unknown mode returns the plain
    keys all None. None = no verified read (no trail, no rows, stale or non-adjacent bars, or -- in
    "iv" mode -- no recorded IV); callers fail closed on None. Decisions keyed by bar CLOSE: a
    stable bar at close C covers C-5..C."""
    now_dt = now or datetime.now(ET)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=ET)
    now_ts = now_dt.timestamp()
    symbol = str(symbol).upper()
    mode = str(threshold_mode or "atr").lower().strip()
    if mode not in THRESHOLD_MODES:
        return {m: None for m in ORACLE_MODES}
    suffix = "" if mode == "atr" else "-iv"
    path = Path(trail_path) if trail_path else default_trail()
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
        bars = _bars_from_rows(rows, trade_date, now_ts)
        n = DEFAULTS["consecutive_bars"]
        if len(bars) < n:
            return out
        win = bars[-n:]
        # Current and adjacent, or no read: the window must end on the newest scheduled close and
        # hold no missing bar. A gap would join two bars that were never consecutive.
        if abs(win[-1]["end_ts"] - _latest_close_ts(trade_date, now_ts)) > 0.5:
            return out
        if any(
            abs(b["end_ts"] - a["end_ts"] - BAR_SECONDS) > 0.5 for a, b in zip(win, win[1:], strict=False)
        ):
            return out
        last_close = win[-1]["end_ts"]
        # The threshold in "iv" mode: close x IV x sensitivity, the IV read at-or-before the last
        # completed bar's close (the same as-of rule the spot rows get). No recorded IV is no
        # verified read in this mode -- None for both oracles. There is no ATR fallback on a
        # binding path: a gate that silently substitutes ATR for a missing IV would bless ticks on
        # a basis the arm never chose.
        thr_iv: float | None = None
        if mode == "iv":
            iv = _iv_at(con, symbol, trade_date, last_close)
            if iv is None or iv <= 0:
                return out
            thr_iv = float(win[-1]["close"]) * (iv / 100.0) * IV_SENSITIVITY
        avg_n = DEFAULTS["avg_atr_period"]
        multi = _multi_roll(con, path, symbol, trade_date)
        per = _Roll(DEFAULTS["atr_period"])
        for bar in bars:
            multi.push(bar)
            per.push(bar)
        for name, roll in (("multi", multi), ("perday", per)):
            atr = roll.atr
            thr = (atr * DEFAULTS["atr_multiplier"] if atr else None) if mode == "atr" else thr_iv
            out[f"{name}{suffix}"] = _stable(win, atr, roll.avg(avg_n), thr)
        return out
    except sqlite3.Error:
        return {f"{m}{suffix}": None for m in ORACLE_MODES}
    finally:
        con.close()


def wanted(profiles: dict) -> bool:
    """True when any enabled profile opts into `stabilization_entry`, so a loop pays for the
    oracle only on sessions where a trigger arm runs. `_`-prefixed keys are notes."""
    return any(
        isinstance(p, dict)
        and not str(name).startswith("_")
        and p.get("enabled", True)
        and p.get("stabilization_entry")
        for name, p in (profiles or {}).items()
    )


def all_verdicts(
    symbol: str, trade_date: str, now: datetime | None = None, trail_path: Path | None = None
) -> dict[str, bool | None]:
    """The one dict every caller puts under `snapshot["stabilization_verdicts"]`: ATR mode's plain
    keys and IV mode's "-iv" keys, so arms on either mode share it."""
    return {
        **snapshot_verdicts(symbol, trade_date, now, trail_path),
        **snapshot_verdicts(symbol, trade_date, now, trail_path, threshold_mode="iv"),
    }
