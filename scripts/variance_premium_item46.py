"""Item 46, the variance premium by underlying, tenor and strike (docs/edge-hypotheses-plan.md): its inputs.

    python scripts/variance_premium_item46.py plan [--out FILE]       # chain-path requests
    python scripts/variance_premium_item46.py coverage [--out FILE]   # what the store holds, and the MDE
    python scripts/variance_premium_item46.py study [--account USD]   # the 40 cells, results.json + .md
    python scripts/variance_premium_item46.py report [--json FILE]    # results.md from results.json

`plan` writes the input of `fetch-chain-paths`: for every name, each standard monthly cycle from
`FIRST_EXPIRY` to `LAST_EXPIRY`, the expiry's daily end-of-day path from `PATH_DAYS` (66) calendar days
before it to the expiry itself, the convention the store already holds, so paths fetched for earlier
studies are skipped by the fetch rather than fetched again. It writes under the backtest data dir
(results/research/item46/), never into the repo, and prints counts only.

**Declared, as the plan states:** the equity-index group is SPX (its SPXW third-Friday expiry,
PM-settled), SPY, QQQ and IWM; the non-equity group is GLD, SLV and TLT. A monthly expiry is the third
Friday, or the trading day before it when that Friday is a holiday.

**The coverage step** reads entry quotes and each session's forward only, never an option's exit, so
it runs before any P&L is read. Per name: what became of each path request, the first complete cycle
(SPXW's third-Friday reach is the open question), and why a (cycle, tenor) has no trade: its entry or
expiry session missing, no forward, or no leg. Per cell (group x tenor x leg): the cycles with a trade
and the declared minimum detectable effect from the spread of the per-cycle realised-variance term,
half the entry dollar gamma times the squared session-to-session forward moves summed to expiry, in bp
of the entry forward notional, each group's names equal-weighted within a cycle.

The building blocks are the study's own, declared once here:
- *Forward:* put-call parity at the strike where the call and put mids are closest, F = K + C - P,
  undiscounted. A mid needs an ask above zero and a bid at or under it.
- *Entry:* the first trading day on or after expiry minus the tenor; years to expiry are calendar days
  over 365.
- *Legs:* `straddle` at the strike nearest the forward with both sides bid; `put25`, `put10`, `call25`
  at the bid strike of that right whose Black delta (on the forward, zero rate, IV from the mid) is
  nearest the target. A mid at or under intrinsic has no IV and is never picked.
- A session with no forward is a gap: the next move spans it, and gaps are counted.

**The study step** sells each leg at entry and holds it to expiry, hedged at every session's close
to the leg's Black delta in that expiry's forward (IV from the session's mid, the last IV when the
contract has none), the hedge unwound at expiry. In dollars per contract:
- *received:* the mids x 100, less `core.fees.slippage_dollars` (0.125 of the spread) and
  `core.fees.ic_open_fee` per contract (SPXW at SPX's index fee);
- *paid:* intrinsic against the expiry session's forward x 100, plus settlement: an in-the-money
  SPXW leg `ic_expire_fee`, an ETF leg `assignment_round_trip_fee` on its 100 shares;
- *hedge:* the delta x 100 times each move to the next session with a forward, less
  `HEDGE_COST_BP` of the forward notional traded at every rebalance and at the unwind.
Net in bp of the entry forward notional (100 x F). Each cell's per-cycle value is its group's
equal-weight mean over the names that traded that cycle. The pass test is the declaration's:
a Newey-West t (lag = tenor / 30 rounded up), one-sided p through Holm at 0.025 across the 40
cells, both time-halves, 1.5x costs, and the breadth count. The neighbour legs (20/30 and 8/12 delta)
and the regime split (VIX over VIX3M, VIX's one-year rank tercile, Cboe's files in
`market-files/cboe`) are reported beside a pass, never judged. `max_size` uses `--account`.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sqlite3
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from cherrypick.core import bs, fees, impliedvar
from cherrypick.core import calendar as cal
from cherrypick.core import stats as core_stats

GROUPS = {
    "equity_index": ("SPXW", "SPY", "QQQ", "IWM"),
    "non_equity": ("GLD", "SLV", "TLT"),
}
NAMES = tuple(n for names in GROUPS.values() for n in names)
FIRST_EXPIRY = date(2016, 3, 18)
LAST_EXPIRY = date(2026, 10, 16)
PATH_DAYS = 66
TENORS = (60, 45, 30, 14, 7)
TIME_SPLIT = date(2021, 6, 1)
#: leg -> (right, |delta| target); the straddle is the call and put at the strike nearest the forward
LEGS = {"straddle": None, "put25": ("P", 0.25), "put10": ("P", 0.10), "call25": ("C", 0.25)}
#: the pass bar: one-sided 0.025, Holm across the 40 cells (the first step is 0.025 / 40)
ALPHA = 0.025
CELLS = len(GROUPS) * len(TENORS) * len(LEGS)
MIN_CYCLES = 80
#: the chain store's spelling of a right -> the one `core.bs` and the pure code here use
STORE_SIDES = {"CALL": "C", "PUT": "P"}
#: the neighbours reported beside a passing strike leg: |delta| -> leg
NEIGHBOURS = {
    "put25": {0.20: "put20", 0.25: "put25", 0.30: "put30"},
    "put10": {0.08: "put8", 0.10: "put10", 0.12: "put12"},
    "call25": {0.20: "call20", 0.25: "call25", 0.30: "call30"},
}
ALL_LEGS = {**LEGS, **{leg: (LEGS[base][0], d) for base, ns in NEIGHBOURS.items() for d, leg in ns.items()}}
#: names above zero a cell needs, per group
BREADTH = {"equity_index": 3, "non_equity": 2}
HEDGE_COST_BP = 0.5
PASS_STRESS = 1.5
CASH_SETTLED = {"SPXW"}
FEE_SYMBOL = {"SPXW": "SPX"}
ACCOUNT = 100_000.0
ACCOUNT_SHARE = 0.10
#: a strike this far on the wrong side of the forward is never a candidate (half the IV solves)
ITM_REACH = 0.05


# ------------------------------------------------------------------------------------------------
# Pure.


def monthly_expiry(year: int, month: int) -> date:
    """The standard monthly: the third Friday, or the trading day before it when that is a holiday."""
    d = cal.nth_weekday(year, month, 4, 3)
    return d if cal.is_trading_day(d) else cal.previous_trading_day(d)


def cycles(first: date = FIRST_EXPIRY, last: date = LAST_EXPIRY) -> list[date]:
    out, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        e = monthly_expiry(y, m)
        if first <= e <= last:
            out.append(e)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def path_request(symbol: str, expiry: date) -> dict:
    return {
        "symbol": symbol,
        "expiration": expiry.isoformat(),
        "start": (expiry - timedelta(days=PATH_DAYS)).isoformat(),
        "through": expiry.isoformat(),
    }


def build_requests(names=NAMES, expiries=None) -> list[dict]:
    """Every name's every cycle, names in group order: pure."""
    expiries = cycles() if expiries is None else expiries
    return [path_request(sym, e) for sym in names for e in expiries]


def held(requests: list[dict], complete: set[tuple[str, str, str, str]]) -> int:
    """How many requests the store already holds complete, under the fetch's exact key."""
    return sum((r["symbol"], r["expiration"], r["start"], r["through"]) in complete for r in requests)


def mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or ask <= 0 or bid < 0 or bid > ask:
        return None
    return (bid + ask) / 2.0


def forward(chain: list[tuple[float, str, float | None, float | None]]) -> float | None:
    """Parity at the strike where the call and put mids are closest. `chain` is (strike, right, bid, ask)."""
    mids: dict[float, dict[str, float]] = {}
    for k, right, b, a in chain:
        m = mid(b, a)
        if m is not None:
            mids.setdefault(k, {})[right] = m
    pairs = [(k, s["C"], s["P"]) for k, s in mids.items() if "C" in s and "P" in s]
    if not pairs:
        return None
    k, c, p = min(pairs, key=lambda kcp: (abs(kcp[1] - kcp[2]), kcp[0]))
    return k + c - p


def entry_session(expiry: date, tenor: int) -> date:
    d = expiry - timedelta(days=tenor)
    return d if cal.is_trading_day(d) else cal.next_trading_day(d)


def years_to(session: date, expiry: date) -> float:
    return (expiry - session).days / 365.0


def priced(chain, fwd: float, years: float) -> dict[tuple[float, str], dict]:
    """Every bid contract a leg could use, with its IV: puts up to `ITM_REACH` over the forward, calls
    down to it under."""
    bid = {}
    for k, right, b, a in chain:
        if (right == "P" and k > fwd * (1 + ITM_REACH)) or (right == "C" and k < fwd * (1 - ITM_REACH)):
            continue
        m = mid(b, a)
        if m is not None and b > 0:
            iv = bs.implied_vol(fwd, k, years, m, right, 0.0)
            if iv is not None and iv > 0.0001:
                bid[(k, right)] = {"strike": k, "right": right, "mid": m, "iv": iv}
    return bid


def pick_leg(chain, fwd: float, years: float, leg: str, bid: dict | None = None) -> list[dict] | None:
    """The leg's contracts at entry, each {strike, right, mid, iv}, or None when the chain has none.
    `bid` is `priced(chain, ...)`, passed when several legs read the same session."""
    bid = priced(chain, fwd, years) if bid is None else bid
    if ALL_LEGS[leg] is None:
        both = [k for (k, r) in bid if r == "C" and (k, "P") in bid]
        if not both:
            return None
        k = min(both, key=lambda s: (abs(s - fwd), s))
        return [bid[(k, "C")], bid[(k, "P")]]
    right, target = ALL_LEGS[leg]
    side = [c for (_, r), c in bid.items() if r == right]
    if not side:
        return None

    def gap(c):
        d = bs.delta(fwd, c["strike"], years, c["iv"], right, 0.0)
        return abs(abs(d) - target) if d is not None else 9.0

    return [min(side, key=lambda c: (gap(c), c["strike"]))]


def gamma(fwd: float, contracts: list[dict], years: float) -> float | None:
    """Black gamma per point of the forward, summed over the leg's contracts."""
    from cherrypick.backtest.greeks import bs_gamma

    gs = [bs_gamma(fwd, c["strike"], c["iv"], years) for c in contracts]
    return None if any(g is None for g in gs) else sum(gs)


def rv_term_bp(gam: float, forwards: list[float]) -> float | None:
    """Half the entry gamma times the squared forward moves to expiry, in bp of the entry forward
    notional (the contract multiplier cancels). `forwards` runs from entry to expiry, gaps removed."""
    if len(forwards) < 2 or forwards[0] <= 0:
        return None
    moves = sum((b - a) ** 2 for a, b in zip(forwards, forwards[1:], strict=False))
    return 0.5 * gam * moves / forwards[0] * 1e4


def group_cycle_means(per_name: dict[str, dict[str, float]], names) -> dict[str, float]:
    """{cycle: the equal-weight mean over the group's names with a value that cycle}."""
    by_cycle: dict[str, list[float]] = {}
    for n in names:
        for cyc, v in per_name.get(n, {}).items():
            by_cycle.setdefault(cyc, []).append(v)
    return {c: sum(vs) / len(vs) for c, vs in sorted(by_cycle.items())}


def cell_mde(values: list[float]) -> dict:
    """The declared MDE at both ends of the Holm ladder; a one-sample mean, so no unpaired factor."""
    from cherrypick.core.robustness import planned_mde

    n = len(values)
    out = {"cycles": n, "enough": n >= MIN_CYCLES}
    if n < 2:
        return out
    last = planned_mde(values, n, paired=True, alpha=ALPHA, sided=1)
    first = planned_mde(values, n, paired=True, alpha=ALPHA / CELLS, sided=1)
    out.update({"sd_bp": last["sd"], "mde_bp_holm_last": last["mde"], "mde_bp_holm_first": first["mde"]})
    return out


def trading_days(a: date, b: date) -> list[date]:
    out, d = [], a
    while d <= b:
        out.append(d)
        d = cal.next_trading_day(d)
    return out


def cycle_terms(sessions: dict[date, list], expiry: date) -> tuple[dict, dict]:
    """One (name, cycle): {tenor: {leg: rv term bp}} and {tenor: drop reason or gap count}. `sessions`
    maps each stored session of the path to its chain."""
    fwds = {d: forward(ch) for d, ch in sessions.items()}
    terms: dict[int, dict[str, float]] = {}
    notes: dict[int, dict] = {}
    for tenor in TENORS:
        s = entry_session(expiry, tenor)
        if s not in sessions:
            notes[tenor] = {"drop": "entry_missing"}
            continue
        if expiry not in sessions:
            notes[tenor] = {"drop": "expiry_missing"}
            continue
        if fwds.get(s) is None:
            notes[tenor] = {"drop": "no_forward"}
            continue
        span = trading_days(s, expiry)
        path = [fwds[d] for d in span if fwds.get(d) is not None]
        notes[tenor] = {"gaps": len(span) - len(path)}
        y = years_to(s, expiry)
        legs = {}
        bid = priced(sessions[s], fwds[s], y)
        for leg in LEGS:
            got = pick_leg(sessions[s], fwds[s], y, leg, bid)
            g = gamma(fwds[s], got, y) if got else None
            v = rv_term_bp(g, path) if g is not None else None
            if v is None:
                notes[tenor].setdefault("no_leg", []).append(leg)
            else:
                legs[leg] = v
        terms[tenor] = legs
    return terms, notes


# The study's own: the trade, the hedge, the statistics, the reports beside a pass.


def quotes_by_contract(chain) -> dict[tuple[float, str], tuple]:
    return {(k, r): (b, a) for k, r, b, a in chain}


def hedge(contracts: list[dict], sessions: dict, fwds: dict, entry: date, expiry: date) -> dict:
    """The short leg's daily forward hedge from entry to expiry, in dollars: at each session's close
    with a forward, the hedge is set to the leg's Black delta x 100 (a short option is hedged long its
    delta), IV from that session's mid or the last IV when the contract has none; it earns that times
    the move to the next session with a forward, and is unwound at the last one."""
    days = [d for d in sorted(sessions) if entry <= d <= expiry and fwds.get(d) is not None]
    ivs = {(c["strike"], c["right"]): c["iv"] for c in contracts}
    pnl = cost = held_units = 0.0
    for d, nxt in zip(days, days[1:], strict=False):
        f, y = fwds[d], years_to(d, expiry)
        q = quotes_by_contract(sessions[d])
        units = 0.0
        for (k, right), last in ivs.items():
            m = mid(*q.get((k, right), (None, None)))
            iv = bs.implied_vol(f, k, y, m, right, 0.0) if m is not None else None
            if iv is not None and iv > 0.0001:
                ivs[(k, right)] = last = iv
            units += 100.0 * bs.delta(f, k, y, last, right, 0.0)
        cost += abs(units - held_units) * f * HEDGE_COST_BP / 1e4
        held_units = units
        pnl += held_units * (fwds[nxt] - f)
    if days:
        cost += abs(held_units) * fwds[days[-1]] * HEDGE_COST_BP / 1e4
    return {"pnl": pnl, "cost": cost, "rebalances": max(len(days) - 1, 0)}


def settle_fee(symbol: str, contracts: list[dict], f_exp: float) -> float:
    itm = [c for c in contracts if bs.intrinsic(f_exp, c["strike"], c["right"]) > 0]
    if symbol in CASH_SETTLED:
        return fees.ic_expire_fee(len(itm))
    # a short put assigned delivers shares to sell; a short call, shares to buy back
    return sum(
        fees.assignment_round_trip_fee(
            100, c["strike"], f_exp, direction="long" if c["right"] == "P" else "short", ndigits=4
        )
        for c in itm
    )


def trade(symbol: str, sessions: dict, fwds: dict, expiry: date, tenor: int, leg: str, bid=None) -> dict:
    """One short leg, hedged and held to expiry: dollars, and bp of the entry forward notional; or
    {"drop": reason}. `bid` is the entry session's `priced`, shared across legs."""
    s = entry_session(expiry, tenor)
    if s not in sessions:
        return {"drop": "entry_missing"}
    if expiry not in sessions:
        return {"drop": "expiry_missing"}
    if fwds.get(expiry) is None:
        return {"drop": "no_expiry_forward"}
    if fwds.get(s) is None:
        return {"drop": "no_forward"}
    y = years_to(s, expiry)
    contracts = pick_leg(sessions[s], fwds[s], y, leg, bid)
    if not contracts:
        return {"drop": "no_leg"}
    q = quotes_by_contract(sessions[s])
    leg_quotes = [dict(zip(("bid", "ask"), q[(c["strike"], c["right"])], strict=True)) for c in contracts]
    n = len(contracts)
    premium = 100.0 * sum(c["mid"] for c in contracts)
    slip = fees.slippage_dollars(leg_quotes, 1, {})
    fee = fees.ic_open_fee(FEE_SYMBOL.get(symbol, symbol), 1, legs=n, sell_legs=n, ndigits=4)
    f_exp = fwds[expiry]
    payoff = 100.0 * sum(bs.intrinsic(f_exp, c["strike"], c["right"]) for c in contracts)
    settle = settle_fee(symbol, contracts, f_exp)
    hg = hedge(contracts, sessions, fwds, s, expiry)
    costs = slip + fee + settle + hg["cost"]
    net = premium - payoff + hg["pnl"] - costs
    notional = 100.0 * fwds[s]
    return {
        "entry": s.isoformat(),
        "strikes": [[c["strike"], c["right"]] for c in contracts],
        "premium": round(premium, 2),
        "payoff": round(payoff, 2),
        "hedge_pnl": round(hg["pnl"], 2),
        "costs": round(costs, 4),
        "net": round(net, 4),
        "net_bp": net / notional * 1e4,
        "cost_bp": costs / notional * 1e4,
    }


def nw_lag(tenor: int) -> int:
    return math.ceil(tenor / 30)


def newey_west_t(xs: list[float], lag: int) -> float | None:
    """The mean over its Newey-West standard error (Bartlett weights), or None when undefined."""
    n = len(xs)
    if n < 3:
        return None
    m = statistics.fmean(xs)
    e = [x - m for x in xs]
    s = sum(v * v for v in e) / n
    for j in range(1, min(lag, n - 1) + 1):
        s += 2 * (1 - j / (lag + 1)) * sum(e[i] * e[i - j] for i in range(j, n)) / n
    return m / math.sqrt(s / n) if s > 0 else None


def p_one(t: float | None, n: int) -> float:
    return 1.0 if t is None or n < 2 else 1.0 - core_stats.t_cdf(t, n - 1)


def _mean(xs) -> float | None:
    xs = list(xs)
    return statistics.fmean(xs) if xs else None


def cell_stats(per_name: dict[str, dict[str, dict]], members, tenor: int) -> dict:
    """One cell from its names' trades ({name: {cycle: trade}}): cycles are the unit, each the
    equal-weight mean of the names that traded it."""
    nets = group_cycle_means(
        {n: {c: t["net_bp"] for c, t in per_name.get(n, {}).items()} for n in members}, members
    )
    costs = group_cycle_means(
        {n: {c: t["cost_bp"] for c, t in per_name.get(n, {}).items()} for n in members}, members
    )
    cyc = sorted(nets)
    xs = [nets[c] for c in cyc]
    t = newey_west_t(xs, nw_lag(tenor))
    split = TIME_SPLIT.isoformat()
    by_name = {n: _mean(t_["net_bp"] for t_ in per_name[n].values()) for n in members if per_name.get(n)}
    return {
        "cycles": len(xs),
        "enough": len(xs) >= MIN_CYCLES,
        "mean_bp": _mean(xs),
        "t": t,
        "p": p_one(t, len(xs)),
        "halves": {
            "before": _mean(nets[c] for c in cyc if c < split),
            "after": _mean(nets[c] for c in cyc if c >= split),
        },
        "stressed_mean_bp": _mean(nets[c] - (PASS_STRESS - 1) * costs[c] for c in cyc),
        "cost_bp": _mean(costs[c] for c in cyc),
        "by_name": by_name,
        "names_positive": sum(v is not None and v > 0 for v in by_name.values()),
        "worst_cycle": min(((nets[c], c) for c in cyc), default=(None, None))[::-1],
        "series": {"nets": xs, "costs": [costs[c] for c in cyc], "cycles": cyc},
    }


def judge(cells: dict[str, dict]) -> str:
    """Each declared cell's verdict, in place, and the study's: Holm across every cell (one with too
    few cycles enters at p = 1, so the family stays 40)."""
    ok = core_stats.holm({k: (c["p"] if c["enough"] else 1.0) for k, c in cells.items()}, alpha=ALPHA)
    for k, c in cells.items():
        c["holm"] = ok[k]
        if not c["enough"]:
            c["verdict"] = "no verdict"
            continue
        halves = c["halves"].values()
        passed = (
            ok[k]
            and c["mean_bp"] > 0
            and all(h is not None and h > 0 for h in halves)
            and c["stressed_mean_bp"] > 0
            and c["names_positive"] >= BREADTH[k.split("/")[0]]
        )
        c["verdict"] = "pass" if passed else "fail"
    words = [c["verdict"] for c in cells.values()]
    return "pass" if "pass" in words else "fail" if "fail" in words else "no verdict"


def iv_rv(chain, path: list[float], years: float) -> dict | None:
    """Model-free implied variance at entry against the realised variance of the daily forward log
    moves to expiry, both over the trade's span; None when either cannot be read."""
    rows = [
        {"strike": k, "option_type": "call" if r == "C" else "put", "bid": b or 0.0, "ask": a}
        for k, r, b, a in chain
        if a is not None and a > 0
    ]
    iv = impliedvar.single_term(rows, years=years, rate=0.0)
    if not iv["ok"] or len(path) < 2 or years <= 0:
        return None
    realised = sum(math.log(b / a) ** 2 for a, b in zip(path, path[1:], strict=False))
    return {
        "implied_vol": 100.0 * math.sqrt(iv["variance"]),
        "realised_vol": 100.0 * math.sqrt(realised / years),
        "above": iv["variance"] * years > realised,
    }


def iv_rv_summary(rows: list[dict]) -> dict:
    return {
        "cycles": len(rows),
        "share_above": _mean(r["above"] for r in rows),
        "mean_gap_vol_pts": _mean(r["implied_vol"] - r["realised_vol"] for r in rows),
    }


def regime_at(d: date, vix: dict[date, float], vix3m: dict[date, float]) -> dict:
    """VIX over VIX3M at `d`'s close (below 1 is contango) and VIX's tercile (0 low, 2 high) among the
    252 closes to `d`; None where a file does not reach."""
    out = {"contango": None, "tercile": None}
    if d in vix and d in vix3m and vix3m[d] > 0:
        out["contango"] = vix[d] / vix3m[d] < 1.0
    days = sorted(vix)
    i = bisect.bisect_right(days, d)
    if i and days[i - 1] == d and i >= 252:
        window = [vix[x] for x in days[i - 252 : i]]
        rank = sum(v < vix[d] for v in window) / len(window)
        out["tercile"] = min(int(rank * 3), 2)
    return out


def regime_split(cell: dict, tenor: int, vix, vix3m) -> dict:
    """A passing cell's per-cycle nets by the entry's term structure and VIX tercile: reported only."""
    groups: dict[str, list[float]] = {}
    for c, x in zip(cell["series"]["cycles"], cell["series"]["nets"], strict=True):
        r = regime_at(entry_session(date.fromisoformat(c), tenor), vix, vix3m)
        if r["contango"] is not None:
            groups.setdefault("contango" if r["contango"] else "backwardation", []).append(x)
        if r["tercile"] is not None:
            groups.setdefault(f"vix_tercile_{r['tercile']}", []).append(x)
    return {k: {"cycles": len(v), "mean_bp": _mean(v)} for k, v in sorted(groups.items())}


# ------------------------------------------------------------------------------------------------
# The stores and the steps.


def _out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item46"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _chain_store_path() -> Path:
    from cherrypick.backtest import chainfetch, settings

    return chainfetch.db_path(settings.load(None))


def _chain_store() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{_chain_store_path().as_posix()}?mode=ro", uri=True)


def _complete_paths() -> set[tuple[str, str, str, str]]:
    if not _chain_store_path().exists():
        return set()
    conn = _chain_store()
    try:
        return {
            tuple(r)
            for r in conn.execute(
                "SELECT symbol, expiration, start, through FROM eod_chain_paths WHERE status = 'complete'"
            )
        }
    finally:
        conn.close()


def _path_status(conn, req: dict) -> str:
    row = conn.execute(
        "SELECT status FROM eod_chain_paths"
        " WHERE symbol = ? AND expiration = ? AND start = ? AND through = ?",
        (req["symbol"], req["expiration"], req["start"], req["through"]),
    ).fetchone()
    return row[0] if row else "missing"


def _path_sessions(conn, req: dict) -> dict[date, list]:
    days = [
        r[0]
        for r in conn.execute(
            "SELECT session FROM eod_chain_requests"
            " WHERE symbol = ? AND expiration = ? AND status = 'complete' AND session BETWEEN ? AND ?",
            (req["symbol"], req["expiration"], req["start"], req["through"]),
        )
    ]
    return {
        date.fromisoformat(d): [
            (k, STORE_SIDES.get(r, r), b, a)
            for k, r, b, a in conn.execute(
                "SELECT strike, right, bid, ask FROM eod_chain_quotes WHERE symbol = ? AND session = ?"
                " AND expiration = ?",
                (req["symbol"], d, req["expiration"]),
            )
        ]
        for d in sorted(days)
    }


def coverage() -> dict:
    conn = _chain_store()
    names: dict[str, dict] = {}
    # values[(name, tenor, leg)] = {cycle: rv term bp}
    values: dict[tuple[str, int, str], dict[str, float]] = {}
    try:
        for sym in NAMES:
            status_n: dict[str, int] = {}
            drops = {t: {} for t in TENORS}
            gaps = {t: 0 for t in TENORS}
            first = None
            for e in cycles():
                req = path_request(sym, e)
                st = _path_status(conn, req)
                status_n[st] = status_n.get(st, 0) + 1
                if st != "complete":
                    continue
                first = first or req["expiration"]
                terms, notes = cycle_terms(_path_sessions(conn, req), e)
                for t, note in notes.items():
                    if "drop" in note:
                        drops[t][note["drop"]] = drops[t].get(note["drop"], 0) + 1
                        continue
                    gaps[t] += note["gaps"]
                    for leg in note.get("no_leg", []):
                        drops[t][f"no_{leg}"] = drops[t].get(f"no_{leg}", 0) + 1
                for t, legs in terms.items():
                    for leg, v in legs.items():
                        values.setdefault((sym, t, leg), {})[req["expiration"]] = v
            names[sym] = {
                "paths": status_n,
                "first_complete_cycle": first,
                "drops": {str(t): d for t, d in drops.items()},
                "gap_sessions": {str(t): g for t, g in gaps.items()},
            }
    finally:
        conn.close()
    cells = {}
    for group, members in GROUPS.items():
        for t in TENORS:
            for leg in LEGS:
                per_name = {n: values.get((n, t, leg), {}) for n in members}
                means = group_cycle_means(per_name, members)
                cells[f"{group}/{t}/{leg}"] = cell_mde(list(means.values()))
    return {
        "item": "item46",
        "step": "coverage",
        "store": str(_chain_store_path()),
        "window": [FIRST_EXPIRY.isoformat(), LAST_EXPIRY.isoformat()],
        "names": names,
        "cells": cells,
        "note": (
            "entry quotes and forwards only, no option exit read; MDE in bp of entry forward notional "
            f"from the per-cycle realised-variance term, one-sided at {ALPHA} (Holm's last step) "
            f"and {ALPHA}/{CELLS} (its first); a cell with fewer than {MIN_CYCLES} cycles is no verdict"
        ),
    }


def cmd_plan(args) -> int:
    reqs = build_requests()
    path = Path(args.out) if args.out else _out_dir() / "path_requests.json"
    path.write_text(json.dumps({"requests": reqs}, indent=0), encoding="utf-8")
    print(
        json.dumps(
            {
                "names": len(NAMES),
                "cycles": len(cycles()),
                "requests": len(reqs),
                "held_complete": held(reqs, _complete_paths()),
                "file": str(path),
            }
        )
    )
    return 0


def cmd_coverage(args) -> int:
    if not _chain_store_path().exists():
        print("no chain store: run the item46/fetch-chain-paths row first", file=sys.stderr)
        return 2
    doc = coverage()
    path = Path(args.out) if args.out else _out_dir() / "coverage.json"
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    short = sum(not c["enough"] for c in doc["cells"].values())
    print(json.dumps({"cells": len(doc["cells"]), "cells_under_min_cycles": short, "file": str(path)}))
    return 0


def read_cboe(name: str) -> dict[date, float]:
    from cherrypick.core import home

    path = home.data_dir("market-files") / "cboe" / f"{name}.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return {
            datetime.strptime(r["DATE"], "%m/%d/%Y").date(): float(r["CLOSE"])
            for r in csv.DictReader(fh)
            if r.get("CLOSE")
        }


def collect(log=None) -> tuple[dict, dict, dict]:
    """Every trade from the store: {(name, tenor, leg): {cycle: trade}}, drop counts per name and
    tenor, and the IV-against-realised rows per (name, tenor)."""
    conn = _chain_store()
    trades: dict[tuple[str, int, str], dict[str, dict]] = {}
    drops: dict[str, dict[str, dict[str, int]]] = {}
    ivrows: dict[tuple[str, int], list[dict]] = {}
    try:
        for sym in NAMES:
            drops[sym] = {str(t): {} for t in TENORS}
            for e in cycles():
                req = path_request(sym, e)
                if _path_status(conn, req) != "complete":
                    continue
                sessions = _path_sessions(conn, req)
                fwds = {d: forward(ch) for d, ch in sessions.items()}
                for tenor in TENORS:
                    s = entry_session(e, tenor)
                    bid = priced(sessions[s], fwds[s], years_to(s, e)) if fwds.get(s) is not None else None
                    for leg in ALL_LEGS:
                        tr = trade(sym, sessions, fwds, e, tenor, leg, bid)
                        if "drop" in tr:
                            # a cycle's drop counts once; a missing leg under its own name, as coverage does
                            why = f"no_{leg}" if tr["drop"] == "no_leg" else tr["drop"]
                            if leg in LEGS and (why != tr["drop"] or leg == next(iter(LEGS))):
                                d = drops[sym][str(tenor)]
                                d[why] = d.get(why, 0) + 1
                            continue
                        trades.setdefault((sym, tenor, leg), {})[req["expiration"]] = tr
                    if bid is not None and fwds.get(e) is not None:
                        path = [fwds[d] for d in trading_days(s, e) if fwds.get(d) is not None]
                        row = iv_rv(sessions[s], path, years_to(s, e))
                        if row:
                            ivrows.setdefault((sym, tenor), []).append(row)
            if log:
                log(f"{sym}: done")
    finally:
        conn.close()
    return trades, drops, ivrows


def cells_for(trades: dict, legs) -> dict[str, dict]:
    return {
        f"{group}/{t}/{leg}": cell_stats({n: trades.get((n, t, leg), {}) for n in members}, members, t)
        for group, members in GROUPS.items()
        for t in TENORS
        for leg in legs
    }


def beside_a_pass(key: str, cell: dict, cells: dict, neighbours: dict, trades: dict, account: float) -> dict:
    """The B13 report and the regime split for one passing cell: reported, never judged."""
    from cherrypick.core import robustness

    group, tenor, leg = key.split("/")
    t = int(tenor)
    by_tenor = {float(x): cells[f"{group}/{x}/{leg}"]["mean_bp"] for x in TENORS}
    out = {
        "tenor_neighbourhood": robustness.neighbourhood(by_tenor, float(t)),
        **robustness.report(cell["series"]["nets"], cell["series"]["costs"], pass_stress=PASS_STRESS),
    }
    if leg in NEIGHBOURS:
        means = {
            d: (cell if n == leg else neighbours[f"{group}/{tenor}/{n}"])["mean_bp"]
            for d, n in NEIGHBOURS[leg].items()
        }
        out["strike_neighbourhood"] = robustness.neighbourhood(means, ALL_LEGS[leg][1])
    sizes = {}
    for n in GROUPS[group]:
        ts = list(trades.get((n, t, leg), {}).values())
        if ts:
            r = robustness.report(
                [x["net"] for x in ts], [x["costs"] for x in ts], account=account, account_share=ACCOUNT_SHARE
            )
            sizes[n] = r.get("max_size")
    out["max_size_by_name"] = sizes
    out["regime"] = regime_split(cell, t, read_cboe("VIX"), read_cboe("VIX3M"))
    return out


def study(account: float = ACCOUNT, log=None) -> tuple[dict, dict]:
    trades, drops, ivrows = collect(log)
    cells = cells_for(trades, LEGS)
    verdict = judge(cells)
    extra = [leg for leg in ALL_LEGS if leg not in LEGS]
    neighbours = cells_for(trades, extra)
    beside = {
        k: beside_a_pass(k, c, cells, neighbours, trades, account)
        for k, c in cells.items()
        if c["verdict"] == "pass"
    }
    result = {
        "item": "item46",
        "step": "study",
        "verdict": verdict,
        "window": [FIRST_EXPIRY.isoformat(), LAST_EXPIRY.isoformat()],
        "time_split": TIME_SPLIT.isoformat(),
        "cells": {k: {x: v for x, v in c.items() if x != "series"} for k, c in cells.items()},
        "neighbour_legs": {k: {x: v for x, v in c.items() if x != "series"} for k, c in neighbours.items()},
        "beside_a_pass": beside,
        "iv_vs_rv": {n: {str(t): iv_rv_summary(ivrows.get((n, t), [])) for t in TENORS} for n in NAMES},
        "drops": drops,
        "costs": {
            "option": "core.fees: slippage 0.125 of spread (capped at 0.15 of mid), ic_open_fee per contract",
            "hedge_bp_per_rebalance": HEDGE_COST_BP,
            "settlement": "SPXW ic_expire_fee per ITM leg; ETFs assignment_round_trip_fee on 100 shares",
            "pass_stress": PASS_STRESS,
        },
        "account": account,
        "limits": [
            "positions are held to expiry, so none is open at the window's end",
            "early assignment of ETF options is not modelled: ETF put cells are an upper bound",
            "a crash the sample does not hold is not priced (the peso case)",
            "the seven names are today's choice",
        ],
    }
    trade_log = {f"{n}/{t}/{leg}": v for (n, t, leg), v in trades.items()}
    return result, trade_log


# ------------------------------------------------------------------------------------------------
# results.md: the reading in words, for the studies tab. Licensed-derived, so it stays beside the JSON.


def _bp(x: float | None) -> str:
    return "" if x is None else f"{x:+.1f}"


GROUP_TITLES = {
    "equity_index": "Equity index (SPX, SPY, QQQ, IWM)",
    "non_equity": "Non-equity (GLD, SLV, TLT)",
}
LEG_TITLES = {"straddle": "ATM straddle", "put25": "25Δ put", "put10": "10Δ put", "call25": "25Δ call"}


def findings(result: dict) -> list[str]:
    cells = result["cells"]
    out = []
    passing = [k for k, c in cells.items() if c["verdict"] == "pass"]
    if passing:
        out.append("Cells that pass: " + ", ".join(_cell_name(k) for k in passing) + ".")
    else:
        out.append("No cell passes the declared test.")
    for group in GROUPS:
        mine = {k: c for k, c in cells.items() if k.startswith(group) and c["mean_bp"] is not None}
        if not mine:
            continue
        best = max(mine, key=lambda k: mine[k]["mean_bp"])
        positive = sum(c["mean_bp"] > 0 for c in mine.values())
        out.append(
            f"{GROUP_TITLES[group]}: {positive} of {len(mine)} cells are above zero after costs on average;"
            f" the best is {_cell_name(best)} at {_bp(mine[best]['mean_bp'])} bp a trade"
            f" ({mine[best]['verdict']})."
        )
    for n in ("SPXW", "SPY", "GLD"):
        row = result["iv_vs_rv"].get(n, {}).get("30", {})
        if row.get("cycles"):
            out.append(
                f"{n} at 30 days: implied ran above realised in {row['share_above']:.0%} of {row['cycles']}"
                f" cycles, by {row['mean_gap_vol_pts']:+.1f} vol points on average."
            )
    return out


def _cell_name(key: str) -> str:
    group, tenor, leg = key.split("/")
    return f"{'index' if group == 'equity_index' else 'funds'} {LEG_TITLES[leg]} at {tenor} days"


def report(result: dict) -> str:
    cells = result["cells"]
    counts = {w: sum(c["verdict"] == w for c in cells.values()) for w in ("pass", "fail", "no verdict")}
    lines = [
        "# Item 46: where implied runs above realised",
        "",
        f"**Verdict: {result['verdict']}.** Of the {len(cells)} cells, {counts['pass']} pass,"
        f" {counts['fail']} fail and {counts['no verdict']} have no verdict.",
        "",
        "Each trade sells one option (or one straddle) on a monthly expiry at a fixed number of days out,"
        " hedges its delta at every close in the expiry's forward, and holds it to expiry. That measures"
        " the variance premium by itself: what the seller keeps when implied volatility runs above what"
        " follows. Results are net of option fees and slippage, hedge costs and settlement, in basis"
        " points of the underlying's notional at entry. Each monthly cycle counts once, its names averaged.",
        "",
        "## What it says",
        "",
        *[f"- {x}" for x in findings(result)],
        "",
        "## How to read the tables",
        "",
        "- **mean bp**: the average net per trade, in hundredths of a percent of the notional.",
        "- **t**: the mean over its Newey-West error; **Holm** says whether it clears the bar after the"
        " correction for 40 cells.",
        f"- **before / after**: the mean in cycles expiring before {result['time_split']}, and from it.",
        "- **1.5x costs**: the mean with every cost raised by half. A pass needs all of these above zero.",
        "- **names +**: how many of the group's names are above zero on their own.",
    ]
    for group in GROUPS:
        lines += [
            "",
            f"## {GROUP_TITLES[group]}",
            "",
            "| tenor | leg | verdict | cycles | mean bp | t | Holm | before | after | 1.5x costs | names + |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for t in TENORS:
            for leg in LEGS:
                c = cells[f"{group}/{t}/{leg}"]
                tt = "" if c["t"] is None else f"{c['t']:.2f}"
                holm = "yes" if c["holm"] else "no"
                before, after = _bp(c["halves"]["before"]), _bp(c["halves"]["after"])
                lines.append(
                    f"| {t}d | {LEG_TITLES[leg]} | {c['verdict']} | {c['cycles']} | {_bp(c['mean_bp'])}"
                    f" | {tt} | {holm} | {before} | {after}"
                    f" | {_bp(c['stressed_mean_bp'])} | {c['names_positive']}/{len(GROUPS[group])} |"
                )
    lines += [
        "",
        "## Implied against realised (reported, not judged)",
        "",
        "Model-free implied vol at entry against the realised vol of the daily forward moves to expiry.",
        "",
        "| name | "
        + " | ".join(f"{t}d above" for t in TENORS)
        + " | "
        + " | ".join(f"{t}d gap" for t in TENORS)
        + " |",
        "|---|" + "---|" * (2 * len(TENORS)),
    ]
    for n in NAMES:
        rows = result["iv_vs_rv"][n]
        above = [f"{rows[str(t)]['share_above']:.0%}" if rows[str(t)]["cycles"] else "" for t in TENORS]
        gaps = [f"{rows[str(t)]['mean_gap_vol_pts']:+.1f}" if rows[str(t)]["cycles"] else "" for t in TENORS]
        lines.append(f"| {n} | " + " | ".join(above + gaps) + " |")
    for key, b in result.get("beside_a_pass", {}).items():
        lines += ["", f"## Beside the pass: {_cell_name(key)}", ""]
        cs = b["cost_stress"]["mean_by_multiplier"]
        lines.append("- Costs: " + ", ".join(f"{m}x {_bp(v)} bp" for m, v in cs.items()) + ".")
        lines.append(f"- Tenor neighbours: {b['tenor_neighbourhood']['verdict'].replace('_', ' ')}.")
        if "strike_neighbourhood" in b:
            lines.append(f"- Strike neighbours: {b['strike_neighbourhood']['verdict'].replace('_', ' ')}.")
        lines.append(
            f"- Largest size inside {ACCOUNT_SHARE:.0%} of a ${result['account']:,.0f} account: "
            + ", ".join(f"{n} {s}" for n, s in b["max_size_by_name"].items())
            + "."
        )
        if b["regime"]:
            lines.append(
                "- By regime: "
                + ", ".join(
                    f"{k.replace('_', ' ')} {_bp(v['mean_bp'])} bp ({v['cycles']})"
                    for k, v in b["regime"].items()
                )
                + "."
            )
    return "\n".join(lines) + "\n"


def cmd_study(args) -> int:
    if not _chain_store_path().exists():
        print("no chain store: run the item46/fetch-chain-paths row first", file=sys.stderr)
        return 2
    result, trade_log = study(args.account, log=lambda m: print(m, file=sys.stderr))
    out = _out_dir()
    path = Path(args.json) if args.json else out / "results.json"
    path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    (path.parent / "trades.json").write_text(json.dumps(trade_log), encoding="utf-8")
    path.with_suffix(".md").write_text(report(result), encoding="utf-8")
    counts = {
        w: sum(c["verdict"] == w for c in result["cells"].values()) for w in ("pass", "fail", "no verdict")
    }
    print(json.dumps({"verdict": result["verdict"], **counts, "file": str(path)}))
    return 0


def cmd_report(args) -> int:
    src = Path(args.json) if args.json else _out_dir() / "results.json"
    src.with_suffix(".md").write_text(report(json.loads(src.read_text(encoding="utf-8"))), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("plan", cmd_plan), ("coverage", cmd_coverage)):
        sp = sub.add_parser(name)
        sp.add_argument("--out")
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("study")
    sp.add_argument("--account", type=float, default=ACCOUNT)
    sp.add_argument("--json")
    sp.set_defaults(fn=cmd_study)
    sp = sub.add_parser("report")
    sp.add_argument("--json")
    sp.set_defaults(fn=cmd_report)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
