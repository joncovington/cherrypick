"""Item 37 (docs/edge-hypotheses-plan.md): the curve module's own rule at real end-of-day quotes, the
contango-gated VXX call credit spread, with its variants.

    python scripts/curve_item37.py plan [--out FILE]      # the fetch-chain-paths request file (VXX)
    python scripts/curve_item37.py study [--json PATH]    # read the stores, state each arm's verdict
    python scripts/curve_item37.py report [--json PATH]   # results.md again from an existing results.json

`plan` writes the input of `fetch-chain-paths`: one daily path per monthly expiry the rule can target,
from the first session that targets it to its expiry, minus what the chain store already holds. It also
writes the intraday sample (one gate-open session in 25) for `fetch-intraday-chains`. `study` reads the
chain store, the technicals bars and the exchange's VIX and VIX3M files and writes results.json and
results.md. Both write under the backtest data dir (results/research/item37/), never into the repo: the
chains are licensed.

**The rule is the module's.** Every entry is `cherrypick.curve.engine.plan_entry` fed the stored chain,
every exit is `cherrypick.curve.management.evaluate` and then `core.spreadbook.exit_spread_blocks`; this
script owns only the calendar, the day's quotes, the fills and the books. Each session D, per arm, in
date order: if flat, the gate and the plan on D's chain; then an open position from an earlier session
is managed on D's quotes. A position entered on D is first managed on D+1, and a close on D frees the
slot for D+1.

**The arms** (docs section 37): control, noflip (no regime-flip exit), near (0.40-delta short),
hold-expiry (no profit take and no 7-DTE close: held to expiry, or out on the flip), wide5 ($5 wide).

**Reading choices** the section left open are in RULES_NOTES below and in the results.md. The verdict
follows the section's rule: the design window, Holm across the five arms, both halves, 1.5x costs, then
the holdout, which is simulated only for an arm that passed the design.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path

from cherrypick.core import bs, streamcache
from cherrypick.core import calendar as cal
from cherrypick.core import stats as core_stats
from cherrypick.core.spreadbook import exit_spread_blocks
from cherrypick.curve import clock as curve_clock
from cherrypick.curve import engine, management
from cherrypick.curve import regime as curve_regime

SYMBOL = "VXX"
START = date(2019, 5, 13)
END = date(2026, 10, 28)
WINDOWS = {
    "design": (date(2019, 5, 13), date(2022, 12, 30)),
    "holdout": (date(2023, 1, 3), date(2026, 10, 28)),
}
HALVES = ((date(2019, 5, 13), date(2021, 2, 26)), (date(2021, 3, 1), date(2022, 12, 30)))
EPISODES = (
    ("2020-03 spike", date(2020, 2, 19), date(2020, 4, 30)),
    ("2024-08 spike", date(2024, 7, 31), date(2024, 8, 30)),
    ("2025-04 spike", date(2025, 4, 1), date(2025, 4, 30)),
)
# VXX's three one-for-four reverse splits in the local technicals history. The raw closes jump on them.
SPLIT_DATES = (date(2021, 4, 23), date(2023, 3, 7), date(2024, 7, 24))
SPLIT_FACTOR = 0.25  # post-split price x this = the pre-split basis

ARMS = ("control", "noflip", "near", "hold-expiry", "wide5")
ARM_SPECS = {
    "control": {"base": "control", "over": {}, "profit_take": True},
    "noflip": {"base": "noflip", "over": {}, "profit_take": True},
    "near": {"base": "near", "over": {"short_delta_target": 0.40}, "profit_take": True},
    # close_dte -1 never fires (dte is 0 on the expiry day, where the position settles); no profit take
    # because the mark is not offered to `evaluate`. The flip stays: base control.
    "hold-expiry": {"base": "control", "over": {"close_dte": -1}, "profit_take": False},
    "wide5": {"base": "control", "over": {"spread_width": 5.0}, "profit_take": True},
}

# The deployed config's defaults (packages/curve/config.example.json), each as that file declares it.
# tests pin this against the example file.
DEFAULTS = {
    "quantity": 2,
    "max_positions": 1,
    "dte_min": 25,
    "dte_max": 60,
    "dte_target": 35,
    "short_delta_target": 0.3,
    "spread_width": 2.0,
    "min_credit_pct_of_width": 0.15,
    "min_net_credit_to_max_loss": 0.10,
    "profit_take_pct": 0.5,
    "close_dte": 7,
    "contango_max": 0.97,
    "assignment_exposure_tv": 0.05,
    "max_leg_spread_pct": 0.3,
    "allow_delta_computed_fallback": True,
    "max_wing_spread_abs": 0.25,
}
CONFIG = {"defaults": dict(DEFAULTS), "tastytrade_costs": {}}
GATE = DEFAULTS["contango_max"]
QTY = DEFAULTS["quantity"]
MULT = 100

STRESS = 1.5
MIN_TRADES = 30
MIN_HALF = 10
ALPHA = 0.05
RESAMPLES = 20_000
BLOCK = 3.0
SEED = 37
SAMPLE_EVERY = 25
COVERAGE = 0.90  # of a path's sessions held, a request is not repeated
REF_COST = 0.0005  # the share reference's cost, a side

RULES_NOTES = (
    "A p-value is the share of stationary-bootstrap resamples of the arm's design shares whose mean is at or"
    " below zero (add-one), so the smallest it can be is 1 in 20,001. An arm without 30 trades has p = 1 in"
    " the Holm family, which stays five wide.",
    "An entry on the session before a split ex-date is refused (split_next_session): the trade would end"
    " on its own entry quotes. It is a refusal, not a trade.",
    "A split-ended trade closes at that session's quotes whatever the rules say that day, and is not held"
    " to the exit-spread gate. If the day's quotes are unusable the previous priced session's are used.",
    "The traded close and the entry are priced at mid with the suite's slippage and fees on top; the"
    " natural-fill figure is buy at the ask and sell at the bid, fees kept, no slippage.",
    "Exposure is counted on the sessions a position is held through (not the session it closes on). The"
    " sensitivity closes the trade at the first exposed session's natural fill.",
    "A chain whose lowest quoted call strike is above the raw close is the adjusted series; the entry is"
    " refused (chain_not_raw_basis).",
    "The share reference is short the close of a session whose ratio is under the gate, held to the next"
    " close, flat otherwise, on split-adjusted closes, 0.05% a side each time it changes position.",
    "A position settles on its expiry day at the raw close; no exit is tried that day. The holdout is"
    " simulated, flat from its first session, only for an arm that passed the design, so a failing or"
    " no-verdict arm's holdout is not read.",
    "The gate and the flip read the session's closing VIX/VIX3M ratio, as the declared limits say.",
)


# ------------------------------------------------------------------------------------------------
# Pure: the calendar and the planning of paths.


def trading_days(a: date, b: date) -> list[date]:
    d = a if cal.is_trading_day(a) else cal.next_trading_day(a)
    out = []
    while d <= b:
        out.append(d)
        d = cal.next_trading_day(d)
    return out


def arm_params(arm: str) -> dict:
    """The params the module would resolve for the arm: its own PARAM_DEFAULTS, the deployed defaults
    and the arm's one change."""
    spec = ARM_SPECS[arm]
    return {
        **management.PARAM_DEFAULTS,
        **CONFIG["defaults"],
        **spec["over"],
        "arm": spec["base"],
        "base_book": spec["base"],
    }


def targets(days: list[date], params: dict | None = None) -> dict[date, list[date]]:
    """Each target expiry and the sessions that target it: the module's own `target_expiration`."""
    out: dict[date, list[date]] = {}
    for d in days:
        got = curve_clock.target_expiration(d, params or arm_params("control"))
        if got is not None:
            out.setdefault(date.fromisoformat(got["expiration"]), []).append(d)
    return out


def path_requests(days: list[date], through: date = END) -> list[dict]:
    """One daily path per expiry the rule can target: from the first session that targets it to its
    expiry (or the window's last session)."""
    out = []
    for exp, ds in sorted(targets(days).items()):
        out.append(
            {
                "symbol": SYMBOL,
                "expiration": exp.isoformat(),
                "start": min(ds).isoformat(),
                "through": min(exp, through).isoformat(),
            }
        )
    return out


def drop_stored(requests: list[dict], covered) -> tuple[list[dict], int]:
    """The requests the store does not already answer. `covered(request)` is the store's reading."""
    todo = [r for r in requests if not covered(r)]
    return todo, len(requests) - len(todo)


def sample_sessions(gate_open: list[date], every: int = SAMPLE_EVERY) -> list[date]:
    """One session in `every` of the sessions the gate allows, the first being the `every`th."""
    return [d for i, d in enumerate(gate_open, start=1) if i % every == 0]


# ------------------------------------------------------------------------------------------------
# Pure: a session's chain as the module's snapshot.


@dataclass(frozen=True)
class Session:
    date: date
    close: float  # raw
    open: float | None  # raw
    ratio: float | None


def _quote(bid, ask) -> dict | None:
    return streamcache.usable_quote({"bid": bid, "ask": ask, "mid": None, "updated_at": 0.0}, 0.0, 1.0)


def build_snapshot(rows, spot: float, day: date, expiry: date) -> dict | None:
    """`engine.plan_entry`'s input from one stored chain, rows (strike, right, bid, ask). Only calls with a
    usable quote are listed. Each call's IV is inverted from its mid (rate 0, no dividend); a strike whose
    mid is at or under intrinsic has none, so it carries no delta, as in the module. None when no listed
    strike is at or below the raw close: that is the adjusted series, not this day's chain."""
    dte = (expiry - day).days
    entries, quotes, greeks = [], {}, {}
    for strike, right, bid, ask in rows:
        if str(right)[:1].upper() != "C":
            continue
        q = _quote(bid, ask)
        if q is None:
            continue
        sym = f"{SYMBOL} {expiry.isoformat()} C{float(strike):g}"
        entries.append(
            {"option_type": "call", "streamer_symbol": sym, "occ_symbol": sym, "strike_price": float(strike)}
        )
        quotes[sym] = {"bid": q["bid"], "ask": q["ask"], "mid": q["mid"]}
        if q["mid"] > bs.intrinsic(spot, float(strike), "C") + 1e-9:
            iv = bs.implied_vol(spot, float(strike), dte / 365.0, q["mid"], "C", 0.0)
            if iv:
                greeks[sym] = {"iv": iv}
    if not entries or min(e["strike_price"] for e in entries) > spot:
        return None
    return {
        "symbol": SYMBOL,
        "spot": spot,
        "expiration": expiry.isoformat(),
        "dte": dte,
        "chain": entries,
        "quotes": quotes,
        "greeks": greeks,
    }


def plan_refusal(res: dict) -> str:
    """The reason a refused plan names; a wide spread says which leg."""
    if res["reason"] == "spread_too_wide":
        return f"spread_too_wide_{res['detail']['leg']}"
    return res["reason"]


def call_quote(rows, strike: float) -> dict | None:
    for k, right, bid, ask in rows:
        if str(right)[:1].upper() == "C" and float(k) == float(strike):
            return _quote(bid, ask)
    return None


def mark_snapshot(short_q: dict, long_q: dict) -> dict:
    """The exit gate's input, as the provider builds it: per leg the spread in percent and in money."""
    legs, widest = [], None
    for q in (short_q, long_q):
        if q["mid"] > 0:
            pct = (q["ask"] - q["bid"]) / q["mid"]
            widest = pct if widest is None else max(widest, pct)
            legs.append({"pct": round(pct, 4), "abs": round(q["ask"] - q["bid"], 4)})
    return {"ok": True, "leg_spreads": legs, "max_spread_pct": widest}


# ------------------------------------------------------------------------------------------------
# Pure: a trade's money.


@dataclass
class Position:
    arm: str
    entry: date
    expiry: date
    short_k: float
    long_k: float
    credit: float  # per share, at mid
    credit_nat: float  # per share, short bid less long ask
    width: float
    entry_fee: float
    entry_slip: float
    short_q: dict
    long_q: dict
    ratio: float
    dte: int
    selected_by: str
    exposed_sessions: int = 0
    first_exposed: dict | None = None
    last_marks: dict | None = None


def _r(x: float) -> float:
    return round(x + 0.0, 2)


def trade_row(
    pos: Position,
    close: date,
    how: str,
    *,
    exit_mid: float,
    exit_nat: float,
    exit_fee: float,
    exit_slip: float,
    shares: float = 0.0,
    settle: float = 0.0,
    split: bool = False,
    stale: bool = False,
) -> dict:
    """One closed trade in whole-position dollars with signed cash flow: credit `+`, what it cost to
    close or what settlement took `exit` (negative), delivered shares `shares`; gross = their sum; net =
    gross - fees - slippage - settlement. Per-share `exit_mid` is what closing costs at mid."""
    k = MULT * QTY
    credit, exit_cash, share_pnl = _r(pos.credit * k), _r(-exit_mid * k), _r(shares)
    gross = _r(credit + exit_cash + share_pnl)
    fee_t, slip_t, settle_t = _r(pos.entry_fee + exit_fee), _r(pos.entry_slip + exit_slip), _r(settle)
    net = _r(gross - fee_t - slip_t - settle_t)
    max_loss = _r((pos.width - pos.credit) * k)
    nat_gross = pos.credit_nat * k - exit_nat * k + share_pnl
    nat_net = _r(nat_gross - fee_t - settle_t)
    stress = _r(gross - STRESS * (fee_t + slip_t + settle_t))
    return {
        "arm": pos.arm,
        "entry": pos.entry.isoformat(),
        "close": close.isoformat(),
        "expiry": pos.expiry.isoformat(),
        "how": how,
        "short": pos.short_k,
        "long": pos.long_k,
        "width": pos.width,
        "dte": pos.dte,
        "ratio": pos.ratio,
        "selected_by": pos.selected_by,
        "credit": credit,
        "exit": exit_cash,
        "shares": share_pnl,
        "gross": gross,
        "fees": fee_t,
        "slippage": slip_t,
        "settlement": settle_t,
        "net": net,
        "max_loss": max_loss,
        "share": net / max_loss if max_loss > 0 else 0.0,
        "stress_share": stress / max_loss if max_loss > 0 else 0.0,
        "natural_share": nat_net / max_loss if max_loss > 0 else 0.0,
        "nocost_share": gross / max_loss if max_loss > 0 else 0.0,
        "exposed_sessions": pos.exposed_sessions,
        "alt_share": _alt_share(pos, max_loss),
        "split_ended": split,
        "stale_close": stale,
    }


def _alt_share(pos: Position, max_loss: float) -> float | None:
    """The trade's net share had it been closed at the first exposed session's natural fill (None when it
    was never exposed)."""
    fe = pos.first_exposed
    if fe is None or max_loss <= 0:
        return None
    k = MULT * QTY
    cost = engine.close_cost(SYMBOL, [_bidask(fe["short"]), _bidask(fe["long"])], QTY, CONFIG, sell_legs=1)
    net = (
        pos.credit * k
        - (fe["short"]["ask"] - fe["long"]["bid"]) * k
        - pos.entry_fee
        - pos.entry_slip
        - cost["fee"]
    )
    return net / max_loss


def _bidask(q: dict) -> dict:
    return {"bid": q["bid"], "ask": q["ask"]}


def traded_close(pos: Position, day: date, how: str, short_q: dict, long_q: dict, **flags) -> dict:
    cost = engine.close_cost(SYMBOL, [_bidask(short_q), _bidask(long_q)], QTY, CONFIG, sell_legs=1)
    return trade_row(
        pos,
        day,
        how,
        exit_mid=short_q["mid"] - long_q["mid"],
        exit_nat=short_q["ask"] - long_q["bid"],
        exit_fee=cost["fee"],
        exit_slip=cost["slippage"],
        **flags,
    )


def settled(pos: Position, day: date, spot: float, dispose: float) -> dict:
    """Held to expiry: intrinsic at the raw close, and each in-the-money leg delivers shares at that
    close, disposed at the next session's open with the $5 event and the share-side fees."""
    s_in, l_in = engine.settle_intrinsic(pos.short_k, spot), engine.settle_intrinsic(pos.long_k, spot)
    shares = fee = 0.0
    for strike, action in ((pos.short_k, "Sell to Open"), (pos.long_k, "Buy to Open")):
        got = engine.assignment_from({"strike": strike, "action": action}, spot, QTY)
        if got is not None:
            shares += engine.share_pnl(got["direction"], got["shares"], got["basis"], dispose)
            fee += engine.assignment_fee(got, dispose)
    return trade_row(
        pos, day, "expiry", exit_mid=s_in - l_in, exit_nat=s_in - l_in, exit_fee=0.0, exit_slip=0.0,
        shares=shares, settle=fee,
    )  # fmt: skip


# ------------------------------------------------------------------------------------------------
# Pure: one arm through the sessions.


def _flat_result() -> dict:
    return {
        "trades": [],
        "refusals": [],  # (session, reason)
        "attempts": 0,
        "gated": 0,
        "busy": 0,
        "left_open": 0,
        "exit_blocked": 0,
        "exit_unpriced": 0,
        "exposed_sessions": 0,
    }


def _enter(arm, params, s, i, sessions, chain_at, snapshot_at) -> tuple[Position | None, str | None]:
    """(position, None), or (None, reason) for a refusal, or (None, None) when the gate is shut."""
    if s.ratio is None:
        return None, "regime_unmeasured"
    if s.ratio >= GATE:
        return None, None
    got = curve_clock.target_expiration(s.date, params)
    if got is None:
        return None, "no_target_expiration"
    expiry = date.fromisoformat(got["expiration"])
    cut = next((x for x in SPLIT_DATES if s.date < x <= expiry), None)
    if cut is not None and i + 1 < len(sessions) and sessions[i + 1].date == cut:
        return None, "split_next_session"
    rows = chain_at(s.date, expiry)
    if not rows:
        return None, "no_chain"
    snap = snapshot_at(s.date, expiry, s.close)
    if snap is None:
        return None, "chain_not_raw_basis"
    res = engine.plan_entry(snap, params, CONFIG)
    if not res["ok"]:
        return None, plan_refusal(res)
    plan = res["plan"]
    legs = {leg["leg_role"]: leg for leg in plan["legs"]}
    sq, lq = (
        snap["quotes"][legs["short_call"]["streamer_symbol"]],
        snap["quotes"][legs["long_call"]["streamer_symbol"]],
    )
    cost = engine.entry_cost(SYMBOL, [_bidask(sq), _bidask(lq)], QTY, CONFIG)
    return (
        Position(
            arm=arm,
            entry=s.date,
            expiry=expiry,
            short_k=plan["short_strike"],
            long_k=plan["long_strike"],
            credit=plan["credit"],
            credit_nat=sq["bid"] - lq["ask"],
            width=plan["width"],
            entry_fee=cost["fee"],
            entry_slip=cost["slippage"],
            short_q=sq,
            long_q=lq,
            ratio=s.ratio,
            dte=plan["dte"],
            selected_by=plan["short_selected_by"],
        ),
        None,
    )


def dispose_price(sessions: list[Session], i: int) -> float:
    """The next session's open (its close when none is stored), on the basis the shares were delivered
    at: a split ex-date divides out."""
    if i + 1 >= len(sessions):
        return sessions[i].close
    nxt = sessions[i + 1]
    px = nxt.open or nxt.close
    return px * SPLIT_FACTOR if nxt.date in SPLIT_DATES else px


def _manage(pos, params, profit_take, s, i, sessions, chain_at, out) -> dict | None:
    """The trade closed this session, or None. Order: expiry, the split, then the module's `evaluate`
    and the exit-spread gate."""
    d = s.date
    if d >= pos.expiry:
        return settled(pos, d, s.close, dispose_price(sessions, i))
    rows = chain_at(d, pos.expiry)
    sq, lq = call_quote(rows, pos.short_k), call_quote(rows, pos.long_k)
    priced = sq is not None and lq is not None
    if priced:
        pos.last_marks = {"short": sq, "long": lq}
    cut = next((x for x in SPLIT_DATES if pos.entry < x <= pos.expiry), None)
    if cut is not None and i + 1 < len(sessions) and sessions[i + 1].date == cut:
        use = (
            (sq, lq)
            if priced
            else (pos.last_marks["short"], pos.last_marks["long"])
            if pos.last_marks
            else None
        )
        if use is not None:
            return traded_close(pos, d, "split", *use, split=True, stale=not priced)
    regime = (
        None
        if s.ratio is None
        else {"ok": True, "ratio": s.ratio, "regime": curve_regime.classify(s.ratio, params)}
    )
    cost = engine.spread_close_cost(sq, lq) if priced and profit_take else None
    decision = management.evaluate(
        {"expiration": pos.expiry.isoformat(), "entry_credit": pos.credit},
        params,
        now=datetime.combine(d, time(16, 0)),
        close_cost=cost,
        regime=regime,
    )
    if decision.acts:
        if not priced:
            out["exit_unpriced"] += 1
        elif exit_spread_blocks(mark_snapshot(sq, lq), params):
            out["exit_blocked"] += 1
        else:
            return traded_close(pos, d, decision.reason, sq, lq)
    elif priced:
        tv = sq["mid"] - max(0.0, s.close - pos.short_k)
        if management.assignment_exposed(tv, params):
            pos.exposed_sessions += 1
            out["exposed_sessions"] += 1
            if pos.first_exposed is None:
                pos.first_exposed = {"short": sq, "long": lq}
    return None


def simulate(
    arm: str,
    sessions: list[Session],
    chain_at,
    snapshot_at,
    *,
    entry_from: date,
    entry_to: date,
) -> dict:
    """One arm, flat at `entry_from`, entering only through `entry_to` and following its position to the
    close wherever that falls. A position still open on the last session is left out and counted."""
    params = arm_params(arm)
    profit_take = ARM_SPECS[arm]["profit_take"]
    out = _flat_result()
    pos: Position | None = None
    for i, s in enumerate(sessions):
        if s.date < entry_from:
            continue
        if pos is None and s.date > entry_to:
            break
        held, new = pos, None
        if held is None and s.date <= entry_to:
            new, reason = _enter(arm, params, s, i, sessions, chain_at, snapshot_at)
            if new is not None:
                out["attempts"] += 1
            elif reason is None:
                out["gated"] += 1
            else:
                out["attempts"] += 1
                out["refusals"].append((s.date.isoformat(), reason))
        elif held is not None:
            out["busy"] += 1
        if held is not None:
            row = _manage(held, params, profit_take, s, i, sessions, chain_at, out)
            if row is not None:
                out["trades"].append(row)
                held = None
        pos = new if new is not None else held
    if pos is not None:
        out["left_open"] += 1
    return out


# ------------------------------------------------------------------------------------------------
# Pure: the numbers.


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def max_drawdown(nets: list[float]) -> float:
    cum = peak = dd = 0.0
    for x in nets:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def bootstrap(
    values: list[float], *, resamples: int = RESAMPLES, block: float = BLOCK, seed: int = SEED
) -> dict:
    """Stationary bootstrap of the mean: the one-sided p of a mean at or below zero (add-one) and the
    95% interval."""
    n = len(values)
    rng = random.Random(seed)
    means = sorted(
        sum(values[j] for j in core_stats.stationary_indices(n, block, rng)) / n for _ in range(resamples)
    )
    below = sum(1 for m in means if m <= 0)
    lo = means[int(0.025 * resamples)]
    hi = means[min(resamples - 1, math.ceil(0.975 * resamples) - 1)]
    return {"p": (below + 1) / (resamples + 1), "ci": [lo, hi]}


def in_range(trade: dict, a: date, b: date) -> bool:
    return a <= date.fromisoformat(trade["entry"]) <= b


def summarize(trades: list[dict]) -> dict:
    """Per-window figures: counts, means, medians, the worst, the drawdown, costs and net by year."""
    n = len(trades)
    if not n:
        return {"n": 0}
    ordered = sorted(trades, key=lambda t: (t["close"], t["entry"]))
    nets = [t["net"] for t in ordered]
    shares = [t["share"] for t in trades]
    mean_loss = mean([t["max_loss"] for t in trades])
    dd = max_drawdown(nets)
    by_year: dict[str, dict] = {}
    for t in trades:
        y = by_year.setdefault(t["entry"][:4], {"n": 0, "net": 0.0})
        y["n"] += 1
        y["net"] = _r(y["net"] + t["net"])
    return {
        "n": n,
        "mean_net": _r(mean([t["net"] for t in trades])),
        "median_net": _r(statistics.median([t["net"] for t in trades])),
        "mean_share": mean(shares),
        "median_share": statistics.median(shares),
        "worst": min(t["net"] for t in trades),
        "max_drawdown": _r(dd),
        "drawdown_units": dd / mean_loss if mean_loss else None,
        "mean_max_loss": _r(mean_loss),
        "total": {
            c: _r(sum(t[c] for t in trades))
            for c in ("credit", "exit", "shares", "gross", "fees", "slippage", "settlement", "net")
        },
        "by_year": by_year,
        "by_how": {h: sum(1 for t in trades if t["how"] == h) for h in sorted({t["how"] for t in trades})},
        "mean_stress_share": mean([t["stress_share"] for t in trades]),
        "mean_natural_share": mean([t["natural_share"] for t in trades]),
        "mean_nocost_share": mean([t["nocost_share"] for t in trades]),
        "exposed_trades": sum(1 for t in trades if t["exposed_sessions"]),
        "exposed_sessions": sum(t["exposed_sessions"] for t in trades),
        "mean_share_if_closed_exposed": mean(
            [t["alt_share"] if t["alt_share"] is not None else t["share"] for t in trades]
        ),
        "split_ended": sum(1 for t in trades if t["split_ended"]),
        "mean_share_without_split_ended": mean([t["share"] for t in trades if not t["split_ended"]]),
        "n_without_split_ended": sum(1 for t in trades if not t["split_ended"]),
    }


def episodes(trades: list[dict], a: date, b: date) -> dict:
    """The pre-declared episodes inside the window [a, b]: each one's trades listed, and the mean share of
    the trades outside every episode."""
    listed = {}
    inside: set[int] = set()
    for name, e0, e1 in EPISODES:
        if e1 < a or e0 > b:
            continue
        hit = [t for t in trades if e0 <= date.fromisoformat(t["entry"]) <= e1]
        inside.update(id(t) for t in hit)
        listed[name] = [
            {"entry": t["entry"], "close": t["close"], "how": t["how"], "net": t["net"], "share": t["share"]}
            for t in hit
        ]
    rest = [t["share"] for t in trades if id(t) not in inside]
    return {"episodes": listed, "n_without": len(rest), "mean_share_without": mean(rest)}


def refusal_counts(refusals: list[tuple[str, str]]) -> dict:
    by_reason: dict[str, int] = {}
    by_year: dict[str, dict[str, int]] = {}
    for day, reason in refusals:
        by_reason[reason] = by_reason.get(reason, 0) + 1
        y = by_year.setdefault(day[:4], {})
        y[reason] = y.get(reason, 0) + 1
    return {"by_reason": dict(sorted(by_reason.items())), "by_year": dict(sorted(by_year.items()))}


def judge(trades_by_arm: dict[str, list[dict]]) -> dict:
    """The design checks per arm. Holm runs across every arm in the family; an arm without enough trades
    carries p = 1."""
    out: dict[str, dict] = {}
    for arm, tr in trades_by_arm.items():
        shares = [t["share"] for t in tr]
        d: dict = {"n": len(tr), "enough": len(tr) >= MIN_TRADES, "p": None}
        halves = []
        for a, b in HALVES:
            h = [t["share"] for t in tr if in_range(t, a, b)]
            halves.append({"n": len(h), "read": len(h) >= MIN_HALF, "mean": mean(h)})
        d["halves"] = halves
        if d["enough"]:
            boot = bootstrap(shares)
            sd = statistics.stdev(shares) if len(shares) > 1 else 0.0
            d.update(
                p=boot["p"],
                ci=boot["ci"],
                mean=mean(shares),
                se=sd / math.sqrt(len(shares)),
                stress_mean=mean([t["stress_share"] for t in tr]),
            )
        out[arm] = d
    holm = core_stats.holm({a: (d["p"] if d["enough"] else 1.0) for a, d in out.items()}, ALPHA)
    adj = core_stats.holm_adjusted({a: (d["p"] if d["enough"] else 1.0) for a, d in out.items()})
    for arm, d in out.items():
        d["holm_p"] = adj[arm]
        if not d["enough"]:
            d["conditions"] = None
            continue
        read = [h for h in d["halves"] if h["read"]]
        d["conditions"] = {
            "mean_positive": d["mean"] > 0,
            "holm": bool(holm[arm]),
            "halves_positive": bool(read) and all(h["mean"] > 0 for h in read),
            "stress_positive": d["stress_mean"] > 0,
        }
        d["mde_mean"] = 1.645 * d["se"]
    return out


def arm_verdict(design: dict, holdout: dict | None) -> dict:
    """pass / fail / no verdict for one arm, with why."""
    if not design["enough"]:
        return {
            "verdict": "no verdict",
            "why": f"design window: {design['n']} closed trades, under {MIN_TRADES}",
        }
    failed = [k for k, v in design["conditions"].items() if not v]
    if failed:
        return {"verdict": "fail", "why": "design window misses: " + ", ".join(failed)}
    if holdout is None or holdout["n"] < MIN_TRADES:
        n = 0 if holdout is None else holdout["n"]
        return {"verdict": "no verdict", "why": f"holdout: {n} closed trades, under {MIN_TRADES}"}
    if holdout["mean"] > 0:
        return {"verdict": "pass", "why": "design checks hold and the holdout mean is above zero"}
    return {"verdict": "fail", "why": "the holdout mean is not above zero"}


def holdout_reading(trades: list[dict]) -> dict:
    if len(trades) < MIN_TRADES:
        return {"n": len(trades), "mean": mean([t["share"] for t in trades]), "ci": None}
    boot = bootstrap([t["share"] for t in trades])
    return {"n": len(trades), "mean": mean([t["share"] for t in trades]), "ci": boot["ci"]}


def flip_divergence(control: list[dict], noflip: list[dict]) -> dict:
    """Control trades closed on the flip whose entry the noflip arm held past it."""
    flips = [t for t in control if t["how"] == "regime_flip"]
    held = {t["entry"]: t for t in noflip}
    diverged = sum(1 for t in flips if t["entry"] in held and held[t["entry"]]["how"] != "regime_flip")
    return {"flip_divergence_count": diverged, "control_flip_exits": len(flips)}


# ------------------------------------------------------------------------------------------------
# Pure: the share reference.


def adjusted(session: Session) -> float:
    return session.close / (SPLIT_FACTOR ** sum(1 for x in SPLIT_DATES if x > session.date))


def share_reference(sessions: list[Session], a: date, b: date) -> dict:
    """Short VXX at the close of a session whose ratio is under the gate, held to the next close; flat
    otherwise; split-adjusted closes, REF_COST a side each time the position changes."""
    eq = peak = 1.0
    dd = worst = 0.0
    short = False
    days = 0
    first = last = None
    for prev, cur in zip(sessions, sessions[1:], strict=False):
        if not (a <= cur.date <= b):
            continue
        want = prev.ratio is not None and prev.ratio < GATE
        r = 0.0
        if want:
            r = -(adjusted(cur) / adjusted(prev) - 1.0)
        if want != short:
            r -= REF_COST
        short = want
        eq *= 1.0 + r
        peak = max(peak, eq)
        dd = max(dd, 1.0 - eq / peak)
        worst = min(worst, r)
        days += 1
        first = first or prev.date
        last = cur.date
    years = (last - first).days / 365.25 if first and last and last > first else 0.0
    cagr = (eq ** (1 / years) - 1) if years > 0 and eq > 0 else None
    return {"sessions": days, "cagr": cagr, "max_drawdown": dd, "worst_day": worst, "final": eq}


# ------------------------------------------------------------------------------------------------
# The whole run, over whatever sessions and chains it is handed.


def run_all(sessions: list[Session], chain_at, snapshot_at) -> dict:
    d0, d1 = WINDOWS["design"]
    h0, h1 = WINDOWS["holdout"]
    arms: dict[str, dict] = {a: {} for a in ARMS}
    sims: dict[str, dict] = {}
    for a in ARMS:
        sims[a] = simulate(a, sessions, chain_at, snapshot_at, entry_from=d0, entry_to=d1)
    design = judge({a: sims[a]["trades"] for a in ARMS})
    holdouts: dict[str, dict | None] = {}
    hsims: dict[str, dict] = {}
    for a in ARMS:
        cond = design[a]["conditions"]
        if cond is not None and all(cond.values()):
            hsims[a] = simulate(a, sessions, chain_at, snapshot_at, entry_from=h0, entry_to=h1)
            holdouts[a] = holdout_reading(hsims[a]["trades"])
        else:
            holdouts[a] = None
    verdict: dict[str, dict] = {}
    for a in ARMS:
        verdict[a] = arm_verdict(design[a], holdouts[a])
        arms[a]["design"] = _window_block(sims[a], d0, d1, design[a])
        arms[a]["holdout"] = _window_block(hsims[a], h0, h1, holdouts[a]) if a in hsims else {"read": False}
    result = {
        "verdict": verdict,
        "window": f"{d0}..{d1}" + (f"; holdout {h0}..{h1}" if hsims else ""),
        "run_id": f"item37-{date.today().isoformat()}",
        "family": {"arms": list(ARMS), "alpha": ALPHA, "resamples": RESAMPLES, "block": BLOCK, "seed": SEED},
        "arms": arms,
        "flip_divergence": {"design": flip_divergence(sims["control"]["trades"], sims["noflip"]["trades"])},
        "reference": {
            "design": share_reference(sessions, d0, d1),
            "holdout": share_reference(sessions, h0, h1),
        },
        "notes": list(RULES_NOTES),
    }
    if "control" in hsims and "noflip" in hsims:
        result["flip_divergence"]["holdout"] = flip_divergence(
            hsims["control"]["trades"], hsims["noflip"]["trades"]
        )
    return result


def _window_block(sim: dict, a: date, b: date, judged: dict) -> dict:
    return {
        "read": True,
        "summary": summarize(sim["trades"]),
        "judged": judged,
        "episodes": episodes(sim["trades"], a, b),
        "refusals": refusal_counts(sim["refusals"]),
        "attempts": sim["attempts"],
        "refused": len(sim["refusals"]),
        "gated_sessions": sim["gated"],
        "busy_sessions": sim["busy"],
        "left_open": sim["left_open"],
        "exit_blocked": sim["exit_blocked"],
        "exit_unpriced": sim["exit_unpriced"],
        "trades": sim["trades"],
    }


# ------------------------------------------------------------------------------------------------
# The morning sample: the same plan on a 10:00 chain, against the close's.


def morning_check(rows, spot: float, day: date, expiry: date, close_plan: dict | None, params: dict) -> dict:
    """`plan_entry` on the 10:00 chain, rows (strike, right, bid, ask, delta). Reported against the close's
    plan (`close_plan` is plan_entry's result there, None when the close had no plan to compare)."""
    snap = build_snapshot([(k, r, b, a) for k, r, b, a, _ in rows], spot, day, expiry)
    res = (
        {"ok": False, "reason": "chain_not_raw_basis"}
        if snap is None
        else engine.plan_entry(snap, params, CONFIG)
    )
    out = {
        "session": day.isoformat(),
        "morning_ok": res["ok"],
        "morning_reason": None if res["ok"] else plan_refusal(res),
    }
    if close_plan is not None:
        out["close_ok"] = close_plan["ok"]
        out["close_reason"] = None if close_plan["ok"] else plan_refusal(close_plan)
        if res["ok"] and close_plan["ok"]:
            out["same_short"] = res["plan"]["short_strike"] == close_plan["plan"]["short_strike"]
            out["same_long"] = res["plan"]["long_strike"] == close_plan["plan"]["long_strike"]
    if res["ok"]:
        k = res["plan"]["short_strike"]
        sym = f"{SYMBOL} {expiry.isoformat()} C{k:g}"
        ours = (snap["greeks"].get(sym) or {}).get("iv")
        out["our_delta"] = engine.bs_call_delta(spot, k, (expiry - day).days, ours) if ours else None
        out["vendor_delta"] = next(
            (dl for kk, r, _b, _a, dl in rows if float(kk) == k and str(r)[:1].upper() == "C"), None
        )
    return out


def morning_summary(checks: list[dict]) -> dict:
    n = len(checks)
    both = [c for c in checks if "close_ok" in c]
    diff = [
        abs(c["our_delta"] - c["vendor_delta"])
        for c in checks
        if c.get("our_delta") is not None and c.get("vendor_delta") is not None
    ]
    return {
        "sessions": n,
        "morning_refused": sum(1 for c in checks if not c["morning_ok"]),
        "close_refused": sum(1 for c in both if not c["close_ok"]),
        "compared": len(both),
        "refusal_differs": sum(1 for c in both if c["morning_ok"] != c["close_ok"]),
        "short_differs": sum(1 for c in checks if c.get("same_short") is False),
        "long_differs": sum(1 for c in checks if c.get("same_long") is False),
        "mean_abs_delta_gap": mean(diff),
        "delta_pairs": len(diff),
    }


# ------------------------------------------------------------------------------------------------
# I/O: the stores.


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item37"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _chain_store():
    from cherrypick.backtest import chainfetch, settings

    path = chainfetch.db_path(settings.load(None))
    return _ro(path) if path.exists() else None


def store_coverage(conn):
    """`covered(request)` over the chain store: a path row answers it (complete or empty, spanning the
    request), or at least COVERAGE of its sessions are held."""
    if conn is None:
        return lambda r: False
    try:
        paths_ = conn.execute(
            "SELECT expiration, start, through FROM eod_chain_paths "
            "WHERE symbol = ? AND status IN ('complete', 'empty')",
            (SYMBOL,),
        ).fetchall()
    except sqlite3.OperationalError:
        paths_ = []
    try:
        held: dict[str, set[str]] = {}
        for sess, exp in conn.execute(
            "SELECT DISTINCT session, expiration FROM eod_chain_quotes WHERE symbol = ?", (SYMBOL,)
        ):
            held.setdefault(exp, set()).add(sess)
    except sqlite3.OperationalError:
        held = {}

    def covered(r: dict) -> bool:
        if any(e == r["expiration"] and s <= r["start"] and t >= r["through"] for e, s, t in paths_):
            return True
        need = trading_days(date.fromisoformat(r["start"]), date.fromisoformat(r["through"]))
        got = sum(1 for d in need if d.isoformat() in held.get(r["expiration"], ()))
        return bool(need) and got >= COVERAGE * len(need)

    return covered


def load_ratios() -> dict[date, float]:
    from cherrypick.overview import files

    def close(name):
        return dict(files.parse_cboe(files.cboe_path(name).read_text(encoding="utf-8")))

    vix, v3m = close("VIX"), close("VIX3M")
    out = {}
    for d in vix.keys() & v3m.keys():
        r = curve_regime.ratio(vix[d], v3m[d])
        if r is not None:
            out[d] = r
    return out


def load_sessions(ratios: dict[date, float]) -> tuple[list[Session], int]:
    from cherrypick.technicals import paths as tpaths
    from cherrypick.technicals import store

    hist = store.connect(tpaths.history_db())
    try:
        bars = store.raw_bars(hist, SYMBOL)
    finally:
        hist.close()
    out = []
    for b in bars:
        d = date.fromisoformat(b.date)
        if START <= d <= END and b.close:
            out.append(Session(d, float(b.close), float(b.open) if b.open else None, ratios.get(d)))
    missing = len(trading_days(START, END)) - len(out)
    return out, missing


def cmd_plan(args) -> int:
    days = trading_days(START, END)
    todo, stored = drop_stored(path_requests(days), store_coverage(_chain_store()))
    path = Path(args.out) if args.out else _out_dir() / "chain_requests.json"
    path.write_text(json.dumps({"requests": todo}, indent=1), encoding="utf-8")
    report = {"requests": len(todo), "already_stored": stored, "file": str(path)}
    try:
        ratios = load_ratios()
        gate_open = [d for d in days if d in ratios and ratios[d] < GATE]
        picked, reqs = sample_sessions(gate_open), []
        for d in picked:
            got = curve_clock.target_expiration(d, arm_params("control"))
            if got:
                reqs.append(
                    {"symbol": SYMBOL, "session": d.isoformat(), "expiration": got["expiration"],
                     "start": "10:00", "end": "10:30", "interval": "5m"}
                )  # fmt: skip
        ipath = path.with_name("intraday_requests.json")
        ipath.write_text(json.dumps({"requests": reqs}, indent=1), encoding="utf-8")
        report["intraday_requests"] = len(reqs)
        report["intraday_file"] = str(ipath)
    except (OSError, KeyError, ValueError) as exc:
        report["intraday_requests"] = f"not planned: {type(exc).__name__}"
    print(json.dumps(report))
    return 0


def _make_chain_at(conn):
    @lru_cache(maxsize=8192)
    def cached(session: str, expiry: str):
        return tuple(
            (k, r, b, a)
            for k, r, b, a in conn.execute(
                "SELECT strike, right, bid, ask FROM eod_chain_quotes "
                "WHERE symbol = ? AND session = ? AND expiration = ?",
                (SYMBOL, session, expiry),
            )
        )

    return lambda d, e: cached(d.isoformat(), e.isoformat())


def make_snapshot_at(chain_at):
    @lru_cache(maxsize=4096)
    def cached(day: date, expiry: date, spot: float):
        return build_snapshot(chain_at(day, expiry), spot, day, expiry)

    return cached


def paper_reading() -> dict | None:
    """The paper ledger's entry attempts for control: sessions with the gate open and how many filled."""
    try:
        from cherrypick.curve import db as curve_db

        path = Path(curve_db.default_db_path())
        if not path.exists():
            return None
        conn = _ro(path)
        try:
            rows = conn.execute(
                "SELECT trade_date, outcome FROM curve_entry_attempts WHERE arm = 'control' AND symbol = ?",
                (SYMBOL,),
            ).fetchall()
        finally:
            conn.close()
    except (sqlite3.Error, ImportError):
        return None
    days = {}
    for d, o in rows:
        if o != "not_contango":
            days[d] = o if d not in days or days[d] != "filled" else "filled"
    reasons: dict[str, int] = {}
    for o in days.values():
        reasons[o] = reasons.get(o, 0) + 1
    gate_open = len(days)
    filled = sum(1 for o in days.values() if o == "filled")
    return {
        "gate_open_sessions": gate_open,
        "filled": filled,
        "no_fill_share": (gate_open - filled) / gate_open if gate_open else None,
        "outcomes": dict(sorted(reasons.items())),
    }


def morning_sample(sessions: list[Session], ratios, chain_at) -> dict | None:
    from cherrypick.backtest import intradayfetch, settings

    path = intradayfetch.db_path(settings.load(None))
    if not path.exists():
        return None
    conn = _ro(path)
    by_day = {s.date: s for s in sessions}
    gate_open = [
        s.date for s in sessions if s.ratio is not None and s.ratio < GATE and START <= s.date <= END
    ]
    params = arm_params("control")
    checks = []
    try:
        for d in sample_sessions(gate_open):
            got = curve_clock.target_expiration(d, params)
            if got is None:
                continue
            expiry = date.fromisoformat(got["expiration"])
            try:
                bars = conn.execute(
                    "SELECT bar, strike, right, bid, ask, delta, underlying FROM intraday_chain_quotes "
                    "WHERE symbol = ? AND session = ? AND expiration = ? AND bar >= '10:00' ORDER BY bar",
                    (SYMBOL, d.isoformat(), expiry.isoformat()),
                ).fetchall()
            except sqlite3.OperationalError:
                return None
            if not bars:
                continue
            first = bars[0][0]
            rows = [(k, r, b, a, dl) for bar, k, r, b, a, dl, _u in bars if bar == first]
            spot = next((u for bar, *_x, u in bars if bar == first and u), None)
            if not spot:
                continue
            snap = build_snapshot(chain_at(d, expiry), by_day[d].close, d, expiry)
            close_plan = None if snap is None else engine.plan_entry(snap, params, CONFIG)
            checks.append(morning_check(rows, float(spot), d, expiry, close_plan, params))
    finally:
        conn.close()
    return {"checks": checks, "summary": morning_summary(checks)} if checks else None


def cmd_study(args) -> int:
    ratios = load_ratios()
    sessions, missing = load_sessions(ratios)
    conn = _chain_store()
    if conn is None:
        raise SystemExit("no chain store: run the fetch first (plan, then fetch-chain-paths)")
    chain_at = _make_chain_at(conn)
    snapshot_at = make_snapshot_at(chain_at)
    try:
        result = run_all(sessions, chain_at, snapshot_at)
        result["data"] = {
            "sessions": len(sessions),
            "missing_bars": missing,
            "split_dates": [d.isoformat() for d in SPLIT_DATES],
        }
        result["paper"] = paper_reading()
        result["morning_sample"] = morning_sample(sessions, ratios, chain_at)
    finally:
        conn.close()
    path = Path(args.json) if args.json else _out_dir() / "results.json"
    path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    path.with_suffix(".md").write_text(report(result), encoding="utf-8")
    print(json.dumps({"verdict": {a: v["verdict"] for a, v in result["verdict"].items()}, "file": str(path)}))
    return 0


# ------------------------------------------------------------------------------------------------
# results.md beside results.json, for the console's studies tab.


def _pc(x: float | None, nd: int = 1) -> str:
    if x is None:
        return ""
    v = round(100 * x, nd)
    return f"{'−' if v < 0 else '+' if v > 0 else ''}{abs(v):.{nd}f}%"


def _usd(x: float | None) -> str:
    if x is None:
        return ""
    v = round(x)
    return f"{'−' if v < 0 else ''}${abs(v):,}"


def _table(head: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    return out + ["| " + " | ".join(r) + " |" for r in rows]


CHECK_LABELS = {
    "mean_positive": "mean net share above zero",
    "holm": "p passing Holm across five arms",
    "halves_positive": "positive in each half read",
    "stress_positive": "positive at 1.5x costs",
}


def _design(result: dict, arm: str) -> dict:
    return result["arms"][arm]["design"]


def _arm_lines(result: dict) -> list[str]:
    out = []
    for a in ARMS:
        blk = _design(result, a)
        d, s = blk["judged"], blk["summary"]
        if not d["enough"]:
            out.append(
                f"**{a}:** {d['n']} closed design trades from {blk['attempts']} entry attempts, "
                f"{blk['refused']} refused: no verdict, which is not a fail."
            )
            continue
        failed = [CHECK_LABELS[k] for k, v in d["conditions"].items() if not v]
        out.append(
            f"**{a}** (design, {s['n']} trades): mean {_pc(s['mean_share'])} of max loss a trade, "
            f"{_usd(s['mean_net'])}; Holm-adjusted p {d['holm_p']:.4f}; at 1.5x costs "
            f"{_pc(d['stress_mean'])}. "
            + (f"Misses: {'; '.join(failed)}." if failed else "Every design check holds.")
        )
    return out


def _versus_lines(result: dict) -> list[str]:
    out = []
    base = _design(result, "control")["summary"]
    fd = result["flip_divergence"]["design"]
    if base.get("n") and _design(result, "noflip")["summary"].get("n"):
        out.append(
            f"**control against noflip:** the flip rule ended {fd['control_flip_exits']} control trades, "
            f"and noflip held {fd['flip_divergence_count']} of them past it. That count, not the trade "
            "counts, is the pair's real difference."
        )
    for a, what in (
        ("hold-expiry", "holding to expiry"),
        ("wide5", "the $5 width"),
        ("near", "the 0.40 delta"),
    ):
        v = _design(result, a)["summary"]
        if base.get("n") and v.get("n"):
            out.append(
                f"**{a} against control:** {what} moves the design mean share by "
                f"{_pc(v['mean_share'] - base['mean_share'])} "
                f"({_pc(base['mean_share'])} to {_pc(v['mean_share'])})."
            )
    return out


def _reference_line(result: dict) -> list[str]:
    ref = result["reference"]["design"]
    if ref["cagr"] is None:
        return []
    means = [_design(result, a)["summary"].get("mean_share") for a in ARMS]
    spreads_pay = any(m is not None and m > 0 for m in means)
    shares_pay = ref["cagr"] > 0
    if shares_pay and not spreads_pay:
        tail = "the signal pays and no spread does, so the cost is the structure's"
    elif shares_pay:
        tail = "both pay, so the structure keeps some of the signal"
    elif not spreads_pay:
        tail = "the gated shares do not pay either, so the window held no signal to harvest"
    else:
        tail = "a spread pays where the shares do not"
    return [
        f"**Without the spread:** gated short VXX shares returned {_pc(ref['cagr'])} a year in the design "
        f"window, worst day {_pc(ref['worst_day'])}, drawdown {_pc(ref['max_drawdown'], 0)}; {tail}."
    ]


def _caveat_lines(result: dict) -> list[str]:
    turned = []
    for a in ARMS:
        blk = _design(result, a)
        wo = blk["episodes"]["mean_share_without"] if blk["summary"].get("n") else None
        if wo is not None and (blk["summary"]["mean_share"] > 0) != (wo > 0):
            turned.append(a)
    spike = (
        f"the sign of the design mean turns on the declared episodes for {', '.join(turned)}."
        if turned
        else "no arm's design mean changes sign when the declared episodes are removed."
    )
    need = [
        f"{a} {_pc(_design(result, a)['judged']['mde_mean'])}"
        for a in ARMS
        if _design(result, a)["judged"].get("mde_mean") is not None
    ]
    control = _design(result, "control")
    rate = control["refused"] / control["attempts"] if control["attempts"] else None
    paper = result.get("paper") or {}
    paper_text = (
        f"paper's no-fill share on gate-open sessions is {_pc(paper['no_fill_share'], 0)} "
        f"over {paper['gate_open_sessions']} sessions"
        if paper.get("no_fill_share") is not None
        else "paper's ledger was not read"
    )
    return [
        f"**One spike:** {spike}",
        "**What the sample could see:** the design mean an arm needs for its one-sided bound to clear "
        f"zero is {', '.join(need) if need else 'not computed (too few trades)'} of max loss. A fail reads "
        "as no edge that large, not as no edge.",
        f"**Refusals:** control refused {control['refused']} of {control['attempts']} attempts in the "
        f"design window ({_pc(rate, 0)}); {paper_text}.",
    ]


def findings(result: dict) -> list[str]:
    """The plain-English reading, worked out from the figures each time the study runs."""
    verdict = result["verdict"]
    passing = [a for a, v in verdict.items() if v["verdict"] == "pass"]
    if passing:
        head = (
            f"**{', '.join(passing)} pass.** Each cleared the design checks and then had a holdout mean "
            "above zero. All that pass are reported and the user picks; control stays unless a variant's "
            "design mean is higher."
        )
    else:
        head = (
            "**No arm passes.** "
            + "; ".join(f"{a}: {verdict[a]['why']}" for a in ARMS)
            + ". Curve stays as it is in paper, and this is the evidence the retirement rule needs."
        )
    return [
        head,
        *_arm_lines(result),
        *_versus_lines(result),
        *_reference_line(result),
        *_caveat_lines(result),
    ]


READING = (
    "**mean net share**: a trade's net dollars over the position's max loss at entry, averaged. Net is "
    "after fees, slippage and settlement.",
    "**p (Holm)**: the one-sided stationary-bootstrap p that the mean is at or below zero, adjusted by "
    "Holm across the five arms; it must be under 0.05.",
    "**half 1 / half 2**: the mean over entries 2019-05-13 to 2021-02-26 and 2021-03-01 to 2022-12-30, "
    "with the trade count; a half under 10 trades is not read.",
    "**at 1.5x**: the same trades with every fee, slippage and settlement figure multiplied by 1.5.",
    "**holdout**: the mean over entries 2023-01-03 to 2026-10-28 with its 95% interval, for an arm that "
    "passed the design only.",
    "**dollars** are for the whole position at quantity 2, so they are not comparable between widths; "
    "shares of max loss are.",
    "**refusals** are entry attempts on a session with the gate open and no position held; a shut gate "
    "is not a refusal.",
)


def _holdout_cell(h: dict) -> str:
    if not h.get("read"):
        return "not read"
    j = h["judged"]
    ci = f" [{_pc(j['ci'][0])}, {_pc(j['ci'][1])}]" if j.get("ci") else ""
    return _pc(j["mean"]) + ci


def _judged_table(result: dict) -> list[str]:
    rows = []
    for a in ARMS:
        d = _design(result, a)["judged"]
        hold = _holdout_cell(result["arms"][a]["holdout"])
        word = result["verdict"][a]["verdict"]
        if not d["enough"]:
            rows.append([a, str(d["n"]), "", "", "", "", "", hold, word])
            continue
        h1, h2 = d["halves"]
        rows.append(
            [
                a,
                str(d["n"]),
                _pc(d["mean"]),
                f"{d['holm_p']:.4f}",
                f"{_pc(h1['mean'])} ({h1['n']})",
                f"{_pc(h2['mean'])} ({h2['n']})",
                _pc(d["stress_mean"]),
                hold,
                word,
            ]
        )
    head = [
        "arm",
        "trades",
        "mean net share",
        "p (Holm)",
        "half 1",
        "half 2",
        "at 1.5x",
        "holdout",
        "verdict",
    ]
    return _table(head, rows)


MONEY = ("credit", "exit", "shares", "gross", "fees", "slippage", "settlement", "net")


def _window_tables(result: dict, w: str, read: list[str]) -> list[str]:
    blocks = {a: result["arms"][a][w] for a in read}
    out = ["", f"## {w.capitalize()} window", ""]
    rows = []
    for a, b in blocks.items():
        s = b["summary"]
        if not s["n"]:
            rows.append([a, "0"] + [""] * 7 + [str(b["left_open"])])
            continue
        du = f"{s['drawdown_units']:.2f}" if s["drawdown_units"] is not None else ""
        rows.append(
            [a, str(s["n"]), _usd(s["mean_net"]), _usd(s["median_net"]), _pc(s["mean_share"]),
             _pc(s["median_share"]), _usd(s["worst"]), _usd(s["max_drawdown"]), du, str(b["left_open"])]
        )  # fmt: skip
    head = ["arm", "trades", "mean $", "median $", "mean share", "median share", "worst $", "max drawdown $",
            "drawdown / mean max loss", "left open"]  # fmt: skip
    out += _table(head, rows)
    money = [
        [a, *[_usd(b["summary"]["total"][c]) for c in MONEY]] for a, b in blocks.items() if b["summary"]["n"]
    ]
    out += ["", "Money columns, whole-position dollars summed over the window:", ""]
    out += _table(["arm", *MONEY], money)
    years = sorted({y for b in blocks.values() for y in b["summary"].get("by_year", {})})
    rows = []
    for a, b in blocks.items():
        by = b["summary"].get("by_year", {})
        rows.append([a, *[f"{_usd(by[y]['net'])} ({by[y]['n']})" if y in by else "" for y in years]])
    out += ["", "Net dollars by entry year (trades):", ""] + _table(["arm", *years], rows)
    return out + _refusal_tables(blocks, w) + _reported_table(blocks)


def _refusal_tables(blocks: dict, w: str) -> list[str]:
    reasons = sorted({r for b in blocks.values() for r in b["refusals"]["by_reason"]})
    rows = []
    for a, b in blocks.items():
        by = b["refusals"]["by_reason"]
        rows.append(
            [a, str(b["attempts"]), str(b["refused"]), str(b["gated_sessions"]), str(b["busy_sessions"]),
             str(b["exit_blocked"]), str(b["exit_unpriced"]), *[str(by.get(r, 0)) for r in reasons]]
        )  # fmt: skip
    head = ["arm", "attempts", "refused", "gated", "busy", "exit blocked", "exit unpriced", *reasons]
    out = [
        "",
        "Refusals by reason (attempts, refusals, sessions gated shut, sessions holding a position):",
        "",
    ]
    out += _table(head, rows)
    control = blocks.get("control")
    if control and control["refusals"]["by_year"]:
        by_year = control["refusals"]["by_year"]
        rows = [
            [y, str(sum(c.values())), ", ".join(f"{r} {n}" for r, n in sorted(c.items()))]
            for y, c in by_year.items()
        ]
        out += ["", "Control's refusals by year:", ""] + _table(["year", "refused", "by reason"], rows)
    return out


def _reported_table(blocks: dict) -> list[str]:
    rows = []
    for a, b in blocks.items():
        s = b["summary"]
        if s["n"]:
            rows.append(
                [a, _pc(s["mean_nocost_share"]), _pc(s["mean_natural_share"]), str(s["exposed_trades"]),
                 str(s["exposed_sessions"]), _pc(s["mean_share_if_closed_exposed"]), str(s["split_ended"]),
                 _pc(s["mean_share_without_split_ended"]), _pc(b["episodes"]["mean_share_without"])]
            )  # fmt: skip
    head = ["arm", "no-cost mean", "natural-fill mean", "exposed trades", "exposed sessions",
            "mean if closed when exposed", "split-ended", "mean without them",
            "mean without the episodes"]  # fmt: skip
    out = ["", "Reported, not judged:", ""] + _table(head, rows)
    for a, b in blocks.items():
        for name, trs in b["episodes"]["episodes"].items():
            if trs:
                listed = "; ".join(f"{t['entry']} {t['how']} {_usd(t['net'])}" for t in trs)
                out += ["", f"{a}, trades opened in the {name}: {listed}"]
    return out


def _morning_lines(ms: dict | None) -> list[str]:
    if not ms:
        return []
    s = ms["summary"]
    gap = f"{s['mean_abs_delta_gap']:.3f}" if s["mean_abs_delta_gap"] is not None else "n/a"
    return [
        "## The 10:00 sample",
        "",
        f"{s['sessions']} sessions, one gate-open session in 25. The morning chain refused "
        f"{s['morning_refused']}; the close refused {s['close_refused']} of the {s['compared']} compared. "
        f"They disagreed on whether to enter in {s['refusal_differs']}, on the short strike in "
        f"{s['short_differs']} and on the wing in {s['long_differs']}. The vendor's delta against ours on "
        f"the short differs by {gap} on average over {s['delta_pairs']} sessions. Not judged.",
        "",
    ]


def report(result: dict) -> str:
    verdict = result["verdict"]
    lines = [
        "# Item 37: curve's own rule at real quotes",
        "",
        "**Verdicts:** " + ", ".join(f"{a} {verdict[a]['verdict']}" for a in ARMS) + ". Each arm is the "
        "module's own VXX call credit spread, run by `plan_entry` and `evaluate` on end-of-day quotes, "
        "entered on a session the VIX/VIX3M gate allows with no position open. The design window "
        "decides; the holdout is read only for an arm that passes it.",
        "",
        "## The judged checks",
        "",
        *_judged_table(result),
        "",
        "## What it says",
        "",
        *[f"- {x}" for x in findings(result)],
        "",
        "## How to read the tables",
        "",
        *[f"- {x}" for x in READING],
    ]
    for w in ("design", "holdout"):
        read = [a for a in ARMS if result["arms"][a][w].get("read")]
        if read:
            lines += _window_tables(result, w, read)
    lines += ["", "## The signal without the spread", ""]
    ref_rows = [
        [w, str(r["sessions"]), _pc(r["cagr"]), _pc(r["worst_day"]), _pc(r["max_drawdown"], 0)]
        for w, r in result["reference"].items()
    ]
    lines += _table(["window", "sessions", "CAGR", "worst day", "max drawdown"], ref_rows)
    lines += [
        "",
        "Gated short VXX shares: short the close of a session whose ratio is under 0.97, held to the "
        "next close, flat otherwise, split-adjusted, 0.05% a side, no borrow.",
        "",
        *_morning_lines(result.get("morning_sample")),
        "## Reading choices",
        "",
        *[f"- {x}" for x in result.get("notes", [])],
    ]
    data = result.get("data")
    if data:
        lines += [
            "",
            f"Sessions read: {data['sessions']}; trading days with no VXX bar: {data['missing_bars']}.",
        ]
    return "\n".join(lines) + "\n"


def cmd_report(args) -> int:
    src = Path(args.json) if args.json else _out_dir() / "results.json"
    src.with_suffix(".md").write_text(report(json.loads(src.read_text(encoding="utf-8"))), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan", help="the fetch-chain-paths request file")
    pl.add_argument("--out")
    pl.set_defaults(fn=cmd_plan)
    st = sub.add_parser("study", help="read the stores and state each arm's verdict")
    st.add_argument("--json")
    st.set_defaults(fn=cmd_study)
    rp = sub.add_parser("report", help="results.md from an existing results.json (local)")
    rp.add_argument("--json")
    rp.set_defaults(fn=cmd_report)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
