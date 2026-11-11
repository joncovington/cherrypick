"""Item 71, a LEAPS call as leverage (research/edge-hypotheses-plan.md, section 71): a long-dated call
held, against the same dollars put into the same starting exposure another way.

    python packages/backtest/scripts/leaps_leverage_item71.py listing [--out-dir DIR]  # 2-year probes
    python packages/backtest/scripts/leaps_leverage_item71.py plan [--out-dir DIR]     # paths to fetch
    python packages/backtest/scripts/leaps_leverage_item71.py fund                     # the fund check
    python packages/backtest/scripts/leaps_leverage_item71.py study [--json PATH]      # the verdict
    python packages/backtest/scripts/leaps_leverage_item71.py report [--json PATH]     # results.md

`listing` writes the one-session probes that say which standard monthlies two years out the vendor
listed (item 36's probes already answer the year-long band), the input of
`python -m cherrypick.backtest fetch-chain-paths --requests FILE`. `plan` refuses until every probe is
answered, then writes one request file per symbol: each arm's no-refusal skeleton of long cycles, net of
what the chain store holds. `study` writes results.json and results.md under the backtest results dir
(research/item71/). Everything licensed stays under ~/.cherrypick; nothing here writes into the repo.

**The question is the user's:** leverage and a small capital outlay are the point of the trade, so the
call is judged against what the same dollars buy in the same account, not against the shares its delta
stands for. Three rivals start each cycle with the call's own debit:

- `fund`: a daily-reset fund at the call's entry leverage (delta x spot / price), modelled on the
  underlying's total return, financed at the 3-month bill plus a fitted spread, less a 0.95% fee. The
  spread is fitted on 2011-2015, before either window, to six real leveraged funds on three of the
  judged underlyings, and the fit must track them in the design window (the `fund` guard).
- `financed`: the entry delta in shares, the debit as the margin and the rest borrowed at the bill
  rate, held static for the cycle. Only this rival exists for the out-of-the-money arms, whose
  leverage no daily-reset fund survives.
- `shares` (reported only): the debit in shares, no leverage.

**Reused from item 36, imported:** the market (stored chains, closes, dividends, splits, the 3-month
bill), the vendor listing and the year-long expiry rule (`leap_for`: pmcc's monthly nearest 360 DTE in
240-540), deltas solved from the mid at no rate and no yield (the broker's convention), pmcc's fees and
slippage over `tastytrade_costs`, its long spread bar, and the stationary bootstrap.

**Readings chosen where the declaration is silent** (each also in the report):
- One evaluation a session, at the close. The strike is the listed call whose delta is nearest the
  arm's target; on a tie the higher strike (the smaller debit). A call without a solvable vol has no
  delta and is skipped, as pmcc skips it.
- The position closes on the first session at or under the arm's close DTE, at the mid less costs; an
  unquoted close is retried the next session; a call still held at expiry settles at intrinsic with
  pmcc's $5 event. The next cycle enters the session after a close.
- A week is measured when the call was held at the previous Friday close and quoted at both closes.
  Every rival's week is read on the same weeks, so every pair shares its capital and its weeks.
- A cycle that holds a split ex-date is dropped unbooked on the split day for the call and every rival;
  its weeks are excluded.
- Dividends: a rival holding shares (or the fund) earns each ex-date's dividend; the call does not.
"""

from __future__ import annotations

import argparse
import bisect
import importlib.util
import json
import math
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from cherrypick.core import stats as core_stats

_ITEM36 = Path(__file__).resolve().parent / "pmcc_item36.py"


def _load_item36():
    spec = importlib.util.spec_from_file_location("pmcc_item36", _ITEM36)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("pmcc_item36", module)  # its dataclasses look their module up by name
    spec.loader.exec_module(module)
    return module


i36 = _load_item36()
clock, engine, core_fees = i36.clock, i36.engine, i36.core_fees
Market = i36.Market

# ------------------------------------------------------------------------------------------------
# Declarations.

DESIGN, HOLDOUT, HALVES = i36.DESIGN, i36.HOLDOUT, i36.HALVES
SYMBOLS = i36.SYMBOLS
FUNDS, STOCKS = i36.FUNDS, i36.STOCKS  # funds judged, stocks reported


@dataclass(frozen=True)
class Arm:
    name: str
    delta: float  # the target call delta at entry, broker convention
    tenor: str  # "1y": pmcc's nearest-360 monthly | "2y": nearest 730 in 540-900
    close_dte: int
    rival: str  # the judged rival: "fund" | "financed"
    min_entries: int = 4


ARMS: dict[str, Arm] = {
    "c90-45": Arm("c90-45", 0.90, "1y", 45, "fund"),
    "c80-45": Arm("c80-45", 0.80, "1y", 45, "fund"),
    "c90-120": Arm("c90-120", 0.90, "1y", 120, "fund"),
    "c90-2y": Arm("c90-2y", 0.90, "2y", 45, "fund", min_entries=3),
    "c50-45": Arm("c50-45", 0.50, "1y", 45, "financed"),
    "c30-45": Arm("c30-45", 0.30, "1y", 45, "financed"),
}
RIVALS = ("fund", "financed", "shares")

TWO_YEAR = (730, 540, 900)  # aim, min, max DTE
TWO_YEAR_MONTHS = range(17, 32)  # standard monthlies 17 to 31 months on: every one the band can hold
LISTING_SLACK = (
    31  # a probe on the month's first session asks about every expiry a day of that month can pick
)

FUND_FEE = 0.0095  # a year, on the fund's value
FUND_TRADE_COST = 0.0002  # of the fund's value, each way (a leveraged fund's quoted spread)
SESSIONS_A_YEAR = 252
FIT_WINDOW = (date(2011, 1, 3), date(2015, 12, 31))
FIT_GRID = [round(-0.02 + 0.0005 * k, 4) for k in range(121)]  # the spread over bills, -2% to +4%
#: (underlying, real fund, leverage): the fit and the gating check.
FUND_PAIRS = (("SPY", "SSO", 2), ("SPY", "UPRO", 3), ("QQQ", "QLD", 2), ("QQQ", "TQQQ", 3),
              ("IWM", "UWM", 2), ("IWM", "TNA", 3))  # fmt: skip
FUND_REPORTED = (("GLD", "UGL", 2), ("SLV", "AGQ", 2))  # commodity funds: swaps and futures, reported
FUND_TOLERANCE = 0.015  # |modelled - real| annualised return, in the design window, per pair
SPLIT_LIKE = 0.20  # a real fund session this far from its multiple of the underlying is left out
#: The pairs whose design-window gap gates (2x). The 3x pairs track with noise of either sign (read on
#: public bars before any option quote, 2026-11-10), so they gate through the handicap instead: a
#: fund-rival arm must beat the fund credited with the model's largest shortfall against any of the six.
FUND_GATED_LEVERAGE = (2,)

COST_SCALES = (1.0, 1.5)
SEED = 71
ALPHA = 0.05
MIN_WEEKS = {"design": 120, "holdout": 90}
MAX_MISSING = 0.20
MIN_JUDGED_FUNDS = 4
TWO_THIRDS = 2 / 3
DATA_REFUSALS = ("no_leap_listed", "no_long_chain", "no_delta")

#: The replay's corrections, oldest first (`orchestrator.studyversion`).
REPLAY_CORRECTIONS: tuple = ()


def long_params() -> dict:
    """pmcc's own long-leg parameters (its spread bar)."""
    return i36.spec_params(i36.SPECS["long-alone"])


# ------------------------------------------------------------------------------------------------
# Pure: expiries and cycles.


def two_year_monthlies(day: date) -> list[date]:
    out = []
    for k in TWO_YEAR_MONTHS:
        y, m = day.year + (day.month - 1 + k) // 12, (day.month - 1 + k) % 12 + 1
        out.append(clock.standard_monthly(y, m))
    return out


def two_year_candidates(g: date) -> list[date]:
    """The standard monthlies a 2-year probe on `g` asks about: each a day of g's month can pick."""
    _aim, lo, hi = TWO_YEAR
    return [e for e in two_year_monthlies(g) if lo <= (e - g).days <= hi + LISTING_SLACK]


def two_year_for(day: date, listed: i36.Listed | None = None) -> date | None:
    """The listed standard monthly nearest 730 DTE within 540-900; the nearer expiry on a tie."""
    aim, lo, hi = TWO_YEAR
    cands = [e for e in two_year_monthlies(day) if lo <= (e - day).days <= hi]
    if listed is not None:
        g = i36.probe_day(day)
        cands = [e for e in cands if listed(g, e)]
    return min(cands, key=lambda e: (abs((e - day).days - aim), e)) if cands else None


def expiry_for(arm: Arm, day: date, listed: i36.Listed | None = None) -> date | None:
    return i36.leap_for(day, listed) if arm.tenor == "1y" else two_year_for(day, listed)


def arm_cycles(
    arm: Arm, sessions: list[date], listed: i36.Listed | None = None
) -> list[tuple[date, date, date]]:
    """The no-refusal skeleton: (entry session, expiry, close session), the close the first session at
    or under the arm's close DTE, the next entry the session after it."""
    out, i = [], 0
    while i < len(sessions):
        d = sessions[i]
        exp = expiry_for(arm, d, listed)
        if exp is None:
            i += 1
            continue
        j = i
        while j < len(sessions) and (exp - sessions[j]).days > arm.close_dte:
            j += 1
        if j >= len(sessions):
            out.append((d, exp, sessions[-1]))
            break
        out.append((d, exp, sessions[j]))
        i = j + 1
    return out


# ------------------------------------------------------------------------------------------------
# Pure: the strike, leverage, financing.


def pick_strike(greeks: dict[float, tuple[float, float]], target: float) -> float | None:
    """The strike whose delta is nearest `target`; the higher strike on a tie."""
    if not greeks:
        return None
    return min(greeks, key=lambda k: (round(abs(greeks[k][0] - target), 9), -k))


def leverage(delta: float, spot: float, price: float) -> float | None:
    """Dollar exposure per dollar of debit."""
    return delta * spot / price if price > 0 else None


def implied_rate(
    spot: float, strike: float, years: float, call: float, put: float, pv_div: float
) -> float | None:
    """The financing rate in the call-put pair at one strike (European parity: C - P = S - PV(D) -
    K e^(-rT)). Indicative for an American pair, where parity is an inequality."""
    x = (spot - pv_div - (call - put)) / strike
    if years <= 0 or x <= 0:
        return None
    return -math.log(x) / years


def fund_step(r_under: float, rate: float, lev: float, spread: float, fee: float = FUND_FEE) -> float:
    """One session's return of a daily-reset fund at `lev`: the levered total return, less financing on
    the borrowed part at the bill plus `spread`, less the fee."""
    return lev * r_under - (lev - 1.0) * (rate + spread) / SESSIONS_A_YEAR - fee / SESSIONS_A_YEAR


def total_returns(
    closes: dict[date, float], dividends: list[tuple[date, float]], split_days: list[date]
) -> dict[date, float]:
    """{session: total return from the previous session's close}, dividends with an ex-date after the
    previous session and on or before this one added back; a session with a split ex-date in that span
    is left out."""
    days = sorted(closes)
    divs = sorted(dividends)
    ex = [d for d, _ in divs]
    out = {}
    for a, b in zip(days, days[1:], strict=False):
        if any(a < s <= b for s in split_days) or closes[a] <= 0:
            continue
        lo, hi = bisect.bisect_right(ex, a), bisect.bisect_right(ex, b)
        paid = sum(amt for _, amt in divs[lo:hi])
        out[b] = (closes[b] + paid) / closes[a] - 1.0
    return out


def annualised(rets: list[float]) -> float | None:
    """Annualised compound return of a list of session returns."""
    if not rets or any(r <= -1 for r in rets):
        return None
    return math.exp(sum(math.log1p(r) for r in rets) * SESSIONS_A_YEAR / len(rets)) - 1.0


def fund_gap(
    under: dict[date, float],
    real: dict[date, float],
    rate_fn: Callable[[date], float],
    lev: float,
    spread: float,
    lo: date,
    hi: date,
) -> dict:
    """Modelled against real annualised return on the sessions both have in [lo, hi]. A session where
    the real fund sits more than `SPLIT_LIKE` from its multiple of the underlying is a split or a bad
    print the files miss (2026-11-10: unrecorded splits on UPRO, QLD, UWM, TNA, AGQ), left out of both
    and counted."""
    both = sorted(d for d in under.keys() & real.keys() if lo <= d <= hi)
    days = [d for d in both if abs(real[d] - lev * under[d]) <= SPLIT_LIKE]
    model = [fund_step(under[d], rate_fn(d), lev, spread) for d in days]
    got = [real[d] for d in days]
    m, r = annualised(model), annualised(got)
    return {"sessions": len(days), "split_like": len(both) - len(days), "model": m, "real": r,
            "gap": None if m is None or r is None else m - r}  # fmt: skip


def fit_spread(pairs: list[tuple[dict, dict, float]], rate_fn, lo: date, hi: date, grid=FIT_GRID) -> float:
    """The spread over bills that best fits the pairs' annualised returns (least squares over a grid)."""

    def loss(s: float) -> float:
        total = 0.0
        for under, real, lev in pairs:
            g = fund_gap(under, real, rate_fn, lev, s, lo, hi)["gap"]
            total += 1e6 if g is None else g * g
        return total

    return min(grid, key=lambda s: (loss(s), abs(s)))


# ------------------------------------------------------------------------------------------------
# The replay of one arm on one symbol.


@dataclass
class Pos:
    strike: float
    exp: date
    opened: date
    capital: float
    lev: float
    delta: float
    fund_value: float
    fin_shares: float
    loan: float
    cash_shares: float
    flow: float = 0.0


@dataclass
class Run:
    market: str
    arm: str
    scale: float
    snaps: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=lambda: defaultdict(int))
    cycles: list[dict] = field(default_factory=list)
    drops: list[dict] = field(default_factory=list)


COMPONENTS = ("long", "fees", "slippage", "settlement")


def simulate(m: Market, arm: Arm, *, spread: float, scale: float = 1.0, costs: dict | None = None,
             drop_cost: str | None = None) -> Run:  # fmt: skip
    """One arm on one symbol over its sessions, the three rivals beside it on the same dollars.
    `drop_cost` forgets one cost kind in the components only, to show the accounting guard failing."""
    params = long_params()
    cfg = {"tastytrade_costs": costs or {}}
    run = Run(m.name, arm.name, scale)
    cnt = run.counts
    ends = set(i36.week_ends(m.sessions))
    cum = dict.fromkeys(COMPONENTS, 0.0)
    cash = [0.0]
    rival = dict.fromkeys(RIVALS, 0.0)  # realised dollars, every rival
    divs = sorted(m.dividends)
    ex = [d for d, _ in divs]
    state: dict = {"pos": None, "closed_on": None, "refused": None}

    def book(kind: str, amount: float, pos: Pos) -> None:
        cash[0] += amount
        pos.flow += amount
        if kind != drop_cost:
            cum[kind] += amount

    def costs_of(q: dict, *, opening: bool, selling: bool) -> tuple[float, float]:
        fee = engine.leg_fee(m.fee_symbol, 1, opening=opening, selling=selling)
        slip = core_fees.slippage_dollars([{"bid": q["bid"], "ask": q["ask"]}], 1, cfg)
        return fee * scale, slip * scale

    def refuse(reason: str) -> None:
        cnt["refused_" + reason] += 1
        state["refused"] = reason

    def enter(d: date, s: float) -> None:
        exp = expiry_for(arm, d, m.listed)
        if exp is None:
            return refuse("no_leap_listed")
        if not m.chain(d, exp):
            return refuse("no_long_chain")
        g = m.greeks(d, exp, "C")
        k = pick_strike(g, arm.delta)
        if k is None:
            return refuse("no_delta")
        q = m.quote(d, exp, k, "C")
        pct = engine._spread_pct(q)
        if pct is not None and pct > params["max_leg_spread_pct"]:
            return refuse("spread_too_wide")
        cap = 100.0 * q["mid"]
        lev = leverage(g[k][0], s, q["mid"])
        delta = g[k][0]
        n_fin = 100.0 * delta
        pos = Pos(k, exp, d, cap, lev, delta, fund_value=cap, fin_shares=n_fin,
                  loan=n_fin * s - cap, cash_shares=cap / s)  # fmt: skip
        book("long", -cap, pos)
        fee, slip = costs_of(q, opening=True, selling=False)
        book("fees", -fee, pos)
        book("slippage", -slip, pos)
        rival["fund"] -= cap * FUND_TRADE_COST * scale
        put = m.quote(d, exp, k, "P")
        years = (exp - d).days / 365.0
        r_imp = None
        if put is not None:
            r_imp = implied_rate(s, k, years, q["mid"], put["mid"], s * m.div_yield(d) * years)
        run.cycles.append({"opened": d.isoformat(), "exp": exp.isoformat(), "strike": k, "delta": delta,
                           "lev": lev, "capital": cap, "spot": s, "rate": m.rate_fn(d),
                           "implied_rate": r_imp})  # fmt: skip
        state["pos"] = pos
        cnt["entries"] += 1

    def close(d: date, s: float, pos: Pos, reason: str) -> bool:
        q = m.quote(d, pos.exp, pos.strike, "C")
        if q is None and d < pos.exp:
            cnt["close_unquoted"] += 1
            return False
        if q is None:
            intrinsic = max(0.0, s - pos.strike)
            book("long", 100.0 * intrinsic, pos)
            if intrinsic > 0:
                book("settlement", -engine.settlement_fee(1) * scale, pos)
            cnt["settled_at_expiry"] += 1
        else:
            book("long", 100.0 * q["mid"], pos)
            fee, slip = costs_of(q, opening=False, selling=True)
            book("fees", -fee, pos)
            book("slippage", -slip, pos)
        rival["fund"] += pos.fund_value - pos.capital - pos.fund_value * FUND_TRADE_COST * scale
        run.cycles[-1].update({"closed": d.isoformat(), "reason": reason})
        cnt["closed_" + reason] += 1
        state["pos"], state["closed_on"] = None, d
        return True

    def carry(prev: date, d: date, s0: float, s1: float, pos: Pos) -> None:
        """One session of every rival: dividends with an ex-date in (prev, d] are paid."""
        lo, hi = bisect.bisect_right(ex, prev), bisect.bisect_right(ex, d)
        paid = sum(a for _, a in divs[lo:hi])
        r_u = (s1 + paid) / s0 - 1.0
        rate = m.rate_fn(d)
        pos.fund_value *= 1.0 + fund_step(r_u, rate, pos.lev, spread)
        rival["financed"] += pos.fin_shares * (s1 - s0 + paid) - pos.loan * rate * (d - prev).days / 365.0
        rival["shares"] += pos.cash_shares * (s1 - s0 + paid)

    def snapshot(d: date) -> None:
        pos = state["pos"]
        q = m.quote(d, pos.exp, pos.strike, "C") if pos else None
        if pos is not None and q is None:
            cnt["unpriced_weeks"] += 1
            run.snaps.append({"end": d, "priced": False, "has_pos": True, "cap": pos.capital})
            return
        mark = 100.0 * q["mid"] if q else 0.0
        comps = dict(cum)
        comps["long"] += mark
        rv = dict(rival)
        if pos is not None:
            rv["fund"] += pos.fund_value - pos.capital
        run.snaps.append({"end": d, "priced": True, "has_pos": pos is not None,
                          "cap": pos.capital if pos else None, "comps": comps, "cash": cash[0],
                          "marks": mark, "rivals": rv,
                          "flat_why": state["refused"] if pos is None else None})  # fmt: skip

    prev = None
    for d in m.sessions:
        s = m.closes[d]
        pos = state["pos"]
        state["refused"] = None
        if pos is not None and d in m.split_days:
            # Abandoned unbooked: its weeks are excluded (`split_weeks`) for the call and every rival,
            # so the levels it leaves behind never enter a weekly difference.
            run.drops.append({"day": d.isoformat(), "opened": pos.opened.isoformat()})
            run.cycles[-1].update({"closed": d.isoformat(), "reason": "split_drop"})
            cnt["split_drops"] += 1
            state["pos"], state["closed_on"] = None, d
            pos = None
        if pos is not None and prev is not None:
            carry(prev, d, m.closes[prev], s, pos)
        if pos is None:
            if state["closed_on"] is None or d > state["closed_on"]:
                enter(d, s)
        elif (pos.exp - d).days <= arm.close_dte:
            close(d, s, pos, "close_dte")
        if d in ends:
            snapshot(d)
        prev = d
    return run


def split_weeks(m: Market, run: Run) -> set[date]:
    """Week-ends of every cycle that held a split ex-date (dropped for the call and every rival)."""
    ends = i36.week_ends(m.sessions)
    out: set[date] = set()
    for c in run.cycles:
        a = date.fromisoformat(c["opened"])
        b = date.fromisoformat(c.get("closed") or m.sessions[-1].isoformat())
        if any(a <= s <= b for s in m.split_days):
            k0, k1 = a.isocalendar()[:2], b.isocalendar()[:2]
            out |= {e for e in ends if k0 <= e.isocalendar()[:2] <= k1}
    return out


# ------------------------------------------------------------------------------------------------
# Pure: weeks and pairs.


def weekly(run: Run, excluded: set[date] = frozenset()) -> dict[date, dict]:
    """{week end: {"call": r, "fund": r, "financed": r, "shares": r}} on the weeks the call was held at the
    previous close and quoted at both; each return on the previous close's capital."""
    out = {}
    for a, b in zip(run.snaps, run.snaps[1:], strict=False):
        if b["end"] in excluded or not a["has_pos"] or not a["priced"] or not b["priced"] or not a["cap"]:
            continue
        cap = a["cap"]
        row = {"call": (sum(b["comps"].values()) - sum(a["comps"].values())) / cap}
        for k in RIVALS:
            row[k] = (b["rivals"][k] - a["rivals"][k]) / cap
        out[b["end"]] = row
    return out


def missing(run: Run, excluded: set[date], lo: date, hi: date) -> set[date]:
    """Weeks of [lo, hi] missing for the data: the held call unquoted at either close, or flat because
    the last entry was refused for the data."""
    out = set()
    for a, b in zip(run.snaps, run.snaps[1:], strict=False):
        if not lo <= b["end"] <= hi or b["end"] in excluded:
            continue
        if a["has_pos"]:
            bad = not a["priced"] or not b["priced"]
        else:
            bad = a.get("flat_why") in DATA_REFUSALS
        if bad:
            out.add(b["end"])
    return out


def diffs(weeks: dict[date, dict], rival: str, lo: date, hi: date) -> dict[date, float]:
    return {w: r["call"] - r[rival] for w, r in sorted(weeks.items()) if lo <= w <= hi}


def judged_status(arm: Arm, run: Run, excluded: set[date], half: str, lo: date, hi: date) -> dict:
    got = [w for w in weekly(run, excluded) if lo <= w <= hi]
    weeks = [s["end"] for s in run.snaps if lo <= s["end"] <= hi]
    entries = sum(1 for c in run.cycles if lo.isoformat() <= c["opened"] <= hi.isoformat())
    miss = len(missing(run, excluded, lo, hi)) / max(len(weeks), 1)
    reasons = []
    if entries < arm.min_entries:
        reasons.append(f"{entries} entries (< {arm.min_entries})")
    if len(got) < MIN_WEEKS[half]:
        reasons.append(f"{len(got)} priced weeks (< {MIN_WEEKS[half]})")
    if miss > MAX_MISSING:
        reasons.append(f"{miss:.0%} of weeks missing (> {MAX_MISSING:.0%})")
    return {"judged": not reasons, "reasons": reasons, "entries": entries, "weeks": len(got), "missing": miss}


def accounting_check(run: Run, tolerance: float = 0.005) -> list[str]:
    """Rows add up: at every priced snapshot the call's components sum to its cash flows plus its mark."""
    bad = []
    for sn in run.snaps:
        if sn["priced"] and abs(sum(sn["comps"].values()) - (sn["cash"] + sn["marks"])) > tolerance:
            bad.append(f"{sn['end']}: components {sum(sn['comps'].values()):.4f} against "
                       f"{sn['cash'] + sn['marks']:.4f}")  # fmt: skip
    return bad


def two_thirds(n_ahead: int, n: int) -> bool:
    return n > 0 and n_ahead >= math.ceil(TWO_THIRDS * n)


def year_mean(per_sym: dict[str, dict[date, float]]) -> dict[int, float]:
    by = defaultdict(list)
    for v in per_sym.values():
        for w, x in v.items():
            by[w.year].append(x)
    return {y: sum(xs) / len(xs) * 52 for y, xs in sorted(by.items())}


def arm_test(per_sym: dict[str, dict[date, float]], idx, grid: list[date]) -> dict:
    """Pooled equal-weight mean (a year), its bootstrap lower bound and p, and funds ahead."""
    b = i36.bootstrap_pooled(per_sym, grid, idx)
    ahead, n = i36.funds_ahead(per_sym)
    yr = lambda x: None if x is None else x * 52  # noqa: E731
    return {"pooled": yr(b["mean"]), "lower": yr(b["lower"]), "p": b["p"], "ahead": ahead, "n": n}


def design_verdict(
    t: dict, halves: dict, stressed: float | None, no_best: float | None, handicap: float = 0.0
) -> dict:
    """Every design clause but Holm (applied across arms after). `handicap` (a year) is the fund model's
    largest shortfall against a real fund, credited to a fund rival; zero for the financed rival."""
    clauses = {
        "pooled_positive": t["pooled"] is not None and t["pooled"] > 0,
        "beats_handicap": t["pooled"] is not None and t["pooled"] - handicap > 0,
        "lower_bound_positive": t["lower"] is not None and t["lower"] > 0,
        "two_thirds_ahead": two_thirds(t["ahead"], t["n"]),
        "each_half": all(v is not None and v > 0 for v in halves.values()),
        "costs_1_5x": stressed is not None and stressed > 0,
        "best_year_out": no_best is not None and no_best > 0,
    }
    return {"clauses": clauses, "ok": all(clauses.values())}


def holdout_verdict(t: dict, stressed: float | None, handicap: float = 0.0) -> dict:
    clauses = {
        "pooled_positive": t["pooled"] is not None and t["pooled"] > 0,
        "beats_handicap": t["pooled"] is not None and t["pooled"] - handicap > 0,
        "two_thirds_ahead": two_thirds(t["ahead"], t["n"]),
        "costs_1_5x": stressed is not None and stressed > 0,
    }
    return {"clauses": clauses, "ok": all(clauses.values())}


def median(xs: list[float]) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


# ------------------------------------------------------------------------------------------------
# Plan: listing probes and chain paths.


def listing_requests(symbol: str) -> list[dict]:
    """The 2-year listing probes: on the first session of each month, one session of every standard
    monthly a day of that month can pick in the 2-year band."""
    chain_symbol = SYMBOLS[symbol][0]
    a, b = i36.listed_window(symbol)
    out = []
    for g in sorted({i36.probe_day(d) for d in i36.sessions_between(a, b)}):
        for e in two_year_candidates(g):
            out.append({"symbol": chain_symbol, "expiration": e.isoformat(), "start": g.isoformat(),
                        "through": g.isoformat(), "kind": "listing2y"})  # fmt: skip
    return out


def listing_gaps(conn: sqlite3.Connection | None, symbol: str) -> list[dict]:
    reqs = listing_requests(symbol) + i36.listing_requests(symbol)
    return i36.stored_filter(reqs, *i36.stored_for(conn, SYMBOLS[symbol][0])) if conn else reqs


def path_requests(symbol: str, listed: i36.Listed | None = None) -> list[dict]:
    """Every arm's skeleton long path for `symbol`, deduplicated."""
    chain_symbol = SYMBOLS[symbol][0]
    a, b = i36.listed_window(symbol)
    sessions = i36.sessions_between(a, b)
    seen, out = set(), []
    for arm in ARMS.values():
        for d0, exp, c in arm_cycles(arm, sessions, listed):
            key = (exp.isoformat(), d0.isoformat(), min(c, b).isoformat())
            if key in seen:
                continue
            seen.add(key)
            out.append({"symbol": chain_symbol, "expiration": key[0], "start": key[1], "through": key[2],
                        "kind": "long-" + arm.name})  # fmt: skip
    return out


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item71"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_listing(args) -> int:
    out_dir = Path(args.out_dir) if args.out_dir else _out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    db = i36._chain_db()
    conn = i36._ro(db) if db.exists() else None
    gaps, per_symbol = [], {}
    for name in SYMBOLS:
        g = listing_gaps(conn, name)
        per_symbol[name] = {"unanswered": len(g)}
        gaps += g
    if conn:
        conn.close()
    path = out_dir / "listing_requests.json"
    path.write_text(json.dumps({"requests": gaps}, indent=1), encoding="utf-8")
    print(json.dumps({"per_symbol": per_symbol, "unanswered": len(gaps), "file": str(path)}, indent=1))
    return 0


def cmd_plan(args) -> int:
    out_dir = Path(args.out_dir) if args.out_dir else _out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    db = i36._chain_db()
    conn = i36._ro(db) if db.exists() else None
    short = {n: len(listing_gaps(conn, n)) for n in SYMBOLS}
    short = {n: k for n, k in short.items() if k}
    if short:
        print(json.dumps({"listing_unanswered": short, "next": "run listing, fetch its file, then this"}))
        return 2
    per_symbol, every = {}, []
    for name in ["XSP(SPY)", "QQQ", "IWM", "GLD", "SLV", "SMH", *STOCKS]:  # the judged funds first
        reqs = path_requests(name, i36.listing_from(conn, name))
        net = i36.stored_filter(reqs, *i36.stored_for(conn, SYMBOLS[name][0]))
        per_symbol[name] = {"paths": len(reqs), "paths_net": len(net),
                            "calls_net": sum(map(i36.vendor_calls, net))}  # fmt: skip
        every += net
    if conn:
        conn.close()
    path = out_dir / "chain_requests.json"  # one file: one fetch row in the research queue
    path.write_text(json.dumps({"requests": every}, indent=1), encoding="utf-8")
    print(json.dumps({"per_symbol": per_symbol, "paths_net": len(every), "file": str(path)}, indent=1))
    return 0


# ------------------------------------------------------------------------------------------------
# The fund model's fit and check (public bars only).


def _series(hist, symbol: str) -> dict[date, float]:
    from cherrypick.technicals import store

    bars = [b for b in store.raw_bars(hist, symbol) if b.close]
    closes = {date.fromisoformat(b.date): b.close for b in bars}
    divs = [(date.fromisoformat(x.ex_date), x.amount) for x in store.dividends(hist, symbol)[0]]
    splits = [date.fromisoformat(x.ex_date) for x in store.splits(hist, symbol)]
    return total_returns(closes, divs, splits)


def fund_model(hist=None, rate_fn=None) -> dict:
    """Fit the spread on 2011-2015; check every pair in the design window (gating) and report the
    holdout and the commodity funds."""
    from cherrypick.technicals import paths as tpaths
    from cherrypick.technicals import store

    hist = hist or store.connect(tpaths.history_db())
    rate_fn = rate_fn or i36.rate_reader()
    wanted = {s for u, f, _ in FUND_PAIRS + FUND_REPORTED for s in (u, f)}
    return fund_check({s: _series(hist, s) for s in sorted(wanted)}, rate_fn)


def fund_check(
    series: dict[str, dict[date, float]], rate_fn, pairs=FUND_PAIRS, reported=FUND_REPORTED
) -> dict:
    """The fit on 2011-2015 and the design-window check (each gated pair within `FUND_TOLERANCE`)."""
    spread = fit_spread([(series[u], series[f], lev) for u, f, lev in pairs], rate_fn, *FIT_WINDOW)
    rows, ok, under = [], True, 0.0
    for (u, f, lev), fitted in [(p, True) for p in pairs] + [(p, False) for p in reported]:
        gated = fitted and lev in FUND_GATED_LEVERAGE
        row = {"under": u, "fund": f, "lev": lev, "gated": gated, "fitted": fitted}
        for label, (lo, hi) in (("fit", FIT_WINDOW), ("design", DESIGN), ("holdout", HOLDOUT)):
            row[label] = fund_gap(series[u], series[f], rate_fn, lev, spread, lo, hi)
        g = row["design"]["gap"]
        if gated and (g is None or abs(g) > FUND_TOLERANCE):
            ok = False
        if fitted and g is not None:
            under = max(under, -g)  # how far the model fell short of a real fund
        rows.append(row)
    return {"spread": spread, "fee": FUND_FEE, "ok": ok, "tolerance": FUND_TOLERANCE, "handicap": under,
            "pairs": rows}  # fmt: skip


def cmd_fund(args) -> int:
    print(json.dumps(fund_model(), indent=1, default=str))
    return 0


# ------------------------------------------------------------------------------------------------
# The study.


def analyse(
    runs: dict, stressed: dict, markets: dict[str, Market], excluded: dict, handicap: float = 0.0
) -> dict:
    """Per arm: the judged rival's paired difference, design clauses, Holm, holdout; every rival reported."""
    grid_design = sorted(
        {s["end"] for r in runs.values() for s in r.snaps if DESIGN[0] <= s["end"] <= DESIGN[1]}
    )
    grid_hold = sorted(
        {s["end"] for r in runs.values() for s in r.snaps if HOLDOUT[0] <= s["end"] <= HOLDOUT[1]}
    )
    idx_d = i36.resample_indices(len(grid_design), seed=SEED)
    idx_h = i36.resample_indices(len(grid_hold), seed=SEED)
    out: dict = {"arms": {}, "status": {}}
    pvals = {}
    for arm in ARMS.values():
        a_out: dict = {"rival": arm.rival, "judged": {}, "reported": {}}
        judged: dict[str, dict] = {"design": {}, "holdout": {}}
        for half, (lo, hi) in (("design", DESIGN), ("holdout", HOLDOUT)):
            for name in FUNDS:
                run = runs[(name, arm.name)]
                st = judged_status(arm, run, excluded[(name, arm.name)], half, lo, hi)
                a_out["judged"].setdefault(name, {})[half] = st
                if st["judged"]:
                    judged[half][name] = run
        enough = len(judged["design"]) >= MIN_JUDGED_FUNDS

        def per(runs_by, rival, lo, hi, src=runs, name=arm.name):
            return {n: diffs(weekly(src[(n, name)], excluded[(n, name)]), rival, lo, hi) for n in runs_by}

        d = per(judged["design"], arm.rival, *DESIGN)
        t = arm_test(d, idx_d, grid_design)
        halves = {
            h: i36.pooled_mean(per(judged["design"], arm.rival, lo, hi)) for h, (lo, hi) in HALVES.items()
        }
        halves = {h: None if v is None else v * 52 for h, v in halves.items()}
        st_d = i36.pooled_mean(per(judged["design"], arm.rival, *DESIGN, src=stressed))
        best = i36.best_year(d)
        nb = i36.pooled_mean(i36.without_year(d, best))
        hc = handicap if arm.rival == "fund" else 0.0
        dv = design_verdict(
            t, halves, None if st_d is None else st_d * 52, None if nb is None else nb * 52, hc
        )
        a_out.update({"design": t, "halves": halves, "costs_1_5x": None if st_d is None else st_d * 52,
                      "best_year": best, "best_year_out": None if nb is None else nb * 52,
                      "design_clauses": dv["clauses"], "enough_funds": enough,
                      "by_year": year_mean(d)})  # fmt: skip
        if enough and t["p"] is not None:
            pvals[arm.name] = t["p"]
        a_out["_design_ok"] = enough and dv["ok"]
        # Every rival, every symbol, both windows: reported.
        for name in SYMBOLS:
            run = runs[(name, arm.name)]
            wk = weekly(run, excluded[(name, arm.name)])
            row = {}
            for label, (lo, hi) in (("design", DESIGN), ("holdout", HOLDOUT)):
                cell = {"weeks": sum(1 for w in wk if lo <= w <= hi)}
                for k in ("call", *RIVALS):
                    xs = [r[k] for w, r in wk.items() if lo <= w <= hi]
                    cell[k] = sum(xs) / len(xs) * 52 if xs else None
                    cell[k + "_drawdown"] = i36.max_drawdown(
                        {w: r[k] for w, r in wk.items() if lo <= w <= hi}
                    )
                cyc = [c for c in run.cycles if lo.isoformat() <= c["opened"] <= hi.isoformat()]
                cell["entries"] = len(cyc)
                cell["lev_median"] = median([c["lev"] for c in cyc])
                cell["financing_spread_median"] = median(
                    [c["implied_rate"] - c["rate"] for c in cyc if c["implied_rate"] is not None]
                )
                row[label] = cell
            row["counts"] = dict(run.counts)
            a_out["reported"][name] = row
        out["arms"][arm.name] = a_out
    holm = core_stats.holm(pvals, ALPHA) if pvals else {}
    for arm in ARMS.values():
        a = out["arms"][arm.name]
        a["holm"] = holm.get(arm.name, False)
        if not a["enough_funds"]:
            out["status"][arm.name] = "no_verdict"
            continue
        if not (a.pop("_design_ok") and a["holm"]):
            out["status"][arm.name] = "failed"
            continue
        names = [n for n in FUNDS if a["judged"][n]["holdout"]["judged"]]
        if len(names) < MIN_JUDGED_FUNDS:
            out["status"][arm.name] = "no_verdict"
            continue
        h = {
            n: diffs(weekly(runs[(n, arm.name)], excluded[(n, arm.name)]), arm.rival, *HOLDOUT) for n in names
        }
        th = arm_test(h, idx_h, grid_hold)
        hs = i36.pooled_mean({n: diffs(weekly(stressed[(n, arm.name)], excluded[(n, arm.name)]), arm.rival,
                                       *HOLDOUT) for n in names})  # fmt: skip
        hv = holdout_verdict(th, None if hs is None else hs * 52, handicap if arm.rival == "fund" else 0.0)
        a["holdout"], a["holdout_clauses"] = th, hv["clauses"]
        out["status"][arm.name] = "passed" if hv["ok"] else "failed"
    for a in out["arms"].values():
        a.pop("_design_ok", None)
    return out


def cmd_study(args) -> int:
    from cherrypick.technicals import paths as tpaths
    from cherrypick.technicals import store

    db = i36._chain_db()
    if not db.exists():
        print(json.dumps({"error": "no chain store"}))
        return 2
    vend, hist = i36._ro(db), store.connect(tpaths.history_db())
    gaps = {n: len(listing_gaps(vend, n)) for n in SYMBOLS}
    if any(gaps.values()):
        print(json.dumps({"listing_unanswered": {n: k for n, k in gaps.items() if k}}))
        return 2
    fund = fund_model(hist)
    costs = i36.costs_config()
    markets = {n: i36.load_market(n, vend, hist) for n in SYMBOLS}
    runs, stressed, excluded, guards = {}, {}, {}, {"fund": fund["ok"], "accounting": []}
    for n, m in markets.items():
        for arm in ARMS.values():
            r = simulate(m, arm, spread=fund["spread"], costs=costs)
            runs[(n, arm.name)] = r
            stressed[(n, arm.name)] = simulate(m, arm, spread=fund["spread"], costs=costs, scale=1.5)
            excluded[(n, arm.name)] = split_weeks(m, r)
            guards["accounting"] += [f"{n} {arm.name}: {x}" for x in accounting_check(r)]
    parity = i36.parity_check(markets["XSP(SPY)"], DESIGN[0], HOLDOUT[1])
    guards["parity"] = parity
    ok = fund["ok"] and not guards["accounting"] and parity.get("ok", False)
    result = analyse(runs, stressed, markets, excluded, fund["handicap"])
    if not ok:
        result["status"] = dict.fromkeys(ARMS, "no_verdict")
    result.update({"study": "item71", "guards": guards, "guards_ok": ok, "fund": fund,
                   "windows": {"design": [str(x) for x in DESIGN], "holdout": [str(x) for x in HOLDOUT]},
                   "corrections": list(REPLAY_CORRECTIONS)})  # fmt: skip
    out = Path(args.json) if args.json else _out_dir() / "results.json"
    out.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    (out.parent / "results.md").write_text(report(result), encoding="utf-8")
    print(json.dumps({"status": result["status"], "guards_ok": ok, "file": str(out)}, indent=1))
    return 0


def _pct(x) -> str:
    return "" if x is None else f"{x * 100:+.1f}"


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def report(result: dict) -> str:
    lines = ["# Item 71: a LEAPS call as leverage, against the same dollars elsewhere", ""]
    lines.append(
        "Verdicts: " + ", ".join(f"`{a}` {s.replace('_', ' ')}" for a, s in result["status"].items()) + "."
    )
    lines += ["", "Figures are % a year on the call's debit (the capital every rival starts with).", "",
              "## Guards", ""]  # fmt: skip
    f = result["fund"]
    lines.append(
        f"- **fund**: {'ok' if f['ok'] else 'failed'}; fitted spread over bills {f['spread'] * 100:+.2f}%, "
        f"fee {f['fee'] * 100:.2f}%, handicap credited to the fund rival {f['handicap'] * 100:.2f} a year"
    )
    for p in f["pairs"]:
        g = p["design"]["gap"]
        role = "gated" if p["gated"] else ("handicap" if p.get("fitted") else "reported")
        lines.append(
            f"  - {p['fund']} ({p['lev']}x {p['under']}, {role}): design gap "
            f"{_pct(g)}, holdout gap {_pct(p['holdout']['gap'])}, fit gap {_pct(p['fit']['gap'])}"
        )
    lines.append(f"- **accounting**: {'ok' if not result['guards']['accounting'] else 'failed'}")
    lines.append(
        f"- **parity**: {'ok' if result['guards']['parity'].get('ok') else 'failed'} "
        f"{json.dumps({k: v for k, v in result['guards']['parity'].items() if k != 'ok'})}"
    )
    head = ["arm", "rival", "verdict", "pooled", "lower bound", "p", "Holm", "funds ahead", "halves",
            "1.5x costs", "best year out"]  # fmt: skip
    lines += ["", "## Design window, against the judged rival", "", _row(head), _row(["---"] * len(head))]
    for name, a in result["arms"].items():
        t = a["design"]
        lines.append(_row([
            name, a["rival"], result["status"][name].replace("_", " "), _pct(t["pooled"]), _pct(t["lower"]),
            "" if t["p"] is None else f"{t['p']:.4f}", "yes" if a["holm"] else "no",
            f"{t['ahead']} of {t['n']}", " / ".join(_pct(v) for v in a["halves"].values()),
            _pct(a["costs_1_5x"]), _pct(a["best_year_out"]),
        ]))  # fmt: skip
    head = ["arm", "symbol", "window", "weeks", "call", "fund", "financed", "shares", "call drawdown",
            "fund drawdown", "leverage", "financing over bills"]  # fmt: skip
    lines += ["", "## Every rival, per symbol (reported)", "", _row(head), _row(["---"] * len(head))]
    for name, a in result["arms"].items():
        for sym, row in a["reported"].items():
            for w in ("design", "holdout"):
                c = row[w]
                lines.append(_row([
                    name, sym, w, str(c["weeks"]), _pct(c["call"]), _pct(c["fund"]), _pct(c["financed"]),
                    _pct(c["shares"]), _pct(c["call_drawdown"]), _pct(c["fund_drawdown"]),
                    "" if c["lev_median"] is None else f"{c['lev_median']:.1f}x",
                    _pct(c["financing_spread_median"]),
                ]))  # fmt: skip
    lines += ["", "## Judged-ness", ""]
    for name, a in result["arms"].items():
        for sym, st in a["judged"].items():
            for w, s in st.items():
                if not s["judged"]:
                    lines.append(f"- {name} {sym} {w}: " + "; ".join(s["reasons"]))
    return "\n".join(lines) + "\n"


def cmd_report(args) -> int:
    path = Path(args.json) if args.json else _out_dir() / "results.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    (path.parent / "results.md").write_text(report(result), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("listing", "plan"):
        p = sub.add_parser(name)
        p.add_argument("--out-dir")
    sub.add_parser("fund")
    for name in ("study", "report"):
        p = sub.add_parser(name)
        p.add_argument("--json")
    args = ap.parse_args(argv)
    return {"listing": cmd_listing, "plan": cmd_plan, "fund": cmd_fund, "study": cmd_study,
            "report": cmd_report}[args.cmd](args)  # fmt: skip


if __name__ == "__main__":
    raise SystemExit(main())
