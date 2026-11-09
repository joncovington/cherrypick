"""Item 49, the dip-sold put on TQQQ (docs/edge-hypotheses-plan.md): a cash-secured 16-delta put sold by
a resting order 20% over the ask, against the same put sold at the close and against buying it back at
the defence line instead of rolling.

    python packages/backtest/scripts/ivan_tqqq_item49.py expirations          # TQQQ's and QQQ's listed
    expiries (network, once) python packages/backtest/scripts/ivan_tqqq_item49.py plan [--out FILE]    # the
    expiry paths still missing (local) python packages/backtest/scripts/ivan_tqqq_item49.py study --window
    design|holdout [--json PATH] python packages/backtest/scripts/ivan_tqqq_item49.py report               #
    results.md from the results files (local)

`expirations` asks the vendor once for each name's listed expiries. `plan` reads them, the technicals
history's raw bars and the chain store, and writes every (name, expiry, start, through) path an arm
could need that the store does not already hold: the input of `fetch-chain-paths`. Unlike item 29,
an exit here depends on option prices, so the plan is a superset: every session's entry expiry from
that session to its expiry, and every monthly a roll could reach from 112 days before it. `study`
replays the arms on one window. The holdout is read once: its run refuses when a holdout result
exists, and reads only the arms that passed the design window. Everything is written under the
backtest results directory (results/research/item49/), never into the repo: the chains are licensed.

**The rule** (shared by every arm unless it says otherwise):
- **Expiry:** the listed expiry 24 to 44 calendar days out nearest 31, the earlier on a tie.
- **Strike:** the two-sided put nearest 16 delta, Black-Scholes from its mid with r = q = 0, the lower
  strike on a tie. `dist15` takes the put nearest 85% of the close instead.
- **Entry:** at each session's close except a Friday's, with fewer than CAP open chains and no
  split ex-date the next session, a sell limit at the put's close ask x 1.20, rounded up to the tick
  (QQQ $0.01; TQQQ $0.01 under $3 and $0.05 from $3, the penny program as assumed here). It lives
  for the next session only. One order, and so at most one new chain, per session.
- **The fill, from end-of-day data.** The put is repriced by Black-Scholes at the next session's
  open, at the order session's IV, less half the order session's spread. If that clears the limit,
  it fills at the open at that price. Otherwise, if the same reprice at the session low clears the
  limit, it fills at the limit (`overnight` stops here: only an opening fill counts). IV held flat
  understates fills; declared.
- **Management, at each close, in this order:** a split ex-date the next session, or the window's
  last session, closes the chain at the ask. The take profit buys back at the ask once the ask is at
  or under half the leg's credit. The defence fires on a close at or under the strike x 1.05: buy
  back at the ask, then sell the first monthly at least 28 days beyond the current expiry, no more
  than 180 days out, at the lowest two-sided strike whose mid pays the buy-back, filled at the bid.
  Never a debit. At most 3 rolls; with no credit roll the chain is closed. A put in the money at its
  expiry session is assigned 100 shares at the strike, sold at the next session's open.
- **Collateral:** strike x 100 in cash per contract (a spread's: its width x 100). At most CAP open
  chains per arm.

**Arms.** Judged: `rest20` (the rule), `deep` (cushion 1.40), `atclose` (no order: sell at the
close's mid on any session with room), `overnight`, `noroll` (at the defence line, buy back and end
the chain), `trend` (an order only while the adjusted close is above its 200-session average), `qqq`
(the rule on QQQ, defence at 1.7%). Reported: `cushion10`, `tp21` (half the credit or 21 days left),
`tp75`, `hold` (no take profit), `spread` (a long put nearest 10% of the close under the short),
`dist15`, `single` (one open chain).

**Costs.** `cherrypick.core.fees`: $1.00 a contract to open, clearing and ORF on every contract, the
FINRA TAF on sells; an assignment's fee and its share sale's fees. A chain's stress takes fees at
1.5x and every fill $0.02 a leg worse. Its `limit` costing fills every resting entry at its limit,
never better. Its `mid` costing fills at the mid, reported only.

**Measured,** per chain from first sale to final close: the net as a share of the first leg's
collateral. Per calendar month: the summed net of the chains filled that month over their summed
collateral, for the paired comparisons (a month in which one arm opened nothing reads zero for it).
Reported beside them: the chain and leg win rates, rolls, the worst chain, opening fills, and the net
against the 3-month bill yield on the same collateral-days.

**The verdict,** per judged arm: in the design window the mean chain net above zero with a one-sided
t above 2, clustered by the month the chain filled, Holm across the seven; positive in each half
(split 2020-09-01); positive at the stress and at `limit`; then the holdout's mean above zero. Under
30 closed chains in a window is no verdict. The resting-order claim also needs a passing `rest20`,
`deep`, `overnight` or `trend` to beat both `atclose` and `noroll` in the design window, by a paired
monthly difference with a one-sided t above 2.
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
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from cherrypick.core import bs
from cherrypick.core import fees as core_fees
from cherrypick.core import stats as core_stats

NAMES = ("TQQQ", "QQQ")
DTE_RANGE = (24, 44)
DTE_TARGET = 31
DELTA = 0.16
DIST = 0.85
MULT = 100
ROLL_GAP_DAYS = 28
MAX_ROLLS = 3
MAX_ROLL_DTE = 180
ROLL_LOOKBACK_DAYS = MAX_ROLL_DTE  # a second roll straight after a first can reach ~170 days out
TP_DTE = 21
CAP = 5
STRESS_FEES = 1.5
STRESS_SLIP = 0.02
DESIGN = ("2016-05-02", "2024-12-31")
HOLDOUT = ("2025-01-02", "2026-10-28")
WINDOWS = {"design": DESIGN, "holdout": HOLDOUT}
HALF_SPLIT = "2020-09-01"
MIN_CHAINS = 30
T_BAR = 2.0
ALPHA = 0.05
TREND_SESSIONS = 200


@dataclass(frozen=True)
class Arm:
    sym: str = "TQQQ"
    entry: str = "rest"  # rest | close
    cushion: float = 1.20
    overnight: bool = False
    tp: float | None = 0.5  # the share of the leg's credit kept before buying back; None holds
    tp_dte: int | None = None
    defence: float = 0.05
    roll: bool = True
    trend: bool = False
    strike: str = "delta"  # delta | dist
    long_below: float | None = None  # a spread's long, this share of the close under the short
    cap: int = CAP


REST20 = Arm()
JUDGED: dict[str, Arm] = {
    "rest20": REST20,
    "deep": replace(REST20, cushion=1.40),
    "atclose": replace(REST20, entry="close"),
    "overnight": replace(REST20, overnight=True),
    "noroll": replace(REST20, roll=False),
    "trend": replace(REST20, trend=True),
    "qqq": replace(REST20, sym="QQQ", defence=0.017),
}
REPORTED: dict[str, Arm] = {
    "cushion10": replace(REST20, cushion=1.10),
    "tp21": replace(REST20, tp_dte=TP_DTE),
    "tp75": replace(REST20, tp=0.75),
    "hold": replace(REST20, tp=None),
    "spread": replace(REST20, long_below=0.10),
    "dist15": replace(REST20, strike="dist"),
    "single": replace(REST20, cap=1),
}
ARMS = {**JUDGED, **REPORTED}
RESTING = ("rest20", "deep", "overnight", "trend")
RIVALS = ("atclose", "noroll")

# The one alias map. Results stored under ~/.cherrypick before the 2026-11 rename spell the base arm
# `maria` and the claim key `crazy_ivan`; those names came from the source's author and were
# retired from the code. `canon` rewrites them to the current names wherever a stored result is read.
ARM_ALIASES = {"maria": "rest20", "crazy_ivan": "resting_claim"}


def canon(obj):
    """A stored result with the retired names mapped to the current ones (keys and arm lists)."""
    if isinstance(obj, dict):
        return {ARM_ALIASES.get(k, k) if isinstance(k, str) else k: canon(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [ARM_ALIASES.get(x, x) if isinstance(x, str) else canon(x) for x in obj]
    return obj


# ------------------------------------------------------------------------------------------------
# Pure: bars, expiries and ticks.


@dataclass(frozen=True)
class Day:
    date: str
    open: float  # raw: the chain's strike basis
    low: float
    close: float
    adj: float  # split-adjusted close, for the trend average


def days_from(raw_bars, splits) -> list[Day]:
    out = []
    for b in raw_bars:
        if not (b.open and b.low and b.close):
            continue
        f = math.prod(s.to_factor / s.for_factor for s in splits if s.ex_date > b.date)
        out.append(Day(b.date, b.open, b.low, b.close, b.close / f))
    return out


def _cal(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def pick_expiry(session: str, expiries: list[str]) -> str | None:
    lo, hi = DTE_RANGE
    k = bisect.bisect_left(expiries, session)
    fits = [e for e in expiries[k:] if lo <= _cal(session, e) <= hi]
    return min(fits, key=lambda e: (abs(_cal(session, e) - DTE_TARGET), e)) if fits else None


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7 + 14)


def monthlies(expiries: list[str]) -> list[str]:
    """The standard monthly of each month: its third Friday, or the Thursday before when that Friday
    is a holiday and the Thursday is listed instead."""
    have, out = set(expiries), []
    months = sorted({e[:7] for e in expiries})
    for m in months:
        f = third_friday(int(m[:4]), int(m[5:]))
        for d in (f, f - timedelta(days=1)):
            if d.isoformat() in have:
                out.append(d.isoformat())
                break
    return out


def roll_target(current: str, session: str, monthly: list[str]) -> str | None:
    """The first monthly at least ROLL_GAP_DAYS after the current expiry, if within MAX_ROLL_DTE."""
    floor = (date.fromisoformat(current) + timedelta(days=ROLL_GAP_DAYS)).isoformat()
    k = bisect.bisect_left(monthly, floor)
    if k >= len(monthly) or _cal(session, monthly[k]) > MAX_ROLL_DTE:
        return None
    return monthly[k]


def tick(sym: str, price: float) -> float:
    return 0.01 if sym == "QQQ" or price < 3.0 else 0.05


def round_up(sym: str, price: float) -> float:
    t = tick(sym, price)
    return round(math.ceil(round(price / t, 6)) * t, 2)


def sma_above(days: list[Day], i: int, n: int = TREND_SESSIONS) -> bool:
    if i + 1 < n:
        return False
    return days[i].adj > statistics.fmean(d.adj for d in days[i + 1 - n : i + 1])


# ------------------------------------------------------------------------------------------------
# Pure: legs, quotes and fees on one session's chain.

Chain = dict  # {(strike, right): (bid, ask)}


def _two_sided(q) -> bool:
    return q is not None and q[0] is not None and q[1] is not None and q[1] > 0 and q[1] >= q[0] >= 0


def puts(chain: Chain) -> list[float]:
    return sorted(k for (k, r), q in chain.items() if r == "P" and _two_sided(q))


def mid(q) -> float:
    return (q[0] + q[1]) / 2


def put_delta(chain: Chain, k: float, spot: float, years: float) -> tuple[float, float] | None:
    """(|delta|, IV) of the put at k, from its mid."""
    vol = bs.implied_vol(spot, k, years, mid(chain[(k, "P")]), "P", 0.0)
    if not vol:
        return None
    d = bs.delta(spot, k, years, vol, "P", 0.0)
    return (abs(d), vol) if d is not None else None


def choose_short(arm: Arm, chain: Chain, spot: float, years: float) -> float | None:
    ks = puts(chain)
    if not ks:
        return None
    if arm.strike == "dist":
        return min(ks, key=lambda k: (abs(k - DIST * spot), k))
    deltas = {}
    for k in ks:
        if k < spot:
            got = put_delta(chain, k, spot, years)
            if got is not None:
                deltas[k] = got[0]
    return min(deltas, key=lambda k: (abs(deltas[k] - DELTA), k)) if deltas else None


def choose_long(arm: Arm, chain: Chain, short_k: float, spot: float) -> float | None:
    """A spread's long: the two-sided put nearest long_below of the close under the short."""
    below = [k for k in puts(chain) if k < short_k]
    return min(below, key=lambda k: (abs(k - (short_k - arm.long_below * spot)), -k)) if below else None


def legs_for(arm: Arm, chain: Chain, spot: float, years: float) -> tuple[float, float | None] | None:
    short_k = choose_short(arm, chain, spot, years)
    if short_k is None:
        return None
    if arm.long_below is None:
        return short_k, None
    long_k = choose_long(arm, chain, short_k, spot)
    return (short_k, long_k) if long_k is not None else None


def credit_quote(chain: Chain, legs) -> tuple[float, float, float] | None:
    """(natural credit to sell the set, its mid, buy-back at the natural) per share, or None."""
    s, lk = legs
    qs = chain.get((s, "P"))
    if not _two_sided(qs):
        return None
    if lk is None:
        return qs[0], mid(qs), qs[1]
    ql = chain.get((lk, "P"))
    if not _two_sided(ql):
        return None
    return qs[0] - ql[1], mid(qs) - mid(ql), qs[1] - ql[0]


def n_legs(legs) -> int:
    return 1 if legs[1] is None else 2


def collateral(legs) -> float:
    s, lk = legs
    return (s - lk if lk is not None else s) * MULT


def open_fee(sym: str, legs, mult: float = 1.0) -> float:
    return core_fees.ic_open_fee(sym, quantity=1, legs=n_legs(legs), sell_legs=1, ndigits=4) * mult


def close_fee(sym: str, legs, mult: float = 1.0) -> float:
    return (
        core_fees.ic_close_fee(sym, quantity=1, legs=n_legs(legs), sell_legs=n_legs(legs) - 1, ndigits=4)
        * mult
    )


# ------------------------------------------------------------------------------------------------
# Pure: the resting order and its fill.


@dataclass(frozen=True)
class Order:
    placed: int
    expiry: str
    legs: tuple
    limit: float  # the set's net credit per share
    vols: tuple  # each leg's IV at the order session
    halves: tuple  # each leg's half spread at the order session


def make_order(sym: str, arm: Arm, i: int, day: Day, expiry: str, chain: Chain) -> Order | str:
    years = _cal(day.date, expiry) / 365
    legs = legs_for(arm, chain, day.close, years)
    if legs is None:
        return "no strikes"
    vols, halves = [], []
    for k in (x for x in legs if x is not None):
        q = chain[(k, "P")]
        v = bs.implied_vol(day.close, k, years, mid(q), "P", 0.0)
        if not v:
            return "no vol"
        vols.append(v)
        halves.append((q[1] - q[0]) / 2)
    s_ask = chain[(legs[0], "P")][1]
    ask = s_ask if legs[1] is None else s_ask - chain[(legs[1], "P")][0]
    if ask <= 0:
        return "no credit"
    return Order(i, expiry, legs, round_up(sym, ask * arm.cushion), tuple(vols), tuple(halves))


def model_credit(order: Order, spot: float, session: str) -> tuple[float, float]:
    """(the set's modelled natural credit, its modelled mid) at `spot`, at the order's IVs."""
    years = max(_cal(session, order.expiry), 0) / 365
    strikes = [k for k in order.legs if k is not None]
    mids = [bs.price(spot, k, years, v, "P", 0.0) for k, v in zip(strikes, order.vols, strict=True)]
    nat = mids[0] - order.halves[0] - (mids[1] + order.halves[1] if len(mids) > 1 else 0.0)
    m = mids[0] - (mids[1] if len(mids) > 1 else 0.0)
    return nat, m


def fill_order(order: Order, day: Day, overnight: bool) -> tuple[float, float, bool] | None:
    """(fill credit, mid at the fill, at the open?) for an order on this session, or None."""
    nat, m = model_credit(order, day.open, day.date)
    if nat >= order.limit:
        return nat, m, True
    if overnight:
        return None
    nat, m = model_credit(order, day.low, day.date)
    if nat >= order.limit:
        return order.limit, order.limit + (m - nat), False
    return None


# ------------------------------------------------------------------------------------------------
# One chain, from first sale to final close.

COSTINGS = ("natural", "stress", "limit", "mid")


@dataclass
class ChainState:
    arm: str
    sym: str
    order_date: str
    filled: int
    legs: tuple
    expiry: str
    credit: float  # the current leg's natural credit per share, the take profit's basis
    collateral0: float
    at_open: bool
    cash: dict = field(default_factory=lambda: dict.fromkeys(COSTINGS, 0.0))
    leg_cash: float = 0.0
    leg_nets: list = field(default_factory=list)
    rolls: int = 0
    stale: int = 0
    coll_days: float = 0.0
    leg_start: str = ""
    entry_date: str = ""
    max_collateral: float = 0.0


def _book_open(c: ChainState, sym: str, legs, nat: float, mid_px: float, limit_px: float | None) -> None:
    n = n_legs(legs)
    fee = open_fee(sym, legs)
    c.cash["natural"] += nat * MULT - fee
    c.cash["stress"] += (nat - STRESS_SLIP * n) * MULT - fee * STRESS_FEES
    c.cash["limit"] += (limit_px if limit_px is not None else nat) * MULT - fee
    c.cash["mid"] += mid_px * MULT - fee
    c.leg_cash = nat * MULT - fee


def _book_close(c: ChainState, sym: str, legs, buy: float, mid_px: float) -> None:
    n = n_legs(legs)
    fee = close_fee(sym, legs)
    for k in ("natural", "limit"):
        c.cash[k] -= buy * MULT + fee
    c.cash["stress"] -= (buy + STRESS_SLIP * n) * MULT + fee * STRESS_FEES
    c.cash["mid"] -= mid_px * MULT + fee
    c.leg_nets.append(c.leg_cash - buy * MULT - fee)


def _book_flat(c: ChainState, amount: float) -> None:
    """A cash flow every costing shares: settlement, an assignment's share sale and its fees."""
    for k in COSTINGS:
        c.cash[k] += amount
    c.leg_nets.append(c.leg_cash + amount)


def _accrue(c: ChainState, through: str) -> None:
    c.coll_days += collateral(c.legs) * max(_cal(c.leg_start, through), 1)
    c.leg_start = through


def manage(
    c: ChainState,
    arm: Arm,
    days: list[Day],
    t: int,
    chain: Chain,
    monthly: list[str],
    chain_at,
    *,
    last: int,
    split_next: bool,
) -> str | None:
    """One close's management. Returns how the chain ended, or None while it is still open."""
    d = days[t]
    q = credit_quote(chain, c.legs)
    expiry_session = d.date == c.expiry or (t + 1 < len(days) and d.date < c.expiry < days[t + 1].date)
    if q is None:
        if expiry_session:
            return _expire(c, arm, days, t)
        c.stale += 1
        if not (split_next or t >= last):
            return None
        # forced out with no quote: intrinsic at the close
        _accrue(c, d.date)
        s, lk = c.legs
        value = bs.intrinsic(d.close, s, "P") - (bs.intrinsic(d.close, lk, "P") if lk else 0.0)
        _book_close(c, arm.sym, c.legs, value, value)
        return "split" if split_next else "end"
    nat_credit, mid_px, buy = q
    mid_buy = mid_px
    if split_next or t >= last:
        _accrue(c, d.date)
        _book_close(c, arm.sym, c.legs, buy, mid_buy)
        return "split" if split_next else "end"
    if arm.tp is not None and buy <= c.credit * (1 - arm.tp):
        _accrue(c, d.date)
        _book_close(c, arm.sym, c.legs, buy, mid_buy)
        return "tp"
    if arm.tp_dte is not None and _cal(d.date, c.expiry) <= arm.tp_dte:
        _accrue(c, d.date)
        _book_close(c, arm.sym, c.legs, buy, mid_buy)
        return "dte"
    if d.close <= c.legs[0] * (1 + arm.defence):
        _accrue(c, d.date)
        _book_close(c, arm.sym, c.legs, buy, mid_buy)
        if not arm.roll:
            return "defence"
        if c.rolls >= MAX_ROLLS:
            return "max-rolls"
        target = roll_target(c.expiry, d.date, monthly)
        if target is None:
            return "no-roll"
        new = roll_legs(arm, chain_at(d.date, target), d.close, buy)
        if new is None:
            return "no-roll"
        legs, nat, m = new
        c.legs, c.expiry, c.credit, c.rolls = legs, target, nat, c.rolls + 1
        c.max_collateral = max(c.max_collateral, collateral(legs))
        _book_open(c, arm.sym, legs, nat, m, None)
        return None
    if expiry_session:
        return _expire(c, arm, days, t)
    return None


def roll_legs(arm: Arm, chain: Chain, spot: float, paid: float) -> tuple[tuple, float, float] | None:
    """The lowest short strike whose set's mid pays `paid`, sold at the natural: (legs, credit, mid)."""
    for k in puts(chain):
        lk = None
        if arm.long_below is not None:
            lk = choose_long(arm, chain, k, spot)
            if lk is None:
                continue
        q = credit_quote(chain, (k, lk))
        if q is not None and q[1] >= paid and q[0] > 0:
            return (k, lk), q[0], q[1]
    return None


def _expire(c: ChainState, arm: Arm, days: list[Day], t: int) -> str:
    d = days[t]
    _accrue(c, d.date)
    s, lk = c.legs
    if lk is not None:
        itm = sum(1 for k in (s, lk) if bs.intrinsic(d.close, k, "P") > 0)
        value = bs.intrinsic(d.close, s, "P") - bs.intrinsic(d.close, lk, "P")
        _book_flat(c, -value * MULT - core_fees.ic_expire_fee(itm))
        return "expired-itm" if itm else "expired"
    if d.close >= s:
        _book_flat(c, 0.0)
        return "expired"
    dispose = days[t + 1].open if t + 1 < len(days) else d.close
    fee = core_fees.assignment_round_trip_fee(MULT, s, dispose, direction="long")
    _book_flat(c, MULT * (dispose - s) - fee)
    return "assigned"


def finish(c: ChainState, how: str, exit_date: str) -> dict:
    nets = {k: round(v, 2) for k, v in c.cash.items()}
    return {
        "arm": c.arm,
        "name": c.sym,
        "order": c.order_date,
        "entry": c.entry_date,
        "exit": exit_date,
        "how": how,
        "rolls": c.rolls,
        "net": nets["natural"],
        "nets": nets,
        "collateral": c.collateral0,
        "share": nets["natural"] / c.collateral0,
        "share_stress": nets["stress"] / c.collateral0,
        "share_limit": nets["limit"] / c.collateral0,
        "share_mid": nets["mid"] / c.collateral0,
        "coll_days": c.coll_days,
        "max_collateral": c.max_collateral,
        "leg_wins": sum(x > 0 for x in c.leg_nets),
        "legs_n": len(c.leg_nets),
        "at_open": c.at_open,
        "stale": c.stale,
    }


# ------------------------------------------------------------------------------------------------
# Running one arm over a window.


def run_arm(
    name: str,
    arm: Arm,
    days: list[Day],
    expiries: list[str],
    split_dates: list[str],
    window: tuple[str, str],
    chain_at,
) -> tuple[list[dict], dict]:
    w0, w1 = window
    dates = [d.date for d in days]
    first = bisect.bisect_left(dates, w0)
    last = bisect.bisect_right(dates, w1) - 1
    monthly = monthlies(expiries)
    splits = set(split_dates)
    open_: list[ChainState] = []
    done: list[dict] = []
    counts: dict[str, int] = {}
    pending: Order | None = None

    def count(k: str) -> None:
        counts[k] = counts.get(k, 0) + 1

    for t in range(first, last + 1):
        d = days[t]
        split_next = t + 1 < len(days) and days[t + 1].date in splits
        # 1. yesterday's order, filled or gone
        if pending is not None:
            got = None if d.date in splits else fill_order(pending, d, arm.overnight)
            if got is None:
                count("unfilled")
            else:
                nat, m, at_open = got
                c = ChainState(name, arm.sym, days[pending.placed].date, t, pending.legs, pending.expiry,
                               nat, collateral(pending.legs), at_open, leg_start=d.date)  # fmt: skip
                c.entry_date = d.date
                c.max_collateral = c.collateral0
                _book_open(c, arm.sym, pending.legs, nat, m, pending.limit)
                open_.append(c)
                count("filled-open" if at_open else "filled-low")
            pending = None
        # 2. every open chain at this close
        still = []
        for c in open_:
            how = manage(c, arm, days, t, chain_at(d.date, c.expiry), monthly, chain_at,
                         last=last, split_next=split_next)  # fmt: skip
            if how is None:
                still.append(c)
            else:
                done.append(finish(c, how, d.date))
        open_ = still
        # 3. tonight's order, or the close's sale
        if t >= last or len(open_) >= arm.cap or split_next:
            continue
        if arm.entry == "rest" and date.fromisoformat(d.date).weekday() == 4:
            continue
        if arm.trend and not sma_above(days, t):
            count("below-trend")
            continue
        exp = pick_expiry(d.date, expiries)
        if exp is None:
            count("no expiry")
            continue
        chain = chain_at(d.date, exp)
        if not chain:
            count("no chain")
            continue
        if arm.entry == "close":
            legs = legs_for(arm, chain, d.close, _cal(d.date, exp) / 365)
            q = credit_quote(chain, legs) if legs else None
            if q is None or q[1] <= 0:
                count("no strikes")
                continue
            c = ChainState(
                name, arm.sym, d.date, t, legs, exp, q[1], collateral(legs), False, leg_start=d.date
            )
            c.entry_date = d.date
            c.max_collateral = c.collateral0
            _book_open(c, arm.sym, legs, q[1], q[1], None)
            open_.append(c)
            count("sold-close")
            continue
        got = make_order(arm.sym, arm, t, d, exp, chain)
        if isinstance(got, str):
            count(got)
            continue
        pending = got
    return done, counts


# ------------------------------------------------------------------------------------------------
# Statistics and the verdict.


def clustered(xs: list[float], groups: list[str]) -> dict:
    """The mean, with a one-sided t clustered by group (sum of within-group residuals)."""
    n = len(xs)
    out: dict = {"n": n, "mean": statistics.fmean(xs) if xs else None}
    if n < 2:
        return out
    m = out["mean"]
    by: dict[str, float] = {}
    for x, g in zip(xs, groups, strict=True):
        by[g] = by.get(g, 0.0) + (x - m)
    g = len(by)
    se = math.sqrt(sum(v * v for v in by.values()) * g / max(g - 1, 1)) / n
    out["sd"] = statistics.stdev(xs)
    out["clusters"] = g
    if se <= 0:
        out["t"] = math.inf if m > 0 else -math.inf
        out["p"] = 0.0 if m > 0 else 1.0
    else:
        out["t"] = m / se
        out["p"] = 1 - core_stats.t_cdf(out["t"], g - 1) if g > 1 else 1.0
    return out


def plain_t(xs: list[float]) -> dict:
    n = len(xs)
    out: dict = {"n": n, "mean": statistics.fmean(xs) if xs else None}
    if n >= 2:
        sd = statistics.stdev(xs)
        out["t"] = (
            out["mean"] / (sd / math.sqrt(n)) if sd > 0 else (math.inf if out["mean"] > 0 else -math.inf)
        )
    return out


def monthly_returns(chains: list[dict]) -> dict[str, float]:
    net: dict[str, float] = {}
    coll: dict[str, float] = {}
    for c in chains:
        m = c["entry"][:7]
        net[m] = net.get(m, 0.0) + c["net"]
        coll[m] = coll.get(m, 0.0) + c["collateral"]
    return {m: net[m] / coll[m] for m in net if coll[m] > 0}


def paired(a: list[dict], b: list[dict]) -> dict:
    ra, rb = monthly_returns(a), monthly_returns(b)
    months = sorted(set(ra) | set(rb))
    return plain_t([ra.get(m, 0.0) - rb.get(m, 0.0) for m in months])


def arm_report(chains: list[dict], window: tuple[str, str], rate_at=None) -> dict:
    out = clustered([c["share"] for c in chains], [c["entry"][:7] for c in chains])
    for k in ("stress", "limit", "mid"):
        xs = [c[f"share_{k}"] for c in chains]
        out[k] = statistics.fmean(xs) if xs else None
    h1 = [c["share"] for c in chains if c["entry"] < HALF_SPLIT]
    h2 = [c["share"] for c in chains if c["entry"] >= HALF_SPLIT]
    out["halves"] = [statistics.fmean(h) if h else None for h in (h1, h2)]
    out["win"] = sum(c["net"] > 0 for c in chains)
    out["leg_win"] = sum(c["leg_wins"] for c in chains)
    out["legs"] = sum(c["legs_n"] for c in chains)
    out["pl"] = round(sum(c["net"] for c in chains), 2)
    out["worst"] = min((c["net"] for c in chains), default=None)
    out["at_open"] = sum(c["at_open"] for c in chains)
    out["rolled"] = sum(c["rolls"] > 0 for c in chains)
    out["how"] = {}
    for c in chains:
        out["how"][c["how"]] = out["how"].get(c["how"], 0) + 1
    out["stale"] = sum(c["stale"] for c in chains)
    cd = sum(c["coll_days"] for c in chains)
    out["net_per_coll_year"] = out["pl"] / cd * 365 if cd else None
    if rate_at is not None and chains:
        out["bill_per_coll_year"] = (
            sum(c["coll_days"] * rate_at(c["entry"]) for c in chains) / cd if cd else None
        )
    if out.get("sd") is not None:
        out["mde"] = core_stats.minimum_detectable_effect(out["sd"], out["n"])
    return out


def _pos(x) -> bool:
    return x is not None and x > 0


def judge_design(design: dict[str, dict], chains: dict[str, list[dict]]) -> dict[str, dict]:
    pvals = {a: (design[a].get("p", 1.0) if design[a]["n"] >= MIN_CHAINS else 1.0) for a in JUDGED}
    holm = core_stats.holm(pvals, ALPHA)
    out = {}
    for a in JUDGED:
        d = design[a]
        if d["n"] < MIN_CHAINS:
            out[a] = {"verdict": "no verdict", "why": f"design {d['n']} chains"}
            continue
        checks = {
            "mean": _pos(d["mean"]),
            "t": (d.get("t") or 0) > T_BAR,
            "holm": holm[a],
            "halves": all(_pos(x) for x in d["halves"]),
            "stress": _pos(d["stress"]),
            "limit": _pos(d["limit"]),
        }
        out[a] = {"verdict": "design pass" if all(checks.values()) else "fail", "checks": checks}
    claim = {}
    for a in RESTING:
        claim[a] = {r: paired(chains[a], chains[r]) for r in RIVALS}
    return {"arms": out, "claim": claim}


def judge_holdout(design_verdict: dict, holdout: dict[str, dict]) -> dict[str, dict]:
    out = {}
    for a, v in design_verdict["arms"].items():
        if v["verdict"] != "design pass":
            out[a] = v
            continue
        h = holdout.get(a)
        if h is None or h["n"] < MIN_CHAINS:
            out[a] = {**v, "verdict": "no verdict", "why": f"holdout {h['n'] if h else 0} chains"}
            continue
        checks = {**v["checks"], "holdout": _pos(h["mean"])}
        out[a] = {"verdict": "pass" if checks["holdout"] else "fail", "checks": checks}
    return out


def claim_holds(verdict: dict[str, dict], claim: dict) -> list[str]:
    """The resting arms that pass and beat both rivals in the design window."""
    return [
        a
        for a in RESTING
        if verdict.get(a, {}).get("verdict") == "pass"
        and all((claim[a][r].get("t") or 0) > T_BAR and _pos(claim[a][r]["mean"]) for r in RIVALS)
    ]


# ------------------------------------------------------------------------------------------------
# The stores and the commands.


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item49"
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
        raise SystemExit(f"no expiries at {path}: run `ivan_tqqq_item49.py expirations` first")
    return {s: sorted(v) for s, v in json.loads(path.read_text(encoding="utf-8"))["expirations"].items()}


def _rates():
    spec = importlib.util.spec_from_file_location(
        "s28", Path(__file__).resolve().parents[3] / "scripts" / "share_ladder_item28.py"
    )
    s28 = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("s28", s28)
    spec.loader.exec_module(s28)
    rates = s28.load_rates()
    return (lambda session: s28.rate_before(rates, session) / 100) if rates else None


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


def wanted_sessions(days: list[Day], expiries: list[str], windows) -> dict[str, set[str]]:
    """{expiry: sessions} every arm could read: each session's entry expiry from that session to its
    expiry, and each monthly from ROLL_LOOKBACK_DAYS before it, inside a window."""
    want: dict[str, set[str]] = {}
    dates = [d.date for d in days]
    monthly = monthlies(expiries)
    for w0, w1 in windows:
        a, b = bisect.bisect_left(dates, w0), bisect.bisect_right(dates, w1) - 1
        for t in range(a, b + 1):
            exp = pick_expiry(dates[t], expiries)
            if exp is None:
                continue
            k = bisect.bisect_right(dates, min(exp, w1)) - 1
            want.setdefault(exp, set()).update(dates[t : k + 1])
        for m in monthly:
            start = (date.fromisoformat(m) - timedelta(days=ROLL_LOOKBACK_DAYS)).isoformat()
            lo, hi = max(start, w0), min(m, w1)
            if lo > hi:
                continue
            i, j = bisect.bisect_left(dates, lo), bisect.bisect_right(dates, hi) - 1
            want.setdefault(m, set()).update(dates[i : j + 1])
    return want


def missing_spans(sym: str, want: dict[str, set[str]], have, dates: list[str]) -> list[dict]:
    """The wanted sessions the store lacks, as contiguous runs of sessions: one request a run."""
    pos = {d: i for i, d in enumerate(dates)}
    out = []
    for exp in sorted(want):
        gaps = sorted(pos[s] for s in want[exp] - have(exp))
        run: list[int] = []
        for i in gaps + [None]:
            if run and (i is None or i != run[-1] + 1):
                out.append(
                    {"symbol": sym, "expiration": exp, "start": dates[run[0]], "through": dates[run[-1]]}
                )
                run = []
            if i is not None:
                run.append(i)
    return out


def stored_sessions(conn, sym: str, dates: list[str]) -> dict[str, set[str]]:
    """{expiry: sessions} the store has answered, read from its own records of finished work (a
    complete or empty path covers every session in its range; a single request its one session),
    never by scanning the quotes, which takes minutes on a name the size of QQQ."""
    out: dict[str, set[str]] = {}
    done = ("complete", "empty")
    for exp, a, b in conn.execute(
        "SELECT expiration, start, through FROM eod_chain_paths WHERE symbol = ? AND status IN (?, ?)",
        (sym, *done),
    ):
        i, j = bisect.bisect_left(dates, a), bisect.bisect_right(dates, b)
        out.setdefault(exp, set()).update(dates[i:j])
    for exp, s in conn.execute(
        "SELECT expiration, session FROM eod_chain_requests WHERE symbol = ? AND status IN (?, ?)",
        (sym, *done),
    ):
        out.setdefault(exp, set()).add(s)
    return out


def _store():
    from cherrypick.backtest import chainfetch, settings

    path = chainfetch.db_path(settings.load(None))
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def cmd_plan(args) -> int:
    expiries = load_expiries()
    conn = _store()
    reqs = []
    try:
        for sym in NAMES:
            days, _ = load_days(sym)
            want = wanted_sessions(days, expiries[sym], WINDOWS.values())

            covered = stored_sessions(conn, sym, [d.date for d in days])
            reqs += missing_spans(sym, want, lambda e, c=covered: c.get(e, set()), [d.date for d in days])
    finally:
        conn.close()
    out = Path(args.out) if args.out else _out_dir() / "path_requests.json"
    out.write_text(json.dumps({"requests": reqs}, indent=0), encoding="utf-8")
    by_name = {s: sum(r["symbol"] == s for r in reqs) for s in NAMES}
    print(json.dumps({"requests": len(reqs), "by_name": by_name, "file": str(out)}))
    return 0


def _chain_reader(conn):
    def make(sym: str):
        @lru_cache(maxsize=8192)
        def chain_at(session: str, expiry: str) -> Chain:
            return {
                (k, "P"): (b, a)
                for k, r, b, a in conn.execute(
                    "SELECT strike, right, bid, ask FROM eod_chain_quotes "
                    "WHERE symbol = ? AND session = ? AND expiration = ?",
                    (sym, session, expiry),
                )
                if r[0].upper() == "P"
            }

        return chain_at

    return make


def run_window(wname: str, arms: list[str]) -> tuple[dict, dict, dict]:
    expiries = load_expiries()
    conn = _store()
    make = _chain_reader(conn)
    chains: dict[str, list[dict]] = {}
    counts: dict[str, dict] = {}
    loaded: dict[str, tuple] = {}
    try:
        for a in arms:
            arm = ARMS[a]
            if arm.sym not in loaded:
                loaded[arm.sym] = (*load_days(arm.sym), make(arm.sym))
            days, split_dates, chain_at = loaded[arm.sym]
            chains[a], counts[a] = run_arm(
                a, arm, days, expiries[arm.sym], split_dates, WINDOWS[wname], chain_at
            )
            print(wname, a, len(chains[a]), flush=True)
    finally:
        conn.close()
    rate_at = _rates()
    reports = {a: arm_report(chains[a], WINDOWS[wname], rate_at) for a in arms}
    return chains, counts, reports


def cmd_study(args) -> int:
    d = _out_dir()
    if args.window == "design":
        chains, counts, reports = run_window("design", list(ARMS))
        verdict = judge_design(reports, chains)
        result = {
            "window": "design",
            "reports": reports,
            "counts": counts,
            "verdict": verdict,
            "chains": chains,
        }
        out = Path(args.json) if args.json else d / "results-design.json"
    else:
        done = d / "results-holdout.json"
        if done.exists():
            raise SystemExit(f"the holdout has been read ({done}); it is read once")
        design = canon(json.loads((d / "results-design.json").read_text(encoding="utf-8")))
        passed = [a for a, v in design["verdict"]["arms"].items() if v["verdict"] == "design pass"]
        chains, counts, reports = run_window("holdout", passed) if passed else ({}, {}, {})
        verdict = judge_holdout(design["verdict"], reports)
        result = {
            "window": "holdout",
            "arms_read": passed,
            "reports": reports,
            "counts": counts,
            "verdict": verdict,
            "resting_claim": claim_holds(verdict, design["verdict"]["claim"]),
            "chains": chains,
        }
        out = done
    out.write_text(json.dumps(result, default=str), encoding="utf-8")
    (d / "results.md").write_text(report(d), encoding="utf-8")
    arms = result["verdict"]["arms"] if args.window == "design" else result["verdict"]
    for a, v in arms.items():
        failed = [k for k, x in v.get("checks", {}).items() if not x]
        print(a, v["verdict"], failed or "", v.get("why", ""))
    return 0


def _money(x: float | None) -> str:
    if x is None:
        return ""
    return f"{'−' if round(x) < 0 else '+' if round(x) > 0 else ''}${abs(x):,.0f}"


def _pct(x: float | None) -> str:
    return "" if x is None else f"{100 * x:.2f}%"


def report(d: Path) -> str:
    """results.md from whatever results files exist. Derived from licensed chains, so it stays where
    the JSON is and never enters the repo."""
    lines = ["# Item 49: the dip-sold put on TQQQ", ""]
    for wname in ("design", "holdout"):
        p = d / f"results-{wname}.json"
        if not p.exists():
            lines += [f"## {wname.capitalize()}: not run", ""]
            continue
        r = canon(json.loads(p.read_text(encoding="utf-8")))
        w = WINDOWS[wname]
        verdicts = r["verdict"]["arms"] if wname == "design" else r["verdict"]
        lines += [
            f"## {wname.capitalize()}: {w[0]} to {w[1]}",
            "",
            "| arm | verdict | chains | win % | leg win % | mean net / collateral | t | stress | at the limit"
            " | P&L | worst | rolled | opening fills |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for a, v in r["reports"].items():
            n = v["n"]
            verdict = verdicts.get(a, {}).get("verdict", "reported")
            lines.append(
                f"| {a} | {verdict} | {n} | {_pct(v['win'] / n) if n else ''}"
                f" | {_pct(v['leg_win'] / v['legs']) if v['legs'] else ''} | {_pct(v['mean'])}"
                f" | {v.get('t', 0):.2f} | {_pct(v['stress'])} | {_pct(v['limit'])} | {_money(v['pl'])}"
                f" | {_money(v['worst'])} | {v['rolled']} | {v['at_open']} |"
            )
        if wname == "design":
            lines += ["", "**Against the rivals** (paired monthly difference, design):", ""]
            for a, rv in r["verdict"]["claim"].items():
                bits = [f"{x}: mean {_pct(s['mean'])}, t {s.get('t', 0):.2f}" for x, s in rv.items()]
                lines.append(f"- {a}: " + "; ".join(bits))
        else:
            held = ", ".join(r["resting_claim"]) or "none"
            lines += ["", f"**The resting-order claim holds for:** {held}."]
        lines.append("")
    return "\n".join(lines) + "\n"


def cmd_report(args) -> int:
    (_out_dir() / "results.md").write_text(report(_out_dir()), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("expirations").set_defaults(fn=cmd_expirations)
    p = sub.add_parser("plan")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_plan)
    s = sub.add_parser("study")
    s.add_argument("--window", choices=tuple(WINDOWS), required=True)
    s.add_argument(
        "--json", help="the design window only: write elsewhere; the holdout always writes its own file"
    )
    s.set_defaults(fn=cmd_study)
    r = sub.add_parser("report", help="results.md from the existing results files (local)")
    r.set_defaults(fn=cmd_report)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
