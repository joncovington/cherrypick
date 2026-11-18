"""Item 88: the sweep-and-engulf candle rule, on daily bars of liquid ETFs and 4-hour gold.

The declaration is research/edge-hypotheses-plan.md section 88; this script runs it. Its inputs are
public and broker bars kept under the home, never the repo, and so are its results.

The rule, from a published video (the clip's own words, as rebuilt and backtested on screen):

- **Signal** at a bar's close: long when the bar trades below the previous bar's low and closes above
  the previous bar's high (the "sweep", then the "engulf"); short is the mirror. With `colour` "same",
  the previous bar must be the signal's colour (up for a long, down for a short). With `ema`, a long
  needs the close above the 200-bar EMA of closes and a short below it.
- **Entry** at the next bar's open. **Stop** at the signal bar's low (long) or high (short), or entry
  minus/plus k × ATR(14) for an `atr` stop. **Target** at R × the stop distance. A next open already
  past the stop is no trade (counted).
- **Exit** at the first of the stop or the target. Inside one bar, a stop and a target both touched is
  the stop. A later bar that opens past either fills at that open. One position per fund at a time; a
  signal while one is held is counted, never taken.

The judged arm, `etf-daily`, is the stated rule (same colour, EMA 200, candle stop, 2R) on every fund
in `ETFS`, one row per entry session (the trades entered that session pooled), judged by RULE (the
kit's three bars, delta not applicable) and CONTROL_RULE (against random entries), both needed. The
4-hour gold replication and the variants are reported, never judged.

- `study`: one run over the whole history, cut at the split, written to `{results}/results.json` with
  a top-level `verdict` (`{"etf-daily": word}`). `--smoke N` reads the last N sessions per fund only,
  never a verdict.

Inputs: `data/public/item88/bars.db` (`scripts/fetch_item88_bars.py`: the funds' daily bars, Yahoo's
hourly GC=F), `data/market-files/gold_bars.db` (the gold bar recorder: each dated contract's hourly and
daily bars), the 3-month bill (`core.treasury`, else FRED's DGS3MO in `data/public/fred`).
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
from zoneinfo import ZoneInfo

from cherrypick.core import fees, home, progress, treasury

from cherrypick.backtest.study import rule as kit_rule

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The kit's pass rule on one row per entry session. One judged arm, so no Holm step.
RULE = {
    "t_bar": 2.0,
    "min_n": 150,
    "holdout_min_n": 60,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 88},
    "delta": False,
    "splits": ["2016-01-01"],
}

#: The second rule: each entry session's net less the mean of its placebos'.
CONTROL_RULE = {
    "placebos": 20,
    "seed": 88,
    "t_bar": 2.0,
    "min_n": 150,
    "holdout_min_n": 60,
    "split": "2016-01-01",
    "halves": "2008-01-01",
}

#: The declared funds: US equity index, international, bonds, metals, oil and sectors, each with
#: weekly options. Today's list, so a hindsight label (declared). Held equal to the fetch script's.
ETFS = (
    "SPY", "QQQ", "IWM", "DIA", "EFA", "EEM", "TLT", "GLD",
    "SLV", "USO", "XLE", "XLF", "XLK", "XLU", "XLV", "XLI",
)  # fmt: skip

#: The stated rule and the replay's declared numbers.
SPEC = {
    "through": "2026-11-13",
    "rule": {"colour": "same", "ema": 200, "stop": "candle", "r": 2.0},
    "atr": 14,
    "notional": 10_000.0,
    "slippage_per_share": 0.01,
    "rate_stale_days": 10,
}

#: Reported beside the judged arm, never judged.
VARIANTS = {
    "any-colour": {"colour": "any", "ema": 200, "stop": "candle", "r": 2.0},
    "no-ema": {"colour": "same", "ema": None, "stop": "candle", "r": 2.0},
    "atr2-stop": {"colour": "same", "ema": 200, "stop": ("atr", 2.0), "r": 2.0},
    "tuned": {"colour": "same", "ema": 200, "stop": ("atr", 5.0), "r": 1.5},
}

#: The 4-hour gold replication, reported only. One micro contract (10 oz); the declared cost is a tick
#: of slippage and $1.50 of fees a contract a side (the suite has no futures fee in core).
GOLD = {
    "oz": 10.0,
    "cost_per_side": 1.0 + 1.50,
    "cycle": "GJMQVZ",
    "yahoo_before": "2026-02-02",
    "schedule_from": "2025-06-01",
    "alignments": ("utc", "session"),
}

#: The research-queue chain, queued with `run.py queue-chain --script <this file> --after
#: item88/fetch`. The fetch is its own row (`scripts/fetch_item88_bars.py`, a root script: it reaches
#: the broker, which nothing in this package may).
CHAIN = ({"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},)

ET = ZoneInfo("America/New_York")
PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"
H4_MS = 4 * 3_600_000


# --------------------------------------------------------------------------- the rule (pure)


def ema_series(closes: list[float], n: int) -> list[float]:
    """The n-bar EMA of the closes, seeded at the first close."""
    k, out, e = 2.0 / (n + 1), [], None
    for c in closes:
        e = c if e is None else e + k * (c - e)
        out.append(e)
    return out


def atr_series(bars: list[dict], n: int) -> list[float | None]:
    """Wilder's ATR: the mean true range of the first n bars, then smoothed; None before then."""
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


def signal(prev: dict, cur: dict, ema: float | None, rule: dict) -> int | None:
    """+1 long, -1 short, or None, read at `cur`'s close."""
    if cur["l"] < prev["l"] and cur["c"] > prev["h"]:
        side = 1
    elif cur["h"] > prev["h"] and cur["c"] < prev["l"]:
        side = -1
    else:
        return None
    if rule["colour"] == "same" and not (prev["c"] - prev["o"]) * side > 0:
        return None
    if rule["ema"] is not None and (ema is None or not (cur["c"] - ema) * side > 0):
        return None
    return side


def levels(side: int, entry: float, sig: dict, atr: float | None, rule: dict) -> tuple[float, float] | None:
    """(stop, target), or None when the entry is already past the stop (or no ATR yet)."""
    if rule["stop"] == "candle":
        stop = sig["l"] if side > 0 else sig["h"]
    else:
        if atr is None:
            return None
        stop = entry - side * rule["stop"][1] * atr
    risk = (entry - stop) * side
    if risk <= 0:
        return None
    return stop, entry + side * rule["r"] * risk


def walk(side: int, stop: float, target: float, bars: list[dict], j0: int, last: int | None = None):
    """(exit index, price, why) from the entry bar `j0`. The entry bar is read from its open (the entry);
    a later bar opening past a level fills at its open; inside a bar the stop wins a tie. Unclosed by
    `last` (default the final bar): closed at that bar's close, `end`."""
    last = len(bars) - 1 if last is None else last
    for j in range(j0, last + 1):
        b = bars[j]
        if j > j0:
            if (b["o"] - stop) * side <= 0:
                return j, b["o"], "stop_gap"
            if (b["o"] - target) * side >= 0:
                return j, b["o"], "target_gap"
        hit_stop = (b["l"] <= stop) if side > 0 else (b["h"] >= stop)
        hit_target = (b["h"] >= target) if side > 0 else (b["l"] <= target)
        if hit_stop:
            return j, stop, "stop"
        if hit_target:
            return j, target, "target"
    return last, bars[last]["c"], "end"


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


def warm_up(rule: dict, atr_n: int) -> int:
    """The first bar the rule may signal on: the EMA's length, and the ATR's for an ATR stop."""
    return max(1, rule["ema"] or 0, atr_n if rule["stop"] != "candle" else 0)


def run_fund(symbol: str, bars: list[dict], rule: dict, spec: dict = SPEC) -> tuple[list[dict], dict]:
    """Every trade the rule takes on one fund's bars, one position at a time, and the skips by reason."""
    closes = [b["c"] for b in bars]
    ema = ema_series(closes, rule["ema"]) if rule["ema"] else [None] * len(bars)
    atr = atr_series(bars, spec["atr"])
    warm = warm_up(rule, spec["atr"])
    trades, skipped, free_from = [], {"in_trade": 0, "past_stop": 0, "no_next_bar": 0}, 0
    for i in range(warm, len(bars)):
        side = signal(bars[i - 1], bars[i], ema[i], rule)
        if side is None:
            continue
        if i < free_from:
            skipped["in_trade"] += 1
            continue
        if i + 1 >= len(bars):
            skipped["no_next_bar"] += 1
            continue
        entry = bars[i + 1]["o"]
        lv = levels(side, entry, bars[i], atr[i], rule)
        if lv is None:
            skipped["past_stop"] += 1
            continue
        stop, target = lv
        j, px, why = walk(side, stop, target, bars, i + 1)
        shares = max(1, int(spec["notional"] // entry))
        money = stock_money(side, shares, entry, px, spec["slippage_per_share"])
        risk = (entry - stop) * side
        trades.append(
            {
                "symbol": symbol,
                "date": bars[i + 1]["day"],
                "exit_date": bars[j]["day"],
                "side": side,
                "entry": entry,
                "exit": px,
                "why": why,
                "shares": shares,
                "bp": shares * entry,
                "days": days_between(bars[i + 1]["day"], bars[j]["day"]),
                "risk_atr": risk / atr[i] if atr[i] else None,
                **money,
            }
        )
        free_from = j
    return trades, skipped


# --------------------------------------------------------------------------- the control (pure)


def placebo(bars: list[dict], atr: list, eligible: list[int], side: int, risk_atr: float, rule: dict, rng):
    """One random entry on the same fund and part: a random eligible bar's next open, the given side,
    the trade's own stop distance in ATR units at that bar, the same R. None when it cannot be placed."""
    k = eligible[rng.randrange(len(eligible))]
    if atr[k] is None or k + 1 >= len(bars):
        return None
    entry = bars[k + 1]["o"]
    risk = risk_atr * atr[k]
    if risk <= 0:
        return None
    stop, target = entry - side * risk, entry + side * rule["r"] * risk
    j, px, _ = walk(side, stop, target, bars, k + 1)
    shares = max(1, int(SPEC["notional"] // entry))
    return stock_money(side, shares, entry, px, SPEC["slippage_per_share"])["net"]


def control_diffs(trades: list[dict], funds: dict[str, list[dict]], rule: dict, split: str) -> list[dict]:
    """Per entry session: the session's trade nets summed, less the mean over the placebo draws of their
    placebos' nets summed. A placebo's side is drawn from the arm's own long share in the trade's part
    (never the trade's own side, which would carry the outcome), its bar from the same fund and part."""
    by_part: dict[str, list[dict]] = {"design": [], "holdout": []}
    for t in trades:
        by_part["design" if t["date"] < split else "holdout"].append(t)
    p_long = {p: (sum(t["side"] > 0 for t in ts) / len(ts) if ts else 0.5) for p, ts in by_part.items()}
    atrs = {s: atr_series(b, SPEC["atr"]) for s, b in funds.items()}
    eligible: dict[tuple[str, str], list[int]] = {}
    for s, b in funds.items():
        warm = max(rule["ema"] or 0, SPEC["atr"])
        for p in ("design", "holdout"):
            eligible[(s, p)] = [
                k
                for k in range(warm, len(b) - 1)
                if (b[k + 1]["day"] < split) == (p == "design") and atrs[s][k] is not None
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
                if not el or not t["risk_atr"]:
                    got.append(None)
                    continue
                side = 1 if rng.random() < p_long[part] else -1
                got.append(placebo(funds[t["symbol"]], atrs[t["symbol"]], el, side, t["risk_atr"], rule, rng))
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
    """Reported: counts, win rate, mean net, costs, hold, exits by reason, longs and shorts apart."""
    if not trades:
        return {"trades": 0}
    return {
        "trades": len(trades),
        "win_rate": sum(1 for t in trades if t["net"] > 0) / len(trades),
        "mean_net": statistics.fmean(t["net"] for t in trades),
        "mean_return": statistics.fmean(t["net"] / t["bp"] for t in trades),
        "mean_costs": statistics.fmean(t["fees"] + t["slippage"] for t in trades),
        "mean_days": statistics.fmean(t["days"] for t in trades),
        "exits": {w: sum(1 for t in trades if t["why"] == w) for w in sorted({t["why"] for t in trades})},
        "long_share": sum(t["side"] > 0 for t in trades) / len(trades),
        "by_side": {
            s: statistics.fmean(t["net"] for t in trades if t["side"] == v)
            for s, v in (("long", 1), ("short", -1))
            if any(t["side"] == v for t in trades)
        },
    }


# --------------------------------------------------------------------------- 4-hour gold (reported)


def front_schedule(
    daily: dict[str, dict[str, float]], start: str | None = None, cycle: str = GOLD["cycle"]
) -> dict[str, str]:
    """{session: front contract} by daily volume, from `start`: the cycle contract with the most volume
    on a session becomes the front from the next session (decided at the close, so never with
    hindsight), and the front never moves back to an earlier month. The first session with a volume
    leader starts it; a front with no bar on a session gives way to that session's leader at once.
    `start` must be where every near month has bars: before about 2015 the store holds only a few
    far-dated contracts, and a forward-only rule started there would stay on a far month."""

    def key(sym: str) -> tuple[int, int]:
        body = sym.split(":", 1)[0]
        return 2000 + int(body[4:]), "FGHJKMNQUVXZ".index(body[3])

    contracts = sorted((c for c in daily if c.split(":", 1)[0][3] in cycle), key=key)
    days = sorted({d for c in contracts for d in daily[c] if start is None or d >= start})
    out: dict[str, str] = {}
    front = None
    pending = None
    for d in days:
        if pending:
            front, pending = pending, None
        live = [c for c in contracts if daily[c].get(d, 0) > 0 and (front is None or key(c) >= key(front))]
        if not live:
            continue
        leader = max(live, key=lambda c: (daily[c][d], [-x for x in key(c)]))
        if front is None or daily.get(front, {}).get(d, 0) <= 0:
            front = leader
        out[d] = front
        if key(leader) > key(front):
            pending = leader
    return out


def trade_day(ts_ms: int) -> str:
    """A futures hour's session: the ET date, moved to the next day from 18:00 ET (the CME day)."""
    et = datetime.fromtimestamp(ts_ms / 1000.0, tz=ET)
    d = et.date()
    if et.hour >= 18:
        d = date.fromordinal(d.toordinal() + 1)
    return d.isoformat()


def bucket(ts_ms: int, alignment: str) -> int:
    """The 4-hour bar an hour belongs to: UTC hours 00/04/08/12/16/20 (the broker's own 4-hour
    candles), or 4-hour steps from the 18:00 ET session open."""
    if alignment == "utc":
        return ts_ms - ts_ms % H4_MS
    et = datetime.fromtimestamp(ts_ms / 1000.0, tz=ET)
    hours_in = (et.hour - 18) % 24
    return ts_ms - (hours_in % 4) * 3_600_000 - et.minute * 60_000


def four_hour(hours: list[dict], alignment: str) -> list[dict]:
    """4-hour bars from hourly ones (`ts`, `o h l c`, `contract`), never across a contract change."""
    out: list[dict] = []
    for h in sorted(hours, key=lambda x: x["ts"]):
        b = bucket(h["ts"], alignment)
        if out and out[-1]["ts"] == b and out[-1]["contract"] == h["contract"]:
            cur = out[-1]
            cur["h"], cur["l"], cur["c"] = max(cur["h"], h["h"]), min(cur["l"], h["l"]), h["c"]
        else:
            out.append(
                {"ts": b, "o": h["o"], "h": h["h"], "l": h["l"], "c": h["c"],
                 "contract": h["contract"], "day": trade_day(b)}
            )  # fmt: skip
    return out


def run_gold(bars: list[dict], rule: dict) -> tuple[list[dict], dict]:
    """The rule on stitched 4-hour gold: a signal whose previous bar is another contract is skipped; a
    trade still open at a contract change closes at the old contract's last bar (`roll`)."""
    ema = ema_series([b["c"] for b in bars], rule["ema"]) if rule["ema"] else [None] * len(bars)
    atr = atr_series(bars, SPEC["atr"])
    warm = warm_up(rule, SPEC["atr"])
    trades, skipped, free_from = [], {"in_trade": 0, "past_stop": 0, "roll": 0, "no_next_bar": 0}, 0
    for i in range(warm, len(bars)):
        side = signal(bars[i - 1], bars[i], ema[i], rule)
        if side is None:
            continue
        if bars[i - 1]["contract"] != bars[i]["contract"]:
            skipped["roll"] += 1
            continue
        if i < free_from:
            skipped["in_trade"] += 1
            continue
        if i + 1 >= len(bars) or bars[i + 1]["contract"] != bars[i]["contract"]:
            skipped["no_next_bar"] += 1
            continue
        entry = bars[i + 1]["o"]
        lv = levels(side, entry, bars[i], atr[i], rule)
        if lv is None:
            skipped["past_stop"] += 1
            continue
        last = i + 1
        while last + 1 < len(bars) and bars[last + 1]["contract"] == bars[i]["contract"]:
            last += 1
        j, px, why = walk(side, *lv, bars, i + 1, last=last)
        if why == "end" and last + 1 < len(bars):
            why = "roll"
        gross = side * GOLD["oz"] * (px - entry)
        trades.append(
            {"date": bars[i + 1]["day"], "side": side, "why": why, "gross": gross,
             "net": gross - 2 * GOLD["cost_per_side"], "contract": bars[i]["contract"]}
        )  # fmt: skip
        free_from = j
    return trades, skipped


def gold_summary(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    wins = [t["net"] for t in trades if t["net"] > 0]
    losses = [-t["net"] for t in trades if t["net"] <= 0]
    return {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades),
        "mean_net": statistics.fmean(t["net"] for t in trades),
        "profit_factor": (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else None,
        "long_share": sum(t["side"] > 0 for t in trades) / len(trades),
        "exits": {w: sum(1 for t in trades if t["why"] == w) for w in sorted({t["why"] for t in trades})},
    }


# --------------------------------------------------------------------------- inputs (read-only)


def bars_db() -> Path:
    return home.data_dir("public") / "item88" / "bars.db"


def gold_db() -> Path:
    return home.data_dir("market-files") / "gold_bars.db"


def load_funds(path: Path, through: str, last: int | None = None) -> dict[str, list[dict]]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        out = {}
        for sym in ETFS:
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


def load_gold_hours(bars_path: Path, gold_path: Path, through: str) -> tuple[list[dict], dict]:
    """Hourly gold, stitched: Yahoo's GC=F before `yahoo_before` (contract unknown, `yahoo`), then each
    session's front contract by `front_schedule` from the recorder's dated contracts. Empty when the
    recorder holds no dated contracts."""
    info: dict = {}
    hours: list[dict] = []
    conn = sqlite3.connect(f"file:{bars_path}?mode=ro", uri=True)
    try:
        for ts, o, h, lo, c in conn.execute(
            "SELECT ts_ms, open, high, low, close FROM yahoo_gc_1h ORDER BY ts_ms"
        ):
            if trade_day(ts) < GOLD["yahoo_before"]:
                hours.append({"ts": ts, "o": o, "h": h, "l": lo, "c": c, "contract": "yahoo"})
    finally:
        conn.close()
    info["yahoo_hours"] = len(hours)
    if not gold_path.exists():
        return hours, {**info, "contracts": "absent"}
    conn = sqlite3.connect(f"file:{gold_path}?mode=ro", uri=True)
    try:
        daily: dict[str, dict[str, float]] = {}
        for sym, d, v in conn.execute(
            "SELECT symbol, et_day, volume FROM gold_bars WHERE interval = '1d' AND symbol LIKE '/GC___:XCEC'"
        ):
            daily.setdefault(sym, {})[d] = v
        front = front_schedule(daily, start=GOLD["schedule_from"])
        n = 0
        for sym, ts, o, h, lo, c in conn.execute(
            "SELECT symbol, ts_ms, open, high, low, close FROM gold_bars WHERE interval = '1h' "
            "AND symbol LIKE '/GC___:XCEC' ORDER BY ts_ms"
        ):
            d = trade_day(ts)
            if GOLD["yahoo_before"] <= d <= through and front.get(d) == sym:
                hours.append({"ts": ts, "o": o, "h": h, "l": lo, "c": c, "contract": sym})
                n += 1
    finally:
        conn.close()
    rolls = sorted({(d, s) for d, s in front.items() if d >= GOLD["yahoo_before"]})
    return hours, {**info, "contract_hours": n, "fronts": sorted({s for _, s in rolls})}


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


def fingerprint(funds: dict[str, list[dict]], hours: list[dict]) -> dict:
    """What the run read, as counts and a hash of them: never a price."""
    lines = [
        f"{s}:{len(b)}:{b[0]['day'] if b else ''}:{b[-1]['day'] if b else ''}"
        for s, b in sorted(funds.items())
    ]
    lines.append(f"gold:{len(hours)}")
    return {"funds": {s: len(b) for s, b in funds.items()}, "gold_hours": len(hours),
            "sha256": hashlib.sha256("\n".join(lines).encode()).hexdigest()}  # fmt: skip


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item88"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- the run


def study(
    funds: dict[str, list[dict]], hours: list[dict], rate, reporter: progress.Reporter | None = None
) -> dict:
    split = RULE["splits"][0]
    trades, skipped = [], {}
    if reporter is not None:
        reporter.split("runs", list(funds) * (1 + len(VARIANTS)), unit="fund runs")
    for sym, bars in funds.items():
        ts, sk = run_fund(sym, bars, SPEC["rule"])
        trades += ts
        skipped[sym] = sk
        if reporter is not None:
            reporter.advance("runs", key=sym, current=sym)
    rows = session_rows(trades, rate)
    bars_block = kit_rule.evaluate(RULE, {"etf-daily": rows})
    control = judge_control(control_diffs(trades, funds, SPEC["rule"], split), CONTROL_RULE)

    def parts(ts: list[dict]) -> dict:
        return {"design": summary([t for t in ts if t["date"] < split]),
                "holdout": summary([t for t in ts if t["date"] >= split])}  # fmt: skip

    variants = {}
    for name, rule in VARIANTS.items():
        vt = [t for sym, bars in funds.items() for t in run_fund(sym, bars, rule)[0]]
        variants[name] = parts(vt)
        if reporter is not None:
            for sym in funds:
                reporter.advance("runs", key=sym, current=f"{name} {sym}")
    gold = {}
    for alignment in GOLD["alignments"]:
        bars4 = four_hour(hours, alignment)
        for name, rule in (("stated", SPEC["rule"]), ("tuned", VARIANTS["tuned"])):
            gt, gs = run_gold(bars4, rule)
            gold[f"{alignment}/{name}"] = {
                "all": gold_summary(gt),
                "yahoo_span": gold_summary([t for t in gt if t["contract"] == "yahoo"]),
                "contract_span": gold_summary([t for t in gt if t["contract"] != "yahoo"]),
                "skipped": gs,
            }
    return {
        **bars_block,
        "control": {"etf-daily": control},
        "verdict": {"etf-daily": both(bars_block["verdict"]["etf-daily"], control["verdict"])},
        "reported": {
            "arm": parts(trades),
            "by_fund": {s: parts([t for t in trades if t["symbol"] == s]) for s in funds},
            "years": {
                y: summary([t for t in trades if t["date"][:4] == y])
                for y in sorted({t["date"][:4] for t in trades})
            },
            "variants": variants,
            "gld_alone": parts([t for t in trades if t["symbol"] == "GLD"]),
            "gold_4h": gold,
            "skipped": skipped,
        },
        "window": {
            "through": SPEC["through"],
            "splits": RULE["splits"],
            "from": min((b[0]["day"] for b in funds.values() if b), default=None),
        },  # fmt: skip
        "corrections": list(REPLAY_CORRECTIONS),
    }


def cmd_study(args) -> int:
    funds = load_funds(bars_db(), SPEC["through"], last=args.smoke)
    hours, gold_info = load_gold_hours(bars_db(), gold_db(), SPEC["through"])
    if args.smoke:
        hours = hours[-args.smoke * 6 :]
    with progress.Reporter("engulf_item88-study") as rep:
        block = study(funds, hours, bill_reader(), reporter=rep)
    doc = {"note": "derived from personal-use broker and public data: never commit or publish", "item": 88,
           "spec": SPEC, "control_rule": CONTROL_RULE, "gold": {**GOLD, **gold_info}, **block,
           "inputs": fingerprint(funds, hours), "written_at": datetime.now(UTC).isoformat()}  # fmt: skip
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
