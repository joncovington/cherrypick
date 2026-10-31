"""Item 29, the share ladder in options (docs/edge-hypotheses-plan.md): on the ladder's trigger days, a
call debit spread, a put credit spread or a ZEBRA in place of the share lot, each paired against shares
of the same entry delta over the same closes.

    python scripts/share_ladder_item29.py expirations          # each name's listed expiries (network, once)
    python scripts/share_ladder_item29.py plan [--out FILE]    # the expiry paths to fetch (local)
    python scripts/share_ladder_item29.py study [--json PATH] [--names A,B]
    python scripts/share_ladder_item29.py report [--json PATH] # results.md from results.json (local)

`expirations` asks the vendor once for SOXL's and TQQQ's listed expiries. `plan` reads those and the
technicals history's raw daily bars, and writes every (name, expiry, start, through) path any trigger
in a window could need, its rolls included: the input of `fetch-chain-paths`. Every exit and roll is
set by the share closes alone, so the plan is complete before a single option quote is read. `study`
replays the arms and states the verdict. All three write under the backtest results directory
(results/research/item29/), never into the repo: the chains are licensed.

**Bars.** The technicals history's raw closes, so a strike matches its own day's chain; the trigger
and the target read split-adjusted ratios. A split ex-date inside a trade ends the trade at the
session before it, marked at that session's quotes (an adjusted contract's strikes and deliverable
change), counted as `split`.

**The trigger** is item 26's `once`: a session whose low reaches 93% of the previous close. The
structure and its share lot enter at that session's close. One open position per name and arm; a
position closed in a session frees its slot from the next. **The target** is the first later close at
or above 104% of the entry close.

**Tenors.** `7d`: the first listed expiry 5 or more calendar days out. `21d`: the first 18 or more out.
`75d`: the expiry 60 to 90 days out nearest 75 (the earlier on a tie). With no such expiry the entry is
skipped, counted. The expiry rule bites at the expiry session for `7d` and `21d`, and at the first
session with 21 or fewer days left for `75d`.

**Structures,** one set, strikes from the entry close S. `call-spread`: long the call nearest S, short
the first strike at or above 1.04 S. `put-spread`: short the put nearest S, long the first strike at or
below 0.96 S. `zebra`: long 2 calls at the strike whose delta is nearest 0.70, short 1 call nearest S.
"Nearest" takes the lower strike on a tie. Only strikes quoted on both sides count. Delta is
Black-Scholes from the leg's mid, the 3-month bill rate before the session and no dividend, over
calendar days to expiry.

**Rules at the expiry rule.** `close` closes the structure at that session's quotes; `roll` closes it
and opens the same structure and tenor from that session's close, the target staying 104% of the
first entry close, until the target, the window's end or a split; a roll that cannot be opened ends
the trade (`no-roll`). `exercise` (ZEBRA, reported only): when the long calls are in the money at the
expiry rule they become 200 shares at their strike, the short is closed, and the shares are held to
the target as a share lot; out of the money, the set is closed as `close` does.

**Fills and costs.** The natural: buy at the ask, sell at the bid. A leg with no two-sided quote on
an exit session is marked at its last earlier quote in the trade (counted `stale`); on its expiry
session, at intrinsic value against the close, with the broker's settlement fee on each ITM symbol.
Fees from `cherrypick.core.fees`: $1.00 a contract to open, clearing and ORF on every contract, the
FINRA TAF on sells. The share lot pays item 26's 0.05% a side. The stress takes the options' fees at
1.5x and every option fill $0.05 worse; the share lot is unchanged.

**Measured,** per trade: the structure's net less the share lot's, per share of the lot (the
structure's entry delta x 100, rounded to whole shares). A trade whose delta rounds under one share is
skipped, counted. Beside it: the mid-priced difference, the structure's return on capital at risk
(the debit, or the width less the credit, on the first set), the share lot's on cost, the hold and
the worst trade.

**The verdict,** per judged arm (3 structures x 3 tenors x {close, roll}): in the design window, the
mean difference above zero with a one-sided t above 2, one trade one observation, Holm across the 18;
the mean positive in each time-half and on each name; positive at the stress; then the holdout's mean
above zero. Under 30 closed trades in an arm in a window is no verdict.
"""

from __future__ import annotations

import argparse
import bisect
import importlib.util
import json
import math
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from cherrypick.core import bs
from cherrypick.core import fees as core_fees
from cherrypick.core import stats as core_stats

NAMES = ("SOXL", "TQQQ")
DROP = 0.07
GAIN = 0.04
SHORT_PUT_FLOOR = 0.96
ZEBRA_DELTA = 0.70
TENORS = ("7d", "21d", "75d")
STRUCTURES = ("call-spread", "put-spread", "zebra")
RULES = ("close", "roll")
ARMS = tuple(f"{s}-{t}-{r}" for s in STRUCTURES for t in TENORS for r in RULES)
REPORTED = tuple(f"zebra-{t}-exercise" for t in TENORS)
MIN_DAYS = {"7d": 5, "21d": 18}
LONG_RANGE = (60, 90)
LONG_TARGET_DAYS = 75
LONG_EXIT_DAYS = 21
SHARE_COST = 0.0005
STRESS_FEES = 1.5
STRESS_SLIP = 0.05
WEEKLY = ("2022-01-03", "2024-12-31")
LONG = ("2016-05-02", "2024-12-31")
HOLDOUT = ("2025-01-02", "2026-10-28")
WINDOWS = {
    "design": {"7d": WEEKLY, "21d": WEEKLY, "75d": LONG},
    "holdout": {"7d": HOLDOUT, "21d": HOLDOUT, "75d": HOLDOUT},
}
MIN_TRADES = 30
T_BAR = 2.0
ALPHA = 0.05
MULT = 100


# ------------------------------------------------------------------------------------------------
# Pure: the share bars, the trigger and each trade's schedule.


@dataclass(frozen=True)
class Day:
    date: str
    low: float  # split-adjusted
    close: float  # split-adjusted
    raw: float  # the day's own close, on its chain's strike basis


def days_from(raw_bars, splits) -> list[Day]:
    """Raw bars to Days: each adjusted price divides out the splits after its date, so a ratio across
    an ex-date reads the move, not the split."""
    out = []
    for b in raw_bars:
        if not b.close or not b.low:
            continue
        f = math.prod(s.to_factor / s.for_factor for s in splits if s.ex_date > b.date)
        out.append(Day(b.date, b.low / f, b.close / f, b.close))
    return out


def triggers(days: list[Day], start: str, end: str) -> list[int]:
    """Indices of sessions in [start, end] whose low reaches (1 - DROP) of the previous close."""
    return [
        i
        for i in range(1, len(days))
        if start <= days[i].date <= end and days[i].low <= (1 - DROP) * days[i - 1].close
    ]


def _cal(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def pick_expiry(tenor: str, session: str, expiries: list[str]) -> str | None:
    if tenor in MIN_DAYS:
        k = bisect.bisect_left(expiries, session)
        return next((e for e in expiries[k:] if _cal(session, e) >= MIN_DAYS[tenor]), None)
    lo, hi = LONG_RANGE
    fits = [e for e in expiries if lo <= _cal(session, e) <= hi]
    return min(fits, key=lambda e: (abs(_cal(session, e) - LONG_TARGET_DAYS), e)) if fits else None


def rule_index(days: list[Day], tenor: str, expiry: str, after: int) -> int | None:
    """The session the expiry rule bites at, after index `after`: the expiry session (the last on or
    before the expiry) for the weekly tenors, the first with LONG_EXIT_DAYS or fewer left for `75d`."""
    if tenor in MIN_DAYS:
        k = bisect.bisect_right([d.date for d in days], expiry) - 1
        return k if k > after else None
    for j in range(after + 1, len(days)):
        if _cal(days[j].date, expiry) <= LONG_EXIT_DAYS:
            return j
    return None


@dataclass(frozen=True)
class Segment:
    open: int
    expiry: str
    close: int


@dataclass
class Schedule:
    entry: int
    exit: int
    how: str  # target | rule | end | split | no-expiry
    last: int  # the last session the trade may reach: the window's end, or the session before a split
    last_how: str  # end | split
    segments: list[Segment] = field(default_factory=list)


def schedule(
    days: list[Day], i: int, tenor: str, expiries: list[str], end: str, split_dates: list[str], roll: bool
) -> Schedule | None:
    """The trade a trigger at index i makes, from the share closes alone: each segment's open, expiry
    and close, and how the trade ended. None when no expiry fits at entry."""
    target = (1 + GAIN) * days[i].close
    dates = [d.date for d in days]
    last, cap_how = bisect.bisect_right(dates, end) - 1, "end"
    cut = next((s for s in split_dates if s > days[i].date), None)
    if cut is not None and bisect.bisect_left(dates, cut) - 1 < last:
        last, cap_how = bisect.bisect_left(dates, cut) - 1, "split"
    hit = next((j for j in range(i + 1, last + 1) if days[j].close >= target), None)
    exp = pick_expiry(tenor, days[i].date, expiries)
    if exp is None:
        return None
    out = Schedule(i, i, "", last, cap_how)
    at = i
    while True:
        rule = rule_index(days, tenor, exp, at)
        stops = [(hit, "target"), (last, cap_how), (rule, "rule")]
        j, how = min(((x, h) for x, h in stops if x is not None and x > at), key=lambda t: t[0])
        out.segments.append(Segment(at, exp, j))
        if how != "rule" or not roll:
            out.exit, out.how = j, how
            return out
        nxt = pick_expiry(tenor, days[j].date, expiries)
        if nxt is None:
            out.exit, out.how = j, "no-expiry"
            return out
        at, exp = j, nxt


def path_requests(sym: str, days: list[Day], expiries: list[str], split_dates: list[str]) -> list[dict]:
    """Every expiry path any trigger in any window could use, rolls included, one per (name, expiry):
    from the earliest session it is opened to the latest it is held. Every trigger is planned, not only
    the ones an arm takes, so a skipped entry never leaves a later one without its chain."""
    span: dict[str, list[str]] = {}
    for windows in WINDOWS.values():
        for tenor, (w0, w1) in windows.items():
            for i in triggers(days, w0, w1):
                sch = schedule(days, i, tenor, expiries, w1, split_dates, roll=True)
                for seg in sch.segments if sch else []:
                    a, b = days[seg.open].date, days[seg.close].date
                    have = span.setdefault(seg.expiry, [a, b])
                    have[0], have[1] = min(have[0], a), max(have[1], b)
    return [{"symbol": sym, "expiration": e, "start": a, "through": b} for e, (a, b) in sorted(span.items())]


# ------------------------------------------------------------------------------------------------
# Pure: legs, fills and fees on one session's chain.

Chain = dict  # {(strike, right): (bid, ask)}


@dataclass(frozen=True)
class Leg:
    strike: float
    right: str
    qty: int  # + long, - short


def quoted(chain: Chain, right: str) -> list[float]:
    return sorted(k for (k, r), (b, a) in chain.items() if r == right and _two_sided(b, a))


def _two_sided(b, a) -> bool:
    return b is not None and a is not None and a > 0 and a >= b >= 0


def nearest(strikes: list[float], s: float) -> float | None:
    return min(strikes, key=lambda k: (abs(k - s), k)) if strikes else None


def leg_delta(chain: Chain, leg: Leg, spot: float, years: float, rate: float) -> float | None:
    b, a = chain[(leg.strike, leg.right)]
    vol = bs.implied_vol(spot, leg.strike, years, (b + a) / 2, leg.right, rate)
    return bs.delta(spot, leg.strike, years, vol, leg.right, rate) if vol else None


def choose_legs(structure: str, chain: Chain, spot: float, years: float, rate: float) -> list[Leg] | str:
    """The structure's legs on this chain, or why it cannot be built."""
    calls, puts = quoted(chain, "C"), quoted(chain, "P")
    if structure == "call-spread":
        long_k = nearest(calls, spot)
        short_k = next((k for k in calls if k >= (1 + GAIN) * spot), None)
        if long_k is None or short_k is None or short_k <= long_k:
            return "no strikes"
        return [Leg(long_k, "C", 1), Leg(short_k, "C", -1)]
    if structure == "put-spread":
        short_k = nearest(puts, spot)
        long_k = next((k for k in reversed(puts) if k <= SHORT_PUT_FLOOR * spot), None)
        if short_k is None or long_k is None or long_k >= short_k:
            return "no strikes"
        return [Leg(short_k, "P", -1), Leg(long_k, "P", 1)]
    if structure == "zebra":
        short_k = nearest(calls, spot)
        if short_k is None:
            return "no strikes"
        deltas = {}
        for k in calls:
            if k < short_k:
                d = leg_delta(chain, Leg(k, "C", 1), spot, years, rate)
                if d is not None:
                    deltas[k] = d
        if not deltas:
            return "no strikes"
        long_k = min(deltas, key=lambda k: (abs(deltas[k] - ZEBRA_DELTA), k))
        return [Leg(long_k, "C", 2), Leg(short_k, "C", -1)]
    raise ValueError(structure)


def position_delta(legs: list[Leg], chain: Chain, spot: float, years: float, rate: float) -> float | None:
    """The structure's delta in shares."""
    total = 0.0
    for leg in legs:
        d = leg_delta(chain, leg, spot, years, rate)
        if d is None:
            return None
        total += leg.qty * d * MULT
    return total


def fill(legs: list[Leg], chain: Chain, opening: bool, slip: float) -> float | None:
    """Cash for the whole set at the natural, worse by `slip` a leg; + received, - paid. None when a
    leg has no two-sided quote."""
    cash = 0.0
    for leg in legs:
        q = chain.get((leg.strike, leg.right))
        if q is None or not _two_sided(*q):
            return None
        b, a = q
        buying = (leg.qty > 0) == opening
        px = a + slip if buying else max(0.0, b - slip)
        cash += (-px if buying else px) * abs(leg.qty) * MULT
    return cash


def mid_cash(legs: list[Leg], chain: Chain, opening: bool) -> float | None:
    cash = 0.0
    for leg in legs:
        q = chain.get((leg.strike, leg.right))
        if q is None or not _two_sided(*q):
            return None
        m = (q[0] + q[1]) / 2
        cash += (-m if (leg.qty > 0) == opening else m) * abs(leg.qty) * MULT
    return cash


def intrinsic_cash(legs: list[Leg], spot: float) -> float:
    return sum(leg.qty * bs.intrinsic(spot, leg.strike, leg.right) * MULT for leg in legs)


def option_fee(sym: str, legs: list[Leg], opening: bool, mult: float = 1.0) -> float:
    contracts = sum(abs(leg.qty) for leg in legs)
    sells = sum(abs(leg.qty) for leg in legs if (leg.qty < 0) == opening)
    fn = core_fees.ic_open_fee if opening else core_fees.ic_close_fee
    return fn(sym, quantity=1, legs=contracts, sell_legs=sells, ndigits=4) * mult


def settle_fee(legs: list[Leg], spot: float) -> float:
    return core_fees.ic_expire_fee(sum(1 for leg in legs if bs.intrinsic(spot, leg.strike, leg.right) > 0))


def share_net(shares: int, entry: float, exit_: float) -> float:
    return shares * (exit_ - entry) - SHARE_COST * shares * (entry + exit_)


def capital_at_risk(legs: list[Leg], open_cash: float) -> float:
    """The debit, or for a credit the width less the credit."""
    if open_cash <= 0:
        return -open_cash
    width = max(leg.strike for leg in legs) - min(leg.strike for leg in legs)
    return width * MULT - open_cash


# ------------------------------------------------------------------------------------------------
# Pricing a schedule.


@dataclass
class Cost:
    slip: float = 0.0
    fees: float = 1.0


NATURAL, STRESS = Cost(), Cost(STRESS_SLIP, STRESS_FEES)


def price_trade(
    sym: str,
    structure: str,
    sch: Schedule,
    days: list[Day],
    chain_at,
    rate_at,
    *,
    exercise: bool = False,
) -> dict | str:
    """One scheduled trade on the chains, or why it was skipped. `chain_at(session, expiry)` returns a
    Chain; `rate_at(session)` the bill rate as a decimal. Prices the natural, the stress and the mid
    together so the three share one set of legs."""
    first = sch.segments[0]
    e0 = days[sch.entry]
    years0 = _cal(e0.date, first.expiry) / 365
    chain0 = chain_at(e0.date, first.expiry)
    if not chain0:
        return "no chain"
    legs0 = choose_legs(structure, chain0, e0.raw, years0, rate_at(e0.date))
    if isinstance(legs0, str):
        return legs0
    delta0 = position_delta(legs0, chain0, e0.raw, years0, rate_at(e0.date))
    if delta0 is None or round(delta0) < 1:
        return "no delta"
    shares = round(delta0)
    nets = {"natural": 0.0, "stress": 0.0, "mid": 0.0}
    stale, risk, priced = 0, None, 0
    exit_index, how = sch.exit, sch.how
    for n, seg in enumerate(sch.segments):
        d_open = days[seg.open]
        if n == 0:
            chain, legs = chain0, legs0
        else:
            chain = chain_at(d_open.date, seg.expiry)
            legs = choose_legs(structure, chain, d_open.raw, _cal(d_open.date, seg.expiry) / 365,
                               rate_at(d_open.date))  # fmt: skip
        opened = None if isinstance(legs, str) else {name: fill(legs, chain, True, c.slip) for name, c in
                                                     (("natural", NATURAL), ("stress", STRESS))}  # fmt: skip
        if opened is None or None in opened.values():
            if n == 0:
                return "no quote"
            # a roll that cannot be opened ends the trade where the last set closed
            exit_index, how = seg.open, "no-roll"
            break
        nets["natural"] += opened["natural"] - option_fee(sym, legs, True)
        nets["stress"] += opened["stress"] - option_fee(sym, legs, True, STRESS_FEES)
        nets["mid"] += mid_cash(legs, chain, True) - option_fee(sym, legs, True)
        if n == 0:
            risk = capital_at_risk(legs, opened["natural"])
        long_leg = next((leg for leg in legs if leg.qty > 0), None)
        ex_now = (exercise and n == len(sch.segments) - 1 and sch.how == "rule"
                  and days[seg.close].raw > long_leg.strike)  # fmt: skip
        got, was_stale = _close(
            sym, [leg for leg in legs if leg.qty < 0] if ex_now else legs, seg, days, chain_at
        )
        stale += was_stale
        priced += 1
        for name in nets:
            nets[name] += got[name]
        if ex_now:
            extra = _exercise(long_leg, sch, days)
            for name in nets:
                nets[name] += extra["net"]
            exit_index, how = extra["exit"], "exercise-" + extra["how"]
    entry_raw, exit_raw = e0.raw, days[exit_index].raw
    lot = share_net(shares, entry_raw, exit_raw)
    return {
        "name": sym,
        "entry": e0.date,
        "exit": days[exit_index].date,
        "how": how,
        "segments": priced,
        "shares": shares,
        "delta": round(delta0, 2),
        "net": round(nets["natural"], 2),
        "lot_net": round(lot, 2),
        "diff": (nets["natural"] - lot) / shares,
        "diff_stress": (nets["stress"] - lot) / shares,
        "diff_mid": (nets["mid"] - lot) / shares,
        "risk": risk,
        "return": nets["natural"] / risk if risk else None,
        "lot_return": lot / (shares * entry_raw),
        "hold_days": _cal(e0.date, days[exit_index].date),
        "stale": stale,
        "legs": [[leg.strike, leg.right, leg.qty] for leg in legs0],
    }


def _close(sym: str, legs: list[Leg], seg: Segment, days: list[Day], chain_at) -> tuple[dict, int]:
    """Closing cash under each costing, and whether a stale or intrinsic mark was used."""
    d = days[seg.close]
    chain = chain_at(d.date, seg.expiry)
    out = {}
    for name, cost in (("natural", NATURAL), ("stress", STRESS)):
        cash = fill(legs, chain, False, cost.slip)
        if cash is not None:
            out[name] = cash - option_fee(sym, legs, False, cost.fees)
    if out:
        out["mid"] = mid_cash(legs, chain, False) - option_fee(sym, legs, False)
        return out, 0
    if d.date >= seg.expiry:
        v = intrinsic_cash(legs, d.raw) - settle_fee(legs, d.raw)
        return {"natural": v, "stress": v - (STRESS_FEES - 1) * settle_fee(legs, d.raw), "mid": v}, 1
    for j in range(seg.close - 1, seg.open - 1, -1):
        chain = chain_at(days[j].date, seg.expiry)
        nat = fill(legs, chain, False, 0.0)
        if nat is not None:
            return {
                "natural": nat - option_fee(sym, legs, False),
                "stress": fill(legs, chain, False, STRESS_SLIP) - option_fee(sym, legs, False, STRESS_FEES),
                "mid": mid_cash(legs, chain, False) - option_fee(sym, legs, False),
            }, 1
    v = intrinsic_cash(legs, d.raw)
    return {"natural": v, "stress": v, "mid": v}, 1


def _exercise(long_leg: Leg, sch: Schedule, days: list[Day]) -> dict:
    """The long calls as shares at their strike, held to the trade's target, the window's end or the
    session before a split: their net from the exercise on (the premium was paid at the open)."""
    shares = long_leg.qty * MULT
    target = (1 + GAIN) * days[sch.entry].close
    k = sch.segments[-1].close
    j = next((x for x in range(k + 1, sch.last + 1) if days[x].close >= target), None)
    j, how = (j, "target") if j is not None else (max(k, sch.last), sch.last_how)
    exit_raw = days[j].raw
    fee = core_fees.assignment_round_trip_fee(shares, long_leg.strike, exit_raw, direction="long")
    return {"net": shares * (exit_raw - long_leg.strike) - fee, "exit": j, "how": how}


# ------------------------------------------------------------------------------------------------
# Running the arms and the verdict.


def run_arm(
    sym: str,
    arm: str,
    days: list[Day],
    expiries: list[str],
    split_dates: list[str],
    window,
    chain_at,
    rate_at,
) -> tuple[list[dict], dict]:
    structure, tenor, rule = arm.rsplit("-", 2)
    w0, w1 = window
    trades, counts = [], {"triggers": 0, "busy": 0}
    free_from = -1
    for i in triggers(days, w0, w1):
        counts["triggers"] += 1
        if i < free_from:
            counts["busy"] += 1
            continue
        sch = schedule(days, i, tenor, expiries, w1, split_dates, roll=(rule == "roll"))
        if sch is None:
            counts["no expiry"] = counts.get("no expiry", 0) + 1
            continue
        got = price_trade(sym, structure, sch, days, chain_at, rate_at, exercise=(rule == "exercise"))
        if isinstance(got, str):
            counts[got] = counts.get(got, 0) + 1
            continue
        trades.append(got)
        exit_i = bisect.bisect_left([d.date for d in days], got["exit"])
        free_from = exit_i + 1
    return trades, counts


def summary(trades: list[dict], key: str = "diff") -> dict:
    xs = [t[key] for t in trades]
    n = len(xs)
    out = {"n": n, "mean": statistics.fmean(xs) if xs else None}
    if n >= 2:
        sd = statistics.stdev(xs)
        out["sd"] = sd
        t = out["mean"] / (sd / math.sqrt(n)) if sd > 0 else (math.inf if out["mean"] > 0 else -math.inf)
        out["t"] = t
        out["p"] = 1 - core_stats.t_cdf(t, n - 1) if math.isfinite(t) else (0.0 if t > 0 else 1.0)
    return out


def halves(window: tuple[str, str]) -> tuple[tuple[str, str], tuple[str, str]]:
    a, b = date.fromisoformat(window[0]), date.fromisoformat(window[1])
    mid = a + (b - a) / 2
    return (window[0], mid.isoformat()), ((mid + timedelta(days=1)).isoformat(), window[1])


def arm_report(trades: list[dict], window: tuple[str, str]) -> dict:
    h1, h2 = halves(window)
    out = summary(trades)
    out["stress"] = summary(trades, "diff_stress")["mean"]
    out["mid"] = summary(trades, "diff_mid")["mean"]
    out["halves"] = [summary([t for t in trades if lo <= t["entry"] <= hi])["mean"] for lo, hi in (h1, h2)]
    out["names"] = {s: summary([t for t in trades if t["name"] == s])["mean"] for s in NAMES}
    rets = [t["return"] for t in trades if t["return"] is not None]
    out["return_median"] = statistics.median(rets) if rets else None
    out["lot_return_median"] = statistics.median(t["lot_return"] for t in trades) if trades else None
    out["hold_median"] = statistics.median(t["hold_days"] for t in trades) if trades else None
    out["worst"] = min((t["net"] for t in trades), default=None)
    if out.get("sd") is not None:
        out["mde"] = core_stats.minimum_detectable_effect(out["sd"], out["n"])
    out["how"] = {}
    for t in trades:
        out["how"][t["how"]] = out["how"].get(t["how"], 0) + 1
    out["stale"] = sum(t["stale"] for t in trades)
    return out


def _pos(x) -> bool:
    return x is not None and x > 0


def judge(design: dict[str, dict], holdout: dict[str, dict]) -> dict[str, dict]:
    """Each judged arm's verdict from its design and holdout reports."""
    pvals = {a: (design[a].get("p", 1.0) if design[a]["n"] >= MIN_TRADES else 1.0) for a in ARMS}
    holm = core_stats.holm(pvals, ALPHA)
    out = {}
    for a in ARMS:
        d, h = design[a], holdout[a]
        if d["n"] < MIN_TRADES:
            out[a] = {"verdict": "no verdict", "why": f"design {d['n']} trades"}
            continue
        checks = {
            "mean": _pos(d["mean"]),
            "t": (d.get("t") or 0) > T_BAR,
            "holm": holm[a],
            "halves": all(_pos(x) for x in d["halves"]),
            "names": all(_pos(x) for x in d["names"].values()),
            "stress": _pos(d["stress"]),
        }
        if not all(checks.values()):
            out[a] = {"verdict": "fail", "checks": checks}
            continue
        if h["n"] < MIN_TRADES:
            out[a] = {"verdict": "no verdict", "checks": checks, "why": f"holdout {h['n']} trades"}
            continue
        checks["holdout"] = _pos(h["mean"])
        out[a] = {"verdict": "pass" if checks["holdout"] else "fail", "checks": checks}
    return out


# ------------------------------------------------------------------------------------------------
# The stores and the commands.


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item29"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_days(sym: str) -> tuple[list[Day], list[str]]:
    from cherrypick.technicals import adjust, store
    from cherrypick.technicals import paths as tpaths

    hist = store.connect(tpaths.history_db())
    try:
        raw = store.raw_bars(hist, sym)
        splits = adjust.dedupe_splits(raw, store.splits(hist, sym))
    finally:
        hist.close()
    return days_from(raw, splits), sorted(s.ex_date for s in splits)


def load_expiries() -> dict[str, list[str]]:
    path = _out_dir() / "expirations.json"
    if not path.exists():
        raise SystemExit(f"no expiries at {path}: run `share_ladder_item29.py expirations` first")
    return json.loads(path.read_text(encoding="utf-8"))["expirations"]


def _rates():
    spec = importlib.util.spec_from_file_location("s28", Path(__file__).with_name("share_ladder_item28.py"))
    s28 = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("s28", s28)
    spec.loader.exec_module(s28)
    rates = s28.load_rates()
    if not rates:
        raise SystemExit("no Treasury curves: run `share_ladder_item28.py fetch` first")
    return lambda session: s28.rate_before(rates, session) / 100


def cmd_expirations(args) -> int:
    from cherrypick.backtest import settings
    from cherrypick.backtest.vendor import client_for

    client = client_for(settings.load(None))
    try:
        got = {s: client.expirations(s) for s in NAMES}
    finally:
        client.close()
    out = _out_dir() / "expirations.json"
    out.write_text(json.dumps({"expirations": got}), encoding="utf-8")
    print(json.dumps({s: [len(v), v[0], v[-1]] for s, v in got.items()} | {"file": str(out)}))
    return 0


def cmd_plan(args) -> int:
    expiries = load_expiries()
    reqs = []
    for sym in NAMES:
        days, split_dates = load_days(sym)
        reqs += path_requests(sym, days, expiries[sym], split_dates)
    out = Path(args.out) if args.out else _out_dir() / "path_requests.json"
    out.write_text(json.dumps({"requests": reqs}, indent=0), encoding="utf-8")
    by_name = {s: sum(r["symbol"] == s for r in reqs) for s in NAMES}
    print(json.dumps({"requests": len(reqs), "by_name": by_name, "file": str(out)}))
    return 0


def _chains():
    from cherrypick.backtest import chainfetch, settings

    path = chainfetch.db_path(settings.load(None))
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)

    def make(sym: str):
        @lru_cache(maxsize=4096)
        def chain_at(session: str, expiry: str) -> Chain:
            return {
                (k, r[0].upper()): (b, a)
                for k, r, b, a in conn.execute(
                    "SELECT strike, right, bid, ask FROM eod_chain_quotes "
                    "WHERE symbol = ? AND session = ? AND expiration = ?",
                    (sym, session, expiry),
                )
            }

        return chain_at

    return conn, make


def cmd_study(args) -> int:
    names = [n for n in NAMES if not args.names or n in set(args.names.split(","))]
    expiries = load_expiries()
    rate_at = _rates()
    conn, make = _chains()
    result: dict = {"windows": {}, "counts": {}}
    try:
        per: dict = {w: {a: [] for a in ARMS + REPORTED} for w in WINDOWS}
        for sym in names:
            days, split_dates = load_days(sym)
            chain_at = make(sym)
            for wname, windows in WINDOWS.items():
                got = {}
                for arm in ARMS + REPORTED:
                    window = windows[arm.rsplit("-", 2)[1]]
                    trades, counts = run_arm(
                        sym, arm, days, expiries[sym], split_dates, window, chain_at, rate_at
                    )
                    per[wname][arm] += trades
                    got[arm] = len(trades)
                    result["counts"].setdefault(wname, {}).setdefault(arm, {})[sym] = counts
                print(wname, sym, got, flush=True)
        for wname, windows in WINDOWS.items():
            result["windows"][wname] = {
                a: arm_report(per[wname][a], windows[a.rsplit("-", 2)[1]]) for a in ARMS + REPORTED
            }
        result["trades"] = per
    finally:
        conn.close()
    result["verdict"] = judge(result["windows"]["design"], result["windows"]["holdout"])
    out = Path(args.json) if args.json else _out_dir() / "results.json"
    out.write_text(json.dumps(result, default=str), encoding="utf-8")
    out.with_suffix(".md").write_text(report(result), encoding="utf-8")
    for arm, v in result["verdict"].items():
        failed = [k for k, x in v.get("checks", {}).items() if not x]
        print(arm, v["verdict"], failed or "", v.get("why", ""))
    return 0


def _money(x: float) -> str:
    return f"{'−' if round(x) < 0 else '+' if round(x) > 0 else ''}${abs(x):,.0f}"


def _pct(k: int, n: int) -> str:
    return f"{100 * k / n:.0f}%"


def _label(arm: str) -> str:
    """`call-spread-7d-close` -> `Call spread 7d, close`."""
    structure, tenor, rule = arm.rsplit("-", 2)
    name = "ZEBRA" if structure == "zebra" else structure.replace("-", " ").capitalize()
    return f"{name} {'60–90d' if tenor == '75d' else tenor}, {rule}"


def tally(trades: list[dict]) -> dict:
    """One arm's trades in one window, as the report reads them. A win is a net above zero."""
    n = len(trades)
    return {
        "n": n,
        "win": sum(t["net"] > 0 for t in trades),
        "pl": sum(t["net"] for t in trades),
        "lot_win": sum(t["lot_net"] > 0 for t in trades),
        "lot_pl": sum(t["lot_net"] for t in trades),
        "beat": sum(t["net"] > t["lot_net"] for t in trades),
    }


def findings(result: dict) -> list[str]:
    """The plain-English reading, worked out from the figures each time so it stays true if the
    study is re-run. Judged arms only, except the last line on the reported exercise."""
    out = []
    tallies = {w: {a: tally(ts) for a, ts in result["trades"][w].items()} for w in result["trades"]}
    judged = {w: {a: t for a, t in ts.items() if a in ARMS and t["n"]} for w, ts in tallies.items()}

    def span(vals: list[float]) -> str:
        lo, hi = round(min(vals)), round(max(vals))
        return f"{lo}%" if lo == hi else f"{lo}–{hi}%"

    for w, ts in judged.items():
        if not ts:
            continue
        lot = [100 * t["lot_win"] / t["n"] for t in ts.values()]
        own = [100 * t["win"] / t["n"] for t in ts.values()]
        beat = max(100 * t["beat"] / t["n"] for t in ts.values())
        made = [_label(a) for a, t in ts.items() if t["pl"] > 0]
        out.append(
            f"**{w.capitalize()}:** the share lot wins {span(lot)} of trades, the structures"
            f" {span(own)}. No structure beat its lot in more than {beat:.0f}% of trades. "
            + (
                "Every arm loses money overall."
                if not made
                else f"Arms that made money overall: {'; '.join(made)}."
            )
        )
    zero = [_label(a) for a, t in judged.get("design", {}).items() if t["win"] == 0]
    if zero:
        out.append(
            f"**No winning trade at all** in the design window: {'; '.join(zero)}. The 4% move"
            " comes within days, while a long-dated spread has months left, so it has gained only"
            " a small part of its width, less than what crossing its legs' spreads costs."
        )
    mids = [
        (v["mean"], v["mid"])
        for w in ("design", "holdout")
        for a, v in result["windows"][w].items()
        if a in ARMS and v.get("n") and v.get("mean") is not None and v.get("mid") is not None
    ]
    near = sum(abs(mid) < abs(mean) / 4 for mean, mid in mids)
    if mids:
        out.append(
            f"**Why:** marked at the mid instead of the natural, the gap to the share lot shrinks"
            f" to under a quarter of its size in {near} of {len(mids)} arm-windows. What the"
            " structures lose is mostly the cost of crossing their spreads, which is large next to"
            " a 4% move."
        )
    few = [
        f"{_label(a)} ({v.get('why', '')})"
        for a, v in result["verdict"].items()
        if v.get("verdict") == "no verdict"
    ]
    if few:
        out.append(
            f"**No verdict** (under {MIN_TRADES} closed trades in a window): {'; '.join(few)}. A"
            " roll opened in a long drawdown stays open for months, and each arm holds one"
            " position at a time."
        )
    d = tallies.get("design", {})
    pairs = [(f"zebra-{t}-exercise", f"zebra-{t}-close") for t in TENORS]
    pairs = [(e, c) for e, c in pairs if d.get(e, {}).get("n") and d.get(c, {}).get("n")]
    if pairs:
        bits = [
            f"{'60–90d' if e.split('-')[1] == '75d' else e.split('-')[1]}"
            f" {_pct(d[e]['win'], d[e]['n'])} won and {_money(d[e]['pl'])}"
            f" (closing: {_pct(d[c]['win'], d[c]['n'])}, {_money(d[c]['pl'])})"
            for e, c in pairs
        ]
        out.append("**Exercising the ZEBRA** (reported, not judged), design window: " + "; ".join(bits) + ".")
    return out


def report(result: dict) -> str:
    """results.md beside results.json, for the console's studies tab: the verdict, the reading in
    words, then per arm and window the trades, win rate and P&L for the structure and its share
    lot, and how often the structure beat the lot. Derived from licensed chains, so it stays where
    the JSON is and never enters the repo."""
    words = [v.get("verdict") for a, v in result["verdict"].items() if a in ARMS]
    counts = {w: words.count(w) for w in ("pass", "fail", "no verdict")}
    overall = "pass" if counts["pass"] else "fail" if counts["fail"] else "no verdict"
    lines = [
        "# Item 29: the share ladder in options",
        "",
        f"**Verdict: {overall}.** Of the {len(words)} judged arms, {counts['pass']} pass,"
        f" {counts['fail']} fail and {counts['no verdict']} have no verdict.",
        "",
        "Each trade is one call spread, put spread or ZEBRA bought on a trigger day (the low 7%"
        " under the previous close), set against shares of the same entry delta over the same"
        " closes. P&L is whole-position dollars after fees, the options filled at the natural.",
        "",
        "## What it says",
        "",
        *[f"- {x}" for x in findings(result)],
        "",
        "## How to read the tables",
        "",
        "- **win %**: the share of trades that made money after costs.",
        "- **P&L**: all the arm's trades added up.",
        "- **lot**: the same trades done in shares of the same delta instead.",
        "- **beat the lot**: the share of trades where the structure made more than its shares.",
        "- **close / roll**: at expiry (or 21 days left, for 60–90d), close the trade, or roll it"
        " to the same structure and tenor until the target is met.",
        "- **exercise**: ZEBRA only, reported not judged: take the shares through the long calls"
        " instead of selling them.",
    ]
    titles = {
        "design": f"Design: 7d and 21d {WEEKLY[0]} to {WEEKLY[1]}; 60–90d {LONG[0]} to {LONG[1]}",
        "holdout": f"Holdout: {HOLDOUT[0]} to {HOLDOUT[1]}",
    }
    for wname in result["trades"]:
        lines += [
            "",
            f"## {titles.get(wname, wname.capitalize())}",
            "",
            "| arm | verdict | trades | win % | P&L | lot win % | lot P&L | beat the lot |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for arm, ts in result["trades"][wname].items():
            v = result["verdict"].get(arm, {}).get("verdict", "reported")
            t = tally(ts)
            if not t["n"]:
                lines.append(f"| {_label(arm)} | {v} | 0 | | | | | |")
                continue
            n = t["n"]
            lines.append(
                f"| {_label(arm)} | {v} | {n} | {_pct(t['win'], n)} | {_money(t['pl'])}"
                f" | {_pct(t['lot_win'], n)} | {_money(t['lot_pl'])} | {_pct(t['beat'], n)} |"
            )
    return "\n".join(lines) + "\n"


def cmd_report(args) -> int:
    src = Path(args.json) if args.json else _out_dir() / "results.json"
    src.with_suffix(".md").write_text(report(json.loads(src.read_text(encoding="utf-8"))), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("expirations").set_defaults(fn=cmd_expirations)
    p = sub.add_parser("plan")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_plan)
    s = sub.add_parser("study")
    s.add_argument("--json")
    s.add_argument("--names", help="comma-separated, for a quick look; the verdict needs both names")
    s.set_defaults(fn=cmd_study)
    r = sub.add_parser("report", help="results.md from an existing results.json (local)")
    r.add_argument("--json")
    r.set_defaults(fn=cmd_report)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
