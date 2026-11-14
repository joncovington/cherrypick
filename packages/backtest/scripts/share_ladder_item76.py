"""Item 76, covered calls on the share ladder's stuck lots (research/edge-hypotheses-plan.md): a lot still
open 18 calendar days after its buy enters the "hospital", where one call a lot is sold against it, at three
strike rules, each paired against holding the same lot for its +4% target.

    python packages/backtest/scripts/share_ladder_item76.py plan [--out FILE]   # the paths to fetch (local)
    python packages/backtest/scripts/share_ladder_item76.py study [--json PATH] [--names A,B]   # (local)
    python packages/backtest/scripts/share_ladder_item76.py report [--json PATH]   # results.md (local)

`plan` reads item 29's `expirations.json` and the technicals history's daily bars, and writes
`path_requests.json` (the input of `fetch-chain-paths`) and `plan.json` (the holdout's last session) under
the backtest results directory (results/research/item76/), never into the repo: the chains are licensed.
`study` replays the arms and states the verdict; it reads item 29's expiries, the Treasury curves of item
28 and the chain store.

**The lots** are item 26's `once` at a cap of 3 lots a name, 100 shares a lot: a lot at 93% of the previous
close, filled on daily bars as item 26 fills them, 0.05% a side on shares. Every arm judges the same lots:
the ones `once-3` opens with the baseline's exits. **The hospital:** a lot still open at the close of the
first session 18 or more calendar days after its buy session enters it at that close.

**The call:** one contract a lot, sold at the hospital close and again at each expiry that does not end the
lot, on the expiry 35 to 60 calendar days out nearest 45 (the earlier on a tie); none, and the lot waits for
the next session that has one, counted. Held to its expiry. Strikes are read at the sale's close C, the
lot's cost P per share and the premium already banked on the lot B per share, among strikes quoted on both
sides:
- `breakeven`: the lowest strike K with K + B + (this call's bid) >= P; none qualifies, the highest listed,
  counted.
- `delta30`: the strike whose Black-Scholes delta (from its mid, the 3-month bill rate, no dividend,
  calendar days) is nearest 0.30, the lower on a tie.
- `cost5`: the lowest strike at or above 1.05 P; none, the highest, counted.
At the expiry's close a close at or over K assigns the call (the shares go at K, the lot ends); under K the
call expires and the lot ends if that close is at or over 104% of P (sold at the close), else the next call
is sold at that close. `hold` is the same lot with no call, exited as item 26 exits it; item 27's `decay`
exit on the same lots is reported beside, unjudged.

**Measured,** per hospital lot, the arm's whole-lot net less `hold`'s, in dollars per 100 shares. A lot open
at the window's end is marked at that close, its open call at the ask. Calls are sold at the bid (mid
reported); tastytrade's fees from `cherrypick.core.fees`, the assignment fee on an assignment, nothing to
let a call expire, shares 0.05% a side.

**The verdict,** per arm: in the design window (lots bought 2016-05-02 to 2024-12-31) the mean paired
difference above zero with a one-sided t above 2, one lot one observation, Holm across the 3; positive in
each time-half and on each name; positive with option fees at 1.5x and every call sold $0.05 under the
bid; then the holdout's (2025-01-02 to the last session `plan` recorded) mean above zero. Under 30
hospital lots in the design window is no verdict; a holdout under 10 is read and the verdict provisional.
"""

from __future__ import annotations

import argparse
import bisect
import importlib.util
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from cherrypick.core import fees as core_fees
from cherrypick.core import stats as core_stats

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)  # its dataclasses look their module up by name
    spec.loader.exec_module(module)
    return sys.modules[name]


s29 = _load("share_ladder_item29", HERE / "share_ladder_item29.py")
s27 = _load("share_ladder_item27", HERE.parents[2] / "scripts" / "share_ladder_item27.py")
sl = s27.sl  # item 26

NAMES = s29.NAMES
DROP, GAIN, COST = sl.DROP, sl.GAIN, sl.COST
CAP = 3
LOT_SHARES = 100
HOSPITAL_DAYS = 18
EXPIRY_RANGE = (35, 60)
EXPIRY_TARGET = 45
DELTA_TARGET = 0.30
COST_OVER = 1.05
ARMS = ("breakeven", "delta30", "cost5")
REPORTED = ("decay",)
STRESS_FEES = 1.5
STRESS_SLIP = 0.05
DESIGN = ("2016-05-02", "2024-12-31")
HOLDOUT_START = "2025-01-02"
MIN_LOTS = 30
MIN_HOLDOUT = 10
T_BAR = 2.0
ALPHA = 0.05
# (fill slippage under the bid / over the ask, fee multiple, priced at the mid)
COSTINGS = {
    "natural": (0.0, 1.0, False),
    "stress": (STRESS_SLIP, STRESS_FEES, False),
    "mid": (0.0, 1.0, True),
}


# ------------------------------------------------------------------------------------------------
# Pure: the bars, the ladder's lots and the hospital.


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


def ladder_lots(bars: list[Bar], start: str, end: str) -> tuple[list[Lot], int]:
    """Item 26's `once` at cap CAP over [start, end]: every lot bought, with the baseline exit it makes
    (a target fill at the open or at the level; a lot bought in a session is not sold in it and a slot
    sold in a session frees the next). Returns the lots and the index of the window's last session."""
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
        for px in sl.entry_fills("once", bar, ref, DROP, None, CAP - n_before):
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


def decay_result(lot: Lot, bars: list[Bar], last: int) -> dict:
    """Item 27's `decay` exit on the same lot: a target of 104% to day 30, breakeven after costs from day 31,
    95% of cost from day 121, filled at the open or at the level, from the session after the buy."""
    for j in range(lot.entry + 1, last + 1):
        t = s27.target(lot.price, _cal(bars[lot.entry].date, bars[j].date), decay=True, cost=COST)
        px = bars[j].o if bars[j].o >= t else (t if bars[j].h >= t else None)
        if px is not None:
            return {"net": share_net(lot.shares, lot.price, px), "end": j, "marked": False}
    return {"net": share_net(lot.shares, lot.price, bars[last].c, marked=True), "end": last, "marked": True}


# ------------------------------------------------------------------------------------------------
# Pure: expiries, the sale sessions and each call's segment, from the share closes alone.


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
    """Where a call sold at session j ends: its expiry's session (the last on or before the expiry), the
    window's last session, or the session before a split ex-date; in that order of precedence."""
    e = bisect.bisect_right(dates, exp) - 1
    c = _cut(dates, split_dates, j)
    if exp <= dates[last] and (c is None or e <= c):  # an expiry past the data is not reached
        return e, "expiry"
    if c is None or c >= last:
        return last, "end"
    return c, "split"


def plan_lot(bars: list[Bar], lot: Lot, expiries: list[str], split_dates: list[str], last: int):
    """A hospital lot's calls from the share closes alone, assuming no assignment: each (sale session,
    expiry, end session, how the call ends), up to the first expiry whose close is at or over 104% of the
    lot's cost or the window's end. Under every strike rule the lot has ended by then (a close that high
    either assigns the call or lets it expire and sells the shares), so this bounds every rule's path."""
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


def path_requests(sym: str, bars: list[Bar], lots: list[Lot], expiries, split_dates, last) -> list[dict]:
    """Every (expiry, start, through) path the hospital lots could use, one per (name, expiry)."""
    span: dict[str, list[str]] = {}
    for lot in lots:
        if lot.hospital is None:
            continue
        for j, exp, end, _ in plan_lot(bars, lot, expiries, split_dates, last):
            a, b = bars[j].date, bars[end].date
            have = span.setdefault(exp, [a, b])
            have[0], have[1] = min(have[0], a), max(have[1], b)
    return [{"symbol": sym, "expiration": e, "start": a, "through": b} for e, (a, b) in sorted(span.items())]


# ------------------------------------------------------------------------------------------------
# Pure: the three strike rules.


def pick_breakeven(chain, cost: float, banked: float) -> tuple[float, bool] | None:
    """The lowest strike K with K + banked + bid >= cost; none qualifying, the highest (flagged)."""
    ks = s29.quoted(chain, "C")
    if not ks:
        return None
    ok = [k for k in ks if k + banked + chain[(k, "C")][0] >= cost]
    return (ok[0], False) if ok else (ks[-1], True)


def nearest_delta(deltas: dict[float, float], target: float = DELTA_TARGET) -> float | None:
    """The strike whose delta is nearest the target, the lower on a tie."""
    return min(deltas, key=lambda k: (abs(deltas[k] - target), k)) if deltas else None


def pick_delta30(chain, spot: float, years: float, rate: float) -> tuple[float, bool] | None:
    deltas = {}
    for k in s29.quoted(chain, "C"):
        d = s29.leg_delta(chain, s29.Leg(k, "C", 1), spot, years, rate)
        if d is not None:
            deltas[k] = d
    k = nearest_delta(deltas)
    return None if k is None else (k, False)


def pick_cost5(chain, cost: float) -> tuple[float, bool] | None:
    """The lowest strike at or above 1.05 x cost; none listed, the highest (flagged)."""
    ks = s29.quoted(chain, "C")
    if not ks:
        return None
    ok = [k for k in ks if k >= COST_OVER * cost]
    return (ok[0], False) if ok else (ks[-1], True)


def pick_strike(rule: str, chain, *, cost: float, banked: float, spot: float, years: float, rate: float):
    if rule == "breakeven":
        return pick_breakeven(chain, cost, banked)
    if rule == "delta30":
        return pick_delta30(chain, spot, years, rate)
    if rule == "cost5":
        return pick_cost5(chain, cost)
    raise ValueError(rule)


# ------------------------------------------------------------------------------------------------
# Pure: fills and fees for one call.


def sell_px(q, costing: str) -> float:
    slip, _, mid = COSTINGS[costing]
    return (q[0] + q[1]) / 2 if mid else max(0.0, q[0] - slip)


def buy_px(q, costing: str) -> float:
    slip, _, mid = COSTINGS[costing]
    return (q[0] + q[1]) / 2 if mid else q[1] + slip


def open_fee(sym: str, contracts: float, costing: str) -> float:
    return (
        core_fees.ic_open_fee(sym, quantity=1, legs=1, sell_legs=1, ndigits=4)
        * contracts
        * COSTINGS[costing][1]
    )


def close_fee(sym: str, contracts: float, costing: str) -> float:
    return (
        core_fees.ic_close_fee(sym, quantity=1, legs=1, sell_legs=0, ndigits=4)
        * contracts
        * COSTINGS[costing][1]
    )


def assignment_fee(costing: str) -> float:
    return core_fees.ASSIGNMENT_FEE_PER_SETTLEMENT * COSTINGS[costing][1]


def last_quote(chain_at, bars: list[Bar], exp: str, strike: float, lo: int, hi: int):
    """The call's two-sided quote at session hi, else the latest earlier one down to lo (counted stale),
    else its intrinsic value at hi's close (counted). Returns (bid, ask, stale)."""
    for j in range(hi, lo - 1, -1):
        q = (chain_at(bars[j].date, exp) or {}).get((strike, "C"))
        if q is not None and s29._two_sided(*q):
            return q[0], q[1], int(j != hi)
    iv = max(0.0, bars[hi].c * bars[hi].f - strike)
    return iv, iv, 1


# ------------------------------------------------------------------------------------------------
# One hospital lot under one strike rule.


def run_call_arm(
    rule: str,
    sym: str,
    lot: Lot,
    bars: list[Bar],
    expiries: list[str],
    split_dates: list[str],
    last: int,
    chain_at,
    rate_at,
    hold_px: float | None = None,
) -> dict:
    """The lot's whole-lot net under the rule, at the natural, the stress and the mid together (the path of
    strikes and assignments is the natural's), with the figures reported beside it."""
    dates = [b.date for b in bars]
    opt = dict.fromkeys(COSTINGS, 0.0)
    banked = 0.0  # premium received at the bid, less split buy-backs, in dollars
    prem = 0.0
    n_calls = waits = fallbacks = stale = 0
    strikes: list[tuple[str, float]] = []
    counts = {"no chain": 0, "no strike": 0}
    idx = lot.hospital
    end_idx, how, proceeds, strike_end, realised = last, "end", None, None, 0.0
    while True:
        sale = next_sale(dates, expiries, split_dates, idx, last)
        waits += (sale[0] if sale else last + 1) - idx
        if sale is None:
            break
        j, exp = sale
        sh = lot.shares / bars[j].f  # raw shares on the sale session's basis
        cost = lot.price * bars[j].f  # P per share, same basis
        chain = chain_at(dates[j], exp)
        if not chain:
            counts["no chain"] += 1
            idx, waits = j + 1, waits + 1
            continue
        years = _cal(dates[j], exp) / 365
        got = pick_strike(
            rule,
            chain,
            cost=cost,
            banked=banked / sh,
            spot=bars[j].c * bars[j].f,
            years=years,
            rate=rate_at(dates[j]),
        )
        if got is None:
            counts["no strike"] += 1
            idx, waits = j + 1, waits + 1
            continue
        k, fallback = got
        q = chain[(k, "C")]
        contracts = sh / 100
        fallbacks += fallback
        n_calls += 1
        strikes.append((dates[j], k))
        for name in COSTINGS:
            opt[name] += sell_px(q, name) * sh - open_fee(sym, contracts, name)
        banked += q[0] * sh
        prem += q[0] * sh
        e, kind = segment_end(dates, j, exp, split_dates, last)
        d = bars[e]
        sh_e = lot.shares / d.f
        if kind == "expiry":
            close_raw = d.c * d.f
            if close_raw >= k:
                for name in COSTINGS:
                    opt[name] -= assignment_fee(name)
                end_idx, how, proceeds, strike_end = e, "assigned", k * sh_e * (1 - COST), k / d.f
                realised = (
                    k * sh_e + banked
                )  # the shares at the strike and every premium banked, before costs
                break
            if d.c >= (1 + GAIN) * lot.price:
                end_idx, how, proceeds = e, "sold", lot.shares * d.c * (1 - COST)
                break
            idx = e
            continue
        # the window's end or a split: the open call is bought back at the ask
        bid, ask, was_stale = last_quote(chain_at, bars, exp, k, j, e)
        stale += was_stale
        for name in COSTINGS:
            opt[name] -= buy_px((bid, ask), name) * sh_e + close_fee(sym, sh_e / 100, name)
        banked -= ask * sh_e
        if kind == "end":
            break
        idx = e + 1  # the new call is sold on the ex-date session's close, on the new basis
    if how == "end":
        end_idx, proceeds = last, lot.shares * bars[last].c
    entry_cash = -lot.shares * lot.price * (1 + COST)
    nets = {name: entry_cash + proceeds + opt[name] for name in COSTINGS}
    # a lot waiting or holding with no call at the window's end
    hospital_days = _cal(dates[lot.hospital], dates[end_idx])
    return {
        "net": nets["natural"],
        "net_stress": nets["stress"],
        "net_mid": nets["mid"],
        "how": how,
        "end": dates[end_idx],
        "calls": n_calls,
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


# ------------------------------------------------------------------------------------------------
# The arms over a window, the statistics and the verdict.


def run_window(
    sym, bars, expiries, split_dates, window, chain_at, rate_at
) -> tuple[dict[str, list[dict]], dict]:
    lots, last = ladder_lots(bars, *window)
    for lot in lots:
        lot.hospital = hospital_index(bars, lot, last)
    hospital = [lot for lot in lots if lot.hospital is not None]
    rows: dict[str, list[dict]] = {a: [] for a in ARMS + REPORTED}
    for lot in hospital:
        hold = hold_result(lot, bars, last)
        hold_px = None if hold["marked"] else hold["px"]
        for rule in ARMS:
            got = run_call_arm(rule, sym, lot, bars, expiries, split_dates, last, chain_at, rate_at, hold_px)
            rows[rule].append(lot_row(sym, lot, bars, got, hold))
        dec = decay_result(lot, bars, last)
        days = _cal(bars[lot.hospital].date, bars[dec["end"]].date)
        rows["decay"].append(
            {
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
                "hold_days": _cal(bars[lot.hospital].date, bars[hold["end"]].date),
                "hold_capital_days": lot.shares
                * lot.price
                * _cal(bars[lot.hospital].date, bars[hold["end"]].date),
                "capital_days": lot.shares * lot.price * days,
                "premium": 0.0,
                "calls": 0,
                "waits": 0,
                "fallbacks": 0,
                "stale": 0,
                "below_cost": False,
                "rebound": False,
            }
        )
    return rows, {"lots": len(lots), "hospital": len(hospital)}


def halves(window: tuple[str, str]):
    return s29.halves(window)


def arm_report(rows: list[dict], window: tuple[str, str]) -> dict:
    h1, h2 = halves(window)
    out = s29.summary(rows)
    out["stress"] = s29.summary(rows, "diff_stress")["mean"]
    out["mid"] = s29.summary(rows, "diff_mid")["mean"]
    out["halves"] = [s29.summary([r for r in rows if lo <= r["entry"] <= hi])["mean"] for lo, hi in (h1, h2)]
    out["names"] = {s: s29.summary([r for r in rows if r["name"] == s])["mean"] for s in NAMES}
    if out.get("sd") is not None:
        out["mde"] = core_stats.minimum_detectable_effect(out["sd"], out["n"])
    out["hospital_days"] = sum(r["hospital_days"] for r in rows)
    out["hold_hospital_days"] = sum(r["hold_days"] for r in rows)
    out["capital_days"] = sum(r["capital_days"] for r in rows)
    out["hold_capital_days"] = sum(r.get("hold_capital_days", r["capital_days"]) for r in rows)
    out["premium"] = sum(r["premium"] for r in rows)
    out["below_cost"] = sum(r["below_cost"] for r in rows)
    out["rebound"] = sum(r["rebound"] for r in rows)
    out["worst"] = min((r["net"] for r in rows), default=None)
    out["worst_hold"] = min((r["hold_net"] for r in rows), default=None)
    out["waits"] = sum(r["waits"] for r in rows)
    out["fallbacks"] = sum(r["fallbacks"] for r in rows)
    out["stale"] = sum(r["stale"] for r in rows)
    out["how"] = {}
    for r in rows:
        out["how"][r["how"]] = out["how"].get(r["how"], 0) + 1
    return out


def _pos(x) -> bool:
    return x is not None and x > 0


def judge(design: dict[str, dict], holdout: dict[str, dict]) -> dict[str, dict]:
    """Each judged arm's verdict from its design and holdout reports."""
    pvals = {a: (design[a].get("p", 1.0) if design[a]["n"] >= MIN_LOTS else 1.0) for a in ARMS}
    holm = core_stats.holm(pvals, ALPHA)
    out = {}
    for a in ARMS:
        d, h = design[a], holdout[a]
        if d["n"] < MIN_LOTS:
            out[a] = {"verdict": "no verdict", "why": f"design {d['n']} hospital lots"}
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
        if h["n"] == 0:
            out[a] = {"verdict": "no verdict", "checks": checks, "why": "no holdout hospital lots"}
            continue
        checks["holdout"] = _pos(h["mean"])
        out[a] = {
            "verdict": "pass" if checks["holdout"] else "fail",
            "checks": checks,
            "provisional": h["n"] < MIN_HOLDOUT,
        }
    return out


# ------------------------------------------------------------------------------------------------
# The stores and the commands.


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item76"
    d.mkdir(parents=True, exist_ok=True)
    return d


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


def cmd_plan(args) -> int:
    expiries = s29.load_expiries()
    loaded = {sym: load_bars(sym) for sym in NAMES}
    holdout_end = min(bars[-1].date for bars, _ in loaded.values())
    windows = {"design": DESIGN, "holdout": (HOLDOUT_START, holdout_end)}
    reqs, hospital = [], {}
    for sym, (bars, split_dates) in loaded.items():
        for wname, window in windows.items():
            lots, last = ladder_lots(bars, *window)
            for lot in lots:
                lot.hospital = hospital_index(bars, lot, last)
            reqs += path_requests(sym, bars, lots, expiries[sym], split_dates, last)
            hospital[f"{wname}/{sym}"] = sum(lot.hospital is not None for lot in lots)
    merged: dict[tuple, dict] = {}
    for r in reqs:  # one path a (name, expiry) across the windows
        have = merged.setdefault((r["symbol"], r["expiration"]), dict(r))
        have["start"], have["through"] = min(have["start"], r["start"]), max(have["through"], r["through"])
    out_dir = _out_dir()
    out = Path(args.out) if args.out else out_dir / "path_requests.json"
    requests = [merged[k] for k in sorted(merged)]
    out.write_text(json.dumps({"requests": requests}, indent=0), encoding="utf-8")
    (out_dir / "plan.json").write_text(
        json.dumps({"design": DESIGN, "holdout": [HOLDOUT_START, holdout_end], "hospital_lots": hospital}),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "requests": len(requests),
                "holdout_end": holdout_end,
                "hospital_lots": hospital,
                "file": str(out),
            }
        )
    )
    return 0


def load_plan() -> dict:
    path = _out_dir() / "plan.json"
    if not path.exists():
        raise SystemExit(f"no plan at {path}: run `share_ladder_item76.py plan` first")
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_study(args) -> int:
    names = [n for n in NAMES if not args.names or n in set(args.names.split(","))]
    plan = load_plan()
    windows = {"design": tuple(plan["design"]), "holdout": tuple(plan["holdout"])}
    expiries = s29.load_expiries()
    rate_at = s29._rates()
    conn, make = s29._chains()
    result: dict = {"windows": {}, "lots": {}, "plan": plan}
    per: dict = {w: {a: [] for a in ARMS + REPORTED} for w in windows}
    try:
        for sym in names:
            bars, split_dates = load_bars(sym)
            chain_at = make(sym)
            for wname, window in windows.items():
                rows, n = run_window(sym, bars, expiries[sym], split_dates, window, chain_at, rate_at)
                for a, rs in rows.items():
                    per[wname][a] += rs
                result["lots"].setdefault(wname, {})[sym] = n
                print(wname, sym, n, flush=True)
    finally:
        conn.close()
    for wname, window in windows.items():
        result["windows"][wname] = {a: arm_report(per[wname][a], window) for a in ARMS + REPORTED}
    result["trades"] = per
    result["verdict"] = judge(result["windows"]["design"], result["windows"]["holdout"])
    out = Path(args.json) if args.json else _out_dir() / "results.json"
    out.write_text(json.dumps(result, default=str), encoding="utf-8")
    out.with_suffix(".md").write_text(report(result), encoding="utf-8")
    for arm, v in result["verdict"].items():
        failed = [k for k, x in v.get("checks", {}).items() if not x]
        print(arm, v["verdict"], failed or "", v.get("why", ""))
    return 0


def _money(x: float | None) -> str:
    if x is None:
        return ""
    return f"{'−' if round(x) < 0 else '+' if round(x) > 0 else ''}${abs(x):,.0f}"


def report(result: dict) -> str:
    """results.md beside results.json: the verdict per arm, then per window the paired difference and what
    sits beside it. Derived from licensed chains, so it stays where the JSON is and never enters the repo."""
    lines = [
        "# Item 76: covered calls on the ladder's stuck lots",
        "",
        "Each row is one strike rule against holding the same lot for its +4% target, over the lots that"
        " reached the hospital (still open 18 days after the buy). Differences are dollars per 100"
        " shares, the calls sold at the bid. Under rebound, a lot counts when the baseline sold above"
        " the strike that capped the arm.",
        "",
        "## Verdict",
        "",
    ]
    for arm, v in result["verdict"].items():
        failed = [k for k, x in v.get("checks", {}).items() if not x]
        note = " (provisional)" if v.get("provisional") else ""
        tail = (f"; failed {', '.join(failed)}" if failed else "") + (f"; {v['why']}" if v.get("why") else "")
        lines.append(f"- **{arm}:** {v['verdict']}{note}{tail}")
    for wname, reports in result["windows"].items():
        lines += [
            "",
            f"## {wname.capitalize()}",
            "",
            "| arm | lots | mean diff | t | mid | stress | MDE | premium | below cost | rebound | worst |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for arm, r in reports.items():
            t = "" if r.get("t") is None else f"{r['t']:.2f}"
            cells = [arm, r["n"], _money(r["mean"]), t, _money(r["mid"]), _money(r["stress"])]
            cells += [
                _money(r.get("mde")),
                _money(r["premium"]),
                r["below_cost"],
                r["rebound"],
                _money(r["worst"]),
            ]
            lines.append("| " + " | ".join(str(c) for c in cells) + " |")
        lines += [
            "",
            "Hospital days, capital-days and the baseline's, per arm:",
            "",
            "| arm | hospital days | hold's | capital-days | hold's | waits | fallbacks |",
            "|---|---|---|---|---|---|---|",
        ]
        for arm, r in reports.items():
            lines.append(
                f"| {arm} | {r['hospital_days']} | {r['hold_hospital_days']} | {r['capital_days']:,.0f}"
                f" | {r['hold_capital_days']:,.0f} | {r['waits']} | {r['fallbacks']} |"
            )
    return "\n".join(lines) + "\n"


def cmd_report(args) -> int:
    src = Path(args.json) if args.json else _out_dir() / "results.json"
    src.with_suffix(".md").write_text(report(json.loads(src.read_text(encoding="utf-8"))), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="path_requests.json for fetch-chain-paths and plan.json (local)")
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
