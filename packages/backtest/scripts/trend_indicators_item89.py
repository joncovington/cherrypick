"""Item 89: three free trend indicators, traded as their makers suggest, on daily bars of index funds.

The declaration is research/edge-hypotheses-plan.md section 89; this script runs it. Its inputs are
broker and public bars kept under the home, never the repo, and so are its results.

The indicators come from a published video that compared them with moving-average crosses on crypto
hourly bars. It measured only how often a signal "flips back" within five bars and how late it is, and
said itself that price went its way about as often as against it afterwards. This asks the question
that video did not: does trading them earn after costs, and better than chance? Each is rebuilt from
its maker's published description and code excerpts at its default settings (declared: the full Pine
source was not read):

- **`zlts`**, zero-lag trend signals: a 70-bar EMA of `close + (close - close[34])`, a band of 1.2 x
  the highest ATR(70) of the last 210 bars either side of it; the trend turns up on a close crossing
  above the upper band, down on a close crossing below the lower. Traded stop-and-reverse on each turn.
- **`gpr`**, the Gaussian-process moving average: the posterior mean at the window's last bar of a
  Gaussian process (RBF kernel, length scale = the window, 100 bars; noise sigma 0.01) fitted to the
  window's closes less their mean, the mean added back. Bands at 2 x the mean absolute error of close
  against the line over the window. The state turns up on a close above the upper band while the line
  rises, down on a close below the lower band while it falls. Traded stop-and-reverse on each turn.
- **`zltl`**, zero-lag MA trend levels: a 15-bar EMA and a 15-bar EMA of `close + (close - EMA)`. Their
  cross draws a level one ATR(200) deep from the zero-lag line (below it after an up cross, above after
  a down cross). A retest is a bar that leaves the level in the trend's direction after the bar before
  it was in it (an up level: the previous low at or under its top, this low above it). A retest is
  entered in the trend's direction; the trade ends on a close through the level's far side or the next
  cross, whichever comes first. A broken level gives no more retests.

Every signal is read at a bar's close and filled at the next bar's open, as is every exit. One position
per fund. The judged arms are the three indicators on the four index funds, one row per entry session
(the funds' trades entered that session pooled), judged by RULE (the kit's three bars, delta not
applicable) and CONTROL_RULE (against random entries), both needed. Everything else is reported.

- `study`: one run over the whole history, cut at the split, written to `{results}/results.json` with
  a top-level `verdict` per arm. `--smoke N` reads the last N sessions per fund only, never a verdict.

Inputs: `data/public/item88/bars.db` (item 88's fetch, `scripts/fetch_item88_bars.py`: the broker's
daily bars of 16 funds; this study reads its `etf_daily` table, read-only, and fingerprints what it
read), the 3-month bill (`core.treasury`, else FRED's DGS3MO in `data/public/fred`).
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import random
import sqlite3
import statistics
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from cherrypick.core import fees, home, treasury

from cherrypick.backtest.study import rule as kit_rule

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The kit's pass rule on one row per entry session. Three judged arms; t 2.4 is above Holm's bar for
#: three (one-sided 0.05 / 3, about t 2.13), so Holm never binds.
RULE = {
    "t_bar": 2.4,
    "min_n": 150,
    "holdout_min_n": 60,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 89},
    "delta": False,
    "splits": ["2016-01-01"],
}

#: The second rule: each entry session's net less the mean of its placebos'.
CONTROL_RULE = {
    "placebos": 20,
    "seed": 89,
    "t_bar": 2.4,
    "min_n": 150,
    "holdout_min_n": 60,
    "split": "2016-01-01",
    "halves": "2008-01-01",
}

#: The judged funds: the four US index funds. Item 88's other funds are reported only.
FUNDS = ("SPY", "QQQ", "IWM", "DIA")
OTHER_FUNDS = ("EFA", "EEM", "TLT", "GLD", "SLV", "USO", "XLE", "XLF", "XLK", "XLU", "XLV", "XLI")

#: Each indicator's default settings, as its maker ships them, and the replay's declared numbers.
SPEC = {
    "through": "2026-11-13",
    "zlts": {"length": 70, "mult": 1.2},
    "gpr": {"window": 100, "sigma": 0.01, "mult": 2.0, "forecast": 0},
    "zltl": {"length": 15, "atr": 200},
    "warm_up": 250,
    "flip_back_bars": 5,
    "notional": 10_000.0,
    "slippage_per_share": 0.01,
    "rate_stale_days": 10,
}

ARMS = ("zlts", "gpr", "zltl")

#: Reported beside the judged arms, never judged: the video's own baselines, traded the same way.
BASELINES = ("ema50-price", "ema20-50")

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`. The bars are item
#: 88's, already fetched, so there is no fetch step; a smoke run first, so a crash shows in seconds.
CHAIN = (
    {
        "step": "smoke",
        "kind": "script",
        "args": ["study", "--smoke", "400"],
        "output": "{results}/smoke.json",
    },
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)

PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"


# --------------------------------------------------------------------------- series (pure)


def ema_series(xs: list[float], n: int) -> list[float]:
    """The n-bar EMA, seeded at the first value (the warm-up drops the seed's effect)."""
    k, out, e = 2.0 / (n + 1), [], None
    for x in xs:
        e = x if e is None else e + k * (x - e)
        out.append(e)
    return out


def atr_series(bars: list[dict], n: int) -> list[float | None]:
    """Wilder's ATR (Pine's `ta.atr`): the mean true range of the first n bars, then smoothed."""
    out: list[float | None] = []
    trs: list[float] = []
    a = None
    for i, b in enumerate(bars):
        tr = b["h"] - b["l"]
        if i:
            pc = bars[i - 1]["c"]
            tr = max(tr, abs(b["h"] - pc), abs(b["l"] - pc))
        trs.append(tr)
        if len(trs) == n:
            a = statistics.fmean(trs)
        elif len(trs) > n:
            a = (a * (n - 1) + tr) / n
        out.append(a)
    return out


def highest(xs: list[float | None], n: int) -> list[float | None]:
    """The highest of the last n values, None until n non-None values are in."""
    out: list[float | None] = []
    for i in range(len(xs)):
        win = xs[max(0, i - n + 1) : i + 1]
        out.append(max(win) if len(win) == n and all(v is not None for v in win) else None)
    return out


def crossed_above(a0, a1, b0, b1) -> bool:
    """Pine's crossover: a above b now, at or under it on the bar before."""
    return None not in (a0, a1, b0, b1) and a1 > b1 and a0 <= b0


def crossed_below(a0, a1, b0, b1) -> bool:
    return None not in (a0, a1, b0, b1) and a1 < b1 and a0 >= b0


# --------------------------------------------------------------------------- the indicators (pure)


def zlts_states(bars: list[dict], length: int, mult: float) -> list[int]:
    """Zero-lag trend signals: +1/-1 per bar (0 before the first turn)."""
    closes = [b["c"] for b in bars]
    lag = (length - 1) // 2
    src = [c + (c - closes[i - lag]) if i >= lag else c for i, c in enumerate(closes)]
    zl = ema_series(src, length)
    band = [None if v is None else v * mult for v in highest(atr_series(bars, length), length * 3)]
    state, out = 0, []
    for i in range(len(bars)):
        if i and band[i] is not None and band[i - 1] is not None:
            if crossed_above(closes[i - 1], closes[i], zl[i - 1] + band[i - 1], zl[i] + band[i]):
                state = 1
            elif crossed_below(closes[i - 1], closes[i], zl[i - 1] - band[i - 1], zl[i] - band[i]):
                state = -1
        out.append(state)
    return out


def cholesky_solve(a: list[list[float]], b: list[float]) -> list[float]:
    """x with a x = b for a symmetric positive definite a."""
    n = len(a)
    low = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = a[i][j] - sum(low[i][k] * low[j][k] for k in range(j))
            low[i][j] = math.sqrt(s) if i == j else s / low[j][j]
    y = [0.0] * n
    for i in range(n):
        y[i] = (b[i] - sum(low[i][k] * y[k] for k in range(i))) / low[i][i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (y[i] - sum(low[k][i] * x[k] for k in range(i + 1, n))) / low[i][i]
    return x


def gpr_weights(window: int, sigma: float, forecast: int) -> list[float]:
    """The posterior mean's weights on the window's (centred) values, oldest first: k*' (K + s^2 I)^-1,
    an RBF kernel with length scale `window`, read at the window's last bar plus `forecast`. Fixed for
    fixed settings, so the line is a fixed linear filter of the window."""
    ell2 = 2.0 * window * window
    k = [[math.exp(-((i - j) ** 2) / ell2) + (sigma * sigma if i == j else 0.0) for j in range(window)]
         for i in range(window)]  # fmt: skip
    x_star = window - 1 + forecast
    k_star = [math.exp(-((x_star - i) ** 2) / ell2) for i in range(window)]
    return cholesky_solve(k, k_star)


def gpr_states(bars: list[dict], window: int, sigma: float, mult: float, forecast: int) -> list[int]:
    """The Gaussian-process average's colour: +1/-1 per bar (0 before the first turn)."""
    w = gpr_weights(window, sigma, forecast)
    closes = [b["c"] for b in bars]
    line: list[float | None] = []
    for i in range(len(closes)):
        if i + 1 < window:
            line.append(None)
            continue
        win = closes[i - window + 1 : i + 1]
        m = statistics.fmean(win)
        line.append(m + sum(wk * (x - m) for wk, x in zip(w, win, strict=True)))
    err = [None if v is None else abs(c - v) for c, v in zip(closes, line, strict=True)]
    state, out = 0, []
    for i in range(len(closes)):
        win = err[max(0, i - window + 1) : i + 1]
        if i and len(win) == window and None not in win and line[i - 1] is not None:
            dev = mult * statistics.fmean(win)
            if closes[i] > line[i] + dev and line[i] > line[i - 1]:
                state = 1
            elif closes[i] < line[i] - dev and line[i] < line[i - 1]:
                state = -1
        out.append(state)
    return out


def ema_cross_states(bars: list[dict], fast: int | None, slow: int) -> list[int]:
    """The video's baselines: +1/-1 by the close against the slow EMA (`fast` None), or the fast EMA
    against the slow one."""
    closes = [b["c"] for b in bars]
    s = ema_series(closes, slow)
    f = closes if fast is None else ema_series(closes, fast)
    return [1 if a > b else -1 for a, b in zip(f, s, strict=True)]


def zltl_events(bars: list[dict], length: int, atr_n: int) -> list[dict]:
    """Zero-lag MA trend levels: per bar its `trend` (+1/-1/0), and `cross`, `retest` and `broken`
    flags. A retest needs a live (unbroken) level from a cross on an earlier bar."""
    closes = [b["c"] for b in bars]
    e = ema_series(closes, length)
    zl = ema_series([c + (c - x) for c, x in zip(closes, e, strict=True)], length)
    atr = atr_series(bars, atr_n)
    trend, level, out = 0, None, []
    for i, b in enumerate(bars):
        ev = {"trend": trend, "cross": False, "retest": False, "broken": False}
        if i and atr[i] is not None:
            up = crossed_above(zl[i - 1], zl[i], e[i - 1], e[i])
            down = crossed_below(zl[i - 1], zl[i], e[i - 1], e[i])
            if up or down:
                trend = 1 if up else -1
                top, bottom = (zl[i], zl[i] - atr[i]) if up else (zl[i] + atr[i], zl[i])
                level = {"top": top, "bottom": bottom, "side": trend, "from": i, "live": True}
                ev.update({"trend": trend, "cross": True})
            elif level is not None and level["live"]:
                prev = bars[i - 1]
                if level["side"] > 0:
                    if b["c"] < level["bottom"]:
                        level["live"] = False
                        ev["broken"] = True
                    elif prev["l"] <= level["top"] < b["l"]:
                        ev["retest"] = True
                else:
                    if b["c"] > level["top"]:
                        level["live"] = False
                        ev["broken"] = True
                    elif prev["h"] >= level["bottom"] > b["h"]:
                        ev["retest"] = True
        out.append(ev)
    return out


# --------------------------------------------------------------------------- trades (pure)


def stock_money(side: int, shares: int, entry: float, exit_: float, slip: float) -> dict:
    """One fund trade's money, signed: gross, fees (core's stock fee on each fill), slippage, net."""
    gross = side * shares * (exit_ - entry)
    open_side, close_side = ("buy", "sell") if side > 0 else ("sell", "buy")
    fee = fees.stock_trade_fee(shares, entry, side=open_side) + fees.stock_trade_fee(
        shares, exit_, side=close_side
    )
    slippage = 2 * shares * slip
    return {"gross": gross, "fees": fee, "slippage": slippage, "net": gross - fee - slippage}


def days_between(a: str, b: str) -> int:
    return max(1, (date.fromisoformat(b) - date.fromisoformat(a)).days)


def make_trade(symbol: str, bars: list[dict], side: int, i_in: int, i_out: int | None, spec: dict) -> dict:
    """A trade entered at the open after bar `i_in`, left at the open after bar `i_out` (None: still
    held at the last bar, closed at its close, `end`)."""
    entry = bars[i_in + 1]["o"]
    if i_out is None or i_out + 1 >= len(bars):
        j, px, why = len(bars) - 1, bars[-1]["c"], "end"
    else:
        j, px, why = i_out + 1, bars[i_out + 1]["o"], "signal"
    shares = max(1, int(spec["notional"] // entry))
    return {
        "symbol": symbol,
        "date": bars[i_in + 1]["day"],
        "exit_date": bars[j]["day"],
        "side": side,
        "entry": entry,
        "exit": px,
        "why": why,
        "bars": j - (i_in + 1),
        "shares": shares,
        "bp": shares * entry,
        "days": days_between(bars[i_in + 1]["day"], bars[j]["day"]),
        **stock_money(side, shares, entry, px, spec["slippage_per_share"]),
    }


def reversal_trades(symbol: str, bars: list[dict], states: list[int], warm: int, spec: dict,
                    long_only: bool = False) -> list[dict]:  # fmt: skip
    """Stop-and-reverse on each change of state from bar `warm`: a turn at bar i closes the held trade
    and opens the new side, both at the open after i. A state already set at `warm` is not entered
    until it turns (the first turn after the warm-up opens the first trade). `long_only`: shorts are
    flat instead."""
    trades, held = [], None
    for i in range(warm, len(bars) - 1):
        if states[i] == states[i - 1] or states[i] == 0:
            continue
        if held is not None:
            trades.append(make_trade(symbol, bars, held[0], held[1], i, spec))
            held = None
        if states[i] > 0 or not long_only:
            held = (states[i], i)
    if held is not None:
        trades.append(make_trade(symbol, bars, held[0], held[1], None, spec))
    return trades


def retest_trades(symbol: str, bars: list[dict], events: list[dict], warm: int, spec: dict,
                  long_only: bool = False) -> tuple[list[dict], dict]:  # fmt: skip
    """The trend-level arm: a retest enters the trend's side at the next open; the trade ends at the
    open after a break of its level or the next cross. One position per fund; a retest while one is
    held is counted, never taken."""
    trades, held, skipped = [], None, {"in_trade": 0, "short_flat": 0}
    for i in range(warm, len(bars) - 1):
        ev = events[i]
        if held is not None and (ev["broken"] or ev["cross"]):
            trades.append(make_trade(symbol, bars, held[0], held[1], i, spec))
            held = None
        if ev["retest"]:
            if held is not None:
                skipped["in_trade"] += 1
            elif long_only and ev["trend"] < 0:
                skipped["short_flat"] += 1
            else:
                held = (ev["trend"], i)
    if held is not None:
        trades.append(make_trade(symbol, bars, held[0], held[1], None, spec))
    return trades, skipped


def flip_back(signals: list[tuple[int, int]], ends: list[int], n: int) -> dict:
    """The video's measure: of the signals (bar, side), the share whose opposite (the next signal of the
    other side, or an end such as a broken level, given in `ends`) comes within n bars."""
    if not signals:
        return {"signals": 0}
    backs = 0
    for k, (i, side) in enumerate(signals):
        nxt = [j for j, s in signals[k + 1 :] if s != side][:1] + [e for e in ends if e > i][:1]
        if nxt and min(nxt) - i <= n:
            backs += 1
    return {"signals": len(signals), "flip_back_share": backs / len(signals)}


def turns(states: list[int], warm: int) -> list[tuple[int, int]]:
    return [(i, states[i]) for i in range(warm, len(states)) if states[i] and states[i] != states[i - 1]]


def arm_trades(arm: str, symbol: str, bars: list[dict], spec: dict = SPEC, long_only: bool = False):
    """(trades, info) for one arm on one fund's bars."""
    warm = spec["warm_up"]
    if arm == "zltl":
        events = zltl_events(bars, spec["zltl"]["length"], spec["zltl"]["atr"])
        trades, skipped = retest_trades(symbol, bars, events, warm, spec, long_only)
        rt = [(i, events[i]["trend"]) for i in range(warm, len(bars)) if events[i]["retest"]]
        ends = [i for i in range(warm, len(bars)) if events[i]["broken"] or events[i]["cross"]]
        cr = [(i, events[i]["trend"]) for i in range(warm, len(bars)) if events[i]["cross"]]
        n = spec["flip_back_bars"]
        return trades, {"skipped": skipped, "flip_back": {"retests": flip_back(rt, ends, n),
                                                          "crosses": flip_back(cr, [], n)}}  # fmt: skip
    if arm == "zlts":
        states = zlts_states(bars, spec["zlts"]["length"], spec["zlts"]["mult"])
    elif arm == "gpr":
        g = spec["gpr"]
        states = gpr_states(bars, g["window"], g["sigma"], g["mult"], g["forecast"])
    elif arm == "ema50-price":
        states = ema_cross_states(bars, None, 50)
    elif arm == "ema20-50":
        states = ema_cross_states(bars, 20, 50)
    else:
        raise ValueError(arm)
    trades = reversal_trades(symbol, bars, states, warm, spec, long_only)
    return trades, {"flip_back": {"turns": flip_back(turns(states, warm), [], spec["flip_back_bars"])}}


# --------------------------------------------------------------------------- the control (pure)


def placebo(bars: list[dict], eligible: list[int], side: int, hold: int, rng) -> dict | None:
    """One random entry on the same fund and part: the open after a random eligible bar, held `hold`
    bars, left at that bar's open (or the last close)."""
    k = eligible[rng.randrange(len(eligible))]
    if k + 1 >= len(bars):
        return None
    i_out = k + hold
    return make_trade("placebo", bars, side, k, i_out if i_out + 1 < len(bars) else None, SPEC)


def control_diffs(trades: list[dict], funds: dict[str, list[dict]], split: str) -> list[dict]:
    """Per entry session: the session's trade nets summed, less the mean over the placebo draws of their
    placebos' nets summed. A placebo's side is drawn from the arm's own long share in the trade's part
    and its bars held from the arm's own holding times in that part, never the trade's own (item 83's
    lesson: a copied side or holding time carries the outcome)."""
    by_part: dict[str, list[dict]] = {"design": [], "holdout": []}
    for t in trades:
        by_part["design" if t["date"] < split else "holdout"].append(t)
    p_long = {p: (sum(t["side"] > 0 for t in ts) / len(ts) if ts else 0.5) for p, ts in by_part.items()}
    holds = {p: [max(1, t["bars"]) for t in ts] for p, ts in by_part.items()}
    eligible: dict[tuple[str, str], list[int]] = {}
    for s, b in funds.items():
        for p in ("design", "holdout"):
            eligible[(s, p)] = [
                k for k in range(SPEC["warm_up"], len(b) - 1) if (b[k + 1]["day"] < split) == (p == "design")
            ]
    sessions: dict[str, list[dict]] = {}
    for t in trades:
        sessions.setdefault(t["date"], []).append(t)
    out = []
    for d, ts in sorted(sessions.items()):
        part = "design" if d < split else "holdout"
        rng = random.Random(f"{CONTROL_RULE['seed']}:{d}")
        draws = []
        for _ in range(CONTROL_RULE["placebos"]):
            got = []
            for t in ts:
                el = eligible[(t["symbol"], part)]
                if not el:
                    got.append(None)
                    continue
                side = 1 if rng.random() < p_long[part] else -1
                hold = holds[part][rng.randrange(len(holds[part]))]
                p = placebo(funds[t["symbol"]], el, side, hold, rng)
                got.append(None if p is None else p["net"])
            if all(g is not None for g in got):
                draws.append(sum(got))
        if draws:
            out.append(
                {"date": d, "diff": sum(t["net"] for t in ts) - statistics.fmean(draws), "draws": len(draws)}
            )
    return out


def _mean_t(xs: list[float]) -> dict:
    n = len(xs)
    if n < 2:
        return {"n": n, "mean": statistics.fmean(xs) if xs else None, "t": None}
    m, sd = statistics.fmean(xs), statistics.stdev(xs)
    return {"n": n, "mean": m, "t": m / (sd / math.sqrt(n)) if sd else None}


def judge_control(diffs: list[dict], rule: dict) -> dict:
    """The design mean above zero at the t bar and above zero in each design half; then, after a design
    pass, the holdout mean above zero."""
    design = [d["diff"] for d in diffs if d["date"] < rule["split"]]
    holdout = [d["diff"] for d in diffs if d["date"] >= rule["split"]]
    out: dict = {"design": _mean_t(design), "holdout": {"n": len(holdout)}}
    if len(design) < rule["min_n"]:
        out["design"]["verdict"] = NO_VERDICT
        out["holdout"]["verdict"] = NOT_READ
        return {**out, "verdict": NO_VERDICT}
    halves = {
        "first": _mean_t([d["diff"] for d in diffs if d["date"] < rule["halves"]])["mean"],
        "second": _mean_t([d["diff"] for d in diffs if rule["halves"] <= d["date"] < rule["split"]])["mean"],
    }
    d = out["design"]
    checks = {
        "mean": d["mean"] is not None and d["mean"] > 0,
        "t": d["t"] is not None and d["t"] >= rule["t_bar"],
        "halves": all(v is not None and v > 0 for v in halves.values()),
    }
    d.update({"halves": halves, "checks": checks})
    if not all(checks.values()):
        d["verdict"] = FAIL
        out["holdout"]["verdict"] = NOT_READ
        return {**out, "verdict": FAIL}
    d["verdict"] = PASS
    h = _mean_t(holdout)
    word = PASS if h["mean"] is not None and h["mean"] > 0 else FAIL
    provisional = word == PASS and len(holdout) < rule["holdout_min_n"]
    out["holdout"] = {**h, "verdict": word, "provisional": provisional}
    return {**out, "verdict": word, "provisional": provisional}


def both(bars_word: str, control_word: str) -> str:
    """An arm passes only when both rules pass; a fail in either fails it; else no verdict."""
    if bars_word == PASS and control_word == PASS:
        return PASS
    if FAIL in (bars_word, control_word):
        return FAIL
    return NO_VERDICT


def session_rows(trades: list[dict], rate) -> list[dict]:
    """The kit's rows: one per entry session, its trades pooled. Buying power summed; days weighted by
    buying power, so the bill charged is each trade's own bill summed (one rate a session)."""
    sessions: dict[str, list[dict]] = {}
    for t in trades:
        sessions.setdefault(t["date"], []).append(t)
    rows = []
    for d, ts in sorted(sessions.items()):
        bp = sum(t["bp"] for t in ts)
        rows.append(
            {
                "date": d,
                "net": sum(t["net"] for t in ts),
                "bp": bp,
                "days": sum(t["bp"] * t["days"] for t in ts) / bp,
                "rate": rate(d),
                "trades": len(ts),
            }
        )
    return rows


def summary(trades: list[dict]) -> dict:
    """Reported: counts, win rate, mean net, costs, hold, the long share, longs and shorts apart."""
    if not trades:
        return {"trades": 0}
    return {
        "trades": len(trades),
        "win_rate": sum(1 for t in trades if t["net"] > 0) / len(trades),
        "mean_net": statistics.fmean(t["net"] for t in trades),
        "total_net": sum(t["net"] for t in trades),
        "mean_return": statistics.fmean(t["net"] / t["bp"] for t in trades),
        "mean_gross": statistics.fmean(t["gross"] for t in trades),
        "mean_costs": statistics.fmean(t["fees"] + t["slippage"] for t in trades),
        "mean_days": statistics.fmean(t["days"] for t in trades),
        "long_share": sum(t["side"] > 0 for t in trades) / len(trades),
        "by_side": {
            s: statistics.fmean(t["net"] for t in trades if t["side"] == v)
            for s, v in (("long", 1), ("short", -1))
            if any(t["side"] == v for t in trades)
        },
    }


def hold_return(bars: list[dict], first: str, last_before: str | None) -> float | None:
    """Buy and hold over a part, for context: the first open on or after `first` to the last close
    before `last_before` (None: the last bar)."""
    seg = [b for b in bars if b["day"] >= first and (last_before is None or b["day"] < last_before)]
    return seg[-1]["c"] / seg[0]["o"] - 1 if len(seg) > 1 else None


# --------------------------------------------------------------------------- inputs (read-only)


def bars_db() -> Path:
    return home.data_dir("public") / "item88" / "bars.db"


def load_funds(path: Path, symbols: tuple[str, ...], through: str, last: int | None = None) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        out = {}
        for sym in symbols:
            rows = conn.execute(
                "SELECT day, open, high, low, close FROM etf_daily "
                "WHERE symbol = ? AND day <= ? ORDER BY day",
                (sym, through),
            ).fetchall()
            bars = [{"day": d, "o": o, "h": h, "l": lo, "c": c} for d, o, h, lo, c in rows]
            out[sym] = bars[-last:] if last else bars
        return out
    finally:
        conn.close()


def bill_reader(fred_path: Path | None = None):
    """The 3-month bill on a session: `core.treasury` before the session, else FRED's DGS3MO (the same
    constant-maturity series, for the years the Treasury files do not hold); stale past
    `rate_stale_days`, a run stops rather than guess."""
    fred_path = fred_path or home.data_dir("public") / "fred" / "DGS3MO.csv"
    fred: list[tuple[str, float]] = []
    if fred_path.exists():
        with fred_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    fred.append((row["observation_date"], float(row["DGS3MO"])))
                except (KeyError, ValueError):
                    continue
    days = [d for d, _ in fred]
    seen: dict[str, float] = {}

    def rate(session: str) -> float:
        if session in seen:
            return seen[session]
        got = treasury.yield_before(session, "3m")
        if got is not None and (date.fromisoformat(session) - got[0]).days <= SPEC["rate_stale_days"]:
            seen[session] = got[1] / 100.0
            return seen[session]
        k = bisect.bisect_left(days, session) - 1
        if (
            k < 0
            or (date.fromisoformat(session) - date.fromisoformat(days[k])).days > SPEC["rate_stale_days"]
        ):
            raise SystemExit(f"no 3-month bill within {SPEC['rate_stale_days']} days before {session}")
        seen[session] = fred[k][1] / 100.0
        return seen[session]

    return rate


def fingerprint(funds: dict[str, list[dict]]) -> dict:
    """What the run read, as counts and a hash of them: never a price."""
    lines = [
        f"{s}:{len(b)}:{b[0]['day'] if b else ''}:{b[-1]['day'] if b else ''}"
        for s, b in sorted(funds.items())
    ]
    return {"funds": {s: len(b) for s, b in funds.items()},
            "sha256": hashlib.sha256("\n".join(lines).encode()).hexdigest()}  # fmt: skip


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item89"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- the run


def study(funds: dict[str, list[dict]], others: dict[str, list[dict]], rate) -> dict:
    split = RULE["splits"][0]

    def parts(ts: list[dict]) -> dict:
        return {"design": summary([t for t in ts if t["date"] < split]),
                "holdout": summary([t for t in ts if t["date"] >= split])}  # fmt: skip

    def run(arm: str, fs: dict, long_only: bool = False) -> tuple[list[dict], dict]:
        trades, info = [], {}
        for sym, bars in fs.items():
            ts, inf = arm_trades(arm, sym, bars, long_only=long_only)
            trades += ts
            info[sym] = inf
        return trades, info

    judged, rows, control, reported = {}, {}, {}, {}
    for arm in ARMS:
        trades, info = run(arm, funds)
        judged[arm] = trades
        rows[arm] = session_rows(trades, rate)
        control[arm] = judge_control(control_diffs(trades, funds, split), CONTROL_RULE)
        lo, _ = run(arm, funds, long_only=True)
        ot, oinfo = run(arm, others)
        reported[arm] = {
            "arm": parts(trades),
            "by_fund": {s: parts([t for t in trades if t["symbol"] == s]) for s in funds},
            "years": {
                y: summary([t for t in trades if t["date"][:4] == y])
                for y in sorted({t["date"][:4] for t in trades})
            },  # fmt: skip
            "long_only": parts(lo),
            "other_funds": {
                "pooled": parts(ot),
                "by_fund": {s: parts([t for t in ot if t["symbol"] == s]) for s in others},
            },  # fmt: skip
            "flip_back": {s: i["flip_back"] for s, i in info.items()},
            "other_funds_flip_back": {s: i["flip_back"] for s, i in oinfo.items()},
            **({"skipped": {s: i["skipped"] for s, i in info.items()}} if arm == "zltl" else {}),
        }
    block = kit_rule.evaluate(RULE, rows)
    baselines = {}
    for b in BASELINES:
        bt, binfo = run(b, funds)
        lo, _ = run(b, funds, long_only=True)
        baselines[b] = {"arm": parts(bt), "long_only": parts(lo),
                        "flip_back": {s: i["flip_back"] for s, i in binfo.items()}}  # fmt: skip
    first = {s: b[SPEC["warm_up"]]["day"] for s, b in funds.items() if len(b) > SPEC["warm_up"]}
    hold = {s: {"design": hold_return(funds[s], d, split), "holdout": hold_return(funds[s], split, None)}
            for s, d in first.items()}  # fmt: skip
    return {
        **block,
        "control": control,
        "verdict": {a: both(block["verdict"][a], control[a]["verdict"]) for a in ARMS},
        "reported": {"arms": reported, "baselines": baselines, "buy_and_hold": hold},
        "window": {"through": SPEC["through"], "splits": RULE["splits"], "first_trade_bar": first},
        "corrections": list(REPLAY_CORRECTIONS),
    }


def cmd_study(args) -> int:
    funds = load_funds(bars_db(), FUNDS, SPEC["through"], last=args.smoke)
    others = load_funds(bars_db(), OTHER_FUNDS, SPEC["through"], last=args.smoke)
    if any(not b for b in funds.values()):
        raise SystemExit(f"a judged fund has no bars in {bars_db()}")
    block = study(funds, others, bill_reader())
    doc = {"note": "derived from personal-use broker and public data: never commit or publish", "item": 89,
           "spec": SPEC, "control_rule": CONTROL_RULE, **block,
           "inputs": fingerprint({**funds, **others}),
           "written_at": datetime.now(UTC).isoformat()}  # fmt: skip
    if args.smoke:
        doc = {**doc, "smoke": True, "verdict": "smoke"}
    name = "smoke.json" if args.smoke else "results.json"
    path = Path(args.out) if args.out else out_dir() / name
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("study")
    p.add_argument("--out", default=None)
    p.add_argument("--smoke", type=int, default=None, help="only the last N sessions per fund, no verdict")
    p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
