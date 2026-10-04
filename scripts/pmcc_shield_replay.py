"""What the shield arms would have made beside PMCC's control, replayed from 2011 (2026-10-04).

    python scripts/pmcc_shield_replay.py                        # every symbol, every arm
    python scripts/pmcc_shield_replay.py --symbols SLV GLD
    python scripts/pmcc_shield_replay.py --until 2026-10-02     # the window docs/shield-study.md states
    python scripts/pmcc_shield_replay.py --fetch                # fetch the Cboe vol-index files first
    python scripts/pmcc_shield_replay.py --skew-check           # the model against the sampled quotes

Read-only, except that `--fetch` writes the vol-index histories it needs under
`$CHERRYPICK_HOME/data/pmcc/replay/` (VIX and VXN are read from the copies
`scripts/fetch_market_files.py` already keeps). Prices come from the technicals history store
(`scripts/refresh_dolt_data.py`).

**The question.** The shield arms (packages/pmcc/CLAUDE.md) hold a ~1-year 0.925-delta call and sell
a weekly 0.70-delta call against it; control re-buys a ~21-DTE 0.875-delta call every cycle and
sells an ATM weekly. No options history reaches back far enough to replay either from real quotes,
so both are priced off each symbol's own Cboe vol index, to ask two things: how much of the
difference is the long (held vs re-bought), and how much is the short (a variance-premium bet the IV
assumption decides). The 30% stop is run on and off, to show what it does.

**The model**, stated so it can be argued with:
- IV(K, T) = c * index * term(T) * (1 + b * min(3, max(0, -z))), z = ln(K/S) / (atm * sqrt(T)).
  term(T) is VIX9D/VIX up to 9 days, linear to 1.0 at 30, flat beyond. XSP's c is fitted to the
  check below: the PMCC ledger's recorded leg IVs gave 0.87, which over-prices the weekly ATM short
  by about 5% against fifteen years of WPUT (+2.2 points a year, the same sign in every five-year
  sub-period), so it is 0.83; b (the skew) is the ledger's. QQQ and IWM borrow XSP's values; GLD and
  SLV's come from the video's own SLV prices. SLV's index has a hole (2022-02 to its 2025 relaunch),
  bridged by GVZ x 1.73.
- Fills at model mid. Slippage is 0.125 of the quoted spread per fill (the suite's default cost
  model), with spreads as a fraction of spot per leg type; commission $1 per contract to open and
  $0.14 clearing and regulatory per fill, scaled onto the split-adjusted series.
- Notional sizing: one share-equivalent per dollar of NAV at each entry, held fixed until the position
  closes, as the module holds its quantity. Idle cash earns the 3-month T-bill (annual averages).
- XSP is replayed on SPY's closes (same index, one tenth the size: every figure is a ratio).

**Each arm, as the module runs it.**
- `control`: a long at 17-25 DTE nearest 21 and 0.875 delta, an ATM short at the soonest Friday
  5-11 DTE. The short settles at its expiry; the long rides to the next session, where it is sold
  and the next position entered.
- `shield_hold`: a long at the third-Friday monthly nearest 360 DTE in [240, 540] (what
  `clock.leap_expiration` picks) and 0.925 delta; a 0.70-delta short at the soonest Friday 5-11 DTE,
  never past the long and never at or below its strike. On the short's expiry day it is bought back
  at any moneyness (the `ira` account policy) and the next one sold. The position closes when the
  long reaches 45 DTE, or on the stop (P&L to date at or below -30% of the long's cost); the next
  position enters the following session.
- `shield`: as `shield_hold`, and rolls early when the short's extrinsic falls to 15% of its value
  at sale (85% decayed) or spot reaches its strike, at most once a session.
- Ex-dividend (QQQ, IWM): a short whose life [sale day, expiry] spans an ex-date is refused, at entry
  (no position) and at a roll (the position runs without a short until a later day clears it).

**The put benchmark** (asked 2026-10-04): a short 20-delta put at the same year-long expiry, held
to 45 DTE and re-entered, on the same notional sizing (which keeps it cash-secured). It matches the
shield's net delta on the day it opens and nothing after: by parity the shield is a short ~30-delta
weekly put plus a long ~7.5-delta year put, so the two differ in gamma (weekly against yearly),
vega (the shield is long it, the put short) and tail (the shield's loss stops at its debit, the
put's runs to its strike). Its result rests on the model's year-long skew, which nothing here has
calibrated -- b comes from 21-DTE calls and the term structure is flat past 30 days -- so it prints
beside its own skew sensitivity, and `--skew-check` compares the model against the year-long put
quotes the paper loop samples (`pmcc_skew_samples`) once they exist.

**What it is not.** Daily closes, so an intraday roll trigger fires a session late and an intraday
stop is missed. No early assignment, no pin risk, no gap between the roll time and the bell; an
expiring short is bought back at its intrinsic value, so an OTM buyback costs nothing here and a
few cents in life. No settlement fees. The vol index is an IV proxy, so the short's edge is the
assumption's: the IV-scale rows move it, and that sensitivity is the finding, not noise.

**Validation, every run.** A weekly ATM buy-write on SPY's total-return closes is the same exposure
as Cboe's WPUT (one-week ATM SPX puts on T-bill collateral) by put-call parity. Priced through this
model at IV x1.0, its CAGR must land within 1.5 points a year of WPUT's over the same window, or
every table prints marked UNCALIBRATED and the run exits 1.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
import urllib.request
from bisect import bisect_right
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import home as _home
from cherrypick.overview import files as _files
from cherrypick.technicals import adjust as _adjust
from cherrypick.technicals import paths as _paths
from cherrypick.technicals import store as _store

START = "2011-03-16"  # VXSLV's first print; VIX9D starts 2011-01
CALIBRATION_TOLERANCE = 0.015  # buy-write CAGR vs WPUT's, a year
SLIP = 0.125
COMM_OPEN = 0.01 + 0.0014  # per share-equivalent: $1 per contract to open, $0.14 per fill
COMM_CLOSE = 0.0014
UA = "cherrypick-research/1.0"

# 3-month T-bill, annual averages: carry on idle cash only
TBILL = {
    2011: 0.0005, 2012: 0.0009, 2013: 0.0006, 2014: 0.0003, 2015: 0.0005, 2016: 0.0032, 2017: 0.0093,
    2018: 0.0194, 2019: 0.0206, 2020: 0.0037, 2021: 0.0004, 2022: 0.0202, 2023: 0.0507, 2024: 0.0497,
    2025: 0.0420, 2026: 0.0380,
}  # fmt: skip

# Spreads are a fraction of spot per leg type: measured from the PMCC ledger for XSP, assumed elsewhere.
SYMBOLS = {
    "XSP": dict(
        source="SPY", index="VIX", c=0.83, b=0.20, q=0.014, grid=0.002, settlement="cash", basis="WPUT",
        spread=dict(short_atm=0.00017, short_itm=0.00025, long_21=0.0038, long_year=0.006),
    ),
    "QQQ": dict(
        source="QQQ", index="VXN", c=0.83, b=0.20, q=0.006, grid=0.002, settlement="physical", basis="as XSP",
        spread=dict(short_atm=0.0002, short_itm=0.0003, long_21=0.003, long_year=0.004),
    ),
    "GLD": dict(
        source="GLD", index="GVZ", c=0.95, b=0.05, q=0.0, grid=0.003, settlement="physical", basis="as SLV",
        spread=dict(short_atm=0.0004, short_itm=0.0006, long_21=0.003, long_year=0.005),
    ),
    "IWM": dict(
        source="IWM", index="RVX", c=0.83, b=0.20, q=0.012, grid=0.003, settlement="physical", basis="as XSP",
        spread=dict(short_atm=0.0003, short_itm=0.0004, long_21=0.004, long_year=0.006),
    ),
    "SLV": dict(
        source="SLV", index="VXSLV", c=0.95, b=0.05, q=0.0, grid=0.01, settlement="physical",
        basis="the video's SLV prices", bridge=("GVZ", 1.73, "2022-02-11", "2025-12-31"),
        spread=dict(short_atm=0.0005, short_itm=0.0008, long_21=0.003, long_year=0.006),
    ),
}  # fmt: skip
INDEXES = ("VIX", "VIX9D", "VXN", "GVZ", "RVX", "VXSLV", "WPUT")


@dataclass(frozen=True)
class Arm:
    name: str
    long: str  # "21": re-bought each cycle (control) | "year": held | "stock": the buy-write check
    long_delta: float
    short: str  # "atm" | "delta"
    short_delta: float = 0.0
    early_decay: float | None = None  # roll when extrinsic <= this fraction of its value at sale
    breach_roll: bool = False
    stop_loss_frac: float | None = None
    long_close_dte: int = 45
    resize_weekly: bool = False  # the buy-write check only: WPUT and BXMW re-write their whole NAV weekly


CONTROL = Arm("control", long="21", long_delta=0.875, short="atm")
SHIELD_HOLD = Arm(
    "shield_hold", long="year", long_delta=0.925, short="delta", short_delta=0.70, stop_loss_frac=0.30
)
SHIELD = replace(SHIELD_HOLD, name="shield", early_decay=0.15, breach_roll=True)
ARMS = (
    CONTROL,
    SHIELD_HOLD,
    SHIELD,
    replace(SHIELD_HOLD, name="shield_hold, no stop", stop_loss_frac=None),
    replace(SHIELD, name="shield, no stop", stop_loss_frac=None),
)
BUY_WRITE = Arm("weekly ATM buy-write", long="stock", long_delta=1.0, short="atm", resize_weekly=True)
COMPONENTS = ("prem", "payout", "long", "cost", "interest")


# --------------------------------------------------------------------------- inputs
def replay_dir() -> Path:
    return _home.data_dir("pmcc") / "replay"


def index_path(name: str) -> Path:
    """The routine fetcher's copy where it keeps one, so the shared indexes are never two files."""
    return _files.cboe_path(name) if name in _files.CBOE_INDEXES else replay_dir() / f"{name}.csv"


def fetch_indexes() -> list[str]:
    """Fetch every index the routine fetcher does not keep. A file that parses to less than the one
    on disk is refused, as `fetch_market_files` refuses it."""
    problems = []
    for name in INDEXES:
        if name in _files.CBOE_INDEXES:
            continue
        req = urllib.request.Request(_files.CBOE_URL.format(symbol=name), headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except OSError as exc:
            problems.append(f"{name}: {exc}")
            continue
        path = index_path(name)
        old = _files.parse_cboe(path.read_text(encoding="utf-8")) if path.exists() else []
        refused = _files.history_problems(old, _files.parse_cboe(text))
        if refused:
            problems.append(f"{name} not replaced: {'; '.join(refused)}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    return problems


def index(name: str) -> dict[str, float]:
    return {d.isoformat(): v for d, v in _files.parse_cboe(index_path(name).read_text(encoding="utf-8"))}


def closes(conn, symbol: str, *, total_return: bool = False) -> tuple[dict, dict]:
    """Split-adjusted closes and the factor mapping a raw price onto them. Dividends stay in -- a
    strike follows a split, never a dividend -- unless `total_return`, which the buy-write check is."""
    raw = _store.raw_bars(conn, symbol)
    splits = _adjust.dedupe_splits(raw, _store.splits(conn, symbol))
    bars = _adjust.adjust(raw, splits, _store.dividends(conn, symbol)[0] if total_return else [])
    bars = bars[_adjust.series_break(bars) :]
    return {b.date: b.close for b in bars}, {b.date: b.price_factor for b in bars}


def rate(day: str) -> float:
    return TBILL[int(day[:4])]


def yrs(a: str, b: str) -> float:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days / 365.0


def cycle_dates(days: list[str]) -> list[str]:
    """The last session of each ISO week: the weekly expirations, holiday-shifted by construction."""
    out = []
    for i, d in enumerate(days):
        nxt = days[i + 1] if i + 1 < len(days) else None
        if (
            nxt is None
            or date.fromisoformat(nxt).isocalendar()[:2] != date.fromisoformat(d).isocalendar()[:2]
        ):
            out.append(d)
    return out


def short_expiry(cycles: list[str], d: str, cap: str | None) -> str | None:
    """`clock.short_expiration`: the soonest weekly 5-11 days out, never after `cap` (the long's)."""
    for exp in cycles[bisect_right(cycles, d) : bisect_right(cycles, d) + 3]:
        if cap is not None and exp > cap:
            return None
        if 5 <= (date.fromisoformat(exp) - date.fromisoformat(d)).days <= 11:
            return exp
    return None


def control_long_expiry(cycles: list[str], d: str) -> str | None:
    """Control's long: the weekly 17-25 days out nearest 21, a tie taking the nearer date."""
    best = None
    for exp in cycles[bisect_right(cycles, d) : bisect_right(cycles, d) + 5]:
        dte = (date.fromisoformat(exp) - date.fromisoformat(d)).days
        if 17 <= dte <= 25 and (best is None or abs(dte - 21) < abs(best[1] - 21)):
            best = (exp, dte)
    return best[0] if best else None


def year_expiry(d: str) -> str:
    """`clock.leap_expiration` over a full listing: the third Friday nearest 360 days out in
    [240, 540], a tie taking the nearer. The holiday shift is ignored: the long closes at 45 DTE."""
    today = date.fromisoformat(d)
    best = None
    for k in range(7, 19):
        first = date(today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12 + 1, 1)
        exp = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
        dte = (exp - today).days
        if 240 <= dte <= 540 and (best is None or abs(dte - 360) < abs(best[1] - 360)):
            best = (exp, dte)
    return best[0].isoformat()


def ex_date_in(ex_dates: list[str], start: str, end: str) -> bool:
    i = bisect_right(ex_dates, start) - 1  # `engine.ex_date_in_span`: the CLOSED span
    return any(start <= x <= end for x in ex_dates[max(i, 0) : i + 3])


# --------------------------------------------------------------------------- pricing
def ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _d1(S, K, T, r, q, sig):
    return (math.log(S / K) + (r - q + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))


class Market:
    """One symbol's inputs: closes, the raw-to-adjusted factor, its vol index (bridged where it has a
    hole), VIX and VIX9D for the term structure, and the ex-dates its shorts are refused across."""

    def __init__(self, conn, symbol: str, *, total_return: bool = False):
        self.symbol, self.spec = symbol, SYMBOLS[symbol]
        self.b = self.spec["b"]  # the skew term, overridable for the sensitivity rows
        self.px, self.pf = closes(conn, self.spec["source"], total_return=total_return)
        self.ix = index(self.spec["index"])
        if "bridge" in self.spec:
            name, scale, lo, hi = self.spec["bridge"]
            for d, v in index(name).items():
                if d not in self.ix and lo < d < hi:
                    self.ix[d] = v * scale
        self.vix, self.v9 = index("VIX"), index("VIX9D")
        self.days = sorted(
            d for d in self.px if d in self.ix and d in self.vix and d in self.v9 and d >= START
        )
        self.cycles = cycle_dates(self.days)
        physical = self.spec["settlement"] == "physical"
        divs = _store.dividends(conn, self.spec["source"])[0] if physical else []
        self.ex_dates = sorted(x.ex_date for x in divs)

    def atm(self, d: str, T: float, iv_scale: float) -> float:
        ts, days = self.v9[d] / self.vix[d], T * 365
        term = ts if days <= 9 else 1.0 if days >= 30 else ts + (1 - ts) * (days - 9) / 21
        return self.spec["c"] * self.ix[d] / 100 * iv_scale * term

    def iv(self, d, S, K, T, iv_scale):
        T = max(T, 1 / 365)
        a = self.atm(d, T, iv_scale)
        z = math.log(K / S) / (a * math.sqrt(T))
        return a * (1 + self.b * min(3.0, max(0.0, -z)))

    def price(self, d, S, K, T, iv_scale):
        if T <= 0:
            return max(S - K, 0.0)
        sig, r, q = self.iv(d, S, K, T, iv_scale), rate(d), self.spec["q"]
        d1 = _d1(S, K, T, r, q, sig)
        return S * math.exp(-q * T) * ncdf(d1) - K * math.exp(-r * T) * ncdf(d1 - sig * math.sqrt(T))

    def delta(self, d, S, K, T, iv_scale):
        if T <= 0:
            return 1.0 if S > K else 0.0
        q = self.spec["q"]
        return math.exp(-q * T) * ncdf(_d1(S, K, T, rate(d), q, self.iv(d, S, K, T, iv_scale)))

    def put_price(self, d, S, K, T, iv_scale):
        """By parity off the call, so both rights share one IV surface."""
        if T <= 0:
            return max(K - S, 0.0)
        q = self.spec["q"]
        return self.price(d, S, K, T, iv_scale) - S * math.exp(-q * T) + K * math.exp(-rate(d) * T)

    def put_delta(self, d, S, K, T, iv_scale):
        """Negative, as a put's is."""
        if T <= 0:
            return -1.0 if S < K else 0.0
        return self.delta(d, S, K, T, iv_scale) - math.exp(-self.spec["q"] * T)

    def strike_for_put_delta(self, d, S, T, target, iv_scale):
        """The grid strike whose put delta is nearest `-target`, searched down from spot."""
        g, best = self.spec["grid"], None
        for n in range(600):
            K = S * (1 - g * n)
            if K <= S * 0.05:
                break
            dl = -self.put_delta(d, S, K, T, iv_scale)
            if best is None or abs(dl - target) < abs(best[1] - target):
                best = (K, dl)
            if dl < target - 0.05:
                break
        return best[0]

    def strike_for_delta(self, d, S, T, target, iv_scale):
        """The grid strike nearest `target`, searched down from spot."""
        g, best = self.spec["grid"], None
        for n in range(400):
            K = S * (1 - g * n)
            if K <= S * 0.05:
                break
            dl = self.delta(d, S, K, T, iv_scale)
            if best is None or abs(dl - target) < abs(best[1] - target):
                best = (K, dl)
            if dl > target + 0.04:
                break
        return best[0]


# --------------------------------------------------------------------------- the replay
@dataclass
class Leg:
    K: float
    exp: str
    ext0: float = 0.0  # a short's extrinsic at sale


@dataclass
class Position:
    u: float  # share-equivalents, fixed at entry
    long: Leg | None = None
    short: Leg | None = None
    long_cost: float = 0.0  # the stop's base: the long's premium
    flow: float = 0.0  # every cash flow of this position, costs included, interest not
    settled: bool = False  # control: the short settled, the long rides to the next session


def run(m: Market, arm: Arm, *, iv_scale=1.0, cost_scale=1.0, since=None, until=None) -> dict:
    days = [d for d in m.days if (since is None or d >= since) and (until is None or d <= until)]
    cycle_set = set(m.cycles)
    sp = m.spec["spread"]
    long_kind = "long_21" if arm.long == "21" else "long_year"
    short_kind = "short_atm" if arm.short == "atm" else "short_itm"
    state = {"cash": 1.0, "pos": None, "last_close": None}
    comp = dict.fromkeys(COMPONENTS, 0.0)
    counts = dict.fromkeys(
        (
            "entries",
            "shorts_sold",
            "early_rolls",
            "stops",
            "long_rolls",
            "ex_div_refusals",
            "weeks_short_less",
        ),
        0,
    )
    navs, weeks, net_delta = [], [], []

    def book(kind, amount):
        state["cash"] += amount
        comp[kind] += amount
        if state["pos"] is not None and kind != "interest":
            state["pos"].flow += amount

    def slip(kind, S, mid):
        return cost_scale * min(SLIP * sp[kind] * S, 0.15 * mid) if mid > 0 else 0.0

    def comm(d, opening):
        return cost_scale * (COMM_OPEN if opening else COMM_CLOSE) * m.pf[d]

    def leg_value(d, S, leg, *, is_long):
        if is_long and arm.long == "stock":
            return S
        return m.price(d, S, leg.K, max(yrs(d, leg.exp), 0.0), iv_scale)

    def sell_short(d, S, cap) -> bool:
        pos = state["pos"]
        exp = short_expiry(m.cycles, d, cap)
        if exp is None:
            return False
        if ex_date_in(m.ex_dates, d, exp):
            counts["ex_div_refusals"] += 1
            return False
        t = yrs(d, exp)
        K = S if arm.short == "atm" else m.strike_for_delta(d, S, t, arm.short_delta, iv_scale)
        if arm.long == "year" and K <= pos.long.K:
            return False
        mid = m.price(d, S, K, t, iv_scale)
        book("prem", pos.u * mid)
        book("cost", -pos.u * (slip(short_kind, S, mid) + comm(d, True)))
        pos.short = Leg(K, exp, mid - max(S - K, 0.0))
        counts["shorts_sold"] += 1
        return True

    def buy_short(d, S):
        pos = state["pos"]
        mid = leg_value(d, S, pos.short, is_long=False)
        book("payout", -pos.u * mid)
        if mid > 0:
            book("cost", -pos.u * (slip(short_kind, S, mid) + comm(d, False)))
        pos.short = None

    def sell_long(d, S):
        pos = state["pos"]
        mid = leg_value(d, S, pos.long, is_long=True)
        book("long", pos.u * mid)
        if arm.long != "stock":
            book("cost", -pos.u * (slip(long_kind, S, mid) + comm(d, False)))
        pos.long = None

    def close(d, S):
        if state["pos"].short is not None:
            buy_short(d, S)
        if state["pos"].long is not None:
            sell_long(d, S)
        state["pos"], state["last_close"] = None, d

    def enter(d, S) -> bool:
        lexp = {"21": control_long_expiry(m.cycles, d), "year": year_expiry(d), "stock": None}[arm.long]
        if arm.long != "stock" and lexp is None:
            return False
        sexp = short_expiry(m.cycles, d, lexp)
        if sexp is None:
            return False
        if ex_date_in(m.ex_dates, d, sexp):  # `_entry_guards`: the whole entry is refused
            counts["ex_div_refusals"] += 1
            return False
        pos = state["pos"] = Position(u=state["cash"] / S)
        if arm.long == "stock":
            book("long", -pos.u * S)
            pos.long = Leg(0.0, "9999-12-31")
        else:
            t = yrs(d, lexp)
            K = m.strike_for_delta(d, S, t, arm.long_delta, iv_scale)
            mid = m.price(d, S, K, t, iv_scale)
            book("long", -pos.u * mid)
            book("cost", -pos.u * (slip(long_kind, S, mid) + comm(d, True)))
            pos.long, pos.long_cost = Leg(K, lexp), pos.u * mid
        counts["entries"] += 1
        sell_short(d, S, lexp)
        return True

    def marks(d, S):
        pos = state["pos"]
        if pos is None:
            return 0.0, 0.0
        lv = leg_value(d, S, pos.long, is_long=True) if pos.long else 0.0
        sv = leg_value(d, S, pos.short, is_long=False) if pos.short else 0.0
        return pos.u * lv, pos.u * sv

    week_start, prev = None, None
    for d in days:
        S = m.px[d]
        if prev is not None:
            book("interest", state["cash"] * rate(prev) * yrs(prev, d))
        prev = d
        pos = state["pos"]
        if pos is not None and arm.long == "21":
            if pos.settled:  # the long rides to this session, is sold, and the next position enters
                close(d, S)
                enter(d, S)
            elif pos.short is not None and d >= pos.short.exp:
                buy_short(d, S)  # settles at its intrinsic value at the bell
                pos.settled = True
        elif pos is not None:
            cap = pos.long.exp if arm.long == "year" else None
            lv, sv = marks(d, S)
            if arm.stop_loss_frac is not None and pos.flow + lv - sv <= -arm.stop_loss_frac * pos.long_cost:
                close(d, S)
                counts["stops"] += 1
            elif (
                arm.long == "year"
                and (date.fromisoformat(cap) - date.fromisoformat(d)).days <= arm.long_close_dte
            ):
                close(d, S)
                counts["long_rolls"] += 1
            elif pos.short is None:
                sell_short(d, S, cap)
            elif d >= pos.short.exp:  # the Friday roll: bought back at any moneyness, the next one sold
                buy_short(d, S)
                if arm.resize_weekly:
                    target = (state["cash"] + pos.u * S) / S
                    book("long", -(target - pos.u) * S)
                    pos.u = target
                sell_short(d, S, cap)
            elif arm.early_decay is not None and yrs(d, pos.short.exp) * 365 >= 1:
                s = pos.short
                mid = m.price(d, S, s.K, yrs(d, s.exp), iv_scale)
                if mid - max(S - s.K, 0.0) <= arm.early_decay * s.ext0 or (arm.breach_roll and S <= s.K):
                    buy_short(d, S)
                    sell_short(d, S, cap)
                    counts["early_rolls"] += 1
        elif d != state["last_close"]:
            enter(d, S)

        pos = state["pos"]
        lv, sv = marks(d, S)
        nav = state["cash"] + lv - sv
        navs.append((d, nav))
        if pos is not None and pos.long is not None and nav > 0:
            ld = (
                1.0
                if arm.long == "stock"
                else m.delta(d, S, pos.long.K, max(yrs(d, pos.long.exp), 1e-6), iv_scale)
            )
            sd = m.delta(d, S, pos.short.K, max(yrs(d, pos.short.exp), 1e-6), iv_scale) if pos.short else 0.0
            net_delta.append(pos.u * (ld - sd) * S / nav)
        if d in cycle_set:
            if pos is not None and pos.long is not None and pos.short is None and not pos.settled:
                counts["weeks_short_less"] += 1  # control's long riding to Monday is its design, not a gap
            if week_start is not None and week_start[0] > 0:
                c = dict(comp)
                c["long"] += lv - week_start[1]
                c["payout"] += week_start[2] - sv
                weeks.append((d, {k: x / week_start[0] for k, x in c.items()}))
            comp.update(dict.fromkeys(COMPONENTS, 0.0))
            week_start = (nav, lv, sv)

    out = summarize(navs)
    n = max(len(weeks), 1)
    out.update(
        arm=arm.name,
        decomp={k: sum(w[k] for _, w in weeks) / n * 52 for k in COMPONENTS},
        counts=counts,
        net_delta=statistics.mean(net_delta) if net_delta else None,
        alpha_beta=alpha_beta(navs, [d for d, _ in weeks], m.px),
    )
    return out


def run_put(
    m: Market, *, target=0.20, iv_scale=1.0, cost_scale=1.0, band=None, since=None, until=None
) -> dict:
    """The put benchmark: sell a `target`-delta put at the year-long expiry, hold it to 45 DTE, and
    re-enter the next session. `band` re-strikes whenever the delta leaves it (tested 2026-10-04 and
    the worst variant everywhere: it buys back after every drop and sells again lower)."""
    days = [d for d in m.days if (since is None or d >= since) and (until is None or d <= until)]
    sp = m.spec["spread"]["long_year"]
    state = {"cash": 1.0, "pos": None, "last_close": None}
    counts = {"entries": 0, "restrikes": 0}
    navs = []

    def fill_cost(d, S, mid):
        return cost_scale * (min(SLIP * sp * S, 0.15 * mid) + COMM_OPEN * m.pf[d]) if mid > 0 else 0.0

    def enter(d, S):
        exp = year_expiry(d)
        t = yrs(d, exp)
        K = m.strike_for_put_delta(d, S, t, target, iv_scale)
        u = state["cash"] / S  # the shield's notional sizing; K < S keeps it cash-secured
        mid = m.put_price(d, S, K, t, iv_scale)
        state["cash"] += u * (mid - fill_cost(d, S, mid))
        state["pos"] = {"u": u, "K": K, "exp": exp}
        counts["entries"] += 1

    def close(d, S):
        pos = state["pos"]
        mid = m.put_price(d, S, pos["K"], max(yrs(d, pos["exp"]), 0.0), iv_scale)
        state["cash"] -= pos["u"] * (mid + fill_cost(d, S, mid))
        state["pos"], state["last_close"] = None, d

    prev = None
    for d in days:
        S = m.px[d]
        if prev is not None:
            state["cash"] += state["cash"] * rate(prev) * yrs(prev, d)
        prev = d
        pos = state["pos"]
        if pos is not None:
            t = yrs(d, pos["exp"])
            if (date.fromisoformat(pos["exp"]) - date.fromisoformat(d)).days <= 45:
                close(d, S)
            elif band is not None and not band[0] <= -m.put_delta(d, S, pos["K"], t, iv_scale) <= band[1]:
                close(d, S)
                enter(d, S)  # a re-strike is a same-day roll
                counts["restrikes"] += 1
        elif d != state["last_close"]:
            enter(d, S)
        pos = state["pos"]
        mark = pos["u"] * m.put_price(d, S, pos["K"], max(yrs(d, pos["exp"]), 0.0), iv_scale) if pos else 0.0
        navs.append((d, state["cash"] - mark))
    weeks = [d for d in m.cycles if days[0] <= d <= days[-1]]
    name = f"short {target * 100:.0f}D year put" + (", re-struck" if band else "")
    return {**summarize(navs), "arm": name, "counts": counts, "alpha_beta": alpha_beta(navs, weeks, m.px)}


# --------------------------------------------------------------------------- read-outs
def summarize(navs: list[tuple[str, float]]) -> dict:
    vals, dates = [v for _, v in navs], [d for d, _ in navs]
    span = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days / 365.25
    rets = [math.log(vals[i] / vals[i - 1]) for i in range(1, len(vals)) if vals[i - 1] > 0 and vals[i] > 0]
    excess = [r - rate(dates[i]) / 252 for i, r in enumerate(rets)]
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    wk = [vals[i] / vals[i - 5] - 1 for i in range(5, len(vals), 5) if vals[i - 5] > 0]
    sd = statistics.pstdev(excess)
    return {
        "start": dates[0],
        "end": dates[-1],
        "cagr": vals[-1] ** (1 / span) - 1 if vals[-1] > 0 else -1.0,
        "vol": statistics.pstdev(rets) * math.sqrt(252),
        "sharpe": statistics.mean(excess) / sd * math.sqrt(252) if sd > 0 else 0.0,
        "mdd": mdd,
        "worst_week": min(wk) if wk else None,
    }


def alpha_beta(navs, week_dates, px) -> tuple[float, float]:
    """OLS of the arm's weekly excess return on the underlying's (price only): alpha a year, beta."""
    nav = dict(navs)
    xs, ys = [], []
    for a, b in zip(week_dates, week_dates[1:], strict=False):
        if nav.get(a, 0) > 0 and a in px and b in px:
            rf = rate(a) * yrs(a, b)
            ys.append(nav[b] / nav[a] - 1 - rf)
            xs.append(px[b] / px[a] - 1 - rf)
    if len(xs) < 10:
        return 0.0, 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sum((x - mx) ** 2 for x in xs)
    return (my - beta * mx) * 52, beta


def buy_hold(m: Market, since=None, until=None) -> dict:
    days = [d for d in m.days if (since is None or d >= since) and (until is None or d <= until)]
    navs = [(d, m.px[d] / m.px[days[0]]) for d in days]
    weeks = [d for d in m.cycles if days[0] <= d <= days[-1]]
    return {**summarize(navs), "arm": "buy & hold, price only", "alpha_beta": alpha_beta(navs, weeks, m.px)}


def calibration(conn, since, until) -> dict:
    """The weekly ATM buy-write on SPY's total-return closes against WPUT, over the same sessions."""
    m = Market(conn, "XSP", total_return=True)
    m.ex_dates = []  # parity with a cash-settled index product: nothing to refuse
    r = run(m, BUY_WRITE, since=since, until=until)
    wput = index("WPUT")
    a, b = r["start"], r["end"]
    if a not in wput or b not in wput:
        return {"ok": False, "reason": f"WPUT has no print on {a if a not in wput else b}"}
    span = (date.fromisoformat(b) - date.fromisoformat(a)).days / 365.25
    bench = (wput[b] / wput[a]) ** (1 / span) - 1
    diff = r["cagr"] - bench
    ok = abs(diff) <= CALIBRATION_TOLERANCE
    return {"ok": ok, "start": a, "end": b, "replay": r["cagr"], "wput": bench, "diff": diff}


def _pct(x, w=6, sign=False):
    return f"{'-':>{w}}" if x is None else f"{x * 100:{'+' if sign else ''}{w}.1f}"


def row(r: dict) -> str:
    a, b = r["alpha_beta"]
    nd = r.get("net_delta")
    return (
        f"  {r['arm'][:22]:22} {_pct(r['cagr'])} {_pct(r['vol'])} {r['sharpe']:7.2f} {_pct(r['mdd'])} "
        f"{_pct(r['worst_week'], 8)} {_pct(a, 7, True)} {b:6.2f} {'-' if nd is None else f'{nd:.2f}':>9}"
    )


def detail(r: dict) -> str:
    dc, c = r["decomp"], r["counts"]
    parts = "  ".join(f"{k} {v * 100:+5.1f}" for k, v in dc.items())
    tally = (
        f"entries {c['entries']} shorts {c['shorts_sold']} early {c['early_rolls']} stops {c['stops']} "
        f"long-rolls {c['long_rolls']} short-less wks {c['weeks_short_less']}"
    )
    return f"  {'':22}   a yr, % of NAV: {parts} = {sum(dc.values()) * 100:+5.1f} | {tally}"


def symbol_report(conn, symbol, args) -> dict:
    m = Market(conn, symbol)
    spec = m.spec
    rows = [buy_hold(m, args.since, args.until)]
    rows += [run(m, arm, since=args.since, until=args.until) for arm in ARMS]
    rows.append(run_put(m, since=args.since, until=args.until))
    put_name = rows[-1]["arm"]
    scales = {}
    for arm in (CONTROL, SHIELD_HOLD, SHIELD):
        scales[arm.name] = {
            s: run(m, arm, iv_scale=s, since=args.since, until=args.until)["alpha_beta"][0]
            for s in args.iv_scales
        }
    scales[put_name] = {
        s: run_put(m, iv_scale=s, since=args.since, until=args.until)["alpha_beta"][0] for s in args.iv_scales
    }
    no_costs = {
        arm.name: run(m, arm, cost_scale=0.0, since=args.since, until=args.until)["alpha_beta"][0]
        for arm in (CONTROL, SHIELD_HOLD, SHIELD)
    }
    # The year-long skew is the put's uncalibrated input, and the shield's long sits on it too.
    skew = {}
    for b in (0.0, m.spec["b"], 2 * m.spec["b"]):
        m.b = b
        skew[b] = {
            SHIELD_HOLD.name: run(m, SHIELD_HOLD, since=args.since, until=args.until)["alpha_beta"][0],
            put_name: run_put(m, since=args.since, until=args.until)["alpha_beta"][0],
        }
    m.b = m.spec["b"]
    bridge = f", bridged by {spec['bridge'][0]} x{spec['bridge'][1]}" if "bridge" in spec else ""
    start, end = rows[0]["start"], rows[0]["end"]
    in_window = sum(1 for x in m.ex_dates if start <= x <= end)
    return {
        "symbol": symbol,
        "header": (
            f"{symbol} on {spec['source']} closes, {start}..{end} | IV from {spec['index']}{bridge}, "
            f"c {spec['c']} b {spec['b']} ({spec['basis']}) | ex-dates in the window: "
            f"{in_window if spec['settlement'] == 'physical' else 'none refused (cash-settled)'}"
        ),
        "rows": rows,
        "iv_scales": scales,
        "no_costs": no_costs,
        "skew": skew,
    }


def print_report(rep: dict, calibrated: bool) -> None:
    flag = "" if calibrated else "   ** UNCALIBRATED **"
    print(f"\n=== {rep['header']}{flag}")
    cols = ("CAGR", 6), ("vol", 6), ("Sharpe", 7), ("MDD", 6), ("worst wk", 8), ("alpha", 7), ("beta", 6)
    print(f"  {'arm':22} " + " ".join(f"{c:>{w}}" for c, w in cols) + f" {'netD/NAV':>9}")
    for r in rep["rows"]:
        print(row(r))
        if "decomp" in r:
            print(detail(r))
    print("  alpha a year at IV x" + " / x".join(str(s) for s in next(iter(rep["iv_scales"].values()))) + ":")
    for name, cells in rep["iv_scales"].items():
        print(f"    {name:20} " + "  ".join(f"{_pct(a, 6, True)}" for a in cells.values()))
    print(
        "  alpha a year with every cost removed: "
        + "  ".join(f"{k} {_pct(a, 5, True)}" for k, a in rep["no_costs"].items())
    )
    print(
        "  alpha a year by skew b: "
        + " | ".join(
            f"b {b:.2f}: " + ", ".join(f"{k} {_pct(a, 5, True)}" for k, a in cells.items())
            for b, cells in rep["skew"].items()
        )
    )


def skew_check(conn) -> list[str]:
    """The model's IV against what the paper loop sampled (`pmcc_skew_samples`, skew.py), per symbol
    and target: the observed IV, the model's at the same strike, expiry and session, and the skew `b`
    that would have matched it. The ATM rows test the level, c and the flat term past 30 days; the
    off-spot rows test b where the shield's weekly short and the put benchmark actually sit. Only
    sessions the vol-index files already cover are compared (run `--fetch` for the latest)."""
    path = _home.data_dir("pmcc") / "paper_trades.db"
    if not path.exists():
        return [f"skew check: no ledger at {path}"]
    led = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    led.row_factory = sqlite3.Row
    try:
        rows = led.execute(
            "SELECT session_date, symbol, target, dte, spot, strike, iv FROM pmcc_skew_samples "
            "WHERE usable = 1 AND iv IS NOT NULL AND strike IS NOT NULL ORDER BY symbol, target"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        led.close()
    if not rows:
        return [
            "skew check: no samples yet (the loop records them from 2026-10-05, an hour before each close)"
        ]
    out = ["skew check: observed IV against the model's at the same strike and expiry (medians)"]
    by: dict[tuple[str, str], list] = {}
    for r in rows:
        by.setdefault((r["symbol"], r["target"]), []).append(r)
    markets: dict[str, Market] = {}
    for (symbol, target), group in sorted(by.items()):
        if symbol not in SYMBOLS:
            continue
        m = markets.setdefault(symbol, Market(conn, symbol))
        obs, mod, implied = [], [], []
        for r in group:
            d = r["session_date"]
            if d not in m.ix or d not in m.vix or d not in m.v9:
                continue
            t = max(r["dte"], 1) / 365
            obs.append(r["iv"])
            mod.append(m.iv(d, r["spot"], r["strike"], t, 1.0))
            a = m.atm(d, t, 1.0)
            z = math.log(r["strike"] / r["spot"]) / (a * math.sqrt(t))
            if z < -0.25:  # far enough off spot for the skew term to be measurable
                implied.append((r["iv"] / a - 1) / -z)
        if not obs:
            out.append(
                f"  {symbol:4} {target:14} {len(group)} sample(s), none yet inside the vol-index files"
            )
            continue
        ratio = statistics.median(o / x for o, x in zip(obs, mod, strict=True))
        b_txt = f", b that matches {statistics.median(implied):.2f} (model {m.spec['b']})" if implied else ""
        out.append(
            f"  {symbol:4} {target:14} n {len(obs):3}  observed {statistics.median(obs):.3f}  model "
            f"{statistics.median(mod):.3f}  ratio {ratio:.2f}{b_txt}"
        )
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symbols", nargs="*", default=list(SYMBOLS))
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    ap.add_argument("--iv-scales", nargs="*", type=float, default=[0.9, 1.0, 1.1])
    ap.add_argument("--fetch", action="store_true", help="fetch the Cboe vol-index files first")
    ap.add_argument("--skew-check", action="store_true", help="compare the model with the sampled quotes")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.fetch:
        for p in fetch_indexes():
            print(f"fetch: {p}", file=sys.stderr)
    missing = [n for n in INDEXES if not index_path(n).exists()]
    if missing:
        print(f"missing vol-index files: {', '.join(missing)} -- run with --fetch", file=sys.stderr)
        return 2
    unknown = [s for s in args.symbols if s.upper() not in SYMBOLS]
    if unknown:
        print(f"no pricing parameters for {', '.join(unknown)}; known: {', '.join(SYMBOLS)}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(f"file:{_paths.history_db()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    if args.skew_check:
        print("\n".join(skew_check(conn)))
        conn.close()
        return 0
    cal = calibration(conn, args.since, args.until)
    reports = [symbol_report(conn, s.upper(), args) for s in args.symbols]
    conn.close()

    if args.json:
        print(json.dumps({"calibration": cal, "symbols": reports}, indent=2, default=str))
        return 0 if cal["ok"] else 1
    if "replay" in cal:
        verdict = "OK" if cal["ok"] else f"FAILED (tolerance {CALIBRATION_TOLERANCE * 100:.1f})"
        print(
            f"calibration, {cal['start']}..{cal['end']}: weekly ATM buy-write on SPY total-return closes "
            "at IV x1.0 "
            f"{_pct(cal['replay'], 5)}% a year vs Cboe WPUT {_pct(cal['wput'], 5)}%, "
            f"{cal['diff'] * 100:+.1f} points -- {verdict}"
        )
    else:
        print(f"calibration: not run -- {cal['reason']}")
    for rep in reports:
        print_report(rep, cal["ok"])
    return 0 if cal["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
