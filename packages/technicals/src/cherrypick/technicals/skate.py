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
from cherrypick.core import fees as _fees

from . import adjust, earnings_calendar, liquidity, paths, securities, store

SIDES = ("C", "P")
BANDS = ("fixed", "by_expiry")
FUND_TYPES = securities.NO_EARNINGS
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
    "offset_min": -0.02,
    "offset_max": 0.05,
    # The score (step 2/3): Black-Scholes rate, the hard filters that drop a leg, the soft
    # thresholds shown as a marker, how many name-sides the second quote stage takes, and the
    # month the test odds are stated over.
    "rate": 0.04,
    "hard": {"min_open_interest": 100, "max_spread_pct": 0.10, "max_spread_abs": 0.05},
    "soft": {"skate": 0.84, "skate_recent": 0.80, "yield": 0.06},
    "stage_two": 60,
    "month_days": 35,
}
PKG_ROOT = Path(__file__).resolve().parents[3]


def settings(cfg: dict | None) -> dict[str, Any]:
    """The defaults overlaid with the operator's `skate` block; `_comment` keys are notes, not settings."""
    return {**DEFAULTS, **{k: v for k, v in (cfg or {}).items() if not k.startswith("_")}}


def load_settings() -> dict[str, Any]:
    """The effective settings from the operator's technicals config, for callers outside the CLI."""
    from cherrypick.core import home as _home

    return settings(_home.load_module_config("technicals", PKG_ROOT).get("skate"))


def offsets(cfg: dict) -> list[float]:
    """The strike offsets history is recorded at: inside the band (negative) through past it."""
    lo = round(cfg["offset_min"] / cfg["offset_step"])
    hi = round(cfg["offset_max"] / cfg["offset_step"])
    return [round(i * cfg["offset_step"], 6) for i in range(lo, hi + 1)]


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
    # A longer expiry has fewer windows to judge it on, so a name with just over a year of bars can
    # pass the short expiries and fall short on the long ones. Only those are dropped; the name is
    # dropped only when no expiry has enough.
    short_history = False
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
                    break
                bands[name] = {"haircut": h, "band": round(series[-1], 4), **hist}  # type: ignore[arg-type]
            if len(bands) < len(BANDS):
                short_history = True
                continue
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
        return None, "under a year of history" if short_history else "earnings before every expiry"
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


def trailing_yield(conn, symbol: str, day: str, spot: float) -> float:
    """Dividends gone ex in the year to `day` over the close: the yield the option pricer carries.
    Amounts are on their own date's share basis, so a split inside the year understates it."""
    if spot <= 0:
        return 0.0
    since = (date.fromisoformat(day) - timedelta(days=365)).isoformat()
    divs, _ = store.dividends(conn, symbol)
    return round(sum(d.amount for d in divs if since < d.ex_date <= day) / spot, 5)


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
        # The names' verdicts and the reference funds' (SPY, QQQ, IWM, TLT, the sector funds), judged
        # apart by the same test. Until 2026-10-20 skate read only the first, so the most liquid
        # underlyings there are were never banded.
        tradable = sorted(
            {s for s, v in liquidity.load().items() if v.get("verdict") == liquidity.TRADABLE}
            | liquidity.reference_tradable()
        )
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
                judged["div_yield"] = trailing_yield(conn, store_sym, day, judged["spot"])
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


# ------------------------------------------------------------------------------------------------
# Pure: planning the regular-hours quotes and scoring them (docs/skate-plan.md, steps 2 and 3).
# `scripts/fetch_screener_greeks.py skate` lists the chains and fetches the quotes; every choice it
# makes comes from here, and the scored file is what the console reads.
#
# A chain is the measure run's cache entry: [{"expiration", "dte", "strikes": [[k, call, put], ...]}].


def latest_doc(day: str, root: Path | None = None) -> dict | None:
    """The newest band file on or before `day`."""
    files = [p for p in sorted((root or skate_dir()).glob("????-??-??.json")) if p.stem <= day]
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def monthly_expiry(year: int, month: int) -> date:
    """The standard monthly: the third Friday, or the trading day before it when that is a holiday
    (Good Friday on the 15th puts it on Thursday the 14th)."""
    first = date(year, month, 1)
    third = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
    return third if cal.is_trading_day(third) else cal.previous_trading_day(third)


def is_monthly(d: date) -> bool:
    return d == monthly_expiry(d.year, d.month)


def cycle_expiry(expiries: list[str], today: date) -> str | None:
    """The monthly the user trades: inside 45-60 days, else inside 30-60, the latest there."""
    months = [e for e in expiries if is_monthly(date.fromisoformat(e))]
    for lo, hi in ((45, 60), (30, 60)):
        inside = [e for e in months if lo <= (date.fromisoformat(e) - today).days <= hi]
        if inside:
            return max(inside)
    return None


def snap(band: float, side: str, strikes: list[float]) -> float | None:
    """The first listed strike at or past the band: above it for a call, below it for a put."""
    if side == "C":
        return min((k for k in strikes if k >= band), default=None)
    return max((k for k in strikes if k <= band), default=None)


def around(pick: float, side: str, strikes: list[float]) -> list[tuple[str, float]]:
    """The band's strike with its neighbours: [("closer", k), ("pick", k), ("further", k)], closer
    meaning nearer the money, as far as the chain lists them."""
    ks = sorted(strikes)
    i = ks.index(pick)
    lower = ks[i - 1] if i > 0 else None
    upper = ks[i + 1] if i + 1 < len(ks) else None
    closer, further = (lower, upper) if side == "C" else (upper, lower)
    out = [("closer", closer), ("pick", pick), ("further", further)]
    return [(role, k) for role, k in out if k is not None]


def shift_for(name: dict, spots: dict[str, float] | None, symbol: str) -> float:
    """How far the price has moved since the band day's close, in dollars; 0 with no live spot.

    The band is close +/- haircut x range. The range is the band day's, but the close is not today's
    price: on 2026-10-19 AMZN's call band from Friday's close sat below Monday's price, so its "skate"
    pick was a 0.62-delta call already in the money. A band moves with the price, so every pick and
    every score is read against band + shift."""
    spot = (spots or {}).get(symbol)
    base = name.get("spot")
    return spot - base if spot and base else 0.0


def offset_of(strike: float, band: float, side: str) -> float:
    """How far past the band a strike sits, as a share of the band: negative is inside it."""
    return strike / band - 1 if side == "C" else 1 - strike / band


def curve_at(curve: list[float] | None, grid: list[float], offset: float) -> tuple[float | None, bool]:
    """(the skate rate at `offset`, whether it lies outside the recorded grid and was clamped)."""
    if not curve:
        return None, False
    if offset <= grid[0]:
        return curve[0], offset < grid[0]
    if offset >= grid[-1]:
        return curve[-1], offset > grid[-1]
    i = bisect.bisect_right(grid, offset)
    w = (offset - grid[i - 1]) / (grid[i] - grid[i - 1])
    return curve[i - 1] + w * (curve[i] - curve[i - 1]), False


def hard_pass(quote: dict | None, hard: dict) -> bool:
    """Two-sided, tight (percent of mid OR absolute) and with enough open interest."""
    q = quote or {}
    bid, ask = q.get("bid"), q.get("ask")
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return False
    width, mid = ask - bid, (ask + bid) / 2
    tight = width / mid <= hard["max_spread_pct"] + 1e-9 or width <= hard["max_spread_abs"] + 1e-9
    return tight and (q.get("open_interest") or 0) >= hard["min_open_interest"]


SHORT_ONE = {"order": {"legs": [{"quantity": 1}]}}


def open_fee() -> float:
    """The fee to open one short option contract on the suite's schedule (commission plus clearing and
    regulatory pass-throughs), dollars; no slippage, since the premium is already read at the bid."""
    c = _fees.apply_entry_costs(SHORT_ONE, [{"bid": 0.0, "ask": 0.0}], 1, {})
    return round(c["commission"] + c["pass_through_fees"], 2)


def score_leg(
    row: dict,
    band: str,
    strike: float,
    quote: dict | None,
    spot: float,
    days: int,
    years: float,
    div: float,
    cfg: dict,
    grid: list[float],
    shift: float = 0.0,
) -> dict:
    """One quoted strike judged against one band, moved by `shift` (see `shift_for`): its history,
    its implied odds, its yield."""
    from cherrypick.core import bs

    side = row["side"]
    b = row["bands"][band]
    level = b["band"] + shift
    off = offset_of(strike, level, side)
    hist, outside = curve_at(b["skate"], grid, off)
    recent, _ = curve_at(b["skate_recent"], grid, off)
    q = quote or {}
    bid, ask = q.get("bid"), q.get("ask")
    mid = (bid + ask) / 2 if bid is not None and ask is not None and ask >= bid >= 0 else None
    iv = bs.implied_vol(spot, strike, years, mid, side, cfg["rate"], div) if mid and spot else None
    dlt = bs.delta(spot, strike, years, iv, side, cfg["rate"], div) if iv else None
    # A skate is judged at expiry only (`history`), so the odds it is set against are the model's
    # chance of finishing out of the money, 1 - N(d2) -- not 1 - |delta|, which reads calls about two
    # points too risky and puts about two points too safe at a 0.15-delta band.
    itm = bs.prob_itm(spot, strike, years, iv, side, cfg["rate"], div) if iv else None
    implied = None if itm is None else 1 - itm
    capital = spot if side == "C" else strike
    per = 365 / max(days, 1)
    # Tested on the unrounded yield: 0.05995 rounds to 0.06 and must still miss a 6% bar.
    yield_bid = bid / capital * per if bid and capital else None
    # What opening one short contract costs (commission and pass-through fees, `cherrypick.core.fees`):
    # the premium is quoted gross, and on a few-cent bid this fee is most of it. A fill at the bid
    # already concedes the spread, so no slippage is added. Shown beside the gross; never ranks.
    fee = open_fee()
    net_premium = round(100 * bid - fee, 2) if bid else None
    out: dict[str, Any] = {
        "band": band,
        "band_level": round(level, 4),
        "strike": strike,
        "offset": round(off, 5),
        "outside_grid": outside,
        "quote": quote,
        "mid": mid,
        "iv": None if iv is None else round(iv, 4),
        "delta": None if dlt is None else round(dlt, 4),
        "implied_skate": None if implied is None else round(implied, 4),
        "skate": None if hist is None else round(hist, 4),
        "skate_recent": None if recent is None else round(recent, 4),
        "gap": None if hist is None or implied is None else round(hist - implied, 4),
        "yield_bid": None if yield_bid is None else round(yield_bid, 4),
        "yield_mid": round(mid / capital * per, 4) if mid and capital else None,
        "per_day_bid": round(100 * bid / max(days, 1), 3) if bid else None,
        # Per contract, dollars: the fee to open, the bid premium less it, and the yield on that.
        "fee_open": fee,
        "net_premium_bid": net_premium,
        "net_yield_bid": round(net_premium / (100 * capital) * per, 4)
        if net_premium is not None and capital
        else None,
        # How much history the skate rates stand on: the band's windows (about 252 a year) since
        # `history_from`, which a younger name meets with as little as `min_history`.
        "history_windows": b.get("n"),
        "history_windows_recent": b.get("n_recent"),
        "tested_per_cycle": None if hist is None else round(1 - hist, 4),
        "tested_per_month": None
        if hist is None
        else round(1 - hist ** (cfg["month_days"] / max(days, 1)), 4),
        "hard": hard_pass(quote, cfg["hard"]),
    }
    soft = cfg["soft"]
    out["soft"] = {
        "skate": hist is not None and hist >= soft["skate"],
        "skate_recent": recent is not None and recent >= soft["skate_recent"],
        "yield": yield_bid is not None and yield_bid >= soft["yield"],
    }
    out["soft"]["all"] = all(out["soft"].values())
    return out


def _chain_map(chain: list[dict]) -> dict[str, dict[float, list]]:
    return {e["expiration"]: {float(s[0]): s for s in e["strikes"]} for e in chain or []}


def plan_legs(
    doc: dict,
    chains: dict[str, list[dict]],
    wanted: list[tuple[str, str, str]],
    spots: dict[str, float] | None = None,
) -> list[dict]:
    """For each (symbol, side, expiry) wanted, the strikes to quote per band: the band's pick and its
    neighbours, the band moved to today's price (`shift_for`). One entry per (symbol, side, expiry,
    strike) with the bands and roles it serves."""
    legs: dict[tuple, dict] = {}
    for sym, side, exp in wanted:
        name = (doc.get("names") or {}).get(sym)
        row = next(
            (r for r in (name or {}).get("rows", []) if r["side"] == side and r["expiry"] == exp), None
        )
        listed = _chain_map(chains.get(sym) or []).get(exp)
        if not row or not listed:
            continue
        shift = shift_for(name or {}, spots, sym)
        for band in BANDS:
            pick = snap(row["bands"][band]["band"] + shift, side, list(listed))
            if pick is None:
                continue
            for role, k in around(pick, side, list(listed)):
                entry = legs.setdefault(
                    (sym, side, exp, k),
                    {
                        "symbol": sym,
                        "side": side,
                        "expiry": exp,
                        "strike": k,
                        "option": listed[k][1] if side == "C" else listed[k][2],
                        "roles": {},
                    },
                )
                entry["roles"][band] = role
    return list(legs.values())


def stage_one_expiry(expiries: list[str], today: date) -> str | None:
    """The cycle monthly when the name-side holds one; otherwise its furthest expiry, which in
    earnings season is the last one before the report."""
    return cycle_expiry(expiries, today) or (max(expiries) if expiries else None)


def stage_one(
    doc: dict, chains: dict[str, list[dict]], today: date, spots: dict[str, float] | None = None
) -> list[dict]:
    """Every name-side once: on its cycle monthly, or its furthest expiry where the band file holds
    no monthly (a report comes first) -- and the chain lists it."""
    wanted = []
    for sym, name in (doc.get("names") or {}).items():
        listed = [e["expiration"] for e in chains.get(sym) or []]
        for side in SIDES:
            mine = [r["expiry"] for r in name["rows"] if r["side"] == side and r["expiry"] in listed]
            exp = stage_one_expiry(mine, today)
            if exp:
                wanted.append((sym, side, exp))
    return plan_legs(doc, chains, wanted, spots)


def stage_two(
    doc: dict,
    chains: dict[str, list[dict]],
    picked: list[tuple[str, str]],
    spots: dict[str, float] | None = None,
) -> list[dict]:
    """Every expiry the band file holds for the picked name-sides."""
    wanted = [
        (sym, side, r["expiry"])
        for sym, side in picked
        for r in ((doc.get("names") or {}).get(sym) or {}).get("rows", [])
        if r["side"] == side
    ]
    return plan_legs(doc, chains, wanted, spots)


def score(
    doc: dict, legs: list[dict], spots: dict[str, float], now_days: dict[str, tuple[int, float]], cfg: dict
) -> list[dict]:
    """Group quoted legs by name-side and score each against the bands it serves. `now_days` maps an
    expiry to (calendar days left, years left to its close)."""
    grid = doc["offsets"]
    out: dict[tuple[str, str], dict] = {}
    for leg in legs:
        name = doc["names"][leg["symbol"]]
        row = next(r for r in name["rows"] if r["side"] == leg["side"] and r["expiry"] == leg["expiry"])
        spot = spots.get(leg["symbol"]) or name["spot"]
        shift = shift_for(name, spots, leg["symbol"])
        days, years = now_days[leg["expiry"]]
        entry = out.setdefault(
            (leg["symbol"], leg["side"]),
            {
                "symbol": leg["symbol"],
                "side": leg["side"],
                "spot": spot,
                # The close the bands were set on; every band here is moved by spot less this.
                "band_day_close": name["spot"],
                "fund": name["fund"],
                "earnings": name.get("earnings"),
                "div_yield": name.get("div_yield", 0.0),
                "legs": [],
            },
        )
        for band, role in leg["roles"].items():
            scored = score_leg(
                row,
                band,
                leg["strike"],
                leg.get("quote"),
                spot,
                days,
                years,
                name.get("div_yield", 0.0),
                cfg,
                grid,
                shift,
            )
            entry["legs"].append(
                {
                    "expiry": leg["expiry"],
                    "days": days,
                    "role": role,
                    "option": leg["option"],
                    "same_band": row["same_band"],
                    **scored,
                }
            )
    return list(out.values())


def best_expiries(entry: dict, monthly: str | None) -> dict[str, dict | None]:
    """Per band, the expiry whose pick pays the most per day on the bid among picks that pass the
    hard filters, with its ratio to the monthly's pick."""
    out: dict[str, dict | None] = {}
    for band in BANDS:
        picks = [
            g
            for g in entry["legs"]
            if g["band"] == band and g["role"] == "pick" and g["hard"] and g["per_day_bid"]
        ]
        if not picks:
            out[band] = None
            continue
        top = max(picks, key=lambda g: g["per_day_bid"])
        mon = next((g for g in picks if g["expiry"] == monthly), None)
        out[band] = {
            "expiry": top["expiry"],
            "strike": top["strike"],
            "per_day_bid": top["per_day_bid"],
            "skate": top["skate"],
            "monthly": monthly,
            "vs_monthly": round(top["per_day_bid"] / mon["per_day_bid"], 3) if mon else None,
        }
    return out


def _best_passes(entry: dict, band: str) -> bool:
    """Whether the band's best pick on this name-side passes every soft threshold."""
    best = (entry.get("best") or {}).get(band)
    if not best:
        return False
    leg = next(
        (
            g
            for g in entry["legs"]
            if g["band"] == band
            and g["role"] == "pick"
            and g["expiry"] == best["expiry"]
            and g["strike"] == best["strike"]
        ),
        None,
    )
    return bool(leg and leg.get("hard") and (leg.get("soft") or {}).get("all"))


def mark_both_sides(rows: list[dict]) -> None:
    """Set each row's `both_sides` per band: the name's call AND put best picks both pass every soft
    threshold (plan step 3), a name the range brackets from both sides at once. In place; rows need
    `best` set first."""
    passing: dict[tuple[str, str], set[str]] = {}
    for e in rows:
        for band in BANDS:
            if _best_passes(e, band):
                passing.setdefault((e["symbol"], band), set()).add(e["side"])
    for e in rows:
        e["both_sides"] = {band: passing.get((e["symbol"], band)) == set(SIDES) for band in BANDS}


def select(scored: list[dict], n: int) -> list[tuple[str, str]]:
    """The `n` name-sides whose best hard-passing pick has the widest history-minus-implied gap."""

    def gap(e: dict) -> float:
        return max(
            (g["gap"] for g in e["legs"] if g["role"] == "pick" and g["hard"] and g["gap"] is not None),
            default=float("-inf"),
        )

    ranked = sorted((e for e in scored if gap(e) > float("-inf")), key=gap, reverse=True)
    return [(e["symbol"], e["side"]) for e in ranked[:n]]
