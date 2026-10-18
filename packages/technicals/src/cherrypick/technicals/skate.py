"""The skate screen, step 1: the range band and how often it has held (docs/skate-plan.md).

"Skate" is selling an option you do not want assigned: a covered call above the stock's recent
range, a cash-secured put below it. The band is our reconstruction of a published method whose
formula is not: the range is the highest high less the lowest low over the sessions left to
expiry, smoothed by a simple average, and the band sits a haircut of it above or below the close.

    range_t = max(high, n) - min(low, n)          n = trading sessions to expiry
    band_t  = close_t +/- haircut * SMA(range, smoothing)_t

Two bands are computed for every name, side and expiry, because the haircut is a choice the user
makes on the tab: `fixed` (one haircut at every expiry) and `by_expiry` (a schedule, 0.65 to 14 days
by default). Where both land on the same price the row says so.

**History at a grid of strikes, not at a strike.** The listed strikes are known only to the quote
step (step 2), so for each band this records the skate rate a strike would have had at each offset
past the band, 0 to 5% in quarter points: the share of past sessions on which the close `n`
sessions later stayed below band x (1 + offset) for a call, above band x (1 - offset) for a put.
The score (step 3) reads the rate at the listed strike off that curve. Rates come for the whole
history from `history_from` and for the last `recent_years`.

**Bars are adjusted for splits and nothing else** (`price_bars`). A split changes the strike with
the shares, so history has to be on one share basis. A dividend does not: the price really drops on
the ex-date and an option's strike stays put, so a dividend-adjusted series would show calls
breached that were not and hide puts that were. The series break for a ticker that changed hands
(`adjust.series_break`) still applies.

Pure functions over the history store, the liquidity verdicts, the broker's report dates, the
security list's fund types and the declared leveraged-fund list. Writes only
`data/technicals/skate/<day>.json`.
"""

from __future__ import annotations

import bisect
import json
import time
from collections import deque
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from cherrypick.core import calendar as cal

from . import adjust, earnings_calendar, liquidity, paths, securities, store

SIDES = ("C", "P")
BANDS = ("fixed", "by_expiry")
FUND_TYPES = {securities.FUND, securities.STRUCTURED}
LEVERAGED_FUNDS = Path(__file__).with_name("leveraged_funds.json")

DEFAULTS: dict[str, Any] = {
    "haircut": 0.75,
    # [[max calendar days, haircut], ...], first match wins; past the last, `haircut` applies.
    "by_expiry": [[14, 0.65]],
    "smoothing": 10,
    "max_days": 63,
    "min_sessions": 3,
    "history_from": "2015-01-01",
    "recent_years": 3,
    "min_history": 250,
    "offset_step": 0.0025,
    "offset_max": 0.05,
}


def settings(cfg: dict | None) -> dict[str, Any]:
    """The defaults overlaid with the operator's `skate` block; `_comment` keys are notes, not settings."""
    return {**DEFAULTS, **{k: v for k, v in (cfg or {}).items() if not k.startswith("_")}}


def offsets(cfg: dict) -> list[float]:
    steps = round(cfg["offset_max"] / cfg["offset_step"])
    return [round(i * cfg["offset_step"], 6) for i in range(steps + 1)]


def leveraged_funds(path: Path | None = None) -> dict[str, dict]:
    """The declared list: {symbol: {name, kind, evidence, ...}}."""
    return json.loads((path or LEVERAGED_FUNDS).read_text(encoding="utf-8"))["funds"]


# ------------------------------------------------------------------------------------------------
# Pure: the calendar and the band.


def expiries(day: date, max_days: int, min_sessions: int) -> list[date]:
    """Each week's expiry within `max_days` calendar days after `day`: the Friday, or the trading
    day before it when the Friday is a holiday. Fewer than `min_sessions` sessions away is left out."""
    out = []
    d = day + timedelta(days=(4 - day.weekday()) % 7 or 7)
    while (d - day).days <= max_days:
        e = d if cal.is_trading_day(d) else cal.previous_trading_day(d)
        if e > day and cal.trading_days_between(day, e) >= min_sessions:
            out.append(e)
        d += timedelta(days=7)
    return out


def haircut_for(days: int, band: str, cfg: dict) -> float:
    if band == "by_expiry":
        for max_days, h in cfg["by_expiry"]:
            if days <= max_days:
                return float(h)
    return float(cfg["haircut"])


def smoothed_range(highs: list[float], lows: list[float], n: int, smoothing: int) -> list[float | None]:
    """SMA(smoothing) of (highest high - lowest low over n sessions), None until both windows fill."""
    hi_q: deque[int] = deque()
    lo_q: deque[int] = deque()
    rng: list[float | None] = []
    for i in range(len(highs)):
        while hi_q and highs[hi_q[-1]] <= highs[i]:
            hi_q.pop()
        hi_q.append(i)
        while lo_q and lows[lo_q[-1]] >= lows[i]:
            lo_q.pop()
        lo_q.append(i)
        if hi_q[0] <= i - n:
            hi_q.popleft()
        if lo_q[0] <= i - n:
            lo_q.popleft()
        rng.append(highs[hi_q[0]] - lows[lo_q[0]] if i >= n - 1 else None)
    out: list[float | None] = []
    total, count = 0.0, 0
    for i, r in enumerate(rng):
        if r is not None:
            total += r
            count += 1
        if i >= smoothing and rng[i - smoothing] is not None:
            total -= rng[i - smoothing]  # type: ignore[operator]
            count -= 1
        out.append(total / smoothing if count == smoothing else None)
    return out


def band_series(
    closes: list[float], srange: list[float | None], haircut: float, side: str
) -> list[float | None]:
    sign = 1.0 if side == "C" else -1.0
    return [None if r is None else c + sign * haircut * r for c, r in zip(closes, srange, strict=True)]


def skate_curve(ratios: list[float], side: str, grid: list[float]) -> list[float] | None:
    """Skate rate at each offset past the band from the historical ratios close(t+n) / band(t): a
    call skates below band x (1 + o), a put above band x (1 - o)."""
    if not ratios:
        return None
    srt = sorted(ratios)
    n = len(srt)
    if side == "C":
        return [round(bisect.bisect_left(srt, 1 + o) / n, 4) for o in grid]
    return [round((n - bisect.bisect_right(srt, 1 - o)) / n, 4) for o in grid]


def history(
    dates: list[str],
    closes: list[float],
    band: list[float | None],
    n: int,
    since: str,
    recent: str,
    grid: list[float],
    side: str,
) -> dict[str, Any]:
    """The band's skate curves over the whole history and the recent part, and their counts."""
    full: list[float] = []
    last: list[float] = []
    for t in range(len(closes) - n):
        b = band[t]
        if b is None or b <= 0 or dates[t] < since:
            continue
        r = closes[t + n] / b
        full.append(r)
        if dates[t] >= recent:
            last.append(r)
    return {
        "n": len(full),
        "n_recent": len(last),
        "skate": skate_curve(full, side, grid),
        "skate_recent": skate_curve(last, side, grid),
    }


# ------------------------------------------------------------------------------------------------
# Pure: one name.


def judge(
    symbol: str,
    dates: list[str],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    day: date,
    fund: bool,
    earnings: date | None,
    cfg: dict,
) -> tuple[dict | None, str | None]:
    """(the name's rows, None) or (None, why it was left out)."""
    if not dates or dates[-1] != day.isoformat():
        return None, "no bar on the day"
    if not fund and (earnings is None or earnings < day):
        return None, "earnings date unknown"
    grid = offsets(cfg)
    since = cfg["history_from"]
    recent = (day - timedelta(days=round(365.25 * cfg["recent_years"]))).isoformat()
    rows = []
    for exp in expiries(day, cfg["max_days"], cfg["min_sessions"]):
        if not fund and earnings is not None and earnings <= exp:
            continue  # this expiry spans the report; shorter ones may not
        n = cal.trading_days_between(day, exp)
        days = (exp - day).days
        srange = smoothed_range(highs, lows, n, cfg["smoothing"])
        if srange[-1] is None:
            continue
        for side in SIDES:
            bands: dict[str, Any] = {}
            for name in BANDS:
                h = haircut_for(days, name, cfg)
                series = band_series(closes, srange, h, side)
                hist = history(dates, closes, series, n, since, recent, grid, side)
                if hist["n"] < cfg["min_history"]:
                    return None, "under a year of history"
                bands[name] = {"haircut": h, "band": round(series[-1], 4), **hist}  # type: ignore[arg-type]
            rows.append(
                {
                    "side": side,
                    "expiry": exp.isoformat(),
                    "days": days,
                    "sessions": n,
                    "range": round(srange[-1], 4),
                    "same_band": bands["fixed"]["band"] == bands["by_expiry"]["band"],
                    "bands": bands,
                }
            )
    if not rows:
        return None, "earnings before every expiry"
    return {
        "spot": closes[-1],
        "fund": fund,
        "earnings": None if fund or earnings is None else earnings.isoformat(),
        "rows": rows,
    }, None


# ------------------------------------------------------------------------------------------------
# The nightly build.


def skate_dir() -> Path:
    return paths.data_dir() / "skate"


def price_bars(conn, symbol: str) -> list[adjust.AdjustedBar]:
    """The bars on today's share basis: splits applied, dividends not (see the module note)."""
    raw = store.raw_bars(conn, symbol)
    bars = adjust.adjust(raw, adjust.dedupe_splits(raw, store.splits(conn, symbol)), [])
    return bars[adjust.series_break(bars) :]


def _spellings(sym: str) -> tuple[str, str]:
    return sym.replace("/", "."), sym.replace(".", "/")


def build(
    day: str | None = None, cfg: dict | None = None, db: Path | None = None, out_dir: Path | None = None
) -> dict:
    cfg = settings(cfg)
    started = time.time()
    conn = store.connect(db or paths.history_db())
    try:
        if day is None:
            day = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
        if day is None:
            return {"ok": False, "reason": "the history store holds no bars"}
        on = date.fromisoformat(day)
        tradable = sorted(s for s, v in liquidity.load().items() if v.get("verdict") == liquidity.TRADABLE)
        lev = leveraged_funds()
        at, reports = earnings_calendar.broker_dates(day)
        rows, _ = securities.latest(day)
        types = {r["symbol"]: r.get("type") for r in rows}
        names: dict[str, dict] = {}
        skipped: dict[str, list[str]] = {}
        for sym in tradable:
            store_sym, broker_sym = _spellings(sym)
            if store_sym in lev or broker_sym in lev:
                skipped.setdefault("leveraged, inverse or volatility fund", []).append(sym)
                continue
            fund = (types.get(store_sym) or types.get(broker_sym)) in FUND_TYPES
            rep = reports.get(broker_sym) or reports.get(store_sym)
            bars = [b for b in price_bars(conn, store_sym) if b.date <= day]
            judged, why = judge(
                sym,
                [b.date for b in bars],
                [b.high for b in bars],
                [b.low for b in bars],
                [b.close for b in bars],
                on,
                fund,
                rep[0] if rep else None,
                cfg,
            )
            if judged is None:
                skipped.setdefault(why or "unknown", []).append(sym)
            else:
                names[sym] = judged
    finally:
        conn.close()
    doc = {
        "day": day,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "earnings_reading": at,
        "config": cfg,
        "offsets": offsets(cfg),
        "universe": len(tradable),
        "skipped": skipped,
        "names": names,
    }
    out = (out_dir or skate_dir()) / f"{day}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    tmp.replace(out)
    return {
        "ok": True,
        "day": day,
        "path": str(out),
        "universe": len(tradable),
        "names": len(names),
        "rows": sum(len(v["rows"]) for v in names.values()),
        "skipped": {k: len(v) for k, v in skipped.items()},
        "seconds": round(time.time() - started, 1),
    }
