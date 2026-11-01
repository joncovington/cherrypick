"""Item 36, pmcc's own rule at real quotes (docs/edge-hypotheses-plan.md, section 36).

    python scripts/pmcc_item36.py plan [--out-dir DIR]     # the fetch-chain-paths request files, with counts
    python scripts/pmcc_item36.py pilot [--symbol XSP(SPY)] # the guards on one symbol, no arm read
    python scripts/pmcc_item36.py study [--json PATH]      # guards, six arms, two baselines, the verdict
    python scripts/pmcc_item36.py report [--json PATH]     # results.md from an existing results.json

`plan` writes one request file per symbol (SPY first, the stocks last) under the backtest results dir
(research/item36/), the input of `python -m cherrypick.backtest fetch-chain-paths --requests FILE`,
and prints the counts per symbol and in all, less what the chain store already holds. `study` reads
the store, the bar store and the pmcc paper ledger and writes results.json and results.md beside it.
Everything licensed stays under ~/.cherrypick; nothing here writes into the repo.

**The arms and baselines are pmcc's own code, imported.** Selection is `engine.select_long`,
`engine.plan_entry` and `engine.plan_short` (so `select_short_by_delta`, the spread refusals and the
floor strike); management is `management.evaluate_held_long` called once a session at 30 minutes
before the close (so the stop, the 45-DTE long roll, the expiry roll, the decay roll, the breach roll
and the assignment roll fire in pmcc's order); the expirations are `clock.leap_expiration`,
`clock.short_expiration` and `clock.weekly_expiration`; costs are `engine.leg_fee` and
`core.fees.slippage_dollars` over the module's `tastytrade_costs`; the $5 settlement event is
`engine.settlement_fee`. What this file adds is the data (a stored chain for a session and an expiry
in place of the broker's snapshot), Black-Scholes delta from the mid's implied volatility (the store
holds no greeks), the weekly marking, and the statistics.

**Readings chosen where the declaration is silent** (each also in the report):
- One evaluation a session, at the close's quotes. An action blocked by the spread gate
  (`management.short_execution_gate`) is retried next session and counted.
- P&L to date for the stop is the position's every cash flow, costs (fees, slippage, settlement)
  included, plus the legs' marks.
- The three weekly arms and `hold-30d` need a short at entry (pmcc's `plan_entry`); `hold-30d-monthly`,
  `hold-vrp` and the baselines do not (the declaration lets their long run alone).
- A week is measured when the arm held a position at the previous Friday close and every held leg
  was quoted at both Friday closes; otherwise it is counted (`flat` or `unpriced`), never zero.
- Capital is the open position's: the long's debit in dollars (the put's cash-secured strike).
- An expiring short whose buyback is blocked settles at intrinsic with the $5 event if in the
  money; early assignment is never simulated, only counted (`management.assignment_exposed`).
- A cycle whose skeleton span (the no-stop long cycle) holds a split ex-date is dropped from every arm
  and baseline: its weeks are excluded and a position open on the split day is dropped unbooked.
- The fetch skeleton is the no-stop long cycle; a stop re-entry onto an unstored long is a counted
  refusal (`no_long_chain`), not a gap in the plan.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
from cherrypick.core import bs, treasury
from cherrypick.core import calendar as cal
from cherrypick.core import fees as core_fees
from cherrypick.core import progress as core_progress
from cherrypick.core import stats as core_stats
from cherrypick.pmcc import clock, engine, management

# ------------------------------------------------------------------------------------------------
# Declarations.

DESIGN = (date(2016, 1, 1), date(2021, 12, 31))
HOLDOUT = (date(2022, 1, 1), date(2026, 10, 23))
HALVES = {
    "2016-18": (date(2016, 1, 1), date(2018, 12, 31)),
    "2019-21": (date(2019, 1, 1), date(2021, 12, 31)),
}
WARMUP = date(2015, 11, 2)  # bars before the window, for the realised-volatility gate

#: row label -> (chain symbol, settlement style, fee symbol, kind). XSP is replayed on SPY and treated as
#: pmcc treats XSP: cash-settled, no index exchange fee.
SYMBOLS = {
    "XSP(SPY)": ("SPY", "cash", "XSP", "fund"),
    "QQQ": ("QQQ", "physical", "QQQ", "fund"),
    "IWM": ("IWM", "physical", "IWM", "fund"),
    "GLD": ("GLD", "physical", "GLD", "fund"),
    "SLV": ("SLV", "physical", "SLV", "fund"),
    "SMH": ("SMH", "physical", "SMH", "fund"),
    "AMZN": ("AMZN", "physical", "AMZN", "stock"),
    "TSLA": ("TSLA", "physical", "TSLA", "stock"),
    "AMD": ("AMD", "physical", "AMD", "stock"),
    "NVDA": ("NVDA", "physical", "NVDA", "stock"),
    "PLTR": ("PLTR", "physical", "PLTR", "stock"),
}
FUNDS = tuple(k for k, v in SYMBOLS.items() if v[3] == "fund")
STOCKS = tuple(k for k, v in SYMBOLS.items() if v[3] == "stock")
LISTED_FROM = {"PLTR": date(2020, 9, 30)}

ARM_NAMES = ("shield", "shield_hold", "shield_guard", "hold-30d", "hold-30d-monthly", "hold-vrp")
BASELINES = ("long-alone", "put-benchmark")
REPORTED_ONLY = ("long-alone-nostop",)
#: shield_guard is identical to shield_hold on a cash-settled row by construction; dropped there.
GUARD_DROPPED = {"shield_guard": ("XSP(SPY)",)}
REPORTED_PAIRS = (("hold-30d", "hold-30d-monthly"), ("hold-vrp", "shield_hold"))

COST_SCALES = (1.0, 1.5)
RESAMPLES = 20_000
BLOCK = 8
SEED = 36
ALPHA = 0.05
TWO_THIRDS = 2 / 3
MIN_CYCLES = 4
MIN_WEEKS = {"design": 120, "holdout": 90}
MIN_CYCLES_MONTHLY = {"design": 25, "holdout": 18}
MAX_MISSING = 0.20
MIN_JUDGED_FUNDS = 4
MIN_CELL_WEEKS = 40
REPRODUCTION_FLOOR = 0.80
WPUT_TOLERANCE = 0.02
WPUT_COVERAGE = 0.90  # the share of the span's weeks with a weekly put chain, else the check is not run
PUT_DELTA = 0.20
LONG_CLOSE_DTE = 45
MONTHLY_DTE = (30, 45, 37)
GATE_DTE = (21, 38, 30)
MINUTES_BEFORE_CLOSE = 30
WINDOWS_5Y = tuple((date(y, 1, 1), date(y + 4, 12, 31)) for y in range(2016, 2022))
CRASH = (date(2020, 2, 21), date(2020, 4, 17))
PATH_CALL_DAYS = 7
SHORT_LEAD = 11  # a weekly short's path starts this many days before its expiry
LEDGER_FROM = "2026-10-06"
COMPONENTS = ("long", "premium", "short_mtm", "fees", "slippage", "settlement")


# ------------------------------------------------------------------------------------------------
# Arms.


@dataclass(frozen=True)
class Spec:
    name: str
    kind: str = "call"  # "call": a long with an optional short | "put": the 20-delta year-long put
    base: str = "shield_hold"  # the engine.ARM_RULES entry
    overrides: dict = field(default_factory=dict)
    short: str | None = "weekly"  # None | "weekly" | "monthly"
    needs_short: bool = True  # pmcc's plan_entry refuses an entry without a short
    friday_gate: bool = False  # hold-vrp: sold only on the week's last session, when IV > RV


_THIRTY = {"short_delta_target": 0.30, "short_delta_min": 0.25, "short_delta_max": 0.35}
SPECS: dict[str, Spec] = {
    "shield": Spec("shield", base="shield"),
    "shield_hold": Spec("shield_hold"),
    "shield_guard": Spec("shield_guard", base="shield_guard"),
    "hold-30d": Spec("hold-30d", overrides=_THIRTY),
    "hold-30d-monthly": Spec("hold-30d-monthly", overrides=_THIRTY, short="monthly", needs_short=False),
    "hold-vrp": Spec("hold-vrp", needs_short=False, friday_gate=True),
    "long-alone": Spec("long-alone", short=None, needs_short=False),
    "long-alone-nostop": Spec(
        "long-alone-nostop", short=None, needs_short=False, overrides={"stop_loss_frac": None}
    ),
    "put-benchmark": Spec("put-benchmark", kind="put", short=None, needs_short=False),
}


def spec_params(spec: Spec) -> dict:
    """pmcc's own parameters for an arm: the module defaults, the arm's rules in code, our overrides."""
    return {**management.PARAM_DEFAULTS, **engine.ARM_RULES[spec.base], **spec.overrides, "arm": spec.name}


# ------------------------------------------------------------------------------------------------
# Pure: the calendar.


def sessions_between(a: date, b: date) -> list[date]:
    d = a if cal.is_trading_day(a) else cal.next_trading_day(a)
    out = []
    while d <= b:
        out.append(d)
        d = cal.next_trading_day(d)
    return out


def week_ends(sessions: list[date]) -> list[date]:
    """The last session of each ISO week."""
    out = []
    for i, d in enumerate(sessions):
        if i + 1 == len(sessions) or sessions[i + 1].isocalendar()[:2] != d.isocalendar()[:2]:
            out.append(d)
    return out


def listed_monthlies(day: date) -> list[str]:
    """The standard monthlies a long chosen on `day` can be drawn from (the leap band, 240 to 540)."""
    out = []
    for k in range(7, 20):
        y, m = day.year + (day.month - 1 + k) // 12, (day.month - 1 + k) % 12 + 1
        out.append(clock.standard_monthly(y, m).isoformat())
    return out


def leap_for(day: date) -> date | None:
    pick = clock.leap_expiration(listed_monthlies(day), day)
    return date.fromisoformat(pick["long_expiration"]) if pick else None


def long_cycles(sessions: list[date]) -> list[tuple[date, date, date]]:
    """The no-stop skeleton: (entry session, long expiry, close session), the close being the first
    session at or under 45 DTE and the next entry the session after it."""
    out, i = [], 0
    while i < len(sessions):
        d = sessions[i]
        exp = leap_for(d)
        if exp is None:
            i += 1
            continue
        j = i
        while j < len(sessions) and (exp - sessions[j]).days > LONG_CLOSE_DTE:
            j += 1
        if j >= len(sessions):
            out.append((d, exp, sessions[-1]))
            break
        out.append((d, exp, sessions[j]))
        i = j + 1
    return out


def monthly_expiries(day: date, cap: date | None) -> list[date]:
    """The listed expiries 30 to 45 days out (never past `cap`), nearest 37 first, the nearer on a tie."""
    lo, hi, aim = MONTHLY_DTE
    ok = [
        e
        for e in clock.candidate_expirations(day, weeks=9)
        if lo <= (e - day).days <= hi and (cap is None or e <= cap)
    ]
    return sorted(ok, key=lambda e: (abs((e - day).days - aim), e))


def gate_expiry(day: date) -> date | None:
    lo, hi, aim = GATE_DTE
    ok = [e for e in clock.candidate_expirations(day, weeks=7) if lo <= (e - day).days <= hi]
    return min(ok, key=lambda e: (abs((e - day).days - aim), e)) if ok else None


def split_excluded_weeks(sessions: list[date], split_days: list[date]) -> set[date]:
    """Week-ends of every skeleton long cycle that holds a split ex-date: dropped for every arm."""
    ends = week_ends(sessions)
    out: set[date] = set()
    for d0, _exp, c in long_cycles(sessions):
        if any(d0 <= s <= c for s in split_days):
            k0, k1 = d0.isocalendar()[:2], c.isocalendar()[:2]
            out |= {e for e in ends if k0 <= e.isocalendar()[:2] <= k1}
    return out


# ------------------------------------------------------------------------------------------------
# Pure: quotes, greeks, costs.

Quotes = dict  # {(strike, "C" | "P"): (bid, ask)}


def usable(bid, ask) -> bool:
    """`core.streamcache.usable_quote`'s test: both sides present, not crossed, a positive ask."""
    return bid is not None and ask is not None and ask > 0 and bid <= ask


def solved_vol(spot, strike, years, mid, right, rate, q) -> float | None:
    """The implied volatility of a mid, or None when no volatility prices it (a mid under the
    model's floor, as a deep in-the-money American call can quote): pmcc skips a strike with no delta."""
    if years <= 0 or mid is None or mid <= 0 or spot <= 0:
        return None
    vol = bs.implied_vol(spot, strike, years, mid, right, rate, q)
    if vol is None or vol <= 0.0005:
        return None
    if abs(bs.price(spot, strike, years, vol, right, rate, q) - mid) > max(0.01, 0.01 * mid):
        return None
    return vol


def realised_vol(closes: list[float]) -> float | None:
    """Annualised close-to-close volatility of `closes` (21 closes make 20 returns)."""
    if len(closes) < 2 or any(c <= 0 for c in closes):
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    n = len(rets)
    mean = sum(rets) / n
    return math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1) * 252) if n > 1 else None


def streamer_symbol(exp: date, right: str, strike: float) -> str:
    return f"{exp.isoformat()}|{right}|{strike:g}"


def nearest_strike_index(strikes: list[float], target: float) -> int:
    return min(range(len(strikes)), key=lambda i: (abs(strikes[i] - target), strikes[i]))


# ------------------------------------------------------------------------------------------------
# The market: one symbol's data behind functions, so a test supplies invented chains.


@dataclass
class Market:
    name: str
    chain_symbol: str
    style: str
    fee_symbol: str
    kind: str
    sessions: list[date]  # trading sessions with a close, inside the window
    closes: dict[date, float]  # unadjusted closes, warm-up included
    quote_fn: Callable[[date, date], Quotes]
    rate_fn: Callable[[date], float] = lambda d: 0.0
    ex_dates: list[date] = field(default_factory=list)
    dividends: list[tuple[date, float]] = field(default_factory=list)
    report_dates: list[date] = field(default_factory=list)
    split_days: list[date] = field(default_factory=list)
    _bar_days: list[date] = field(default_factory=list)
    _chains: dict = field(default_factory=dict)
    _greeks: dict = field(default_factory=dict)

    def __post_init__(self):
        self._bar_days = sorted(self.closes)

    def chain(self, day: date, exp: date) -> Quotes:
        key = (day, exp)
        if key not in self._chains:
            if len(self._chains) > 96:
                self._chains.clear()
            self._chains[key] = self.quote_fn(day, exp)
        return self._chains[key]

    def div_yield(self, day: date) -> float:
        lo = day - timedelta(days=365)
        paid = sum(a for d, a in self.dividends if lo < d <= day)
        s = self.closes.get(day)
        return paid / s if s else 0.0

    def greeks(self, day: date, exp: date, right: str) -> dict[float, tuple[float, float]]:
        """{strike: (delta, implied vol)} for the strikes with a usable quote and a solvable vol."""
        key = (day, exp, right)
        if key in self._greeks:
            return self._greeks[key]
        s, years = self.closes[day], (exp - day).days / 365.0
        r, q = self.rate_fn(day), self.div_yield(day)
        out = {}
        for (strike, rt), (bid, ask) in self.chain(day, exp).items():
            if rt != right or not usable(bid, ask) or not 0.25 * s <= strike <= 2.0 * s:
                continue
            vol = solved_vol(s, strike, years, (bid + ask) / 2.0, right, r, q)
            if vol is None:
                continue
            d = bs.delta(s, strike, years, vol, right, r, q)
            if d is not None:
                out[strike] = (d, vol)
        if len(self._greeks) > 4000:
            self._greeks.clear()
        self._greeks[key] = out
        return out

    def view(self, day: date, exp: date) -> tuple[list[dict], dict, dict]:
        """Calls of one expiry as pmcc's selectors take them: (entries, quotes, greeks)."""
        entries, quotes, greeks = [], {}, {}
        g = self.greeks(day, exp, "C")
        for (strike, rt), (bid, ask) in sorted(self.chain(day, exp).items()):
            if rt != "C" or not usable(bid, ask):
                continue
            sym = streamer_symbol(exp, "C", strike)
            entries.append(
                {"option_type": "call", "streamer_symbol": sym, "occ_symbol": sym, "strike_price": strike}
            )
            quotes[sym] = {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0}
            if strike in g:
                greeks[sym] = {"delta": g[strike][0], "iv": g[strike][1]}
        return entries, quotes, greeks

    def quote(self, day: date, exp: date, strike: float, right: str) -> dict | None:
        row = self.chain(day, exp).get((strike, right))
        if row is None or not usable(*row):
            return None
        return {"bid": row[0], "ask": row[1], "mid": (row[0] + row[1]) / 2.0}

    def last_closes(self, day: date, n: int) -> list[float] | None:
        i = bisect.bisect_right(self._bar_days, day)
        days = self._bar_days[max(0, i - n) : i]
        if len(days) < n or days[-1] != day:
            return None
        return [self.closes[d] for d in days]

    def rv(self, day: date, returns: int = 20) -> float | None:
        """Realised volatility over `returns` close-to-close returns, none across a split."""
        i = bisect.bisect_right(self._bar_days, day)
        days = self._bar_days[max(0, i - returns - 1) : i]
        if len(days) < returns + 1 or days[-1] != day:
            return None
        if any(days[k - 1] < s <= days[k] for k in range(1, len(days)) for s in self.split_days):
            return None
        return realised_vol([self.closes[d] for d in days])

    def span_block(self, day: date, exp: date) -> str | None:
        """Why a short sold on `day` to `exp` is refused: a declared ex-date (physical only) or, for a
        stock, a report date inside the closed span. A cash-settled row is only counted by the caller."""
        if self.style == "physical" and any(day <= x <= exp for x in self.ex_dates):
            return "ex_dividend"
        if self.kind == "stock" and any(day <= x <= exp for x in self.report_dates):
            return "earnings_span"
        return None

    def would_refuse_ex_date(self, day: date, exp: date) -> bool:
        return self.style == "cash" and any(day <= x <= exp for x in self.ex_dates)


# ------------------------------------------------------------------------------------------------
# The replay of one arm on one symbol.


@dataclass
class Pos:
    kind: str
    strike: float
    exp: date
    entry_mid: float
    capital: float
    opened: date
    flow: float = 0.0
    short: dict | None = None


@dataclass
class Run:
    market: str
    spec: str
    scale: float
    snaps: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=lambda: defaultdict(int))
    cycles: list[dict] = field(default_factory=list)
    shorts: list[dict] = field(default_factory=list)
    flagged_weeks: set = field(default_factory=set)
    drops: list[dict] = field(default_factory=list)


def vrp_gate(m: Market, d: date, s: float) -> bool | None:
    """hold-vrp's gate: the mean of the call and put implied volatilities at the strike nearest the
    forward, on the expiry nearest 30 days (21 to 38), strictly above the 20-session realised
    volatility. None when either side cannot be measured."""
    e = gate_expiry(d)
    if e is None:
        return None
    years, r, q = (e - d).days / 365.0, m.rate_fn(d), m.div_yield(d)
    forward = s * math.exp((r - q) * years)
    calls, puts = m.greeks(d, e, "C"), m.greeks(d, e, "P")
    both = sorted(set(calls) & set(puts))
    rv = m.rv(d)
    if not both or rv is None:
        return None
    k = both[nearest_strike_index(both, forward)]
    return (calls[k][1] + puts[k][1]) / 2.0 > rv


def costs_config() -> dict:
    """The module's `tastytrade_costs` overrides, from its deployed config where one can be read."""
    try:
        from cherrypick.pmcc import cli

        return dict(cli.load_config().get("tastytrade_costs") or {})
    except Exception:  # noqa: BLE001 -- the suite defaults are the declared fallback
        return {}


def simulate(
    m: Market,
    spec: Spec,
    *,
    scale: float = 1.0,
    costs: dict | None = None,
    no_short: bool = False,
    drop_cost: str | None = None,
    first: date | None = None,
    last: date | None = None,
) -> Run:
    """One arm on one symbol over its sessions. `no_short` runs the arm's long and management with the
    short switched off (the accounting guard's identity); `drop_cost` forgets one cost kind in the
    components only, to show that guard failing."""
    params = spec_params(spec)
    cfg = {"tastytrade_costs": costs or {}}
    run = Run(m.name, spec.name, scale)
    cnt = run.counts
    sessions = [d for d in m.sessions if (first is None or d >= first) and (last is None or d <= last)]
    ends = set(week_ends(sessions))
    cum = dict.fromkeys(COMPONENTS, 0.0)
    cash_total = [0.0]
    state: dict = {"pos": None, "closed_on": None}
    life_id = [0]
    use_short = spec.short is not None and not no_short
    needs_short = spec.needs_short and use_short

    def book(kind: str, amount: float, pos: Pos | None = None) -> None:
        cash_total[0] += amount
        if kind != drop_cost:
            cum[kind] += amount
        if pos is not None:
            pos.flow += amount

    def fill_costs(bid, ask, *, opening: bool, selling: bool) -> tuple[float, float]:
        fee = engine.leg_fee(m.fee_symbol, 1, opening=opening, selling=selling)
        slip = core_fees.slippage_dollars([{"bid": bid, "ask": ask}], 1, cfg)
        return fee * scale, slip * scale

    def pay_costs(q: dict, pos: Pos, *, opening: bool, selling: bool) -> None:
        fee, slip = fill_costs(q["bid"], q["ask"], opening=opening, selling=selling)
        book("fees", -fee, pos)
        book("slippage", -slip, pos)

    def refuse(reason: str) -> None:
        cnt["refused_" + reason] += 1

    def marks(pos: Pos | None, d: date) -> tuple[float | None, float | None, bool]:
        """(long mark, short mark, complete) in dollars; a held leg without a quote is incomplete."""
        if pos is None:
            return 0.0, 0.0, True
        right = "P" if pos.kind == "put" else "C"
        lq = m.quote(d, pos.exp, pos.strike, right)
        sq = m.quote(d, pos.short["exp"], pos.short["K"], "C") if pos.short else None
        ok = lq is not None and (pos.short is None or sq is not None)
        return (
            100.0 * lq["mid"] if lq else None,
            100.0 * sq["mid"] if sq else (0.0 if pos.short is None else None),
            ok,
        )

    # ---------------------------------------------------------------- selling a short
    def sale_expiry(d: date, pos: Pos) -> date | None:
        if spec.short == "weekly":
            pick = clock.short_expiration(d, params, cap=pos.exp.isoformat())
            if pick is None:
                return None
            exp = date.fromisoformat(pick["short_expiration"])
            reason = m.span_block(d, exp)
            if reason:
                refuse(reason)
                return None
            if m.would_refuse_ex_date(d, exp):
                cnt["would_refuse_ex_date"] += 1
            return exp
        for exp in monthly_expiries(d, pos.exp):  # nearest 37 first, the first that clears
            reason = m.span_block(d, exp)
            if reason is None:
                if m.would_refuse_ex_date(d, exp):
                    cnt["would_refuse_ex_date"] += 1
                return exp
        refuse("monthly_no_clear_expiry")
        return None

    def sell_short(d: date, s: float, pos: Pos, buyback: dict | None = None) -> bool:
        """Sell the arm's next short against `pos`. A refusal is counted and returns False."""
        if spec.friday_gate:
            if d not in ends:
                return False
            ok = vrp_gate(m, d, s)
            if ok is None:
                cnt["gate_unmeasured"] += 1
                return False
            if not ok:
                cnt["gate_closed_weeks"] += 1
                return False
        exp = sale_expiry(d, pos)
        if exp is None:
            return False
        entries, quotes, greeks = m.view(d, exp)
        if not entries:
            refuse("short_chain_missing")
            return False
        snap = {
            "spot": s,
            "short_chain": entries,
            "quotes": quotes,
            "greeks": greeks,
            "short_expiration": exp.isoformat(),
            "short_dte": (exp - d).days,
        }
        plan = engine.plan_short(snap, params, long_strike=pos.strike, buyback=buyback)
        if not plan["ok"]:
            refuse(plan["reason"])
            return False
        open_short(d, pos, exp, plan["leg"]["strike"], plan["mid"], plan["tv"])
        return True

    def open_short(d: date, pos: Pos, exp: date, strike: float, mid: float, tv: float) -> None:
        q = m.quote(d, exp, strike, "C")
        life_id[0] += 1
        book("premium", 100.0 * mid, pos)
        pay_costs(q, pos, opening=True, selling=True)
        pos.short = {"K": strike, "exp": exp, "entry_tv": tv, "id": life_id[0]}
        run.shorts.append({"id": life_id[0], "sold": d.isoformat(), "exp": exp.isoformat(), "priced": True})
        cnt["shorts_sold"] += 1

    def buy_back(d: date, pos: Pos, q: dict) -> None:
        book("short_mtm", -100.0 * q["mid"], pos)
        pay_costs(q, pos, opening=False, selling=False)
        pos.short = None

    def expire_unbought(d: date, s: float, pos: Pos) -> None:
        intrinsic = max(0.0, s - pos.short["K"])
        book("short_mtm", -100.0 * intrinsic, pos)
        if intrinsic > 0:
            book("settlement", -engine.settlement_fee(1) * scale, pos)
        cnt["expired_unbought"] += 1
        pos.short = None

    # ---------------------------------------------------------------- closing and entering
    def close_all(d: date, pos: Pos, reason: str) -> bool:
        right = "P" if pos.kind == "put" else "C"
        lq = m.quote(d, pos.exp, pos.strike, right)
        sq = m.quote(d, pos.short["exp"], pos.short["K"], "C") if pos.short else None
        if lq is None or (pos.short is not None and sq is None):
            cnt["close_unquoted"] += 1
            return False
        if pos.kind == "call" and management.short_execution_gate(
            [lq] + ([sq] if sq else []), params, now=at(d)
        ):
            cnt["exit_blocked"] += 1
            return False
        if pos.short is not None:
            buy_back(d, pos, sq)
        if pos.kind == "put":
            book("short_mtm", -100.0 * lq["mid"], pos)
            pay_costs(lq, pos, opening=False, selling=False)
        else:
            book("long", 100.0 * lq["mid"], pos)
            pay_costs(lq, pos, opening=False, selling=True)
        run.cycles.append({"day": d.isoformat(), "reason": reason, "opened": pos.opened.isoformat()})
        cnt["closed_" + reason] += 1
        state["pos"], state["closed_on"] = None, d
        return True

    def at(d: date) -> datetime:
        mins = clock.session_close_min(d) - MINUTES_BEFORE_CLOSE
        return datetime(d.year, d.month, d.day, mins // 60, mins % 60)

    def enter_call(d: date, s: float) -> None:
        lexp = leap_for(d)
        if lexp is None:
            return refuse("no_leap_listed")
        entries, quotes, greeks = m.view(d, lexp)
        if not entries:
            return refuse("no_long_chain")
        if needs_short:
            sexp = sale_expiry(d, Pos("call", 0.0, lexp, 0.0, 0.0, d))
            if sexp is None:
                return refuse("no_short_expiry")
            s_entries, s_quotes, s_greeks = m.view(d, sexp)
            if not s_entries:
                return refuse("short_chain_missing")
            plan = engine.plan_entry(
                {
                    "symbol": m.fee_symbol,
                    "spot": s,
                    "long_chain": entries,
                    "short_chain": s_entries,
                    "quotes": {**quotes, **s_quotes},
                    "greeks": {**greeks, **s_greeks},
                    "short_expiration": sexp.isoformat(),
                    "long_expiration": lexp.isoformat(),
                    "short_dte": (sexp - d).days,
                    "long_dte": (lexp - d).days,
                },
                params,
            )
            if not plan["ok"]:
                return refuse(plan["reason"])
            p = plan["plan"]
            k, mid = p["long_strike"], p["long_mid"]
            short_pick = (sexp, p["short_strike"], p["short_mid"], p["short_tv"])
        else:
            pick = engine.select_long(entries, quotes, greeks, s, params)
            if not pick["ok"]:
                return refuse(pick["reason"])
            q = quotes[pick["entry"]["streamer_symbol"]]
            pct = engine._spread_pct(q)
            if pct is not None and pct > params["max_leg_spread_pct"]:
                return refuse("spread_too_wide")
            k, mid, short_pick = pick["strike"], pick["mid"], None
        lq = m.quote(d, lexp, k, "C")
        pos = Pos("call", k, lexp, mid, 100.0 * mid, d)
        book("long", -100.0 * mid, pos)
        pay_costs(lq, pos, opening=True, selling=False)
        state["pos"] = pos
        cnt["entries"] += 1
        if short_pick is not None:
            sexp, sk, smid, stv = short_pick
            open_short(d, pos, sexp, sk, smid, stv)
        elif use_short:
            sell_short(d, s, pos)

    def enter_put(d: date, s: float) -> None:
        exp = leap_for(d)
        if exp is None:
            return refuse("no_leap_listed")
        g = m.greeks(d, exp, "P")
        cands = [(abs(abs(dl) - PUT_DELTA), -k, k) for k, (dl, _v) in g.items() if k < s]
        if not cands:
            return refuse("no_put_delta")
        k = min(cands)[2]
        q = m.quote(d, exp, k, "P")
        pct = engine._spread_pct(q)
        if pct is not None and pct > params["max_leg_spread_pct"]:
            return refuse("spread_too_wide")
        pos = Pos("put", k, exp, q["mid"], 100.0 * k, d)
        book("premium", 100.0 * q["mid"], pos)
        pay_costs(q, pos, opening=True, selling=True)
        state["pos"] = pos
        cnt["entries"] += 1

    # ---------------------------------------------------------------- one session
    def manage_put(d: date, pos: Pos) -> None:
        if (pos.exp - d).days <= LONG_CLOSE_DTE:
            close_all(d, pos, "long_roll_due")

    def manage_call(d: date, s: float, pos: Pos) -> None:
        lq = m.quote(d, pos.exp, pos.strike, "C")
        sh = pos.short
        sq = m.quote(d, sh["exp"], sh["K"], "C") if sh else None
        if sh is not None and d >= sh["exp"] and sq is None:
            cnt["buyback_unquoted"] += 1
            expire_unbought(d, s, pos)
            sh = None
        pnl = None
        if lq is not None and (sh is None or sq is not None):
            pnl = {"net": pos.flow + 100.0 * lq["mid"] - (100.0 * sq["mid"] if sq else 0.0)}
        tv = engine.short_time_value(sq["mid"], s, sh["K"]) if sh and sq else None
        if (
            sh
            and sq
            and s > sh["K"]
            and management.assignment_exposed(tv, params, settlement_style="physical")
        ):
            cnt["assignment_exposed_sessions"] += 1
            run.flagged_weeks.add(d.isocalendar()[:2])
        decision = management.evaluate_held_long(
            {"long_entry_mid": pos.entry_mid, "quantity": 1},
            params,
            now=at(d),
            short=None
            if sh is None
            else {
                "strike": sh["K"],
                "expiration": sh["exp"].isoformat(),
                "tv": tv,
                "entry_tv": sh["entry_tv"],
            },  # fmt: skip
            spot=s,
            pnl=pnl,
            long_dte=(pos.exp - d).days,
            rolled_today=False,
            session_close_min=clock.session_close_min(d),
            settlement_style=m.style,
        )
        if decision.reason == "unpriced_mark":
            cnt["unpriced_sessions"] += 1
            return
        if decision.reason in ("stop_loss", "long_roll_due"):
            close_all(d, pos, decision.reason)
        elif decision.reason == "no_short" and use_short:
            sell_short(d, s, pos)
        elif decision.reason == "expiry":
            roll_expiring(d, s, pos, sq)
        elif decision.action == "roll_short":
            roll_early(d, s, pos, sq, decision.reason)

    def roll_expiring(d: date, s: float, pos: Pos, sq: dict) -> None:
        """The ira account's expiry-day roll: bought back at the close at any moneyness, the next sold."""
        if management.short_execution_gate([sq], params, now=at(d)):
            cnt["buyback_blocked"] += 1
            return expire_unbought(d, s, pos)
        buy_back(d, pos, sq)
        cnt["expiry_rolls"] += 1
        sell_short(d, s, pos)

    def roll_early(d: date, s: float, pos: Pos, sq: dict, reason: str) -> None:
        """An early roll is one ticket: both legs must clear pmcc's spread test or nothing happens."""
        exp = sale_expiry(d, pos)
        if exp is None:
            return
        entries, quotes, greeks = m.view(d, exp)
        plan = engine.plan_short(
            {"spot": s, "short_chain": entries, "quotes": quotes, "greeks": greeks,
             "short_expiration": exp.isoformat(), "short_dte": (exp - d).days},
            params, long_strike=pos.strike, buyback=sq,
        )  # fmt: skip
        if not plan["ok"]:
            refuse("early_roll_" + plan["reason"])
            return
        buy_back(d, pos, sq)
        open_short(d, pos, exp, plan["leg"]["strike"], plan["mid"], plan["tv"])
        cnt["early_rolls_" + reason] += 1

    def snapshot(d: date) -> None:
        pos = state["pos"]
        lm, sm, ok = marks(pos, d)
        if not ok:
            cnt["unpriced_weeks"] += 1
            if pos is not None and pos.short is not None:
                for life in run.shorts:
                    if life["id"] == pos.short["id"]:
                        life["priced"] = False
            run.snaps.append({"end": d, "priced": False, "comps": None, "cap": pos.capital, "has_pos": True})
            return
        comps = dict(cum)
        if pos is not None:
            comps["long"] += lm if pos.kind == "call" else 0.0
            comps["short_mtm"] -= sm if pos.kind == "call" else lm
        run.snaps.append(
            {"end": d, "priced": True, "comps": comps, "cap": pos.capital if pos else None,
             "has_pos": pos is not None, "cash": cash_total[0],
             "marks": (lm if pos and pos.kind == "call" else 0.0)
             - (sm if pos and pos.kind == "call" else (lm if pos else 0.0))}
        )  # fmt: skip

    for d in sessions:
        s = m.closes[d]
        pos = state["pos"]
        if pos is not None and any(pos.opened <= x <= d for x in m.split_days) and d in m.split_days:
            run.drops.append({"day": d.isoformat(), "opened": pos.opened.isoformat()})
            state["pos"], state["closed_on"] = None, d
            pos = None
        if pos is None:
            if state["closed_on"] is None or d > state["closed_on"]:
                if spec.kind == "put":
                    enter_put(d, s)
                else:
                    enter_call(d, s)
        elif pos.kind == "put":
            manage_put(d, pos)
        else:
            manage_call(d, s, pos)
        if d in ends:
            snapshot(d)
    return run


# ------------------------------------------------------------------------------------------------
# Pure: weeks, pairs, judged-ness.


def measured_weeks(run: Run, excluded: set[date] = frozenset()) -> dict[date, dict]:
    """{week end: {"r", "comps"}} for the weeks the arm held a position at the previous close and every
    held leg was quoted at both closes. Every other week is counted by `week_ledger`, never zero."""
    out = {}
    for prev, cur in zip(run.snaps, run.snaps[1:], strict=False):
        if cur["end"] in excluded or not prev["has_pos"] or not prev["priced"] or not cur["priced"]:
            continue
        cap = prev["cap"]
        if not cap:
            continue
        d = {k: cur["comps"][k] - prev["comps"][k] for k in COMPONENTS}
        out[cur["end"]] = {"r": sum(d.values()) / cap, "comps": {k: v / cap for k, v in d.items()}}
    return out


def week_ledger(run: Run, excluded: set[date], lo: date, hi: date) -> dict:
    """How the weeks of [lo, hi] split: measured, flat (no position at the previous close), unpriced
    (a held leg without a quote), excluded (a split cycle)."""
    got = measured_weeks(run, excluded)
    out = dict.fromkeys(("measured", "flat", "unpriced", "excluded"), 0)
    for prev, cur in zip(run.snaps, run.snaps[1:], strict=False):
        if not lo <= cur["end"] <= hi:
            continue
        if cur["end"] in excluded:
            out["excluded"] += 1
        elif cur["end"] in got:
            out["measured"] += 1
        elif not prev["has_pos"]:
            out["flat"] += 1
        else:
            out["unpriced"] += 1
    return out


def coverage_check(run: Run, excluded: set[date] = frozenset()) -> list[str]:
    """The coverage guard: an unpriced snapshot never yields a return, and every unpriced week is
    counted. Returns the violations."""
    bad = []
    got = measured_weeks(run, excluded)
    for prev, cur in zip(run.snaps, run.snaps[1:], strict=False):
        if (not cur["priced"] or not prev["priced"]) and cur["end"] in got:
            bad.append(f"{cur['end']}: a return from an unpriced mark")
    if sum(1 for s in run.snaps if not s["priced"]) != run.counts.get("unpriced_weeks", 0):
        bad.append("unpriced snapshots and the unpriced_weeks count disagree")
    return bad


def accounting_check(run: Run, tolerance: float = 0.005) -> list[str]:
    """The accounting guard: rows add up. At every priced snapshot the six components sum to the
    independently kept cash flows plus the legs' marks, to the cent."""
    bad = []
    for sn in run.snaps:
        if sn["priced"] and abs(sum(sn["comps"].values()) - (sn["cash"] + sn["marks"])) > tolerance:
            have, want = sum(sn["comps"].values()), sn["cash"] + sn["marks"]
            bad.append(f"{sn['end']}: components {have:.4f} against {want:.4f}")
    return bad


def identity_check(arm: Run, base: Run, tolerance: float = 0.005) -> list[str]:
    """An arm with its short switched off equals long-alone, week by week to the cent: the arm is
    long-alone plus the short, and nothing else."""
    a = {s["end"]: s for s in arm.snaps if s["priced"]}
    bad = []
    for sn in base.snaps:
        o = a.get(sn["end"])
        if sn["priced"] and (
            o is None or abs(sum(o["comps"].values()) - sum(sn["comps"].values())) > tolerance
        ):
            bad.append(f"{sn['end']}: differs from long-alone")
    return bad


def paired(a: dict[date, dict], b: dict[date, dict], lo: date, hi: date) -> dict[date, float]:
    return {w: a[w]["r"] - b[w]["r"] for w in sorted(a.keys() & b.keys()) if lo <= w <= hi}


def mean(xs) -> float | None:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def pooled_mean(per_sym: dict[str, dict[date, float]]) -> float | None:
    """The equal-weight mean across symbols of each symbol's mean."""
    return mean(m for m in (mean(v.values()) for v in per_sym.values()) if m is not None)


def max_drawdown(returns: dict[date, float]) -> float:
    peak = level = worst = 0.0
    for w in sorted(returns):
        level += returns[w]
        peak = max(peak, level)
        worst = min(worst, level - peak)
    return worst


def funds_ahead(per_sym: dict[str, dict[date, float]]) -> tuple[int, int]:
    means = [mean(v.values()) for v in per_sym.values() if v]
    return sum(1 for x in means if x is not None and x > 0), len(means)


def two_thirds(n_ahead: int, n: int) -> bool:
    return n > 0 and n_ahead >= math.ceil(TWO_THIRDS * n)


def best_year(per_sym: dict[str, dict[date, float]]) -> int | None:
    """The calendar year with the highest pooled mean paired difference."""
    by_year = defaultdict(list)
    for v in per_sym.values():
        for w, x in v.items():
            by_year[w.year].append(x)
    return max(by_year, key=lambda y: mean(by_year[y]), default=None) if by_year else None


def without_year(per_sym: dict[str, dict[date, float]], year: int | None) -> dict[str, dict[date, float]]:
    return {s: {w: x for w, x in v.items() if w.year != year} for s, v in per_sym.items()}


def regression_alpha(diffs: dict[date, float], moves: dict[date, float]) -> float | None:
    """The intercept, a year, of the paired difference on the underlying's weekly return."""
    ws = sorted(diffs.keys() & moves.keys())
    if len(ws) < 20:
        return None
    x, y = [moves[w] for w in ws], [diffs[w] for w in ws]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx == 0:
        return None
    beta = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / sxx
    return (my - beta * mx) * 52


# ------------------------------------------------------------------------------------------------
# Pure: the bootstrap (weeks resampled jointly across symbols).


def resample_indices(
    n: int, resamples: int = RESAMPLES, block: float = BLOCK, seed: int = SEED
) -> np.ndarray:
    rng = random.Random(seed)
    return np.array([core_stats.stationary_indices(n, block, rng) for _ in range(resamples)], dtype=np.int32)


def bootstrap_pooled(per_sym: dict[str, dict[date, float]], grid: list[date], idx: np.ndarray) -> dict:
    """The pooled equal-weight mean, its one-sided 95% lower bound and p-value (the share of resamples
    at or under zero). `grid` is the calendar weeks, `idx` resamples of range(len(grid))."""
    syms = [s for s, v in per_sym.items() if v]
    if not syms or not len(grid):
        return {"mean": None, "lower": None, "p": None}
    pos = {w: i for i, w in enumerate(grid)}
    vals = np.zeros((len(syms), len(grid)))
    have = np.zeros((len(syms), len(grid)))
    for r, s in enumerate(syms):
        for w, x in per_sym[s].items():
            if w in pos:
                vals[r, pos[w]], have[r, pos[w]] = x, 1.0
    out = []
    for lo in range(0, len(idx), 2000):
        chunk = idx[lo : lo + 2000]
        total = vals[:, chunk].sum(axis=2)  # symbols x resamples
        n = have[:, chunk].sum(axis=2)
        means = np.where(n > 0, total / np.maximum(n, 1), np.nan)
        out.append(np.nanmean(means, axis=0))
    draws = np.concatenate(out)
    return {
        "mean": pooled_mean(per_sym),
        "lower": float(np.percentile(draws, 100 * ALPHA)),
        "p": float((np.sum(draws <= 0) + 1) / (len(draws) + 1)),
    }


# ------------------------------------------------------------------------------------------------
# Judged-ness: the minimum data, per symbol and half.


def judged_status(
    arm: Run, base: Run, excluded: set[date], half: str, lo: date, hi: date, *, monthly: bool = False
) -> dict:
    """Whether a symbol meets the floors for an arm in a half: completed long cycles, priced weeks (priced
    monthly cycles for the monthly arm) and no more than 20% of the half's weeks missing."""
    got = paired(measured_weeks(arm, excluded), measured_weeks(base, excluded), lo, hi)
    weeks = [s["end"] for s in base.snaps if lo <= s["end"] <= hi]
    cycles = sum(1 for c in base.cycles if lo.isoformat() <= c["day"] <= hi.isoformat())
    lives = [x for x in arm.shorts if lo.isoformat() <= x["sold"] <= hi.isoformat()]
    priced_cycles = sum(1 for x in lives if x["priced"])
    total = max(len(weeks), 1)
    missing = 1 - len(got) / total
    floor = MIN_CYCLES_MONTHLY[half] if monthly else MIN_WEEKS[half]
    have = priced_cycles if monthly else len(got)
    reasons = []
    if cycles < MIN_CYCLES:
        reasons.append(f"{cycles} long cycles (< {MIN_CYCLES})")
    if have < floor:
        reasons.append(f"{have} priced {'cycles' if monthly else 'weeks'} (< {floor})")
    if missing > MAX_MISSING:
        reasons.append(f"{missing:.0%} of weeks missing (> {MAX_MISSING:.0%})")
    return {"judged": not reasons, "reasons": reasons, "cycles": cycles, "priced": have, "weeks": len(got)}


# ------------------------------------------------------------------------------------------------
# Guards.


def reproduction_check(rows: list[dict], select: Callable[[dict], tuple[float, float] | None]) -> dict:
    """Selection against the paper ledger: for each recorded entry the replay's chosen long and short
    strike must each sit within one listed strike of the recorded one, on at least 80% of entries.
    `select(row)` returns the replay's (long, short) strikes with the row's `strikes` list in play."""
    n = ok = 0
    for row in rows:
        got = select(row)
        n += 1
        if got is None:
            continue
        strikes = row["strikes"]
        if all(
            abs(nearest_strike_index(strikes, a) - nearest_strike_index(strikes, b)) <= 1
            for a, b in zip(got, (row["long"], row["short"]), strict=True)
        ):
            ok += 1
    ratio = ok / n if n else None
    return {
        "entries": n,
        "matched": ok,
        "ratio": ratio,
        "ok": ratio is not None and ratio >= REPRODUCTION_FLOOR,
    }


def wput_check(
    m: Market, wput: dict[date, float], lo: date, hi: date, tolerance: float = WPUT_TOLERANCE
) -> dict:
    """The weekly at-the-money put on the symbol's chains, costs removed, on T-bill collateral, against
    the Cboe WPUT index over the same dates: CAGR within `tolerance`."""
    ends = [d for d in week_ends([s for s in m.sessions if lo <= s <= hi])]
    nav, first, last, used = 1.0, None, None, 0
    for d in ends:
        e = clock.weekly_expiration(d, 1)
        if e is None or e not in m.closes:
            continue
        puts = sorted(k for (k, r), q in m.chain(d, e).items() if r == "P" and usable(*q))
        if not puts:
            continue
        k = puts[nearest_strike_index(puts, m.closes[d])]
        q = m.quote(d, e, k, "P")
        payoff = max(k - m.closes[e], 0.0)
        nav *= 1.0 + (q["mid"] - payoff) / k + m.rate_fn(d) * (e - d).days / 365.0
        first = first or d
        last = e
        used += 1
    if not used:
        return {"ok": False, "reason": "no weekly put chains", "weeks": 0}
    a = max((w for w in wput if w <= first), default=None)
    b = max((w for w in wput if w <= last), default=None)
    if a is None or b is None or a == b:
        return {"ok": False, "reason": "WPUT has no print for the span", "weeks": used}
    years = (last - first).days / 365.25
    replay, bench = nav ** (1 / years) - 1, (wput[b] / wput[a]) ** (1 / years) - 1
    return {
        "ok": abs(replay - bench) <= tolerance and used >= WPUT_COVERAGE * len(ends),
        "weeks": used,
        "weeks_possible": len(ends),
        "replay_cagr": replay,
        "wput_cagr": bench,
        "diff": replay - bench,
    }


# ------------------------------------------------------------------------------------------------
# The plan: the fetch-chain-paths request files.


def listed_window(symbol: str) -> tuple[date, date]:
    return max(DESIGN[0], LISTED_FROM.get(symbol, DESIGN[0])), HOLDOUT[1]


def path_requests(symbol: str, chain_symbol: str | None = None) -> list[dict]:
    """Every path request a full study of `symbol` needs, in the order of the plan's table: the
    year-long long and its put, each weekly short's path from 11 days out, the monthly short's earlier
    days, and the hold-vrp gate's Friday chain."""
    chain_symbol = chain_symbol or SYMBOLS[symbol][0]
    a, b = listed_window(symbol)
    sessions = sessions_between(a, b)
    out: list[dict] = []

    def add(kind: str, exp: date, start: date, through: date) -> None:
        if start <= through:
            out.append({"symbol": chain_symbol, "expiration": exp.isoformat(), "start": start.isoformat(),
                        "through": through.isoformat(), "kind": kind})  # fmt: skip

    cycles = long_cycles(sessions)
    for d0, exp, close in cycles:
        add("long", exp, d0, min(close, b))
    seen_weekly = set()
    for k in range(-1, (b - a).days // 7 + 3):
        e = clock.weekly_expiration(a + timedelta(days=7 * k), 0)
        if e is None or e in seen_weekly or e < a or e - timedelta(days=SHORT_LEAD) > b:
            continue
        seen_weekly.add(e)
        add("weekly", e, max(a, e - timedelta(days=SHORT_LEAD)), min(e, b))
    for d0, lexp, close in cycles:  # the monthly arm's sale days along the no-stop skeleton
        day = d0
        while day <= min(close, b):
            ex = monthly_expiries(day, lexp)
            if not ex:
                break
            add("monthly", ex[0], day, min(ex[0] - timedelta(days=SHORT_LEAD + 1), b))
            day = ex[0]
    for d in week_ends(sessions):
        g = gate_expiry(d)
        if g is not None:
            add("gate", g, d, d)
    seen, uniq = set(), []
    for r in out:
        key = (r["expiration"], r["start"], r["through"])
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq


def vendor_calls(req: dict) -> int:
    """The vendor calls a path costs: one per seven-day chunk (`chainfetch`)."""
    days = (date.fromisoformat(req["through"]) - date.fromisoformat(req["start"])).days + 1
    return max(1, math.ceil(days / PATH_CALL_DAYS))


def stored_filter(
    reqs: list[dict], answered: set[tuple[str, str]], paths: list[tuple[str, str, str]]
) -> list[dict]:
    """The requests the store does not already answer. `answered` is {(session, expiration)} complete or
    empty for the symbol; `paths` the symbol's complete or empty (expiration, start, through)."""
    out = []
    for r in reqs:
        if any(e == r["expiration"] and s <= r["start"] and t >= r["through"] for e, s, t in paths):
            continue
        days = sessions_between(date.fromisoformat(r["start"]), date.fromisoformat(r["through"]))
        if days and all((d.isoformat(), r["expiration"]) in answered for d in days):
            continue
        out.append(r)
    return out


def plan_counts(per_symbol: dict[str, dict]) -> dict:
    total = {"paths": 0, "paths_net": 0, "calls": 0, "calls_net": 0}
    for v in per_symbol.values():
        for k in total:
            total[k] += v[k]
    return total


# ------------------------------------------------------------------------------------------------
# The stores (licensed data stays under ~/.cherrypick).


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item36"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _chain_db() -> Path:
    from cherrypick.backtest import chainfetch, settings

    return chainfetch.db_path(settings.load(None))


def sql_quote_fn(conn: sqlite3.Connection, symbol: str) -> Callable[[date, date], Quotes]:
    def fn(day: date, exp: date) -> Quotes:
        return {
            (k, r[:1].upper()): (b, a)
            for k, r, b, a in conn.execute(
                "SELECT strike, right, bid, ask FROM eod_chain_quotes WHERE symbol = ? AND session = ? "
                "AND expiration = ?",
                (symbol, day.isoformat(), exp.isoformat()),
            )
        }

    return fn


def stored_for(conn: sqlite3.Connection, chain_symbol: str) -> tuple[set, list]:
    answered = {
        (s, e)
        for s, e in conn.execute(
            "SELECT session, expiration FROM eod_chain_requests "
            "WHERE symbol = ? AND status IN ('complete', 'empty')",
            (chain_symbol,),
        )
    }
    paths = list(
        conn.execute(
            "SELECT expiration, start, through FROM eod_chain_paths "
            "WHERE symbol = ? AND status IN ('complete', 'empty')",
            (chain_symbol,),
        )
    )
    return answered, paths


def cmd_plan(args) -> int:
    out_dir = Path(args.out_dir) if args.out_dir else _out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    db = _chain_db()
    conn = _ro(db) if db.exists() else None
    order = ["XSP(SPY)", "QQQ", "IWM", "GLD", "SLV", "SMH", *STOCKS]
    per_symbol, files = {}, {}
    for name in order:
        reqs = path_requests(name)
        net = stored_filter(reqs, *stored_for(conn, SYMBOLS[name][0])) if conn else reqs
        per_symbol[name] = {
            "chain_symbol": SYMBOLS[name][0],
            "paths": len(reqs),
            "paths_net": len(net),
            "calls": sum(map(vendor_calls, reqs)),
            "calls_net": sum(map(vendor_calls, net)),
            "by_kind_net": {
                k: sum(1 for r in net if r["kind"] == k) for k in ("long", "weekly", "monthly", "gate")
            },
        }
        path = out_dir / f"chain_requests_{SYMBOLS[name][0]}.json"
        path.write_text(json.dumps({"requests": net}, indent=1), encoding="utf-8")
        files[name] = str(path)
    if conn:
        conn.close()
    print(json.dumps({"per_symbol": per_symbol, "total": plan_counts(per_symbol), "files": files}, indent=1))
    return 0


# ------------------------------------------------------------------------------------------------
# The study.


def load_market(name: str, vend: sqlite3.Connection, hist: sqlite3.Connection, report_dates=None) -> Market:
    from cherrypick.technicals import store

    chain_symbol, style, fee_symbol, kind = SYMBOLS[name]
    bars = [b for b in store.raw_bars(hist, chain_symbol) if b.close and b.date >= WARMUP.isoformat()]
    closes = {date.fromisoformat(b.date): b.close for b in bars}
    a, b = listed_window(name)
    sessions = [d for d in sessions_between(a, b) if d in closes]
    divs = [(date.fromisoformat(x.ex_date), x.amount) for x in store.dividends(hist, chain_symbol)[0]]
    splits = [date.fromisoformat(x.ex_date) for x in store.splits(hist, chain_symbol)]
    rates: dict = {}

    def rate(day: date) -> float:
        if day not in rates:
            got = treasury.yield_before(day.isoformat(), "3m")
            rates[day] = got[1] / 100.0 if got else 0.0
        return rates[day]

    return Market(
        name, chain_symbol, style, fee_symbol, kind, sessions, closes, sql_quote_fn(vend, chain_symbol), rate,
        ex_dates=sorted(d for d, _ in divs),
        dividends=divs,
        report_dates=sorted(report_dates or []),
        split_days=sorted(splits),
    )  # fmt: skip


def read_report_dates(symbols) -> dict[str, list[date]]:
    """Report dates from the local Dolt earnings calendar, as `scripts/pmcc_earnings_refresh.py` reads it."""
    import mysql.connector as _mysql

    cn = _mysql.connect(host="127.0.0.1", port=3306, user="root", database="earnings", connection_timeout=15)
    try:
        cur = cn.cursor()
        out = {}
        for s in symbols:
            cur.execute("SELECT date FROM earnings_calendar WHERE act_symbol = %s ORDER BY date", (s,))
            out[s] = [d for (d,) in cur.fetchall()]
        return out
    finally:
        cn.close()


def read_ledger(path: Path) -> list[dict]:
    """The paper ledger's held-long entries since 2026-10-06: symbol, arm, session, expirations, strikes."""
    if not path.exists():
        return []
    conn = _ro(path)
    try:
        rows = conn.execute(
            "SELECT p.symbol, p.arm, p.entry_session, l.expiration, l.strike, s.expiration, s.strike "
            "FROM pmcc_positions p "
            "JOIN pmcc_legs l ON l.position_id = p.position_id AND l.leg_role = 'long_call' "
            "JOIN pmcc_legs s ON s.position_id = p.position_id AND s.leg_role = 'short_call_1' "
            "WHERE p.entry_session >= ? AND p.arm IN ('shield', 'shield_hold', 'shield_guard')",
            (LEDGER_FROM,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [
        {"symbol": a, "arm": b, "session": c, "long_exp": d, "long": e, "short_exp": f, "short": g}
        for a, b, c, d, e, f, g in rows
    ]


def select_for(markets: dict[str, Market], params: dict) -> Callable[[dict], tuple[float, float] | None]:
    """The replay's choice on a ledger row's session, from the stored chains; fills the row's strikes."""

    def go(row: dict) -> tuple[float, float] | None:
        m = markets.get(row["symbol"])
        if m is None:
            return None
        day, lexp, sexp = (date.fromisoformat(row[k]) for k in ("session", "long_exp", "short_exp"))
        if day not in m.closes:
            return None
        s = m.closes[day]
        le, lq, lg = m.view(day, lexp)
        se, sq, sg = m.view(day, sexp)
        if not le or not se:
            return None
        row["strikes"] = sorted(e["strike_price"] for e in le + se)
        lp = engine.select_long(le, lq, lg, s, params)
        if not lp["ok"]:
            return None
        sp = engine.select_short_by_delta(se, sq, sg, s, params, floor_strike=lp["strike"])
        return (lp["strike"], sp["strike"]) if sp["ok"] else None

    return go


def load_wput() -> dict[date, float]:
    from cherrypick.core import home
    from cherrypick.overview import files

    p = home.data_dir("pmcc") / "replay" / "WPUT.csv"
    return dict(files.parse_cboe(p.read_text(encoding="utf-8"))) if p.exists() else {}


def run_checks(runs: dict, markets: dict[str, Market]) -> list[str]:
    """The accounting and coverage guards over finished runs."""
    out = []
    for (name, spec, scale), r in runs.items():
        excl = split_excluded_weeks(markets[name].sessions, markets[name].split_days)
        out += [f"{name} {spec} x{scale}: {x}" for x in (accounting_check(r) + coverage_check(r, excl))[:3]]
    return out


def run_guards(markets: dict[str, Market], costs: dict, ledger: list[dict], wput: dict) -> dict:
    """The four guards, before any arm is read. Reproduction rows exclude XSP(SPY) (its strikes are
    XSP's, a tenth of the index, not SPY's)."""
    out: dict = {}
    spy = markets.get("XSP(SPY)")
    out["wput"] = (
        wput_check(spy, wput, DESIGN[0], HOLDOUT[1])
        if spy and wput
        else {"ok": False, "reason": "unavailable"}
    )
    rows = [dict(r) for r in ledger if r["symbol"] in markets and r["symbol"] != "XSP(SPY)"]
    out["reproduction"] = (
        reproduction_check(rows, select_for(markets, spec_params(SPECS["shield_hold"])))
        if rows
        else {"entries": 0, "ok": False, "reason": "no ledger entries on the covered sessions"}
    )
    problems = []
    for name, m in list(markets.items())[:1]:  # the first symbol; `run_checks` covers every run after
        base = simulate(m, SPECS["long-alone"], costs=costs)
        arm = simulate(m, SPECS["shield_hold"], costs=costs, no_short=True)
        problems += [f"{name}: {x}" for x in identity_check(arm, base)[:3]]
        problems += run_checks({(name, "long-alone", 1.0): base}, markets)
    out["accounting"] = {"ok": not problems, "problems": problems[:20]}
    out["ok"] = all(out[k]["ok"] for k in ("wput", "reproduction", "accounting"))
    return out


def arm_p(baselines: dict) -> float | None:
    """The arm's p-value: the larger of its two baseline p-values (Holm is taken across the arms)."""
    ps = [b["p"] for b in baselines.values()]
    return None if not ps or any(p is None for p in ps) else max(ps)


def design_checks(baselines: dict) -> dict:
    """The design window's clauses, per baseline; an arm passes only if every one holds against both."""
    out = {}
    for base, b in baselines.items():
        s15 = b["scaled_1_5"]
        out[base] = {
            "mean_positive": b["mean"] is not None and b["mean"] > 0,
            "lower_bound_positive": b["lower"] is not None and b["lower"] > 0,
            "two_thirds_of_funds": two_thirds(*b["funds_ahead"]),
            "each_half_positive": all(x is not None and x > 0 for x in b["halves"].values()),
            "scaled_1_5_positive": s15["mean"] is not None
            and s15["mean"] > 0
            and two_thirds(*s15["funds_ahead"]),
            "best_year_removed_positive": b["mean_without_best_year"] is not None
            and b["mean_without_best_year"] > 0,
        }
    return out


def holdout_passes(baselines: dict) -> bool:
    """The holdout clauses against both baselines: pooled above zero, two thirds of the funds, above zero
    at 1.5x costs, and two thirds of the five-year symbol-window cells ahead."""
    return all(
        b["mean"] is not None
        and b["mean"] > 0
        and two_thirds(*b["funds_ahead"])
        and b["scaled_1_5_mean"] is not None
        and b["scaled_1_5_mean"] > 0
        and b["windows_two_thirds"]
        for b in baselines.values()
    )


def analyse(runs: dict, markets: dict[str, Market], guards_ok: bool) -> dict:
    """The verdicts and every reported table from the runs. `runs[(symbol, spec, scale)]`."""
    excluded = {n: split_excluded_weeks(m.sessions, m.split_days) for n, m in markets.items()}
    weekly = {k: measured_weeks(r, excluded[k[0]]) for k, r in runs.items()}
    funds = [f for f in FUNDS if f in markets]

    def series(arm: str, base: str, symbols, lo, hi, scale=1.0):
        return {
            s: paired(weekly[(s, arm, scale)], weekly[(s, base, scale)], lo, hi)
            for s in symbols
            if (s, arm, scale) in weekly and (s, base, scale) in weekly
        }

    status = {}
    for half, (lo, hi) in (("design", DESIGN), ("holdout", HOLDOUT)):
        for arm in ARM_NAMES:
            for s in markets:
                base = runs[(s, "long-alone", 1.0)]
                status[(s, arm, half)] = judged_status(
                    runs[(s, arm, 1.0)], base, excluded[s], half, lo, hi, monthly=arm == "hold-30d-monthly"
                )

    def judged(arm, half):
        dropped = GUARD_DROPPED.get(arm, ())
        return [s for s in funds if status[(s, arm, half)]["judged"] and s not in dropped]

    grid_d = sorted({w for (s, a, sc), wk in weekly.items() for w in wk if DESIGN[0] <= w <= DESIGN[1]})
    idx = (
        resample_indices(len(grid_d), RESAMPLES, BLOCK, SEED) if grid_d else np.zeros((0, 0), dtype=np.int32)
    )
    design: dict = {}
    for arm in ARM_NAMES:
        js = judged(arm, "design")
        d = {"judged_funds": js, "baselines": {}}
        for base in BASELINES:
            ser = series(arm, base, js, *DESIGN)
            boot = bootstrap_pooled(ser, grid_d, idx)
            ahead = funds_ahead(ser)
            halves = {h: pooled_mean(series(arm, base, js, *rng)) for h, rng in HALVES.items()}
            s15 = series(arm, base, js, *DESIGN, scale=1.5)
            by = best_year(ser)
            d["baselines"][base] = {
                **boot,
                "funds_ahead": ahead,
                "halves": halves,
                "scaled_1_5": {"mean": pooled_mean(s15), "funds_ahead": funds_ahead(s15)},
                "best_year": by,
                "mean_without_best_year": pooled_mean(without_year(ser, by)),
                "per_symbol": {s: mean(v.values()) for s, v in ser.items()},
            }
        design[arm] = d
    enough = len(judged("shield_hold", "design")) >= MIN_JUDGED_FUNDS
    pvals = {a: p for a in ARM_NAMES if (p := arm_p(design[a]["baselines"])) is not None}
    holm_ok = core_stats.holm(pvals) if pvals else {}
    adj = core_stats.holm_adjusted(pvals) if pvals else {}
    verdict, notes = {}, {}
    for arm in ARM_NAMES:
        d = design[arm]
        if not guards_ok or len(d["judged_funds"]) < MIN_JUDGED_FUNDS or not enough or arm not in pvals:
            verdict[arm] = "NO VERDICT"
            notes[arm] = (
                "guards failed" if not guards_ok else "fewer than 4 judged funds in the design window"
            )
            d["checks"], d["passes"] = {}, False
            continue
        d["holm_adjusted_p"], d["holm_reject"] = adj[arm], holm_ok[arm]
        d["checks"] = design_checks(d["baselines"])
        d["passes"] = holm_ok[arm] and all(all(c.values()) for c in d["checks"].values())
    holdout: dict = {}
    for arm in ARM_NAMES:
        if not design[arm]["passes"]:
            continue
        js = judged(arm, "holdout")
        h: dict = {"judged_funds": js, "baselines": {}}
        for base in BASELINES:
            ser = series(arm, base, js, *HOLDOUT)
            s15 = series(arm, base, js, *HOLDOUT, scale=1.5)
            cells = []
            for lo, hi in WINDOWS_5Y:
                for v in series(arm, base, funds, lo, hi).values():
                    if len(v) >= MIN_CELL_WEEKS:
                        cells.append(mean(v.values()) > 0)
            h["baselines"][base] = {
                "mean": pooled_mean(ser),
                "funds_ahead": funds_ahead(ser),
                "scaled_1_5_mean": pooled_mean(s15),
                "window_cells": [sum(cells), len(cells)],
                "windows_two_thirds": two_thirds(sum(cells), len(cells)),
                "per_symbol": {s: mean(v.values()) for s, v in ser.items()},
            }
        h["enough"] = len(js) >= MIN_JUDGED_FUNDS
        h["passes"] = h["enough"] and holdout_passes(h["baselines"])
        holdout[arm] = h
        verdict[arm] = "PASS" if h["passes"] else ("NO VERDICT" if not h["enough"] else "FAIL")
    for arm in ARM_NAMES:
        verdict.setdefault(arm, "FAIL")
    caveats = {}
    for arm in holdout:
        if holdout[arm]["passes"]:
            deeper = 0
            for s in funds:
                a = max_drawdown({w: x["r"] for w, x in weekly[(s, arm, 1.0)].items()})
                b = max_drawdown({w: x["r"] for w, x in weekly[(s, "long-alone", 1.0)].items()})
                deeper += a < b
            caveats[arm] = {"deeper_drawdown_than_long_alone_on": deeper, "of": len(funds)}
    rivals: dict = {"by_year": {}, "crash": {}, "pairs": {}}
    for arm in ARM_NAMES:
        js = design[arm]["judged_funds"]
        for base in BASELINES:
            ser = series(arm, base, js, *DESIGN)
            years = sorted({w.year for v in ser.values() for w in v})
            rivals["by_year"][f"{arm} vs {base}"] = {
                y: pooled_mean({s: {w: x for w, x in v.items() if w.year == y} for s, v in ser.items()})
                for y in years
            }
            calm = {s: {w: x for w, x in v.items() if not CRASH[0] <= w <= CRASH[1]} for s, v in ser.items()}
            rivals["crash"][f"{arm} vs {base}"] = {"with": pooled_mean(ser), "without": pooled_mean(calm)}
    for a, b in REPORTED_PAIRS:
        js = [s for s in judged(a, "design") if s in judged(b, "design")]
        rivals["pairs"][f"{a} vs {b}"] = pooled_mean(series(a, b, js, *DESIGN))
    return {
        "verdict": verdict,
        "notes": notes,
        "rivals": rivals,
        "design": design,
        "holdout": holdout,
        "status": {f"{s}|{a}|{h}": v for (s, a, h), v in status.items()},
        "drawdown_caveats": caveats,
        "tables": tables(runs, markets, weekly, excluded),
    }


def tables(runs, markets, weekly, excluded) -> dict:
    """The reported tables, design window only: per symbol and arm, % a year on capital, drawdown,
    the components, the beta-adjusted alpha, trade counts and flags; stocks included."""
    out: dict = {"per_symbol": {}, "counts": {}, "ledger": {}}
    for s, m in markets.items():
        px = {w: m.closes[w] for w in week_ends(m.sessions)}
        ws = sorted(px)
        moves = {b: px[b] / px[a] - 1 for a, b in zip(ws, ws[1:], strict=False)}
        base = weekly[(s, "long-alone", 1.0)]
        for arm in (*ARM_NAMES, *BASELINES, *REPORTED_ONLY):
            wk = {w: x for w, x in weekly[(s, arm, 1.0)].items() if DESIGN[0] <= w <= DESIGN[1]}
            comps = {c: 52 * (mean(x["comps"][c] for x in wk.values()) or 0.0) for c in COMPONENTS}
            diff = paired(weekly[(s, arm, 1.0)], base, *DESIGN)
            out["per_symbol"][f"{s}|{arm}"] = {
                "pct_a_year": None if not wk else 52 * mean(x["r"] for x in wk.values()),
                "weeks": len(wk),
                "drawdown": max_drawdown({w: x["r"] for w, x in wk.items()}),
                "components_a_year": comps,
                "alpha_vs_long_alone": regression_alpha(diff, moves) if arm != "long-alone" else None,
            }
            r = runs[(s, arm, 1.0)]
            out["counts"][f"{s}|{arm}"] = dict(r.counts) | {
                "flagged_weeks": len(r.flagged_weeks),
                "drops": len(r.drops),
            }
            out["ledger"][f"{s}|{arm}"] = week_ledger(r, excluded[s], *DESIGN)
    return out


def cmd_study(args, guards_only: bool = False) -> int:
    from cherrypick.technicals import paths as tpaths
    from cherrypick.technicals import store

    db = _chain_db()
    vend, hist = _ro(db), store.connect(tpaths.history_db())
    names = [args.symbol] if guards_only and getattr(args, "symbol", None) else list(SYMBOLS)
    try:
        reports = read_report_dates(STOCKS)
    except Exception as exc:  # noqa: BLE001 -- stocks are reported only; funds do not need it
        reports, names = {}, [n for n in names if SYMBOLS[n][3] == "fund"]
        print(f"earnings calendar unreadable ({exc}); stocks left out", file=sys.stderr)
    rep = core_progress.Reporter("item36-study")
    rep.phase("markets", total=len(names), unit="symbols")
    markets = {}
    for n in names:
        markets[n] = load_market(n, vend, hist, reports.get(n))
        rep.advance("markets", current=n)
    rep.close_phase("markets")
    costs = costs_config()
    ledger = read_ledger(Path(args.ledger) if getattr(args, "ledger", None) else _ledger_path())
    guards = run_guards(markets, costs, ledger, load_wput())
    if guards_only:
        rep.finish("done")
        print(json.dumps(guards, indent=1, default=str))
        return 0 if guards["ok"] else 1
    specs = (*ARM_NAMES, *BASELINES, *REPORTED_ONLY)
    rep.phase("replays", total=len(markets) * len(specs) * len(COST_SCALES), unit="runs")
    runs = {}
    for n, m in markets.items():
        for spec in specs:
            for sc in COST_SCALES:
                runs[(n, spec, sc)] = simulate(m, SPECS[spec], scale=sc, costs=costs)
                rep.advance("replays", current=f"{n} {spec}")
    rep.finish("done")
    problems = run_checks(runs, markets)
    if problems:
        guards["accounting"] = {"ok": False, "problems": (guards["accounting"]["problems"] + problems)[:20]}
        guards["ok"] = False
    res = analyse(runs, markets, guards["ok"])
    result = {
        "verdict": res["verdict"],
        "window": f"design {DESIGN[0]}..{DESIGN[1]}; holdout {HOLDOUT[0]}..{HOLDOUT[1]}",
        "run_id": f"item36-{date.today().isoformat()}",
        "guards": guards,
        "symbols": list(markets),
        **{k: v for k, v in res.items() if k != "verdict"},
    }
    path = Path(args.json) if args.json else _out_dir() / "results.json"
    path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    path.with_suffix(".md").write_text(report(result), encoding="utf-8")
    print(json.dumps({"verdict": result["verdict"], "file": str(path)}))
    return 0


def _ledger_path() -> Path:
    from cherrypick.core import home

    return home.data_dir("pmcc") / "paper_trades.db"


# ------------------------------------------------------------------------------------------------
# results.md beside results.json.


def _pct(x: float | None) -> str:
    return "" if x is None else f"{100 * x:+.1f}"


def _year(weekly: float | None) -> float | None:
    """A weekly return as a year's (52 weeks)."""
    return None if weekly is None else 52 * weekly


def report(result: dict) -> str:
    v = result["verdict"]
    lines = [
        "# Item 36: pmcc's own rule at real quotes",
        "",
        "Verdicts: " + ", ".join(f"`{a}` {w.lower()}" for a, w in v.items()) + ".",
        "",
        "Six arms against two baselines (`long-alone`, `put-benchmark`), six funds judged, five stocks "
        "reported. A fail reads as no edge of detectable size, not as no edge. "
        "Figures are % a year on capital.",
        "",
        "## Guards",
        "",
    ]
    g = result["guards"]
    for k in ("wput", "reproduction", "accounting"):
        detail = json.dumps({x: y for x, y in g[k].items() if x != "ok"}, default=str)
        lines.append(f"- **{k}**: {'ok' if g[k].get('ok') else 'failed'} {detail}")
    lines += [
        "", "## Design window", "",
        "| arm | verdict | baseline | pooled | lower bound | funds ahead | each half | 1.5x costs "
        "| best year out |",
        "|---|---|---|---|---|---|---|---|---|",
    ]  # fmt: skip
    for arm, d in result["design"].items():
        for base, b in d["baselines"].items():
            halves = " / ".join(_pct(_year(x)) for x in b["halves"].values())
            lines.append(
                f"| {arm} | {v[arm].lower()} | {base} | {_pct(_year(b['mean']))} | {_pct(_year(b['lower']))} "
                f"| {b['funds_ahead'][0]} of {b['funds_ahead'][1]} | {halves} "
                f"| {_pct(_year(b['scaled_1_5']['mean']))} | {_pct(_year(b['mean_without_best_year']))} |"
            )
    lines += ["", "## Holdout (read once, arms that passed the design only)", ""]
    if not result["holdout"]:
        lines.append("No arm passed the design window, so the holdout was not read.")
    for arm, h in result["holdout"].items():
        for base, b in h["baselines"].items():
            ahead, n = b["funds_ahead"]
            cells, total = b["window_cells"]
            lines.append(
                f"- `{arm}` against `{base}`: {_pct(_year(b['mean']))} a year, {ahead} of {n} funds ahead, "
                f"five-year cells {cells} of {total}."
            )
    for arm, c in result["drawdown_caveats"].items():
        deeper = c["deeper_drawdown_than_long_alone_on"]
        lines.append(f"- Caveat: `{arm}` drew down deeper than `long-alone` on {deeper} of {c['of']} funds.")
    lines += [
        "",
        "## Per symbol, design window",
        "",
        "| symbol | arm | % a year | weeks | max drawdown | alpha vs long-alone |",
        "|---|---|---|---|---|---|",
    ]
    for key, t in result["tables"]["per_symbol"].items():
        s, arm = key.split("|")
        lines.append(
            f"| {s} | {arm} | {_pct(t['pct_a_year'])} | {t['weeks']} | {_pct(t['drawdown'])} "
            f"| {_pct(t['alpha_vs_long_alone'])} |"
        )
    lines += [
        "",
        "## Components, % a year on capital",
        "",
        "| symbol | arm | " + " | ".join(COMPONENTS) + " |",
        "|---|---|" + "---|" * len(COMPONENTS),
    ]
    for key, t in result["tables"]["per_symbol"].items():
        s, arm = key.split("|")
        lines.append(
            f"| {s} | {arm} | " + " | ".join(_pct(t["components_a_year"][c]) for c in COMPONENTS) + " |"
        )
    lines += [
        "",
        "## Weeks and refusals",
        "",
        "| symbol | arm | measured | flat | unpriced | excluded |",
        "|---|---|---|---|---|---|",
    ]
    for key, t in result["tables"]["ledger"].items():
        s, arm = key.split("|")
        lines.append(f"| {s} | {arm} | {t['measured']} | {t['flat']} | {t['unpriced']} | {t['excluded']} |")
    rv = result.get("rivals") or {}
    lines += ["", "## Rival checks, design window, % a year", ""]
    for key, yrs in (rv.get("by_year") or {}).items():
        lines.append(f"- {key} by year: " + ", ".join(f"{y} {_pct(_year(x))}" for y, x in yrs.items()))
    for key, c in (rv.get("crash") or {}).items():
        lines.append(
            f"- {key}, 2020 crash weeks in {_pct(_year(c['with']))}, out {_pct(_year(c['without']))}"
        )
    for key, x in (rv.get("pairs") or {}).items():
        lines.append(f"- {key} (reported, not judged): {_pct(_year(x))}")
    lines += [
        "",
        "Trade counts, refusals by reason and assignment flags are in results.json under tables.counts.",
    ]
    return "\n".join(lines) + "\n"


def cmd_report(args) -> int:
    src = Path(args.json) if args.json else _out_dir() / "results.json"
    src.with_suffix(".md").write_text(report(json.loads(src.read_text(encoding="utf-8"))), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan", help="the fetch-chain-paths request files and counts")
    pl.add_argument("--out-dir")
    pl.set_defaults(fn=cmd_plan)
    pi = sub.add_parser("pilot", help="the guards on one symbol, no arm read")
    pi.add_argument("--symbol", default="XSP(SPY)")
    pi.add_argument("--ledger")
    pi.set_defaults(fn=lambda a: cmd_study(a, guards_only=True))
    st = sub.add_parser("study", help="guards, arms, baselines and the verdict")
    st.add_argument("--json")
    st.add_argument("--ledger")
    st.set_defaults(fn=cmd_study)
    rp = sub.add_parser("report", help="results.md from an existing results.json")
    rp.add_argument("--json")
    rp.set_defaults(fn=cmd_report)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
