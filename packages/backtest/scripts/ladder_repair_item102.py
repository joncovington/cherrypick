"""Item 102: a ratio-call-spread repair on the share ladder's stuck lots (research/edge-hypotheses-plan.md).

The declaration is section 102; this script runs it. Item 76's sibling: the same lots, the same hospital, the
same expiry and baseline, with a stock-repair ratio call spread (buy 1 call, sell 2 at a higher strike, for
about no net debit) in place of item 76's covered call. Results are derived from licensed data and stay under
the backtest results directory, never the repo.

- `plan`: write the chain-path requests this study needs (`{results}/path_requests.json`) and `plan.json`
  (the window's last session).
- `study`: one run over the whole window, judged by the declared RULE and EXTRA_RULE, written to
  `{results}/results.json` as the kit's verdict block. `study --smoke` runs on the sample fetched so far.

**The lots** are item 26's `once` at a cap of 3 lots a name, 100 shares a lot (a lot at 93% of the previous
close, filled on daily bars, 0.05% a side on shares). Every arm judges the same lots, the ones the baseline's
exits open. **The hospital:** a lot still open at the close of the first session 18 or more calendar days
after its buy enters it at that close.

**The repair:** one unit a lot (one long call, two short calls on 100 shares), opened at the hospital close
and again at each expiry that does not end the lot, on the expiry 35 to 60 calendar days out nearest 45;
none, or no strike the rule can pick, and the lot waits for the next session, counted; a waiting lot holds
no option and sells where the baseline sells it. Held to its expiry.
Strikes are read among calls quoted on both sides at the sale's close C, the lot's cost P per share:
- `repair-atm`: the long call K1 at the strike nearest C (the lower on a tie); the short calls at the
  highest strike K2 above K1 whose bid is above zero and whose net debit, ask(K1) - 2 bid(K2), is at most
  `max_debit` (a credit qualifies).
- `repair-cost`: K2 the strike nearest P among those over C (the lower on a tie), its bid above zero; K1 the
  lowest strike under K2 whose net debit is at most `max_debit`.
At the expiry's close S: S at or over K2 ends the lot, the shares going at K2 and the vertical K1/K2 settled
at its intrinsic, K2 - K1 (the shares answer one short call). Under K2 the shares stay and the long call's
intrinsic, S - K1 between the strikes and nothing under K1, is banked in cash. Then the lot ends, sold at the
close, if S plus the cash banked on the lot reaches 104% of P, and otherwise the repair is opened again at
that close. `hold` is the lot with no options, exited as item 26 exits it. Reported beside, unjudged: item
76's `breakeven` covered call and item 27's `decay` exit, on the same lots.

**Measured,** per hospital lot, the arm's whole-lot net less `hold`'s, in dollars per 100 shares, with
options sold at the bid and bought at the ask (mid reported), the suite's fees from `cherrypick.core.fees`
and a settlement fee for each distinct in-the-money option symbol.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from cherrypick.core import fees as core_fees
from cherrypick.core import progress as core_progress
from cherrypick.core import stats as core_stats
from cherrypick.core import treasury

from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.inputs import Inputs, Undeclared

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The kit's capital bar on each lot's paired difference, cut at one date: lots bought before it are the
#: design, from it the holdout. The tail bar needs 100 trades (`killcriteria.MIN_EVIDENCE`) and the
#: planned sample is about 60 lots, so it is declared not applicable (see `judge_arm`); `delta` is false
#: because the baseline holds the same shares.
RULE = {
    "t_bar": 2.0,
    "min_n": 30,
    "holdout_min_n": 10,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 102},
    "delta": False,
    "splits": ["2025-01-01"],
}

#: What item 76 judged beyond the kit's bar, and this study's declared constants.
EXTRA_RULE = {
    "alpha": 0.05,  # Holm across the judged arms, on the design's one-sided p
    "halves_split": "2020-09-01",  # lots bought before it, and from it: item 76's midpoint, fixed
    "stress_fees": 1.5,
    "stress_slip": 0.05,  # every option sold this far under the bid, bought this far over the ask
    "holdout_t_bar": 0.0,  # the holdout counts when the mean paired difference is above zero
    "max_debit": 0.05,  # the repair's net debit, dollars a share, at most
    "wait_sessions": 30,  # sessions after a sale session whose expiry the plan also requests
}

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`.
CHAIN = (
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/path_requests.json"},
    {
        "step": "fetch-sample",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/path_requests.json", "limit": 20},
    },
    {"step": "smoke", "kind": "script", "args": ["study", "--smoke"], "output": "{results}/smoke.json"},
    {
        "step": "fetch-chains",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/path_requests.json"},
    },
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)

NAMES = ("SOXL", "TQQQ")
DROP, GAIN, COST = 0.07, 0.04, 0.0005  # item 26's
CAP = 3
LOT_SHARES = 100
HOSPITAL_DAYS = 18
EXPIRY_RANGE = (35, 60)
EXPIRY_TARGET = 45
START = "2016-05-02"
JUDGED = ("repair-atm", "repair-cost")
REPORTED = ("breakeven", "decay")
ARMS = JUDGED + REPORTED
#: Item 27's `decay` exit: the full gain to day 30, breakeven after costs to day 120, 95% of cost after.
DECAY_FULL_DAYS, DECAY_BREAKEVEN_DAYS, DECAY_FLOOR = 30, 120, 0.95
RATE_STALE_DAYS = 10
# (fill slippage under the bid / over the ask, fee multiple, priced at the mid)
COSTINGS = {
    "natural": (0.0, 1.0, False),
    "stress": (EXTRA_RULE["stress_slip"], EXTRA_RULE["stress_fees"], False),
    "mid": (0.0, 1.0, True),
}
#: Each structure: (legs as (role, quantity), contracts a unit counts for the fee, sells at open, sells at
#: a close). Quantity is positive long, negative short; a role names the strike it sits at.
STRUCTURES = {
    "repair": ((("K1", 1), ("K2", -2)), 3, 2, 1),
    "call": ((("K", -1),), 1, 1, 0),
}


# ------------------------------------------------------------------------------------------------
# Pure: the bars, the ladder's lots and the hospital (item 76's, copied).


@dataclass(frozen=True)
class Bar:
    date: str
    o: float  # split-adjusted
    h: float
    l: float  # noqa: E741
    c: float
    f: float  # raw = adjusted x f: the day's own basis, on which its chain's strikes are quoted


@dataclass
class Lot:
    entry: int
    price: float  # the fill, split-adjusted
    shares: float  # split-adjusted count: 100 raw shares at the entry's basis
    exit: int | None = None  # the baseline's exit session
    exit_px: float | None = None
    hospital: int | None = None


def bars_from(raw_bars, splits) -> list[Bar]:
    """Raw daily bars to Bars: each adjusted price divides out the splits after its date."""
    out = []
    for b in raw_bars:
        if not (b.open and b.high and b.low and b.close):
            continue
        f = math.prod(s.to_factor / s.for_factor for s in splits if s.ex_date > b.date)
        out.append(Bar(b.date, b.open / f, b.high / f, b.low / f, b.close / f, f))
    return out


def _cal(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def once_fill(bar: Bar, ref: float) -> float | None:
    """Item 26's `once` entry: a lot at DROP under the previous close, filled at the open when the session
    opens through it, else at the level when the range reaches it."""
    level = ref * (1 - DROP)
    return min(bar.o, level) if bar.l <= level else None


def ladder_lots(bars: list[Bar], start: str, end: str) -> tuple[list[Lot], int]:
    """Item 26's `once` at cap CAP over [start, end]: every lot bought, with the baseline exit it makes (a
    target fill at the open or at the level; a lot bought in a session is not sold in it and a slot sold in
    a session frees the next). Returns the lots and the index of the window's last session."""
    dates = [b.date for b in bars]
    lo, hi = max(1, bisect.bisect_left(dates, start)), bisect.bisect_right(dates, end)
    held: list[Lot] = []
    every: list[Lot] = []
    for i in range(lo, hi):
        bar, ref = bars[i], bars[i - 1].c
        n_before, keep = len(held), []
        for lot in held:
            target = lot.price * (1 + GAIN)
            px = bar.o if bar.o >= target else (target if bar.h >= target else None)
            if px is None:
                keep.append(lot)
            else:
                lot.exit, lot.exit_px = i, px
        held = keep
        px = once_fill(bar, ref) if CAP - n_before > 0 else None
        if px is not None:
            lot = Lot(i, px, LOT_SHARES * bar.f)
            held.append(lot)
            every.append(lot)
    return every, hi - 1


def hospital_index(bars: list[Bar], lot: Lot, last: int) -> int | None:
    """The first session 18 or more calendar days after the buy, if the lot is still open at its close and
    it falls inside the window."""
    dates = [b.date for b in bars]
    j = next(
        (k for k in range(lot.entry + 1, last + 1) if _cal(dates[lot.entry], dates[k]) >= HOSPITAL_DAYS), None
    )
    if j is None or (lot.exit is not None and lot.exit <= j):
        return None
    return j


def share_net(shares: float, entry: float, exit_: float, *, marked: bool = False) -> float:
    """A lot's shares, whole position, in dollars: the move less item 26's cost a side; a mark at a window's
    end pays no exit cost, as item 26 marks."""
    return shares * (exit_ - entry) - COST * shares * entry - (0.0 if marked else COST * shares * exit_)


def hold_result(lot: Lot, bars: list[Bar], last: int) -> dict:
    """The baseline on a lot: exited as item 26 exits it, or marked at the window's last close."""
    if lot.exit is not None:
        end, px, marked = lot.exit, lot.exit_px, False
    else:
        end, px, marked = last, bars[last].c, True
    return {
        "net": share_net(lot.shares, lot.price, px, marked=marked),
        "end": end,
        "px": px,
        "marked": marked,
    }


def decay_target(price: float, age: int) -> float:
    """Item 27's `decay` target at `age` calendar days."""
    if age <= DECAY_FULL_DAYS:
        return price * (1 + GAIN)
    if age <= DECAY_BREAKEVEN_DAYS:
        return price * (1 + COST) / (1 - COST)
    return price * DECAY_FLOOR


def decay_result(lot: Lot, bars: list[Bar], last: int) -> dict:
    """Item 27's `decay` exit on the same lot, filled at the open or at the level, from the session after
    the buy."""
    for j in range(lot.entry + 1, last + 1):
        t = decay_target(lot.price, _cal(bars[lot.entry].date, bars[j].date))
        px = bars[j].o if bars[j].o >= t else (t if bars[j].h >= t else None)
        if px is not None:
            return {"net": share_net(lot.shares, lot.price, px), "end": j, "marked": False}
    return {"net": share_net(lot.shares, lot.price, bars[last].c, marked=True), "end": last, "marked": True}


# ------------------------------------------------------------------------------------------------
# Pure: expiries, the sale sessions and each unit's segment, from the share closes alone (item 76's).


def pick_expiry(session: str, expiries: list[str]) -> str | None:
    """The expiry 35 to 60 calendar days out nearest 45 (the earlier on a tie)."""
    lo, hi = EXPIRY_RANGE
    fits = [e for e in expiries if lo <= _cal(session, e) <= hi]
    return min(fits, key=lambda e: (abs(_cal(session, e) - EXPIRY_TARGET), e)) if fits else None


def _cut(dates: list[str], split_dates: list[str], j: int) -> int | None:
    """The session before the first split ex-date after session j, or None."""
    nxt = next((s for s in split_dates if s > dates[j]), None)
    return None if nxt is None else bisect.bisect_left(dates, nxt) - 1


def next_sale(dates: list[str], expiries: list[str], split_dates: list[str], idx: int, last: int):
    """The first session from idx, up to the window's last, that has an expiry in range and is not the
    session before a split ex-date. Returns (session, expiry) or None."""
    for j in range(idx, last + 1):
        exp = pick_expiry(dates[j], expiries)
        if exp is not None and _cut(dates, split_dates, j) != j:
            return j, exp
    return None


def segment_end(dates: list[str], j: int, exp: str, split_dates: list[str], last: int) -> tuple[int, str]:
    """Where a unit sold at session j ends: its expiry's session (the last on or before the expiry), the
    window's last session, or the session before a split ex-date; in that order of precedence."""
    e = bisect.bisect_right(dates, exp) - 1
    c = _cut(dates, split_dates, j)
    if exp <= dates[last] and (c is None or e <= c):  # an expiry past the data is not reached
        return e, "expiry"
    if c is None or c >= last:
        return last, "end"
    return c, "split"


def plan_lot(bars: list[Bar], lot: Lot, expiries: list[str], split_dates: list[str], last: int):
    """A hospital lot's units from the share closes alone, assuming no early end: each (sale session,
    expiry, end session, how the unit ends), up to the first expiry whose close is at or over 104% of the
    lot's cost or the window's end. Under every arm the lot has ended by then (a close that high either
    ends the lot at the short strike or leaves the shares to sell at it), so this bounds every arm's path."""
    dates = [b.date for b in bars]
    out, idx = [], lot.hospital
    while True:
        sale = next_sale(dates, expiries, split_dates, idx, last)
        if sale is None:
            return out
        j, exp = sale
        end, how = segment_end(dates, j, exp, split_dates, last)
        out.append((j, exp, end, how))
        if how == "end" or (how == "expiry" and bars[end].c >= (1 + GAIN) * lot.price):
            return out
        idx = end if how == "expiry" else end + 1


def lot_paths(
    bars: list[Bar], lot: Lot, expiries: list[str], split_dates: list[str], last: int
) -> list[tuple]:
    """Every (expiry, first session, last session) the lot could read: its planned units, and the unit a
    wait of up to `wait_sessions` sessions after each sale session would sell instead."""
    dates = [b.date for b in bars]
    out = []
    for j, exp, end, _ in plan_lot(bars, lot, expiries, split_dates, last):
        out.append((exp, dates[j], dates[end]))
        for s in range(j + 1, min(j + EXTRA_RULE["wait_sessions"], last) + 1):
            alt = pick_expiry(dates[s], expiries)
            if alt is None or _cut(dates, split_dates, s) == s:
                continue
            e, _how = segment_end(dates, s, alt, split_dates, last)
            if e >= s:
                out.append((alt, dates[s], dates[e]))
    return out


def path_requests(sym: str, bars: list[Bar], lots: list[Lot], expiries, split_dates, last) -> list[dict]:
    """Every (expiry, start, through) path the hospital lots could use, one per (name, expiry)."""
    span: dict[str, list[str]] = {}
    for lot in lots:
        if lot.hospital is None:
            continue
        for exp, a, b in lot_paths(bars, lot, expiries, split_dates, last):
            have = span.setdefault(exp, [a, b])
            have[0], have[1] = min(have[0], a), max(have[1], b)
    return [{"symbol": sym, "expiration": e, "start": a, "through": b} for e, (a, b) in sorted(span.items())]


# ------------------------------------------------------------------------------------------------
# Pure: the strike rules. A chain is {(strike, "C"): (bid, ask)} on the sale session's raw basis.


def _two_sided(b, a) -> bool:
    return b is not None and a is not None and a > 0 and a >= b >= 0


def quoted(chain: dict) -> list[float]:
    return sorted(k for (k, r), (b, a) in chain.items() if r == "C" and _two_sided(b, a))


def nearest(strikes: list[float], target: float) -> float | None:
    return min(strikes, key=lambda k: (abs(k - target), k)) if strikes else None


def net_debit(chain: dict, k1: float, k2: float) -> float:
    """The repair's debit a share at the natural: the long call at its ask, two short calls at their bid."""
    return chain[(k1, "C")][1] - 2 * chain[(k2, "C")][0]


def pick_repair_atm(chain: dict, close: float, max_debit: float) -> tuple[float, float] | None:
    """The long call at the strike nearest the close; the short calls at the highest strike above it whose
    bid is above zero and whose net debit is within `max_debit`. None when none qualifies."""
    ks = quoted(chain)
    k1 = nearest(ks, close)
    if k1 is None:
        return None
    ok = [k for k in ks if k > k1 and chain[(k, "C")][0] > 0 and net_debit(chain, k1, k) <= max_debit]
    return (k1, ok[-1]) if ok else None


def pick_repair_cost(chain: dict, close: float, cost: float, max_debit: float) -> tuple[float, float] | None:
    """The short calls at the strike nearest the lot's cost among those over the close, their bid above
    zero; the long call at the lowest strike under them whose net debit is within `max_debit`."""
    ks = quoted(chain)
    k2 = nearest([k for k in ks if k > close], cost)
    if k2 is None or chain[(k2, "C")][0] <= 0:
        return None
    ok = [k for k in ks if k < k2 and net_debit(chain, k, k2) <= max_debit]
    return (ok[0], k2) if ok else None


def pick_breakeven(chain: dict, cost: float, banked: float) -> tuple[float, bool] | None:
    """Item 76's: the lowest strike K with K + banked + bid >= cost; none, the highest (flagged)."""
    ks = quoted(chain)
    if not ks:
        return None
    ok = [k for k in ks if k + banked + chain[(k, "C")][0] >= cost]
    return (ok[0], False) if ok else (ks[-1], True)


def pick_strike(arm: str, chain: dict, *, close: float, cost: float, banked: float):
    """The unit's strikes as {role: strike} and whether a fallback was taken, or None."""
    if arm == "repair-atm":
        got = pick_repair_atm(chain, close, EXTRA_RULE["max_debit"])
        return None if got is None else ({"K1": got[0], "K2": got[1]}, False)
    if arm == "repair-cost":
        got = pick_repair_cost(chain, close, cost, EXTRA_RULE["max_debit"])
        return None if got is None else ({"K1": got[0], "K2": got[1]}, False)
    if arm == "breakeven":
        got = pick_breakeven(chain, cost, banked)
        return None if got is None else ({"K": got[0]}, got[1])
    raise ValueError(arm)


# ------------------------------------------------------------------------------------------------
# Pure: fills, fees and settlement for one unit.


def sell_px(q, costing: str) -> float:
    slip, _, mid = COSTINGS[costing]
    return (q[0] + q[1]) / 2 if mid else max(0.0, q[0] - slip)


def buy_px(q, costing: str) -> float:
    slip, _, mid = COSTINGS[costing]
    return (q[0] + q[1]) / 2 if mid else q[1] + slip


def open_fee(sym: str, structure: str, units: float, costing: str) -> float:
    _, legs, sells, _ = STRUCTURES[structure]
    return (
        core_fees.ic_open_fee(sym, quantity=1, legs=legs, sell_legs=sells, ndigits=4)
        * units
        * COSTINGS[costing][1]
    )


def close_fee(sym: str, structure: str, units: float, costing: str) -> float:
    _, legs, _, sells = STRUCTURES[structure]
    return (
        core_fees.ic_close_fee(sym, quantity=1, legs=legs, sell_legs=sells, ndigits=4)
        * units
        * COSTINGS[costing][1]
    )


def settlement_fee(costing: str, symbols: int) -> float:
    return core_fees.ASSIGNMENT_FEE_PER_SETTLEMENT * symbols * COSTINGS[costing][1]


def unit_legs(structure: str, strikes: dict) -> list[tuple[float, int]]:
    """The unit's legs as (strike, quantity), quantity positive long, negative short."""
    return [(strikes[role], qty) for role, qty in STRUCTURES[structure][0]]


def settle(legs: list[tuple[float, int]], s: float) -> dict:
    """A unit at its expiry's close s (raw basis), per share: whether the shares are called away at the
    lowest short strike, the option cash beside that delivery (each leg's intrinsic, less the short call the
    shares answer), and the distinct in-the-money option symbols the broker settles."""
    shorts = [k for k, q in legs if q < 0]
    called = bool(shorts) and s >= min(shorts)
    cash = sum(q * max(s - k, 0.0) for k, q in legs)
    if called:
        cash += max(s - min(shorts), 0.0)
    itm = {k for k, q in legs if (s > k if q > 0 else s >= k)}
    return {"called": called, "at": min(shorts) if called else None, "cash": cash, "symbols": len(itm)}


def last_quote(chain_at, bars: list[Bar], exp: str, strike: float, lo: int, hi: int):
    """The call's two-sided quote at session hi, else the latest earlier one down to lo (counted stale),
    else its intrinsic value at hi's close (counted). Returns (bid, ask, stale)."""
    for j in range(hi, lo - 1, -1):
        q = (chain_at(bars[j].date, exp) or {}).get((strike, "C"))
        if q is not None and _two_sided(*q):
            return q[0], q[1], int(j != hi)
    iv = max(0.0, bars[hi].c * bars[hi].f - strike)
    return iv, iv, 1


# ------------------------------------------------------------------------------------------------
# One hospital lot under one arm.


def run_arm(
    arm: str,
    sym: str,
    lot: Lot,
    bars: list[Bar],
    expiries: list[str],
    split_dates: list[str],
    last: int,
    chain_at,
    hold_px: float | None = None,
) -> dict:
    """The lot's whole-lot net under the arm, at the natural, the stress and the mid together (the path of
    strikes and settlements is the natural's), with the figures reported beside it."""
    structure = "call" if arm == "breakeven" else "repair"
    dates = [b.date for b in bars]
    opt = dict.fromkeys(COSTINGS, 0.0)
    banked = 0.0  # option cash at the natural, gross of fees, less split buy-backs, in dollars
    prem = 0.0
    n_units = waits = fallbacks = stale = 0
    counts = {"no chain": 0, "no strike": 0}
    strikes: list[tuple[str, tuple]] = []
    idx = lot.hospital
    free = idx + 1  # the first session the lot is free of options, so `hold`'s exit can take it
    end_idx, how, proceeds, strike_end, realised = last, "end", None, None, 0.0
    while True:
        sale = next_sale(dates, expiries, split_dates, idx, last)
        if lot.exit is not None and free <= lot.exit <= (sale[0] if sale else last):
            # waiting with no option open the lot is the baseline's: its target is met, it sells there
            waits += lot.exit - idx
            end_idx, how, proceeds = lot.exit, "held", lot.shares * lot.exit_px * (1 - COST)
            break
        waits += (sale[0] if sale else last + 1) - idx
        if sale is None:
            break
        j, exp = sale
        sh = lot.shares / bars[j].f  # raw shares on the sale session's basis
        cost = lot.price * bars[j].f  # P per share, same basis
        chain = chain_at(dates[j], exp)
        if not chain:
            counts["no chain"] += 1
            idx, free, waits = j + 1, j + 1, waits + 1
            continue
        got = pick_strike(arm, chain, close=bars[j].c * bars[j].f, cost=cost, banked=banked / sh)
        if got is None:
            counts["no strike"] += 1
            idx, free, waits = j + 1, j + 1, waits + 1
            continue
        strike_map, fallback = got
        legs = unit_legs(structure, strike_map)
        units = sh / 100
        fallbacks += fallback
        n_units += 1
        strikes.append((dates[j], tuple(k for k, _ in legs)))
        for name in COSTINGS:
            cash = sum(
                (-q * sell_px(chain[(k, "C")], name)) if q < 0 else (-q * buy_px(chain[(k, "C")], name))
                for k, q in legs
            )
            opt[name] += cash * sh - open_fee(sym, structure, units, name)
        credit = sum(-q * (chain[(k, "C")][0] if q < 0 else chain[(k, "C")][1]) for k, q in legs) * sh
        banked += credit
        prem += credit
        e, kind = segment_end(dates, j, exp, split_dates, last)
        d = bars[e]
        sh_e = lot.shares / d.f
        if kind == "expiry":
            s_raw = d.c * d.f
            res = settle(legs, s_raw)
            cash = res["cash"] * sh_e
            for name in COSTINGS:
                opt[name] += cash - settlement_fee(name, res["symbols"])
            if res["called"]:
                end_idx, how, proceeds, strike_end = (
                    e,
                    "assigned",
                    res["at"] * sh_e * (1 - COST),
                    res["at"] / d.f,
                )
                realised = res["at"] * sh_e + cash + banked
                break
            banked += cash
            target = (1 + GAIN) * lot.price
            reached = (
                d.c >= target if structure == "call" else d.c * lot.shares + banked >= target * lot.shares
            )
            if reached:
                end_idx, how, proceeds = e, "sold", lot.shares * d.c * (1 - COST)
                break
            idx, free = e, e + 1
            continue
        # the window's end or a split: the shorts are bought back at the ask, the long sold at the bid
        quotes = {k: last_quote(chain_at, bars, exp, k, j, e) for k, _ in legs}
        stale += sum(q[2] for q in quotes.values())
        for name in COSTINGS:
            cash = sum(
                q * (buy_px(quotes[k][:2], name) if q < 0 else sell_px(quotes[k][:2], name)) for k, q in legs
            )
            opt[name] += cash * sh_e - close_fee(sym, structure, sh_e / 100, name)
        banked += sum(q * (quotes[k][1] if q < 0 else quotes[k][0]) for k, q in legs) * sh_e
        if kind == "end":
            break
        idx, free = e + 1, e + 1  # the new unit is opened on the ex-date session's close, on the new basis
    if how == "end":
        end_idx, proceeds = last, lot.shares * bars[last].c
    entry_cash = -lot.shares * lot.price * (1 + COST)
    nets = {name: entry_cash + proceeds + opt[name] for name in COSTINGS}
    hospital_days = _cal(dates[lot.hospital], dates[end_idx])
    return {
        "net": nets["natural"],
        "net_stress": nets["stress"],
        "net_mid": nets["mid"],
        "how": how,
        "end": dates[end_idx],
        "units": n_units,
        "strikes": strikes,
        "waits": waits,
        "fallbacks": fallbacks,
        "stale": stale,
        "premium": prem,
        "hospital_days": hospital_days,
        "capital_days": lot.shares * lot.price * hospital_days,
        "below_cost": bool(how == "assigned" and realised < lot.shares * lot.price - 1e-9),
        "rebound": bool(how == "assigned" and hold_px is not None and hold_px > strike_end),
        **{f"skipped_{key.replace(' ', '_')}": v for key, v in counts.items()},
    }


def lot_row(sym: str, lot: Lot, bars: list[Bar], got: dict, hold: dict) -> dict:
    dates = [b.date for b in bars]
    base = hold["net"]
    h_days = _cal(dates[lot.hospital], dates[hold["end"]])
    return {
        "name": sym,
        "entry": dates[lot.entry],
        "hospital": dates[lot.hospital],
        **got,
        "hold_net": base,
        "hold_end": dates[hold["end"]],
        "hold_days": h_days,
        "hold_capital_days": lot.shares * lot.price * h_days,
        "diff": got["net"] - base,
        "diff_stress": got["net_stress"] - base,
        "diff_mid": got["net_mid"] - base,
    }


def decay_row(sym: str, lot: Lot, bars: list[Bar], hold: dict, last: int) -> dict:
    dec = decay_result(lot, bars, last)
    days = _cal(bars[lot.hospital].date, bars[dec["end"]].date)
    h_days = _cal(bars[lot.hospital].date, bars[hold["end"]].date)
    return {
        "name": sym,
        "entry": bars[lot.entry].date,
        "hospital": bars[lot.hospital].date,
        "net": dec["net"],
        "net_stress": dec["net"],
        "net_mid": dec["net"],
        "hold_net": hold["net"],
        "diff": dec["net"] - hold["net"],
        "diff_stress": dec["net"] - hold["net"],
        "diff_mid": dec["net"] - hold["net"],
        "how": "marked" if dec["marked"] else "decay",
        "hospital_days": days,
        "hold_days": h_days,
        "hold_capital_days": lot.shares * lot.price * h_days,
        "capital_days": lot.shares * lot.price * days,
        "premium": 0.0,
        "units": 0,
        "waits": 0,
        "fallbacks": 0,
        "stale": 0,
        "below_cost": False,
        "rebound": False,
    }


def kit_row(row: dict, lot: Lot, rate: float) -> dict:
    """The kit's row for one lot: the paired difference as the net, the lot's cost as the capital it is
    measured on, and the days the arm held that capital beyond (or short of) the baseline."""
    return {
        "date": row["entry"],
        "net": round(row["diff"], 2),
        "bp": lot.shares * lot.price,
        "days": row["hospital_days"] - row["hold_days"],
        "rate": rate,
    }


# ------------------------------------------------------------------------------------------------
# The statistics and the verdict.


def summary(rows: list[dict], key: str = "diff") -> dict:
    xs = [r[key] for r in rows]
    n = len(xs)
    out = {"n": n, "mean": statistics.fmean(xs) if xs else None}
    if n >= 2:
        sd = statistics.stdev(xs)
        out["sd"] = sd
        t = out["mean"] / (sd / math.sqrt(n)) if sd > 0 else (math.inf if out["mean"] > 0 else -math.inf)
        out["t"] = t
        out["p"] = 1 - core_stats.t_cdf(t, n - 1) if math.isfinite(t) else (0.0 if t > 0 else 1.0)
    return out


def arm_report(rows: list[dict]) -> dict:
    """One arm's figures over some lots: the paired difference, then what sits beside it."""
    split = EXTRA_RULE["halves_split"]
    out = summary(rows)
    out["stress"] = summary(rows, "diff_stress")["mean"]
    out["mid"] = summary(rows, "diff_mid")["mean"]
    out["halves"] = [
        summary([r for r in rows if r["entry"] < split])["mean"],
        summary([r for r in rows if r["entry"] >= split])["mean"],
    ]
    out["names"] = {s: summary([r for r in rows if r["name"] == s])["mean"] for s in NAMES}
    if out.get("sd") is not None:
        out["mde"] = core_stats.minimum_detectable_effect(out["sd"], out["n"])
    for key in ("hospital_days", "capital_days", "premium", "waits", "fallbacks", "stale", "units"):
        out[key] = sum(r[key] for r in rows)
    out["hold_hospital_days"] = sum(r["hold_days"] for r in rows)
    out["hold_capital_days"] = sum(r["hold_capital_days"] for r in rows)
    out["below_cost"] = sum(r["below_cost"] for r in rows)
    out["rebound"] = sum(r["rebound"] for r in rows)
    out["skipped_no_chain"] = sum(r.get("skipped_no_chain", 0) for r in rows)
    out["skipped_no_strike"] = sum(r.get("skipped_no_strike", 0) for r in rows)
    out["worst"] = min((r["net"] for r in rows), default=None)
    out["worst_hold"] = min((r["hold_net"] for r in rows), default=None)
    out["how"] = {}
    for r in rows:
        out["how"][r["how"]] = out["how"].get(r["how"], 0) + 1
    return out


def _pos(x) -> bool:
    return x is not None and x > 0


def judge_arm(rule: dict, rows: list[dict], holm_ok: bool) -> dict:
    """One judged arm's block, in the kit's shape: the design part on the capital bar (the tail bar declared
    not applicable, the delta bar off), item 76's extra checks, a holdout read only after a design pass."""
    bounds = [None, *rule["splits"], None]
    cut = kit_rule.cut(rows, rule["splits"])
    d_rows, h_rows = cut[0], cut[1] if len(cut) > 1 else []
    design, holdout = [r["kit"] for r in d_rows], [r["kit"] for r in h_rows]
    parts = []

    def capital(kit_rows: list[dict], t_bar: float, floor: int) -> dict:
        return ck.capital_check(
            [r["net"] for r in kit_rows],
            [r["bp"] for r in kit_rows],
            [r["days"] for r in kit_rows],
            [r["rate"] for r in kit_rows],
            t_bar=t_bar,
            min_n=floor,
        )

    cap = capital(design, rule["t_bar"], rule["min_n"])
    rep = arm_report(d_rows)
    extra = {
        "holm": holm_ok,
        "halves": all(_pos(x) for x in rep["halves"]),
        "names": all(_pos(x) for x in rep["names"].values()),
        "stress": _pos(rep["stress"]),
    }
    tail = {
        "verdict": ck.NOT_APPLICABLE,
        "why": "under the bootstrap's 100-trade floor; the worst lot is reported",
    }
    block = ck.verdict(cap, tail, None)
    if block["verdict"] == ck.PASS and not all(extra.values()):
        block = {
            **block,
            "verdict": ck.FAIL,
            "failed": [*block["failed"], *[k for k, v in extra.items() if not v]],
        }
    parts.append({"from": bounds[0], "before": bounds[1], "n": len(design), **block, "extra": extra})
    word = block["verdict"]
    if len(bounds) > 2:
        span = {"from": bounds[1], "before": bounds[2]}
        if word != ck.PASS:
            parts.append({**span, "n": len(holdout), "verdict": kit_rule.NOT_READ})
        else:
            hcap = capital(holdout, EXTRA_RULE["holdout_t_bar"], rule["holdout_min_n"])
            word = hcap["verdict"]
            parts.append({**span, "n": len(holdout), "verdict": word, "bars": {"capital": hcap}})
    return {"n": len(rows), "parts": parts, "verdict": word, "design": rep, "holdout": arm_report(h_rows)}


def judge(rule: dict, per_arm: dict[str, list[dict]]) -> dict:
    """The verdict block for the judged arms: Holm across them on the design's one-sided p, then each arm."""
    design = {
        arm: summary([r for r in rows if r["entry"] < rule["splits"][0]]) for arm, rows in per_arm.items()
    }
    pvals = {a: (d.get("p", 1.0) if d["n"] >= rule["min_n"] else 1.0) for a, d in design.items()}
    holm = core_stats.holm(pvals, EXTRA_RULE["alpha"])
    arms = {arm: judge_arm(rule, rows, holm[arm]) for arm, rows in per_arm.items()}
    return {
        "rule": rule,
        "extra_rule": EXTRA_RULE,
        "arms": arms,
        "verdict": {a: b["verdict"] for a, b in arms.items()},
    }


# ------------------------------------------------------------------------------------------------
# The stores and the commands.


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item102"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_expiries() -> dict[str, list[str]]:
    """Item 29's listing of each name's expiries (its `expirations` step's file), the expiries a call can be
    sold on."""
    from cherrypick.backtest import paths

    path = paths.results_dir() / "research" / "item29" / "expirations.json"
    if not path.exists():
        raise SystemExit(f"no expiries at {path}: item 29's expirations step has not run")
    return json.loads(path.read_text(encoding="utf-8"))["expirations"]


def load_bars(sym: str) -> tuple[list[Bar], list[str]]:
    from cherrypick.technicals import adjust, store
    from cherrypick.technicals import paths as tpaths

    hist = store.connect(tpaths.history_db())
    try:
        raw = store.raw_bars(hist, sym)
        splits = adjust.dedupe_splits(raw, store.splits(hist, sym))
    finally:
        hist.close()
    return bars_from(raw, splits), sorted(s.ex_date for s in splits)


def bill_reader():
    """The 3-month bill on a session, from the Treasury curves `core.treasury` holds; stale past
    RATE_STALE_DAYS the run stops rather than guess."""
    seen: dict[str, float] = {}

    def rate(session: str) -> float:
        if session not in seen:
            got = treasury.yield_before(session, "3m")
            if got is None or (date.fromisoformat(session) - got[0]).days > RATE_STALE_DAYS:
                raise SystemExit(f"no 3-month bill within {RATE_STALE_DAYS} days before {session}")
            seen[session] = got[1] / 100.0
        return seen[session]

    return rate


def requests() -> list[dict]:
    """The chain paths the study reads: one request per (name, expiry), over the hospital lots of the whole
    window. Also records the window's last session in plan.json."""
    expiries = load_expiries()
    loaded = {sym: load_bars(sym) for sym in NAMES}
    end = min(bars[-1].date for bars, _ in loaded.values())
    out, hospital = [], {}
    for sym, (bars, split_dates) in core_progress.track(
        loaded.items(), job="item102-plan", phase="symbols", unit="symbols",
        key=lambda kv: kv[0], current=lambda kv: kv[0],
    ):  # fmt: skip
        lots, last = ladder_lots(bars, START, end)
        for lot in lots:
            lot.hospital = hospital_index(bars, lot, last)
        out += path_requests(sym, bars, lots, expiries[sym], split_dates, last)
        hospital[sym] = sum(lot.hospital is not None for lot in lots)
    (out_dir() / "plan.json").write_text(
        json.dumps({"start": START, "end": end, "hospital_lots": hospital}), encoding="utf-8"
    )
    return out


def load_plan() -> dict:
    path = out_dir() / "plan.json"
    if not path.exists():
        raise SystemExit(f"no plan at {path}: run `ladder_repair_item102.py plan` first")
    return json.loads(path.read_text(encoding="utf-8"))


def chain_store():
    import sqlite3

    from cherrypick.backtest import paths, settings

    db = paths.vendor_dir(settings.load()) / "eod_chains.db"
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def make_chain_at(inputs: Inputs, conn, sym: str, undeclared: list):
    """chain_at(session, expiry) -> {(strike, "C"): (bid, ask)} of a declared path, or None. A path the plan
    did not request is counted in `undeclared` and read as no chain."""
    cache: dict = {}

    def chain_at(session: str, expiry: str):
        key = (session, expiry)
        if key not in cache:
            try:
                ch = inputs.chain(conn, sym, session, expiry)
            except Undeclared:
                undeclared.append((sym, session, expiry))
                ch = None
            cache[key] = (
                None if ch is None else {(k, "C"): (q["bid"], q["ask"]) for k, q in ch.by["C"].items()}
            )
        return cache[key]

    return chain_at


def trades(inputs: Inputs, conn, rep: core_progress.Reporter, names=NAMES) -> tuple[dict, dict]:
    """Each arm's rows from one run over the whole window: the judged arms' rows carry the kit's row under
    `kit`. Reports its lots overall and per name through `rep`."""
    plan = load_plan()
    expiries = load_expiries()
    rate_at = bill_reader()
    work, per = {}, {a: [] for a in ARMS}
    for sym in names:
        bars, split_dates = load_bars(sym)
        lots, last = ladder_lots(bars, plan["start"], plan["end"])
        for lot in lots:
            lot.hospital = hospital_index(bars, lot, last)
        work[sym] = (bars, split_dates, [lot for lot in lots if lot.hospital is not None], last)
    rep.split("lots", [sym for sym in names for _ in work[sym][2]], unit="lots")
    undeclared: list = []
    counts = {}
    for sym in names:
        bars, split_dates, hospital, last = work[sym]
        chain_at = make_chain_at(inputs, conn, sym, undeclared)
        counts[sym] = {"lots": len(hospital)}
        for lot in hospital:
            hold = hold_result(lot, bars, last)
            hold_px = None if hold["marked"] else hold["px"]
            rate = rate_at(bars[lot.hospital].date)
            for arm in ARMS:
                if arm == "decay":
                    row = decay_row(sym, lot, bars, hold, last)
                else:
                    got = run_arm(arm, sym, lot, bars, expiries[sym], split_dates, last, chain_at, hold_px)
                    row = lot_row(sym, lot, bars, got, hold)
                row["kit"] = kit_row(row, lot, rate)
                row["date"] = row["entry"]
                per[arm].append(row)
            rep.advance("lots", key=sym, current=f"{sym} {bars[lot.entry].date}")
    return per, {"lots": counts, "undeclared": len(undeclared), "window": [plan["start"], plan["end"]]}


def cmd_plan(args) -> int:
    path = Path(args.out) if args.out else out_dir() / "path_requests.json"
    reqs = requests()
    path.write_text(json.dumps({"requests": reqs}, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(path), "requests": len(reqs)}))
    return 0


def cmd_study(args) -> int:
    inputs = Inputs(out_dir() / "path_requests.json")
    conn = chain_store()
    job = "item102-smoke" if args.smoke else "item102-study"
    try:
        with core_progress.Reporter(job) as rep:
            per, counts = trades(inputs, conn, rep)
            read = inputs.fingerprint(conn)
    finally:
        conn.close()
    block = judge(RULE, {a: per[a] for a in JUDGED})
    reported = {a: arm_report(per[a]) for a in REPORTED}
    doc = {"licence": "derived from licensed vendor data: never commit or publish", "item": 102, **block}
    doc.update({"reported": reported, "counts": counts, "inputs": read})
    if args.smoke:
        # A look at the sample, never a verdict: the queue fails it on an arm with no trades.
        doc = {**doc, "smoke": True, "verdict": "smoke"}
    doc["trades"] = {a: [{k: v for k, v in r.items() if k != "kit"} for r in rows] for a, rows in per.items()}
    name = "smoke.json" if args.smoke else "results.json"
    path = Path(args.out) if args.out else out_dir() / name
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    for name, fn in (("plan", cmd_plan), ("study", cmd_study)):
        p = sub.add_parser(name)
        p.add_argument("--out", default=None)
        if name == "study":
            p.add_argument("--smoke", action="store_true", help="run on the sample fetched so far")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
