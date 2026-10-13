"""Replay the premium-selling cycle and the education recipes on SPY's recorded end-of-day chains.

    python scripts/premium_cycle_replay.py [--start 2020-01-01] [--end 2026-10-12]
        [--cboe-dir DIR] [--cache FILE] [--json FILE] [--slippage-frac 0.125]

**The question.** The trade cycle for short puts and credit spreads is the standard monthly expiry
entered between 30 and 60 DTE, preferring 45-60, and the education material states four recipes: a
30-DTE 40-delta cash-secured put, a 15-delta covered call at 30-45 DTE, and a ~25-delta put credit
spread bought back at half its credit with no more than $2 risked per $1 of reward. This replays
each on SPY, one contract and one position at a time, re-entering on the next eligible day after a
close, and reports what survives costs. Answered from history, not from a paper arm.

**Data.** The local Dolt `options` clone (`option_chain`: date, expiration, strike, call/put, bid,
ask) and `stocks` clone (`ohlcv` closes, `dividend`), read through the running sql-server with
read-only SELECTs keyed on `(date, act_symbol)`, which is the table's key prefix, so each read is a
seek and never a scan. What the clone holds shapes every result below and is printed by the run:

- **Sampled days**: Mon/Wed/Fri until 2024, most weekdays since.
- **Sampled expirations**: about three to five per day, at fixed DTE buckets. A standard monthly is
  visible at roughly 44-66 DTE, then at ~28-31, ~25, ~14 and ~11. So an entry "in 30-60" is usually
  an entry at 44-60 (the next monthly is rarely listed at 32-43), and a position can only be marked
  -- and so only closed at 50% -- on the few days its expiry is listed.
- **Sparse strikes**: ~11-27 per side, often $5-$15 apart. Strikes in between are priced from the
  day's own quotes: a Black-76 implied vol is backed out of each listed mid (the forward from
  put-call parity at the money, the rate as the carry from spot less dividends due to that
  forward -- see `Slice._fit_parity`), interpolated linearly in strike (flat
  beyond the listed range), and the half-spread interpolated the same way. A listed strike always
  uses its own quote. Deltas are Black-76 forward deltas on those vols. SPY lists $1 strikes on the
  monthlies, so strikes are chosen on a $1 grid. An entry whose short strike falls outside the
  listed strikes, or whose delta misses the target by more than 0.05, is not taken that day.

**Fills and costs (one contract, whole-position dollars, net = gross - fees - settlement):**

- Fill at mid conceding `--slippage-frac` (default 0.125, the suite's `DEFAULT_COSTS`) of each
  leg's bid-ask, capped at 15% of the leg's mid. Slippage is inside the fill, shown as a measure.
- Fees are tastytrade's schedule from `cherrypick.core.fees`: $1/contract to open, $0 to close,
  plus clearing $0.10 and ORF $0.02 per contract per leg and FINRA TAF on sells.
- Expiry at intrinsic on the expiration day's SPY close. Each ITM leg is one $5 settlement event;
  a delivery's share leg is disposed of at that close with the SEC fee and equity TAF on the sale.
- Not modelled, so every short-option result is an upper bound: early assignment (a deep-ITM put,
  or an ITM call before an ex-date -- for covered calls the dividend is forfeited when the call is
  ITM at the close before the ex-date, the one place this is approximated), the weekend between an
  expiry close and the disposal, and interest on cash collateral (included only in the PUT check).

**Strategies.** `sp40` short put at -0.40 delta held to expiry, `sp40-tp50` the same bought back at
half the credit, `cc15` 100 shares plus a 0.15-delta call held to expiry, `pcs25` a -0.25-delta put
credit spread whose width is the widest of $1-$10 with credit/width >= 1/3 (that is the 2:1
risk/reward cap; no qualifying width means no trade that day), `pcs25-5w` a fixed $5 spread
whatever its ratio (the fallback, which mostly breaks the cap and is reported so). Both spreads
close at half their credit.

**Windows.** `30-60` the nearest standard monthly at 30-60 DTE; `45-60` the nearest at 45-60,
falling back to 30-60 when that is empty (monthlies are 28 or 35 days apart); `recipe` the recipe's
own window (puts 28-35 DTE, nearest 30; covered calls 30-45). A standard monthly is the third
Friday, or the Thursday before when the Friday is a holiday. A position that expires is rolled at
that same close when the window allows (which is what PUT and BXM do); one closed early re-enters
on the next chain day.

**Benchmark check.** Cboe's PUT (a one-month ATM cash-secured put, rolled at expiry, collateral in
T-bills) and BXM (ATM buy-write) are compared with ATM variants of this replay (`atm-put` with
collateral interest at the chain's parity-implied rate, `atm-cc` with dividends), each sampled on
the replay's own cycle-end dates, plus WPUT for context. The CSVs are Cboe's public files
(`curl -L https://cdn.cboe.com/api/global/us_indices/daily_prices/PUT_History.csv`, likewise BXM
and WPUT) in `--cboe-dir`; without them the check is skipped.

Deterministic: the same clone, dates and arguments give the same tables. Nothing is written except
the optional `--cache` and `--json` files.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from cherrypick.core import fees as _fees

SYMBOL = "SPY"
MULT = 100
STRIKE_STEP = 1.0
DELTA_TOLERANCE = 0.05
PARITY_BAND = 0.06  # |K/S - 1| for pairs used in the forward/discount fit
ITM_SMILE_CUTOFF = 0.05  # quotes further ITM than this (fraction of F) do not shape the smile
SLIPPAGE_CAP_FRAC_OF_MID = _fees.DEFAULT_COSTS["slippage_cap_frac_of_mid"]
TAKE_PROFIT = 0.5
RATIO_FLOOR = 1.0 / 3.0  # credit / width; the same rule as risk <= 2 x reward
MAX_WIDTH = 10


# --------------------------------------------------------------------------- calendar


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def is_standard_monthly(exp: date) -> bool:
    """Third Friday, or the Thursday before it (Good Friday, Juneteenth)."""
    tf = third_friday(exp.year, exp.month)
    return exp == tf or exp == tf - timedelta(days=1)


# --------------------------------------------------------------------------- data


def _connect(host: str, port: int, database: str):
    import mysql.connector

    return mysql.connector.connect(
        host=host, port=port, user="root", database=database, connection_timeout=30
    )


def load_data(host: str, port: int, start: date, end: date, cache: Path | None) -> dict:
    """Monthly-expiry chain rows, closes and dividends for SPY, keyed by ISO date strings.

    One SELECT per weekday on the `(date, act_symbol)` key prefix. A cache file, when given, is
    reused if it covers the requested dates (it is only a copy of what the clone returned)."""
    if cache and cache.exists():
        data = json.loads(cache.read_text())
        if data["start"] <= start.isoformat() and data["end"] >= end.isoformat():
            return data
    chains: dict[str, dict[str, list]] = {}
    closes: dict[str, float] = {}
    opt = _connect(host, port, "options")
    stk = _connect(host, port, "stocks")
    try:
        oc, sc = opt.cursor(), stk.cursor()
        d = start
        while d <= end:
            if d.weekday() < 5:
                sc.execute("SELECT close FROM ohlcv WHERE date=%s AND act_symbol=%s", (d, SYMBOL))
                row = sc.fetchone()
                if row and row[0] is not None:
                    closes[d.isoformat()] = float(row[0])
                oc.execute(
                    "SELECT expiration, strike, call_put, bid, ask FROM option_chain "
                    "WHERE date=%s AND act_symbol=%s",
                    (d, SYMBOL),
                )
                by_exp: dict[str, list] = defaultdict(list)
                for exp, k, cp, bid, ask in oc.fetchall():
                    if not is_standard_monthly(exp) or bid is None or ask is None:
                        continue
                    by_exp[exp.isoformat()].append([float(k), cp[0], float(bid), float(ask)])
                if by_exp:
                    chains[d.isoformat()] = dict(by_exp)
            d += timedelta(days=1)
        sc.execute("SELECT ex_date, amount FROM dividend WHERE act_symbol=%s", (SYMBOL,))
        dividends = {e.isoformat(): float(a) for e, a in sc.fetchall() if a is not None}
    finally:
        opt.close()
        stk.close()
    data = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "chains": chains,
        "closes": closes,
        "dividends": dividends,
    }
    if cache:
        cache.write_text(json.dumps(data))
    return data


# --------------------------------------------------------------------------- pricing


def _ncdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def black76(cp: str, f: float, k: float, t: float, vol: float, disc: float) -> float:
    if t <= 0 or vol <= 0:
        return disc * max(f - k, 0.0) if cp == "C" else disc * max(k - f, 0.0)
    sd = vol * math.sqrt(t)
    d1 = (math.log(f / k) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    if cp == "C":
        return disc * (f * _ncdf(d1) - k * _ncdf(d2))
    return disc * (k * _ncdf(-d2) - f * _ncdf(-d1))


def implied_vol(cp: str, price: float, f: float, k: float, t: float, disc: float) -> float | None:
    lo, hi = 0.005, 4.0
    if price <= black76(cp, f, k, t, lo, disc) or price >= black76(cp, f, k, t, hi, disc):
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if black76(cp, f, k, t, mid, disc) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def _interp(xs: list[float], ys: list[float], x: float) -> float:
    """Linear in x, flat beyond the ends."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_right(xs, x)
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


class Slice:
    """One day's quotes for one expiration, able to price any strike."""

    def __init__(self, day: date, exp: date, rows: list, spot: float, divs: float = 0.0):
        self.day, self.exp, self.spot, self.divs = day, exp, spot, divs
        self.t = max((exp - day).days, 0) / 365.0
        self.quotes: dict[str, dict[float, tuple[float, float]]] = {"C": {}, "P": {}}
        for k, cp, bid, ask in rows:
            if ask > 0 and ask >= bid >= 0:
                self.quotes[cp][k] = (bid, ask)
        self.forward, self.disc = self._fit_parity()
        self.rate = -math.log(self.disc) / self.t if self.t > 0 and self.disc < 1 else 0.0
        self.smile: dict[str, tuple[list, list, list]] = {}
        for cp in ("C", "P"):
            ks, vs = [], []
            for k in sorted(self.quotes[cp]):
                bid, ask = self.quotes[cp][k]
                itm = (self.forward - k) if cp == "C" else (k - self.forward)
                if itm > ITM_SMILE_CUTOFF * self.forward or bid <= 0:
                    continue
                v = (
                    implied_vol(cp, 0.5 * (bid + ask), self.forward, k, self.t, self.disc)
                    if self.t > 0
                    else None
                )
                if v is not None:
                    ks.append(k)
                    vs.append(v)
            spr_k = sorted(self.quotes[cp])
            spr = [self.quotes[cp][k][1] - self.quotes[cp][k][0] for k in spr_k]
            self.smile[cp] = (ks, vs, spr_k, spr)

    def _fit_parity(self) -> tuple[float, float]:
        """(forward, discount factor) from put-call parity at the money.

        The forward is K + (C - P) interpolated to the spot between the two listed pairs that
        bracket it. The rate is NOT taken from the slope of C - P across strikes: these are
        American options, and the early-exercise value of in-the-money puts steepens that slope
        past 1 (a discount factor above one) on most days. Instead the rate is what carries the
        spot less the dividends due before expiry to that forward, clamped to 0-8%."""
        pts = []
        for k, (cb, ca) in self.quotes["C"].items():
            if k in self.quotes["P"] and abs(k / self.spot - 1) <= PARITY_BAND and cb > 0:
                pb, pa = self.quotes["P"][k]
                if pb > 0:
                    pts.append((k, 0.5 * (cb + ca) - 0.5 * (pb + pa)))
        pts.sort()
        below = [p for p in pts if p[0] <= self.spot]
        above = [p for p in pts if p[0] > self.spot]
        if not below or not above or self.t <= 0:
            return self.spot, 1.0
        (k0, y0), (k1, y1) = below[-1], above[0]
        fwd = k0 + y0 + ((k1 + y1) - (k0 + y0)) * (self.spot - k0) / (k1 - k0)
        if abs(fwd / self.spot - 1) > 0.03:
            return self.spot, 1.0
        carry = self.spot - self.divs
        rate = min(max(math.log(fwd / carry) / self.t, 0.0), 0.08) if carry > 0 else 0.0
        return fwd, math.exp(-rate * self.t)

    def listed_range(self, cp: str) -> tuple[float, float] | None:
        ks = self.smile[cp][0]
        return (ks[0], ks[-1]) if ks else None

    def vol(self, cp: str, k: float) -> float | None:
        ks, vs = self.smile[cp][0], self.smile[cp][1]
        if not ks:
            ks, vs = self.smile["P" if cp == "C" else "C"][:2]
        return _interp(ks, vs, k) if ks else None

    def quote(self, cp: str, k: float) -> tuple[float, float]:
        """(mid, bid-ask spread) at strike k: the listed quote, else modelled."""
        if k in self.quotes[cp]:
            bid, ask = self.quotes[cp][k]
            return 0.5 * (bid + ask), ask - bid
        v = self.vol(cp, k)
        spr_k, spr = self.smile[cp][2], self.smile[cp][3]
        spread = _interp(spr_k, spr, k) if spr_k else 0.0
        if v is None:
            intrinsic = max(self.spot - k, 0.0) if cp == "C" else max(k - self.spot, 0.0)
            return intrinsic, spread
        return black76(cp, self.forward, k, self.t, v, self.disc), spread

    def delta(self, cp: str, k: float) -> float | None:
        v = self.vol(cp, k)
        if v is None or self.t <= 0:
            return None
        sd = v * math.sqrt(self.t)
        d1 = (math.log(self.forward / k) + 0.5 * sd * sd) / sd
        return _ncdf(d1) if cp == "C" else _ncdf(d1) - 1.0

    def strike_for_delta(self, cp: str, target: float) -> float | None:
        """The $1 strike inside the listed range whose delta is nearest `target`."""
        rng = self.listed_range(cp)
        if rng is None:
            return None
        best, best_err = None, None
        k = math.ceil(rng[0])
        while k <= rng[1]:
            d = self.delta(cp, float(k))
            if d is not None:
                err = abs(d - target)
                if best_err is None or err < best_err:
                    best, best_err = float(k), err
            k += STRIKE_STEP
        if best is None or best_err > DELTA_TOLERANCE:
            return None
        return best

    def atm_strike(self) -> float:
        return float(round(self.forward))


# --------------------------------------------------------------------------- fills and fees


def slip(mid: float, spread: float, frac: float) -> float:
    return min(frac * max(spread, 0.0), SLIPPAGE_CAP_FRAC_OF_MID * max(mid, 0.0))


def open_fee(legs: int, sells: int) -> float:
    return _fees.ic_open_fee(SYMBOL, 1, legs=legs, sell_legs=sells, ndigits=4)


def close_fee(legs: int, sells: int) -> float:
    return _fees.ic_close_fee(SYMBOL, 1, legs=legs, sell_legs=sells, ndigits=4)


# --------------------------------------------------------------------------- strategies


@dataclass(frozen=True)
class Spec:
    name: str
    kind: str  # put | cc | pcs | atm_put | atm_cc
    delta: float = 0.0
    take_profit: float | None = None
    width: str | float | None = None  # "ratio" or dollars, spreads only
    recipe_window: tuple[int, int, int] | None = None  # (lo, hi, preferred dte)


SPECS = [
    Spec("sp40", "put", -0.40, None, recipe_window=(28, 35, 30)),
    Spec("sp40-tp50", "put", -0.40, TAKE_PROFIT, recipe_window=(28, 35, 30)),
    Spec("cc15", "cc", 0.15, None, recipe_window=(30, 45, 30)),
    Spec("pcs25", "pcs", -0.25, TAKE_PROFIT, "ratio"),
    Spec("pcs25-5w", "pcs", -0.25, TAKE_PROFIT, 5.0),
]
CHECKS = [Spec("atm-put", "atm_put"), Spec("atm-cc", "atm_cc")]
WINDOWS = ("30-60", "45-60", "recipe")


def choose_expiry(day: date, exps: list[date], window: str, spec: Spec) -> date | None:
    dtes = sorted(((e - day).days, e) for e in exps)

    def nearest(lo: int, hi: int, pref: int | None = None):
        c = [(abs(d - pref) if pref is not None else d, e) for d, e in dtes if lo <= d <= hi]
        return min(c)[1] if c else None

    if window == "30-60":
        return nearest(30, 60)
    if window == "45-60":
        return nearest(45, 60) or nearest(30, 60)
    if window == "recipe":
        if spec.recipe_window is None:
            return None
        lo, hi, pref = spec.recipe_window
        return nearest(lo, hi, pref)
    if window == "roll":
        return nearest(20, 40)
    raise ValueError(window)


@dataclass
class Leg:
    cp: str
    strike: float
    qty: int  # -1 short, +1 long (contracts)
    fill: float  # per-share price paid (+) or received (-) is folded via qty
    mark: float = 0.0


@dataclass
class Position:
    entry_day: date
    exp: date
    legs: list[Leg]
    credit: float  # per-share net credit at fill (spreads/puts), call credit for cc
    capital: float  # dollars at risk / cash secured / share cost
    entry_fees: float
    slippage: float
    entry_spot: float
    rate: float
    shares: int = 0
    dividends: float = 0.0
    extra: dict = field(default_factory=dict)


def _mark_value(legs: list[Leg]) -> float:
    """Dollars to buy everything back at the marks (positive = pay)."""
    return sum(-leg.qty * leg.mark * MULT for leg in legs)


def try_enter(spec: Spec, sl: Slice, frac: float, stats: dict) -> Position | None:
    spot = sl.spot
    if spec.kind in ("put", "atm_put"):
        k = sl.atm_strike() if spec.kind == "atm_put" else sl.strike_for_delta("P", spec.delta)
        if k is None:
            stats["no_strike"] += 1
            return None
        mid, spr = sl.quote("P", k)
        s = slip(mid, spr, frac)
        fill = mid - s
        if fill <= 0:
            return None
        return Position(
            sl.day,
            sl.exp,
            [Leg("P", k, -1, fill, mid)],
            fill,
            k * MULT,
            open_fee(1, 1),
            s * MULT,
            spot,
            sl.rate,
        )
    if spec.kind in ("cc", "atm_cc"):
        k = sl.atm_strike() if spec.kind == "atm_cc" else sl.strike_for_delta("C", spec.delta)
        if k is None:
            stats["no_strike"] += 1
            return None
        mid, spr = sl.quote("C", k)
        s = slip(mid, spr, frac)
        fill = mid - s
        if fill <= 0:
            return None
        return Position(
            sl.day,
            sl.exp,
            [Leg("C", k, -1, fill, mid)],
            fill,
            spot * MULT,
            open_fee(1, 1),
            s * MULT,
            spot,
            sl.rate,
            shares=MULT,
        )
    if spec.kind == "pcs":
        ks = sl.strike_for_delta("P", spec.delta)
        if ks is None:
            stats["no_strike"] += 1
            return None
        smid, sspr = sl.quote("P", ks)
        sslip = slip(smid, sspr, frac)

        def build(w: float):
            lmid, lspr = sl.quote("P", ks - w)
            lslip = slip(lmid, lspr, frac)
            credit = (smid - sslip) - (lmid + lslip)
            return credit, lmid, lslip

        chosen = None
        if spec.width == "ratio":
            for w in range(MAX_WIDTH, 0, -1):
                credit, lmid, lslip = build(float(w))
                stats["best_ratio_seen"] = round(max(stats["best_ratio_seen"], credit / w), 3)
                if credit > 0 and credit / w >= RATIO_FLOOR:
                    chosen = (float(w), credit, lmid, lslip)
                    break
            if chosen is None:
                stats["ratio_unmet"] += 1
                return None
        else:
            w = float(spec.width)
            credit, lmid, lslip = build(w)
            if credit <= 0:
                return None
            chosen = (w, credit, lmid, lslip)
            if credit / w < RATIO_FLOOR:
                stats["over_2to1"] += 1
        w, credit, lmid, lslip = chosen
        legs = [Leg("P", ks, -1, smid - sslip, smid), Leg("P", ks - w, 1, lmid + lslip, lmid)]
        pos = Position(
            sl.day,
            sl.exp,
            legs,
            credit,
            (w - credit) * MULT,
            open_fee(2, 1),
            (sslip + lslip) * MULT,
            spot,
            sl.rate,
        )
        pos.extra["width"] = w
        return pos
    raise ValueError(spec.kind)


def close_cost(pos: Position, sl: Slice, frac: float) -> tuple[float, float, float]:
    """(dollars paid to close at the modelled fill, fees, slippage dollars); marks updated."""
    paid, s_total = 0.0, 0.0
    for leg in pos.legs:
        mid, spr = sl.quote(leg.cp, leg.strike)
        leg.mark = mid
        s = slip(mid, spr, frac)
        s_total += s
        # short: buy back at mid + s; long: sell at mid - s
        paid += (mid + s) * MULT if leg.qty < 0 else -(mid - s) * MULT
    sells = sum(1 for leg in pos.legs if leg.qty > 0)
    return paid, close_fee(len(pos.legs), sells), s_total * MULT


def settle(pos: Position, close: float) -> tuple[float, float]:
    """(dollars paid at expiry for the option legs at intrinsic, settlement fees)."""
    paid, fee = 0.0, 0.0
    net_long = 0
    for leg in pos.legs:
        intrinsic = max(close - leg.strike, 0.0) if leg.cp == "C" else max(leg.strike - close, 0.0)
        leg.mark = intrinsic
        paid += -leg.qty * intrinsic * MULT
        if intrinsic <= 0:
            continue
        fee += _fees.ASSIGNMENT_FEE_PER_SETTLEMENT
        if leg.cp == "P" and leg.qty < 0:  # assigned: 100 shares bought at the strike
            net_long += MULT
        else:
            # a long put exercised sells 100 at the strike; a covered call called away sells the
            # held shares at the strike and they are bought back at the close (no fee on buys)
            fee += _fees.stock_trade_fee(MULT, leg.strike, side="sell", ndigits=4)
            if leg.cp == "P":
                net_long -= MULT
    if net_long > 0:  # delivered shares disposed of at the expiry close
        fee += _fees.stock_trade_fee(net_long, close, side="sell", ndigits=4)
    return paid, fee


# --------------------------------------------------------------------------- the replay


def _prev_close(closes: dict[date, float], days: list[date], d: date) -> float | None:
    i = bisect.bisect_right(days, d)
    return closes[days[i - 1]] if i else None


def run(spec: Spec, window: str, data: dict, start: date, end: date, frac: float) -> dict:
    closes = {date.fromisoformat(k): v for k, v in data["closes"].items()}
    close_days = sorted(closes)
    divs = {date.fromisoformat(k): v for k, v in data["dividends"].items()}
    chain_days = sorted(date.fromisoformat(k) for k in data["chains"])
    chain_days = [d for d in chain_days if start <= d <= end and d in closes]
    stats = defaultdict(int)
    trades: list[dict] = []
    equity: list[tuple[date, float]] = []
    pos: Position | None = None
    realized = 0.0
    last_closed: date | None = None
    hold_start: date | None = None  # covered call: first day shares are held
    hold_basis = 0.0
    held_divs = 0.0  # dividends received while holding shares (cc), all cycles
    slice_cache: dict[tuple[date, date], Slice] = {}

    def get_slice(d: date, e: date) -> Slice | None:
        key = (d, e)
        if key not in slice_cache:
            rows = data["chains"].get(d.isoformat(), {}).get(e.isoformat())
            due = sum(a for ex, a in divs.items() if d < ex <= e)
            slice_cache[key] = Slice(d, e, rows, closes[d], due) if rows else None
        return slice_cache[key]

    def finish(p: Position, day: date, paid: float, exit_fee: float, exit_slip: float, how: str):
        nonlocal realized, last_closed
        option_gross = (p.credit * MULT) - paid  # credit at fill minus paid to close/settle
        share_pnl = 0.0
        if p.shares:
            share_pnl = p.shares * (closes[day] - p.entry_spot)
        fees_total = p.entry_fees + (exit_fee if how != "expired" else 0.0)
        settle_fee = exit_fee if how == "expired" else 0.0
        net = option_gross + share_pnl + p.dividends - fees_total - settle_fee
        if p.shares:
            # shares are held across cycles; only the option leg is realised here, the shares
            # stay in the equity line through `hold_basis`
            realized += option_gross - fees_total - settle_fee
        else:
            realized += net
        trades.append(
            {
                "entry": p.entry_day.isoformat(),
                "exit": day.isoformat(),
                "expiry": p.exp.isoformat(),
                "dte": (p.exp - p.entry_day).days,
                "strikes": [leg.strike for leg in p.legs],
                "credit": round(p.credit, 4),
                "how": how,
                "option_gross": round(option_gross, 2),
                "share_pnl": round(share_pnl, 2),
                "dividends": round(p.dividends, 2),
                "fees": round(fees_total, 2),
                "settlement": round(settle_fee, 2),
                "slippage_measure": round(p.slippage + exit_slip, 2),
                "net": round(net, 2),
                "capital": round(p.capital, 2),
                "rate": p.rate,
                "spot_entry": p.entry_spot,
                "spot_exit": closes[day],
                **p.extra,
            }
        )
        # an expiry rolls at the same close (as PUT and BXM do); an early close re-enters the
        # next chain day
        last_closed = day - timedelta(days=1) if how == "expired" else day

    for day in chain_days:
        # dividends on held shares (covered calls), before any expiry on the same day: forfeited
        # when the call is ITM at the close before the ex-date (it would be assigned early)
        if hold_start is not None:
            for ex, amt in divs.items():
                if equity and equity[-1][0] < ex <= day:
                    prev = _prev_close(closes, close_days, ex - timedelta(days=1))
                    live = pos is not None and pos.exp >= ex
                    if live and prev is not None and prev > pos.legs[0].strike:
                        stats["dividends_forfeited"] += 1
                    else:
                        held_divs += amt * MULT
                        if live:
                            pos.dividends += amt * MULT
        # expiry passed: settle on the expiration day's close
        if pos is not None and pos.exp <= day:
            xc = closes.get(pos.exp) or _prev_close(closes, close_days, pos.exp)
            paid, fee = settle(pos, xc)
            finish(pos, pos.exp if pos.exp in closes else day, paid, fee, 0.0, "expired")
            pos = None
        # take profit on a listed day
        if pos is not None and spec.take_profit is not None:
            sl = get_slice(day, pos.exp)
            if sl is not None and day > pos.entry_day:
                paid, fee, s = close_cost(pos, sl, frac)
                if paid <= spec.take_profit * pos.credit * MULT:
                    finish(pos, day, paid, fee, s, "closed")
                    pos = None
        elif pos is not None:
            sl = get_slice(day, pos.exp)
            if sl is not None:
                close_cost(pos, sl, frac)  # refresh marks only
        # entry
        if pos is None and (last_closed is None or day > last_closed):
            exps = [date.fromisoformat(e) for e in data["chains"][day.isoformat()]]
            exps = [e for e in exps if e > day]
            e = choose_expiry(day, exps, window, spec)
            if e is not None:
                sl = get_slice(day, e)
                if sl is not None:
                    pos = try_enter(spec, sl, frac, stats)
                    if pos is not None:
                        stats["entries"] += 1
                        if pos.shares and hold_start is None:
                            hold_start, hold_basis = day, closes[day]
            else:
                stats["no_expiry_days"] += 1
        unreal = 0.0
        if pos is not None:
            unreal = pos.credit * MULT - _mark_value(pos.legs) - pos.entry_fees
        share_eq = MULT * (closes[day] - hold_basis) + held_divs if hold_start is not None else 0.0
        equity.append((day, realized + unreal + share_eq))
    if pos is not None:
        stats["open_at_end"] = 1
    return {
        "spec": spec.name,
        "window": window,
        "trades": trades,
        "equity": equity,
        "stats": dict(stats),
        "hold": (hold_start, hold_basis, held_divs),
    }


# --------------------------------------------------------------------------- metrics


def max_drawdown(values: list[float]) -> float:
    peak, mdd = -math.inf, 0.0
    for v in values:
        peak = max(peak, v)
        mdd = max(mdd, peak - v)
    return mdd


def summarise(res: dict) -> dict:
    t = res["trades"]
    if not t:
        return {"n": 0, "stats": res["stats"]}
    first = date.fromisoformat(t[0]["entry"])
    last = date.fromisoformat(t[-1]["exit"])
    years = max((last - first).days / 365.25, 1e-9)
    nets = [x["net"] for x in t]
    caps = [x["capital"] for x in t]
    avg_cap = sum(caps) / len(caps)
    per_year: dict[str, float] = defaultdict(float)
    for x in t:
        per_year[x["exit"][:4]] += x["net"]
    eq = [v for d, v in res["equity"] if first <= d <= last]
    out = {
        "n": len(t),
        "win_rate": sum(1 for v in nets if v > 0) / len(t),
        "total_net": sum(nets),
        "avg_net": sum(nets) / len(t),
        "avg_capital": avg_cap,
        "roc_per_trade": sum(x["net"] / x["capital"] for x in t) / len(t),
        "annual_roc": sum(nets) / avg_cap / years,
        "max_dd": max_drawdown(eq),
        "worst": min(nets),
        "per_year": {k: round(v, 2) for k, v in sorted(per_year.items())},
        "fees": sum(x["fees"] + x["settlement"] for x in t),
        "slippage_measure": sum(x["slippage_measure"] for x in t),
        "avg_dte": sum(x["dte"] for x in t) / len(t),
        "closed_early": sum(1 for x in t if x["how"] == "closed"),
        "first": first.isoformat(),
        "last": last.isoformat(),
        "stats": res["stats"],
    }
    if res["hold"][0] is not None:  # covered call: split shares from the call overlay
        hold_start, basis, divs = res["hold"]
        overlay = sum(x["net"] - x["share_pnl"] - x["dividends"] for x in t)
        out["overlay_net"] = overlay
        out["buy_hold_net"] = MULT * (t[-1]["spot_exit"] - basis) + divs
        out["total_net_incl_gaps"] = out["buy_hold_net"] + overlay
        out["max_dd"] = max_drawdown([v for d, v in res["equity"]])
    if "width" in t[0]:
        out["avg_width"] = sum(x["width"] for x in t) / len(t)
        out["avg_ratio"] = sum(x["credit"] / x["width"] for x in t) / len(t)
    return out


# --------------------------------------------------------------------------- benchmark check


def read_cboe(path: Path) -> list[tuple[date, float]]:
    out = []
    with path.open(newline="") as fh:
        for row in csv.reader(fh):
            try:
                out.append((datetime.strptime(row[0], "%m/%d/%Y").date(), float(row[1])))
            except (ValueError, IndexError):
                continue
    return sorted(out)


def _level_on(series: list[tuple[date, float]], d: date) -> float:
    i = bisect.bisect_right([x for x, _ in series], d)
    return series[max(i - 1, 0)][1]


def compounded(returns: list[float]) -> list[float]:
    lv, out = 1.0, [1.0]
    for r in returns:
        lv *= 1 + r
        out.append(lv)
    return out


def check_against_index(res: dict, kind: str, series: list[tuple[date, float]]) -> dict:
    """Cycle returns of the ATM replay vs the index sampled on the same cycle-end dates."""
    t = res["trades"]
    rets, idx_rets = [], []
    prev_exit = date.fromisoformat(t[0]["entry"])
    prev_spot = t[0]["spot_entry"]
    for x in t:
        exit_d = date.fromisoformat(x["exit"])
        if kind == "put":
            tau = (exit_d - prev_exit).days / 365.0
            interest = x["capital"] * (math.exp(x["rate"] * tau) - 1)
            rets.append((x["net"] + interest) / x["capital"])
        else:  # covered call held continuously: shares from the previous cycle end
            share = MULT * (x["spot_exit"] - prev_spot)
            option_net = x["net"] - x["share_pnl"] - x["dividends"]
            rets.append((share + x["dividends"] + option_net) / (MULT * prev_spot))
            prev_spot = x["spot_exit"]
        idx_rets.append(_level_on(series, exit_d) / _level_on(series, prev_exit) - 1)
        prev_exit = exit_d
    first, last = date.fromisoformat(t[0]["entry"]), date.fromisoformat(t[-1]["exit"])
    years = (last - first).days / 365.25
    rep, idx = compounded(rets), compounded(idx_rets)
    daily = [v for d, v in series if first <= d <= last]
    n = len(rets)
    mr, mi = sum(rets) / n, sum(idx_rets) / n
    cov = sum((a - mr) * (b - mi) for a, b in zip(rets, idx_rets, strict=True))
    va = sum((a - mr) ** 2 for a in rets)
    vb = sum((b - mi) ** 2 for b in idx_rets)
    return {
        "first": first.isoformat(),
        "last": last.isoformat(),
        "cycles": n,
        "replay_cagr": rep[-1] ** (1 / years) - 1,
        "index_cagr_sampled": idx[-1] ** (1 / years) - 1,
        "index_cagr_daily": (daily[-1] / daily[0]) ** (1 / years) - 1,
        "replay_mdd_cycles": max_drawdown([math.log(v) for v in rep]),
        "index_mdd_cycles": max_drawdown([math.log(v) for v in idx]),
        "index_mdd_daily": max_drawdown([math.log(v) for v in daily]),
        "corr": cov / math.sqrt(va * vb) if va > 0 and vb > 0 else float("nan"),
        "avg_rate": sum(x["rate"] for x in t) / n,
    }


def index_context(series: list[tuple[date, float]], first: date, last: date) -> dict:
    daily = [v for d, v in series if first <= d <= last]
    years = (last - first).days / 365.25
    return {
        "cagr": (daily[-1] / daily[0]) ** (1 / years) - 1,
        "mdd": max_drawdown([math.log(v) for v in daily]),
    }


# --------------------------------------------------------------------------- report


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def print_report(data: dict, results: dict, checks: dict, frac: float, start: date, end: date) -> None:
    chains = data["chains"]
    days = sorted(d for d in chains if start.isoformat() <= d <= end.isoformat())
    rows = sum(len(r) for d in days for r in chains[d].values())
    per_year = defaultdict(int)
    for d in days:
        per_year[d[:4]] += 1
    print("## Data\n")
    print(f"- chain days {days[0]} .. {days[-1]}: {len(days)} (rows on standard monthlies: {rows})")
    print(f"- chain days per year: {dict(sorted(per_year.items()))}")
    print(f"- SPY closes: {len(data['closes'])}, dividends: {len(data['dividends'])}")
    print(
        f"- fills: mid less {frac:g} of the bid-ask per leg (capped at {SLIPPAGE_CAP_FRAC_OF_MID:g} of mid); "
        f"open fee/leg ${open_fee(1, 1):.4f} sold, ${open_fee(1, 0):.4f} bought; close fee/leg "
        f"${close_fee(1, 0):.4f} (+TAF on sells); "
        f"${_fees.ASSIGNMENT_FEE_PER_SETTLEMENT:.2f} per ITM leg at expiry\n"
    )
    print("## Results (one contract, net of all costs)\n")
    print(
        "| strategy | window | n | avg DTE | win | total net | avg net | avg capital | ROC/trade "
        "| annual ROC | max DD | worst | closed early | fees+settle | slippage (in fills) |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for (name, window), s in results.items():
        if not s.get("n"):
            print(f"| {name} | {window} | 0 | | | | | | | | | | | | |")
            continue
        print(
            f"| {name} | {window} | {s['n']} | {s['avg_dte']:.0f} | {_pct(s['win_rate'])} "
            f"| {s['total_net']:,.0f} | {s['avg_net']:,.0f} | {s['avg_capital']:,.0f} "
            f"| {_pct(s['roc_per_trade'])} | {_pct(s['annual_roc'])} | {s['max_dd']:,.0f} "
            f"| {s['worst']:,.0f} | {s['closed_early']} | {s['fees']:,.0f} | {s['slippage_measure']:,.0f} |"
        )
    print("\n## Net by exit year\n")
    years = sorted({y for s in results.values() for y in s.get("per_year", {})})
    print("| strategy | window | " + " | ".join(years) + " |")
    print("|---|---|" + "---|" * len(years))
    for (name, window), s in results.items():
        py = s.get("per_year", {})
        print(f"| {name} | {window} | " + " | ".join(f"{py.get(y, 0):,.0f}" for y in years) + " |")
    print("\n## Notes per run\n")
    for (name, window), s in results.items():
        extra = []
        if "overlay_net" in s:
            extra.append(
                f"call overlay net {s['overlay_net']:,.0f}, buy-and-hold 100 SPY {s['buy_hold_net']:,.0f}, "
                f"total incl. uncovered gaps {s['total_net_incl_gaps']:,.0f}"
            )
        if "avg_width" in s:
            extra.append(f"avg width ${s['avg_width']:.1f}, avg credit/width {s['avg_ratio']:.2f}")
        if s.get("n"):
            extra.append(f"{s['first']} .. {s['last']}")
        print(f"- {name} {window}: stats {s.get('stats', {})}; " + "; ".join(extra))
    if checks:
        print("\n## Benchmark check (ATM variants vs Cboe indexes, same cycle-end dates)\n")
        print(
            "| replay | index | span | cycles | replay CAGR | index CAGR (sampled) | index CAGR (daily) "
            "| replay MDD | index MDD (sampled) | index MDD (daily) | cycle corr |"
        )
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for (rep, idx), c in checks.items():
            if "cycles" not in c:
                continue
            print(
                f"| {rep} | {idx} | {c['first']}..{c['last']} | {c['cycles']} | {_pct(c['replay_cagr'])} "
                f"| {_pct(c['index_cagr_sampled'])} | {_pct(c['index_cagr_daily'])} "
                f"| {_pct(1 - math.exp(-c['replay_mdd_cycles']))} "
                f"| {_pct(1 - math.exp(-c['index_mdd_cycles']))} "
                f"| {_pct(1 - math.exp(-c['index_mdd_daily']))} | {c['corr']:.2f} |"
            )
        for (rep, idx), c in checks.items():
            if "cagr" in c:
                print(f"- {idx} over {rep}: CAGR {_pct(c['cagr'])}, MDD {_pct(1 - math.exp(-c['mdd']))}")
            elif "avg_rate" in c:
                print(f"- {rep}: mean parity-implied rate {_pct(c['avg_rate'])}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    ap.add_argument("--end", type=date.fromisoformat, default=date.today())
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--cache", type=Path, help="JSON copy of the rows read, reused when it covers the dates")
    ap.add_argument("--cboe-dir", type=Path, help="directory holding PUT/BXM/WPUT _History.csv")
    ap.add_argument("--slippage-frac", type=float, default=_fees.DEFAULT_COSTS["slippage_frac_of_spread"])
    ap.add_argument("--json", type=Path, help="write summaries and trades here")
    args = ap.parse_args(argv)

    data = load_data(args.host, args.port, args.start, args.end, args.cache)
    if not data["chains"]:
        print("no chain rows in range", file=sys.stderr)
        return 1
    raw: dict[tuple[str, str], dict] = {}
    results: dict[tuple[str, str], dict] = {}
    for spec in SPECS:
        for window in WINDOWS:
            if window == "recipe" and spec.recipe_window is None:
                continue
            r = run(spec, window, data, args.start, args.end, args.slippage_frac)
            raw[(spec.name, window)] = r
            results[(spec.name, window)] = summarise(r)
    checks: dict[tuple[str, str], dict] = {}
    for spec in CHECKS:
        r = run(spec, "roll", data, args.start, args.end, args.slippage_frac)
        raw[(spec.name, "roll")] = r
        results[(spec.name, "roll")] = summarise(r)
        if args.cboe_dir and r["trades"]:
            idx = "PUT" if spec.kind == "atm_put" else "BXM"
            p = args.cboe_dir / f"{idx}_History.csv"
            if p.exists():
                checks[(spec.name, idx)] = check_against_index(r, spec.kind[4:], read_cboe(p))
    if args.cboe_dir and raw[("atm-put", "roll")]["trades"]:
        t = raw[("atm-put", "roll")]["trades"]
        first, last = date.fromisoformat(t[0]["entry"]), date.fromisoformat(t[-1]["exit"])
        for idx in ("WPUT", "PUT", "BXM"):
            p = args.cboe_dir / f"{idx}_History.csv"
            if p.exists():
                checks[("context", idx)] = index_context(read_cboe(p), first, last)
    print_report(data, results, checks, args.slippage_frac, args.start, args.end)
    if args.json:
        out = {
            "results": {f"{k[0]}|{k[1]}": v for k, v in results.items()},
            "checks": {f"{k[0]}|{k[1]}": v for k, v in checks.items()},
            "trades": {f"{k[0]}|{k[1]}": r["trades"] for k, r in raw.items()},
        }
        args.json.write_text(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
