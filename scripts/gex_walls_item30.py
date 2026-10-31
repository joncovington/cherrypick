"""Item 30, monthly-expiry GEX walls (docs/edge-hypotheses-plan.md), Test A: does price stop short of
a wall built from the expiry a 20-60 day trade sells, more often than of any level that far away?

    python scripts/gex_walls_item30.py requests [--out FILE]   # monthly chain paths still to fetch
    python scripts/gex_walls_item30.py plan [--out FILE]       # the open-interest fetch plan
    python scripts/gex_walls_item30.py study [--json PATH]

`requests` writes the input of `fetch-chain-paths`: each name's monthly expiry the windows target whose
stored path does not cover the sessions it is targeted on. `plan` writes the input of `fetch-oi`: every
session of both windows for both names. `study` reads both stores and states the verdict. All three
write under the backtest data dir (results/research/item30/), never into the repo: the inputs are
licensed.

**The walls, as declared.** On each session D, per name, the target expiry is the standard monthly
20-60 calendar days out nearest 45. Each strike's vol is Black-Scholes from its own side's mid at D's
close (the 3-month bill rate, no dividend); its gamma from that vol. Open interest is the vendor's
report on D's morning (the previous close's). `core.gex.compute_gex_profile` turns them into dollar
gamma with one gamma per strike, and `core.gex.net_walls` names the walls: the call wall is the most
positive net strike, the put wall the most negative (the GEX module's walls). The single-side peaks
(`totals.call_wall` / `put_wall`) are reported beside them, not judged. A wall on the wrong side of
the close, or nearer than 0.25 or further than 2 of sigma_h, is no wall that day.

**Measured.** sigma_h = at-the-money vol x close x sqrt(h / 252), h = 10 sessions (5 and 20
reported). The excursion M is the highest high (call side) or lowest low (put side) over the next h
sessions, from D's close, in sigma_h. A wall at x is crossed when M >= x. F(x) is the share of all of
that name's and side's sessions in the window whose M reaches x: the crossing rate of any level that
far away. Each wall-day's d = crossed - F(x). Under no effect d averages zero; a wall that holds makes
it negative. The placebo is the same name's and side's wall distance from 21 sessions earlier, read
against this session's path.

**The verdict** (design window, then the holdout; the holdout is computed and reported whatever
the design result, but a design fail stays a fail): the pooled mean d (each
session's mean over its name-side cells) at or below -0.05 with the 95% one-sided upper bound of a
stationary bootstrap (mean block h) below zero; d negative on each name and each side, and in each
half of the window; the pooled mean d below the placebo's; then the holdout's pooled mean d below
zero. Fewer than 200 wall-days for a name in a window is no verdict.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import bs, treasury
from cherrypick.core import calendar as cal
from cherrypick.core import stats as core_stats
from cherrypick.core.gex import compute_gex_profile, net_walls

NAMES = ("SPY", "AAPL")
WINDOWS = {
    "design": (date(2022, 1, 3), date(2024, 12, 31)),
    "holdout": (date(2025, 1, 2), date(2026, 9, 30)),
}
DTE = (20, 60)
DTE_AIM = 45
H = 10
H_REPORTED = (5, 20)
X_RANGE = (0.25, 2.0)
PLACEBO_LAG = 21
EFFECT = -0.05
MIN_WALL_DAYS = 200
RESAMPLES = 2000
SEED = 30
PATH_LEAD = 66  # a monthly's path from 66 days before it, as item 25's
COVERAGE = 0.90
SIDES = ("call", "put")


# ------------------------------------------------------------------------------------------------
# Pure: the calendar.


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def monthly_expiry(year: int, month: int) -> date:
    """The standard monthly: the third Friday, or the trading day before it when that is a holiday."""
    third = third_friday(year, month)
    return third if cal.is_trading_day(third) else cal.previous_trading_day(third)


def monthlies(a: date, b: date) -> list[date]:
    out, y, m = [], a.year, a.month
    while date(y, m, 1) <= b:
        e = monthly_expiry(y, m)
        if a <= e <= b:
            out.append(e)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def target_expiry(day: date, expiries: list[date]) -> date | None:
    """The monthly DTE[0]-DTE[1] calendar days out nearest DTE_AIM; the nearer-dated on a tie."""
    ok = [e for e in expiries if DTE[0] <= (e - day).days <= DTE[1]]
    return min(ok, key=lambda e: (abs((e - day).days - DTE_AIM), e)) if ok else None


def sessions(a: date, b: date) -> list[date]:
    d = a if cal.is_trading_day(a) else cal.next_trading_day(a)
    out = []
    while d <= b:
        out.append(d)
        d = cal.next_trading_day(d)
    return out


# ------------------------------------------------------------------------------------------------
# Pure: the walls.


def bs_gamma(spot: float, strike: float, years: float, vol: float, rate: float) -> float:
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2.0) * years) / (vol * math.sqrt(years))
    return math.exp(-d1 * d1 / 2.0) / math.sqrt(2.0 * math.pi) / (spot * vol * math.sqrt(years))


def walls(
    chain: list[tuple[float, str, float, float]], oi: dict, spot: float, years: float, rate: float
) -> dict | None:
    """The day's walls from the target expiry's chain [(strike, right, bid, ask)] and its open
    interest {(right, strike): contracts}: {net: (call, put), peak: (call, put), atm_vol}. None when
    no strike prices a vol or no contract is open."""
    entries, greeks, contracts, vols = [], {}, {}, {}
    for strike, right, bid, ask in chain:
        if not (bid and ask and ask >= bid > 0):
            continue
        vol = bs.implied_vol(spot, strike, years, (bid + ask) / 2.0, right, rate)
        if not vol:
            continue
        sym = f"{right}{strike}"
        entries.append({"strike_price": strike, "streamer_symbol": sym, "option_type": right})
        greeks[sym] = {"gamma": bs_gamma(spot, strike, years, vol, rate), "iv": vol * 100.0}
        contracts[sym] = oi.get((right, strike), 0)
        if (right == "C") == (strike >= spot):  # the out-of-the-money side
            vols[strike] = vol
    if not vols or not any(contracts.values()):
        return None
    profile = compute_gex_profile(entries, greeks, contracts, {}, spot)
    if not profile.get("ok"):
        return None
    atm = min(vols, key=lambda k: (abs(k - spot), k))
    return {
        "net": net_walls(profile["series"], "net_gex"),
        "peak": (profile["totals"]["call_wall"], profile["totals"]["put_wall"]),
        "atm_vol": vols[atm],
    }


def distance(wall: float | None, side: str, spot: float, sigma: float) -> float | None:
    """The wall's distance from the close in sigma_h, or None when it is no wall that day: on the
    wrong side of the close, or outside `X_RANGE`."""
    if wall is None or sigma <= 0:
        return None
    x = (wall - spot) / sigma if side == "call" else (spot - wall) / sigma
    return x if X_RANGE[0] <= x <= X_RANGE[1] else None


def excursion(bars: list[tuple[date, float, float, float]], i: int, h: int) -> tuple[float, float] | None:
    """(highest high, lowest low) over the h sessions after bars[i], or None near the series' end."""
    nxt = bars[i + 1 : i + 1 + h]
    if len(nxt) < h:
        return None
    return max(b[1] for b in nxt), min(b[2] for b in nxt)


def reach_rate(ms: list[float]):
    """F(x): the share of the excursions `ms` at or beyond x."""
    srt = sorted(ms)
    n = len(srt)

    def f(x: float) -> float:
        return (n - bisect.bisect_left(srt, x)) / n if n else float("nan")

    return f


def upper_bound(
    series: list[float], block: float, resamples: int = RESAMPLES, seed: int = SEED
) -> float | None:
    """The 95% one-sided upper bound of the series' mean, by stationary bootstrap."""
    n = len(series)
    if n < 2:
        return None
    rng = random.Random(seed)
    draws = sorted(
        sum(series[j] for j in core_stats.stationary_indices(n, block, rng)) / n for _ in range(resamples)
    )
    return draws[math.ceil(0.95 * resamples) - 1]


# ------------------------------------------------------------------------------------------------
# Pure: one window's readings and its verdict.


def readings(days: list[dict]) -> dict:
    """Each wall-day's d for one name and window. `days` holds, per session in order: day,
    the walls' distances per kind and side, and M per side (None near the end)."""
    out: dict = {}
    for kind in ("net", "peak"):
        for side in SIDES:
            ms = [d["m"][side] for d in days if d["m"] is not None]
            f = reach_rate(ms)
            rows = []
            for i, d in enumerate(days):
                x = d["x"][kind][side]
                if x is None or d["m"] is None:
                    continue
                lag = days[i - PLACEBO_LAG]["x"][kind][side] if i >= PLACEBO_LAG else None
                rows.append(
                    {
                        "day": d["day"],
                        "x": x,
                        "d": float(d["m"][side] >= x) - f(x),
                        "placebo": None if lag is None else float(d["m"][side] >= lag) - f(lag),
                    }
                )
            out[(kind, side)] = rows
    return out


def pooled(cells: dict[str, list[dict]], key: str = "d") -> list[tuple[date, float]]:
    """Each session's mean of its cells' values, in session order."""
    by_day = defaultdict(list)
    for rows in cells.values():
        for r in rows:
            if r[key] is not None:
                by_day[r["day"]].append(r[key])
    return [(d, statistics.fmean(v)) for d, v in sorted(by_day.items())]


def _mean(xs) -> float | None:
    xs = list(xs)
    return statistics.fmean(xs) if xs else None


def judge(cells: dict[str, list[dict]], mid: date, h: int) -> dict:
    """One window's reading of the net walls: the pooled mean, its bound, each cell, each half, the
    placebo, and whether every design condition holds."""
    series = [v for _, v in pooled(cells)]
    placebo = [v for _, v in pooled(cells, "placebo")]
    halves = [
        _mean(v for d, v in pooled(cells) if d < mid),
        _mean(v for d, v in pooled(cells) if d >= mid),
    ]
    by_cell = {c: _mean(r["d"] for r in rows) for c, rows in cells.items()}
    mean, bound, pmean = _mean(series), upper_bound(series, h), _mean(placebo)
    conditions = {
        "mean_at_or_below_effect": mean is not None and mean <= EFFECT,
        "upper_bound_below_zero": bound is not None and bound < 0,
        "negative_each_cell": all(v is not None and v < 0 for v in by_cell.values()),
        "negative_each_half": all(v is not None and v < 0 for v in halves),
        "below_placebo": mean is not None and pmean is not None and mean < pmean,
    }
    return {
        "sessions": len(series),
        "mean_d": mean,
        "upper_bound": bound,
        "placebo_mean_d": pmean,
        "halves": halves,
        "cells": by_cell,
        "conditions": conditions,
        "passes": all(conditions.values()),
    }


def verdict(design: dict, holdout: dict | None, enough: bool) -> str:
    if not enough:
        return "NO VERDICT"
    if not design["passes"]:
        return "FAIL"
    if holdout is None:
        return "NO VERDICT"
    return "PASS" if holdout["mean_d"] is not None and holdout["mean_d"] < 0 else "FAIL"


def daily_expiry_weeks(expiries_by_session: dict[date, set[date]]) -> int:
    """Weeks in which a name lists more than one expiry within the week: a sign of daily expiries.
    A holiday Thursday standing in for Friday is one expiry, not two."""
    by_week: dict[tuple[int, int], set[date]] = defaultdict(set)
    for exps in expiries_by_session.values():
        for e in exps:
            by_week[e.isocalendar()[:2]].add(e)
    return sum(1 for v in by_week.values() if len(v) > 1)


# ------------------------------------------------------------------------------------------------
# The stores.


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item30"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _chain_store() -> sqlite3.Connection:
    from cherrypick.backtest import chainfetch, settings

    return _ro(chainfetch.db_path(settings.load(None)))


def _oi_store() -> sqlite3.Connection:
    from cherrypick.backtest import oifetch, settings

    return _ro(oifetch.db_path(settings.load(None)))


def targets() -> dict[date, list[date]]:
    """Each target expiry and the sessions that target it, over both windows."""
    a = min(w[0] for w in WINDOWS.values())
    b = max(w[1] for w in WINDOWS.values())
    exps = monthlies(a, b + timedelta(days=DTE[1] + 31))
    out: dict[date, list[date]] = defaultdict(list)
    for w0, w1 in WINDOWS.values():
        for d in sessions(w0, w1):
            e = target_expiry(d, exps)
            if e is not None:
                out[e].append(d)
    return dict(out)


def cmd_requests(args) -> int:
    vend = _chain_store()
    out = []
    for sym in NAMES:
        have = defaultdict(set)
        for day, exp in vend.execute(
            "SELECT DISTINCT session, expiration FROM eod_chain_quotes WHERE symbol = ?", (sym,)
        ):
            have[exp].add(day)
        for e, days in sorted(targets().items()):
            held = sum(d.isoformat() in have.get(e.isoformat(), ()) for d in days)
            if held >= COVERAGE * len(days):
                continue
            out.append(
                {
                    "symbol": sym,
                    "expiration": e.isoformat(),
                    "start": (e - timedelta(days=PATH_LEAD)).isoformat(),
                    "through": max(days).isoformat(),
                }
            )
    path = Path(args.out) if args.out else _out_dir() / "chain_requests.json"
    path.write_text(json.dumps({"requests": out}, indent=1), encoding="utf-8")
    print(json.dumps({"requests": len(out), "file": str(path)}))
    return 0


def cmd_plan(args) -> int:
    days = sorted({d for w0, w1 in WINDOWS.values() for d in sessions(w0, w1)})
    path = Path(args.out) if args.out else _out_dir() / "oi_plan.json"
    path.write_text(
        json.dumps({"sessions": {d.isoformat(): list(NAMES) for d in days}}, indent=0),
        encoding="utf-8",
    )
    print(json.dumps({"sessions": len(days), "names": len(NAMES), "file": str(path)}))
    return 0


def _rate(day: date, cache: dict) -> float:
    if day not in cache:
        got = treasury.yield_before(day.isoformat(), "3m")
        cache[day] = got[1] / 100.0 if got else None
    return cache[day] if cache[day] is not None else 0.0


def at_horizon(walled: list[dict], bars: list, h: int) -> list[dict]:
    """`readings`' input at horizon h from the window's wall-days: pure."""
    out = []
    for w in walled:
        spot = w["spot"]
        sigma = w["atm_vol"] * spot * math.sqrt(h / 252.0)
        ex = excursion(bars, w["i"], h)
        out.append(
            {
                "day": w["day"],
                "x": {
                    kind: {side: distance(w[kind][j], side, spot, sigma) for j, side in enumerate(SIDES)}
                    for kind in ("net", "peak")
                },
                "m": None if ex is None else {"call": (ex[0] - spot) / sigma, "put": (spot - ex[1]) / sigma},
            }
        )
    return out


def load_walls(vend, oist, bars: list, sym: str, w0: date, w1: date, rates: dict) -> tuple[list, dict]:
    """The window's walls per session for one name, and what was missing."""
    idx = {b[0]: i for i, b in enumerate(bars)}
    exps = monthlies(w0, w1 + timedelta(days=DTE[1] + 31))
    missing = defaultdict(int)
    days = []
    for d in sessions(w0, w1):
        e = target_expiry(d, exps)
        i = idx.get(d)
        if e is None or i is None:
            missing["no_target_or_bar"] += 1
            continue
        spot = bars[i][3]
        chain = [
            (k, r[0].upper(), bid, ask)
            for k, r, bid, ask in vend.execute(
                "SELECT strike, right, bid, ask FROM eod_chain_quotes WHERE symbol = ? AND session = ? "
                "AND expiration = ?",
                (sym, d.isoformat(), e.isoformat()),
            )
        ]
        oi = {
            (r, k): n
            for r, k, n in oist.execute(
                "SELECT right, strike, open_interest FROM oi_contracts WHERE symbol = ? AND session = ? "
                "AND expiration = ?",
                (sym, d.isoformat(), e.isoformat()),
            )
        }
        if not chain or not oi:
            missing["no_chain" if not chain else "no_oi"] += 1
            continue
        years = (e - d).days / 365.0
        w = walls(chain, oi, spot, years, _rate(d, rates))
        if w is None:
            missing["no_walls"] += 1
            continue
        days.append({**w, "day": d, "i": i, "spot": spot})
    return days, dict(missing)


def cmd_study(args) -> int:
    from cherrypick.technicals import paths as tpaths
    from cherrypick.technicals import store

    vend, oist = _chain_store(), _oi_store()
    hist = store.connect(tpaths.history_db())
    bars = {
        sym: [
            (date.fromisoformat(b.date), b.high, b.low, b.close) for b in store.raw_bars(hist, sym) if b.close
        ]
        for sym in NAMES
    }
    rates: dict = {}
    result: dict = {"windows": {}, "daily_expiry_weeks": {}}
    for sym in NAMES:
        exps = defaultdict(set)
        for s, e in oist.execute(
            "SELECT DISTINCT session, expiration FROM oi_contracts WHERE symbol = ? AND expiration <= "
            "date(session, '+14 days')",
            (sym,),
        ):
            exps[date.fromisoformat(s)].add(date.fromisoformat(e))
        result["daily_expiry_weeks"][sym] = daily_expiry_weeks(exps)

    def window(wname: str) -> tuple[dict, dict]:
        w0, w1 = WINDOWS[wname]
        mid = w0 + (w1 - w0) / 2
        win: dict = {"from": w0.isoformat(), "to": w1.isoformat(), "missing": {}, "by_h": {}}
        walled = {}
        for sym in NAMES:
            walled[sym], win["missing"][sym] = load_walls(vend, oist, bars[sym], sym, w0, w1, rates)
        judged_counts: dict = {}
        for h in (H, *H_REPORTED):
            cells, peak_cells, counts = {}, {}, {}
            for sym in NAMES:
                r = readings(at_horizon(walled[sym], bars[sym], h))
                for side in SIDES:
                    cells[f"{sym}/{side}"] = r[("net", side)]
                    peak_cells[f"{sym}/{side}"] = r[("peak", side)]
                counts[sym] = len({row["day"] for side in SIDES for row in r[("net", side)]})
            win["by_h"][str(h)] = {
                "net": judge(cells, mid, h),
                "peak": judge(peak_cells, mid, h),
                "wall_days": counts,
            }
            if h == H:
                judged_counts = counts
        result["windows"][wname] = win
        return win["by_h"][str(H)]["net"], judged_counts

    design, counts = window("design")
    enough = all(n >= MIN_WALL_DAYS for n in counts.values())
    # The holdout is computed whatever the design result (the user's call, 2026-10-30, before any
    # data was fetched); verdict() still fails a design fail.
    holdout = window("holdout")[0] if enough else None
    result["verdict"] = verdict(design, holdout, enough)
    result["window"] = f"{WINDOWS['design'][0]}..{WINDOWS['design'][1]}" + (
        f"; holdout {WINDOWS['holdout'][0]}..{WINDOWS['holdout'][1]}" if holdout is not None else ""
    )
    result["run_id"] = f"item30-{date.today().isoformat()}"
    path = Path(args.json) if args.json else _out_dir() / "results.json"
    path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    path.with_suffix(".md").write_text(report(result), encoding="utf-8")
    print(json.dumps({"verdict": result["verdict"], "file": str(path)}))
    return 0


# ------------------------------------------------------------------------------------------------
# results.md beside results.json, for the console's studies tab.

CHECKS = (
    ("mean_at_or_below_effect", "Size of the effect", "−5 points or lower"),
    ("upper_bound_below_zero", "95% upper bound", "below 0"),
    ("negative_each_cell", "Each name and side", "all negative"),
    ("negative_each_half", "Each half of the window", "both negative"),
    ("below_placebo", "Against the placebo", "below the placebo"),
)


def _pts(x: float | None) -> str:
    """A d as points of crossing rate, signed: -0.0044 -> −0.4."""
    if x is None:
        return ""
    v = round(100 * x, 1)
    return f"{'−' if v < 0 else '+' if v > 0 else ''}{abs(v):.1f}"


def _cells_line(cells: dict) -> str:
    return ", ".join(f"{c.replace('/', ' ')} {_pts(v)}" for c, v in cells.items())


def findings(result: dict) -> list[str]:
    """The plain-English reading, worked out from the figures each time the study runs."""
    out = []
    d = result["windows"]["design"]["by_h"][str(H)]["net"]
    failed = [label.lower() for key, label, _ in CHECKS if not d["conditions"][key]]
    m = round(100 * d["mean_d"], 1)
    out.append(
        f"**The judged reading** (net walls, {H} sessions, design window): price reached a wall"
        f" {abs(m):.1f} points {'less' if m < 0 else 'more'} often than any level the same distance"
        f" away; the test needed {_pts(EFFECT)} or lower. "
        + (f"It misses on: {'; '.join(failed)}." if failed else "Every design check holds.")
    )
    neg = [c.replace("/", " ") for c, v in d["cells"].items() if v is not None and v < 0]
    pos = [c.replace("/", " ") for c, v in d["cells"].items() if v is not None and v >= 0]
    if neg and pos:
        out.append(
            f"**By name and side:** {_cells_line(d['cells'])}. Walls reached less often on"
            f" {', '.join(neg)} and more often on {', '.join(pos)}; a barrier on one side only is"
            " not the barrier the test asked about, and the sides cancel in the pooled mean."
        )
    hold = result["windows"].get("holdout", {}).get("by_h", {}).get(str(H), {}).get("net")
    if hold:
        out.append(
            f"**Holdout** (reported, the design having "
            f"{'passed' if d['passes'] else 'failed'}): {_pts(hold['mean_d'])} points."
        )
    near = [
        (w, h, kind, r)
        for w, win in result["windows"].items()
        for h, by in win["by_h"].items()
        for kind, r in by.items()
        if kind in ("net", "peak")
        and not (w == "design" and h == str(H) and kind == "net")
        and not r["conditions"]["mean_at_or_below_effect"]
        and all(v for k, v in r["conditions"].items() if k != "mean_at_or_below_effect")
    ]
    passing = [
        (w, h, kind)
        for w, win in result["windows"].items()
        for h, by in win["by_h"].items()
        for kind, r in by.items()
        if kind in ("net", "peak") and r["passes"] and not (w == "design" and h == str(H) and kind == "net")
    ]
    bits = [f"{w} {h} sessions, {kind} walls ({_pts(r['mean_d'])})" for w, h, kind, r in near]
    out.append(
        "**The reported readings** (5 and 20 sessions, and the single-side peak walls): "
        + (f"{len(passing)} meet every check." if passing else "none meets every check.")
        + (f" Closest, failing only on size: {'; '.join(bits)}." if bits else "")
    )
    out.append(
        "**What the sample could see:** as declared, only an effect of about 7 points or larger."
        " A fail reads as no barrier that strong, not proof of none at all."
    )
    weeks = result.get("daily_expiry_weeks") or {}
    if weeks:
        out.append(
            "**Expiries:** weeks listing more than one expiry: "
            + ", ".join(f"{s} {n}" for s, n in weeks.items())
            + ". SPY's dailies were expected; AAPL's weekly listing is what makes its count small."
        )
    return out


def report(result: dict) -> str:
    word = {"PASS": "pass", "FAIL": "fail"}.get(result["verdict"], "no verdict")
    d = result["windows"]["design"]["by_h"][str(H)]["net"]
    lines = [
        "# Item 30: GEX walls on the monthly expiry",
        "",
        f"**Verdict: {word}.** Test A asks whether price reaches a wall built from the monthly"
        f" expiry's open interest less often over the next {H} sessions than it reaches any level"
        " the same distance away. Test B (whether a short strike behind a wall earns more) is"
        " declared only after Test A passes.",
        "",
        "Each reading is a session on SPY or AAPL with a wall on a side: d is 1 if the path reached"
        " the wall, else 0, less the share of all that name's and side's sessions whose path reached"
        " the same distance (in sigma). With no barrier d averages zero; a wall that holds makes it"
        " negative. Below, d is in points of crossing rate: −5 means 5 points fewer crossings.",
        "",
        "## The judged checks",
        "",
        "| check | needed | result | |",
        "|---|---|---|---|",
    ]
    shown = {
        "mean_at_or_below_effect": _pts(d["mean_d"]),
        "upper_bound_below_zero": _pts(d["upper_bound"]),
        "negative_each_cell": _cells_line(d["cells"]),
        "negative_each_half": " / ".join(_pts(x) for x in d["halves"]),
        "below_placebo": f"{_pts(d['mean_d'])} against {_pts(d['placebo_mean_d'])}",
    }
    for key, label, needed in CHECKS:
        lines.append(f"| {label} | {needed} | {shown[key]} | {'pass' if d['conditions'][key] else 'fail'} |")
    lines += [
        "",
        "## What it says",
        "",
        *[f"- {x}" for x in findings(result)],
        "",
        "## How to read the tables",
        "",
        "- **net walls**: the GEX module's own, assuming dealers short puts and long calls. **peak"
        " walls**: the single strike with the most call or put gamma, assuming nothing; reported.",
        f"- **sessions ahead**: how far the path is read. {H} is judged; 5 and 20 are reported.",
        "- **mean d**: the pooled mean, points of crossing rate. **bound**: its 95% one-sided upper"
        " bound by stationary bootstrap. **placebo**: the same reading with the wall distance from"
        f" {PLACEBO_LAG} sessions earlier, which the walls must beat.",
        "- **half 1 / half 2**, and each **name and side**: the mean d in that part alone.",
        "- **wall-days**: sessions with a wall on at least one side, per name.",
    ]
    for wname, win in result["windows"].items():
        lines += [
            "",
            f"## {wname.capitalize()}: {win['from']} to {win['to']}",
            "",
            "| walls | sessions ahead | verdict | sessions | mean d | bound | placebo | half 1 | half 2"
            " | SPY call | SPY put | AAPL call | AAPL put |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for h in sorted(win["by_h"], key=int):
            for kind in ("net", "peak"):
                r = win["by_h"][h][kind]
                judged = wname == "design" and h == str(H) and kind == "net"
                v = ("pass" if r["passes"] else "fail") if judged else "reported"
                c = r["cells"]
                lines.append(
                    f"| {kind} | {h} | {v} | {r['sessions']} | {_pts(r['mean_d'])}"
                    f" | {_pts(r['upper_bound'])} | {_pts(r['placebo_mean_d'])}"
                    f" | {_pts(r['halves'][0])} | {_pts(r['halves'][1])}"
                    f" | {_pts(c.get('SPY/call'))} | {_pts(c.get('SPY/put'))}"
                    f" | {_pts(c.get('AAPL/call'))} | {_pts(c.get('AAPL/put'))} |"
                )
        days = win["by_h"][str(H)]["wall_days"]
        miss = {s: sum(m.values()) for s, m in win.get("missing", {}).items()}
        lines += [
            "",
            f"Wall-days at {H} sessions: "
            + ", ".join(f"{s} {n}" for s, n in days.items())
            + ". Sessions skipped for a missing chain or open interest: "
            + ", ".join(f"{s} {n}" for s, n in miss.items())
            + ".",
        ]
    return "\n".join(lines) + "\n"


def cmd_report(args) -> int:
    src = Path(args.json) if args.json else _out_dir() / "results.json"
    src.with_suffix(".md").write_text(report(json.loads(src.read_text(encoding="utf-8"))), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    rq = sub.add_parser("requests", help="the monthly chain paths still to fetch")
    rq.add_argument("--out")
    rq.set_defaults(fn=cmd_requests)
    pl = sub.add_parser("plan", help="the open-interest fetch plan")
    pl.add_argument("--out")
    pl.set_defaults(fn=cmd_plan)
    st = sub.add_parser("study", help="read both stores and state the verdict")
    st.add_argument("--json")
    st.set_defaults(fn=cmd_study)
    rp = sub.add_parser("report", help="results.md from an existing results.json (local)")
    rp.add_argument("--json")
    rp.set_defaults(fn=cmd_report)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
