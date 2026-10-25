"""Hypothesis 17's declared test: our own credit-spread list, by setup, side, short and width.

    python scripts/credit_spread_item17.py [--start 2020-01-22] [--end 2025-06-30] [--json OUT]
        [--plan TRADES_JSON] [--chains-db PATH] [--history-db PATH] [--workers N] [--limit-names N]

The spec is docs/edge-hypotheses-plan.md, "### 17. Our own credit-spread list". This prices the
trades `scripts/credit_spread_plan.py --item 17` decided (every signal trade of the eight setup-sides
and its five matched baselines, exclusions applied there) on the end-of-day chains in the backtest
store, and runs the declared pass test once. No AI, no network, no broker: a pure function of
`trades.json`, `eod_chains.db` and `history.db`. The output derives from licensed vendor data, so a
`--json` path outside the backtest data dir is refused while the chain store is the backtest one.

**The structure, as declared.**
- Expiry: the plan's, the latest standard monthly 30-45 days after entry (re-checked here). 45-60
  days is reported only, priced where the store already holds that chain.
- Spot is the raw close on the entry session. The forward comes from put-call parity at r = 0 (the
  median of K + C - P over the three strikes nearest the close with both sides two-sided); no
  forward, or one more than 5% from the close, drops the chain (item 14's rule).
- Delta: Black-76 on that forward, r = 0, T = calendar days / 365, the vol solved from each strike's
  own mid. At r = 0 Black-76 is Black-Scholes with the forward as spot and no carry, so
  `core.bs` computes it.
- Short: the two-sided out-of-the-money strike (put below the close, call above) whose |delta| is
  nearest 0.20, 0.30 or 0.40.
- Width, two families: `steps` puts the long 1, 2, 3 or 5 listed strikes beyond the short; `em` puts
  it at the listed strike nearest short -/+ 0.25, 0.5 or 0.75 x the 1-SD move (ATM IV x
  sqrt(days/365) x close). No trade when the long is the short's own strike.
- Fill: `legbars.structure_prices` working credit; a non-positive working credit is no trade. Fee:
  `core.fees.ic_open_fee(symbol, 1, legs=2, sell_legs=1)`; held to expiry, so no close fee, and $5
  for each leg that finishes in the money.
- Settlement at intrinsic from the expiry session's raw close, capped at the width. A name with no bar
  within four days of expiry (its data ended) settles at its last close before expiry, flagged and
  counted (the template's declared limit).
- Net = (credit - value) x 100 - fees - settlement. Max loss = (width - credit) x 100 + entry fee.
  ret = net / max loss.

**The test** (per setup-side x width family x rule, 32 in all): a rule picks a cell (short, width) in
one half and is scored in the other, both ways. diff = the trade's ret - the mean ret of its baselines
priced in the same cell; passes when, in both directions, the one-sided t of mean diff (standard
errors clustered by expiry date) is above 2 and the rule's own mean net in dollars is positive, and
it survives Holm across the 32 (each test's p is its larger direction's, a no-verdict test p = 1).
Fewer than 100 scored trades in a direction is no verdict. The per-symbol pick earns its place only
if it passes and also beats the pooled pick on the same trades, paired t above 2 in both directions.

Descriptive and never part of the verdict: the planned MDE (B12), the verdict re-read on the two
name halves (B9), and for a passing test the B13 report (cost stress, neighbourhood, drawdown range).
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
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

from cherrypick.core import bs, fees, home, legbars, robustness
from cherrypick.core import stats as core_stats

# ------------------------------------------------------------------------------------ the declaration
DECLARED_START = "2020-01-22"
DECLARED_END = "2025-06-30"
SEED = 20261024
SHORTS = (0.20, 0.30, 0.40)
STEPS = (1, 2, 3, 5)
EM_FRACTIONS = (0.25, 0.5, 0.75)
SETUPS = ("trend", "pullback", "reversion", "breakout")
SETUP_SIDES = tuple((s, "put") for s in SETUPS) + tuple((f"{s}-short", "call") for s in SETUPS)
FAMILIES = ("steps", "em")
RULES = ("pooled", "per_symbol")
MIN_SCORED = 100
MIN_SYMBOL_TRADES = 8
T_BAR = 2.0
ALPHA = 0.05
FWD_TOL = 0.05  # item 14's forward-vs-close bar
SETTLE_LOOKBACK_DAYS = 4  # item 14's: the nearest earlier bar within four days of expiry
REPORTED_DTE = (45, 60)
SETTLE_FEE = fees.ASSIGNMENT_FEE_PER_SETTLEMENT
EPS = 1e-9

PLAN_SCRIPT = Path(__file__).resolve().parent / "credit_spread_plan.py"


def _plan_module():
    spec = importlib.util.spec_from_file_location("credit_spread_plan", PLAN_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plan = _plan_module()


def cell_id(target: float, family: str, width) -> str:
    return (
        f"d{round(target * 100):02d}/s{width}"
        if family == "steps"
        else f"d{round(target * 100):02d}/em{width}"
    )


CELLS: dict[str, list[tuple[str, float, object]]] = {
    "steps": [(cell_id(d, "steps", n), d, n) for d in SHORTS for n in STEPS],
    "em": [(cell_id(d, "em", f), d, f) for d in SHORTS for f in EM_FRACTIONS],
}
ALL_CELLS = [c for fam in FAMILIES for c, _, _ in CELLS[fam]]
CELL_INFO = {c: (fam, d, w) for fam in FAMILIES for c, d, w in CELLS[fam]}


def midpoint(start: str, end: str) -> str:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return (a + (b - a) / 2).isoformat()


def period(entry: str, start: str, mid: str) -> str:
    if entry < start:
        return "reported"
    return "H1" if entry < mid else "H2"


def expiry_in(entry: str, lo: int, hi: int) -> str | None:
    """The latest standard monthly `lo`-`hi` calendar days after `entry` (the plan's monthlies)."""
    d = date.fromisoformat(entry)
    inside = [e for e in plan.MONTHLIES if lo <= (e - d).days <= hi]
    return inside[-1].isoformat() if inside else None


# ------------------------------------------------------------------------------------------ chain
def parity_forward(calls: dict, puts: dict, spot: float) -> float | None:
    """Median of K + C_mid - P_mid over the three strikes nearest `spot` quoted two-sided on both
    sides (r = 0). None with no such strike."""
    both = [k for k in calls if k in puts and legbars.two_sided(calls[k]) and legbars.two_sided(puts[k])]
    both.sort(key=lambda k: (abs(k - spot), k))
    fs = [
        k + (calls[k]["bid"] + calls[k]["ask"]) / 2 - (puts[k]["bid"] + puts[k]["ask"]) / 2 for k in both[:3]
    ]
    return statistics.median(fs) if fs else None


def black76_iv(forward: float, strike: float, years: float, mid: float, right: str) -> float | None:
    """Black-76 vol at r = 0: Black-Scholes with the forward as spot, no rate, no carry."""
    iv = bs.implied_vol(forward, strike, years, mid, right, 0.0)
    return iv if iv and iv > 0.0002 else None


def black76_delta(forward: float, strike: float, years: float, vol: float, right: str) -> float | None:
    return bs.delta(forward, strike, years, vol, right, 0.0)


class Chain:
    """One (symbol, session, expiry) end-of-day chain with its forward, deltas and ATM IV."""

    def __init__(self, rows, spot: float, session: str, expiry: str):
        self.spot = spot
        self.days = max((date.fromisoformat(expiry) - date.fromisoformat(session)).days, 1)
        self.years = self.days / 365.0
        self.by: dict[str, dict[float, dict]] = {"C": {}, "P": {}}
        for k, r, b, a in rows:
            r = "C" if str(r).upper() in ("C", "CALL") else "P"
            self.by[r][float(k)] = {"strike": float(k), "right": r, "bid": b, "ask": a}
        self.forward = parity_forward(self.by["C"], self.by["P"], spot)
        self._iv: dict = {}

    def iv(self, q: dict) -> float | None:
        key = (q["right"], q["strike"])
        if key not in self._iv:
            v = None
            if self.forward and legbars.two_sided(q):
                v = black76_iv(self.forward, q["strike"], self.years, (q["bid"] + q["ask"]) / 2, q["right"])
            self._iv[key] = v
        return self._iv[key]

    def delta(self, q: dict) -> float | None:
        v = self.iv(q)
        return None if v is None else black76_delta(self.forward, q["strike"], self.years, v, q["right"])

    def otm(self, right: str) -> list[dict]:
        """Every listed OTM strike on `right`, from the money outward."""
        if right == "P":
            ks = sorted((k for k in self.by["P"] if k < self.spot), reverse=True)
        else:
            ks = sorted(k for k in self.by["C"] if k > self.spot)
        return [self.by[right][k] for k in ks]

    def atm_iv(self) -> float | None:
        """The vol at the listed strike nearest the forward: the mean of the call's and put's where both
        solve, else the one that does; the next-nearest strike (up to three) when neither does."""
        if not self.forward:
            return None
        ks = sorted(set(self.by["C"]) | set(self.by["P"]), key=lambda k: (abs(k - self.forward), k))
        for k in ks[:3]:
            vs = [self.iv(self.by[r][k]) for r in ("C", "P") if k in self.by[r]]
            vs = [v for v in vs if v]
            if vs:
                return sum(vs) / len(vs)
        return None


def pick_short(chain: Chain, right: str, target: float) -> dict | None:
    """The two-sided OTM strike whose |delta| is nearest `target` (ties: nearer the money). Deltas
    fall monotonically outward, so the walk stops once |delta| is below the target and moving away."""
    best, gap = None, None
    for q in chain.otm(right):
        d = chain.delta(q)
        if d is None:
            continue
        g = abs(abs(d) - target)
        if gap is None or g < gap - EPS:
            best, gap = q, g
        elif abs(d) < target:
            break
    return best


def long_by_steps(chain: Chain, right: str, short: dict, n: int) -> dict | None:
    otm = chain.otm(right)
    i = next(k for k, q in enumerate(otm) if q["strike"] == short["strike"])
    return otm[i + n] if i + n < len(otm) else None


def long_by_em(chain: Chain, right: str, short: dict, frac: float, atm_iv: float) -> dict | None:
    """The listed strike nearest short -/+ frac x the 1-SD move (ties: the nearer the short)."""
    move = atm_iv * math.sqrt(chain.days / 365.0) * chain.spot
    target = short["strike"] - frac * move if right == "P" else short["strike"] + frac * move
    ks = sorted(chain.by[right], key=lambda k: (abs(k - target), abs(k - short["strike"])))
    return chain.by[right][ks[0]] if ks else None


def settle(symbol: str, short: dict, long_: dict, prices: dict, s_exp: float) -> dict:
    right = short["right"]
    w = abs(long_["strike"] - short["strike"])
    vs = bs.intrinsic(s_exp, short["strike"], right)
    vl = bs.intrinsic(s_exp, long_["strike"], right)
    value = min(max(vs - vl, 0.0), w)
    itm = int(vs > 0) + int(vl > 0)
    credit = prices["working"]
    fee_open = fees.ic_open_fee(symbol, 1, legs=2, sell_legs=1, ndigits=4)
    settlement = itm * SETTLE_FEE
    gross = (credit - value) * 100
    net = gross - fee_open - settlement
    max_loss = (w - credit) * 100 + fee_open
    return {
        "width": w,
        "credit": credit,
        "net": net,
        "ret": net / max_loss,
        "max_loss": max_loss,
        "costs": fee_open + settlement,
        "slippage": (prices["mid"] - credit) * 100,
        "liquid": legbars._tight(prices["cost"], abs(prices["mid"]), legbars.LEG_BARS),
    }


def price_cells(chain: Chain, right: str, symbol: str, s_exp: float) -> dict[str, dict | str]:
    """{cell: result, or the reason it is no trade} for all 21 cells of one chain and side."""
    out: dict[str, dict | str] = {}
    atm = chain.atm_iv()
    for target in SHORTS:
        short = pick_short(chain, right, target)
        for fam in FAMILIES:
            for cid, d, w in CELLS[fam]:
                if d != target:
                    continue
                if short is None:
                    out[cid] = "no short strike"
                    continue
                if fam == "steps":
                    long_ = long_by_steps(chain, right, short, w)
                    if long_ is None:
                        out[cid] = "no strike that many steps beyond"
                        continue
                else:
                    if atm is None:
                        out[cid] = "no ATM IV"
                        continue
                    long_ = long_by_em(chain, right, short, w, atm)
                if long_ is None or long_["strike"] == short["strike"]:
                    out[cid] = "long would be the short's strike"
                    continue
                prices = legbars.structure_prices([(short, -1), (long_, 1)])
                if prices is None:
                    out[cid] = "no spread price"
                    continue
                if prices["working"] <= 0:
                    out[cid] = "non-positive working credit"
                    continue
                if prices["working"] >= abs(long_["strike"] - short["strike"]) - EPS:
                    out[cid] = "credit at or above the width"
                    continue
                res = settle(symbol, short, long_, prices, s_exp)
                res["short_delta"] = abs(chain.delta(short))
                out[cid] = res
    return out


# ------------------------------------------------------------------------------- per-name pricing
def _close_at(ds: list[str], cs: list[float], day: str, *, exact: bool) -> tuple[float | None, bool]:
    """(close, settled_at_last_close). Exact: that day's bar or None. Otherwise the last bar on or
    before `day`; flagged when it is more than SETTLE_LOOKBACK_DAYS before (the name's data ended)."""
    i = bisect.bisect_right(ds, day) - 1
    if i < 0:
        return None, False
    if exact:
        return (cs[i], False) if ds[i] == day else (None, False)
    stale = (date.fromisoformat(day) - date.fromisoformat(ds[i])).days > SETTLE_LOOKBACK_DAYS
    return cs[i], stale


def _any_in(sorted_days: list[str], lo: str, hi: str) -> bool:
    """A day in (lo, hi]."""
    i = bisect.bisect_right(sorted_days, lo)
    return i < len(sorted_days) and sorted_days[i] <= hi


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def price_name(job: dict) -> dict:
    """Price one symbol's trades and baselines. `job`: symbol, trades, chains_db, history_db, start,
    end, reports (for the 45-60 day report). Returns compact rows and drop counts."""
    sym = job["symbol"]
    h = _ro(job["history_db"])
    try:
        rows = h.execute("SELECT date, close FROM bars WHERE symbol = ? ORDER BY date", (sym,)).fetchall()
        splits = [
            r[0] for r in h.execute("SELECT ex_date FROM splits WHERE symbol = ? ORDER BY ex_date", (sym,))
        ]
        exdiv = [
            r[0] for r in h.execute("SELECT ex_date FROM dividends WHERE symbol = ? ORDER BY ex_date", (sym,))
        ]
    finally:
        h.close()
    ds, cs = [r[0] for r in rows], [r[1] for r in rows]
    c = _ro(job["chains_db"])
    status_cache: dict = {}
    chain_cache: dict = {}
    drops: Counter = Counter()
    empty: Counter = Counter()

    def status(exp_key):
        if exp_key not in status_cache:
            r = c.execute(
                "SELECT status FROM eod_chain_requests WHERE symbol = ? AND session = ? AND expiration = ?",
                exp_key,
            ).fetchone()
            status_cache[exp_key] = r[0] if r else None
        return status_cache[exp_key]

    def cells_for(entry: str, expiry: str, right: str):
        """({cell: result|reason}, settled_at_last_close) or (None, reason)."""
        key = (sym.upper(), entry, expiry)
        ck = (key, right)
        if ck in chain_cache:
            return chain_cache[ck]
        st = status(key)
        out: tuple
        if st is None:
            out = (None, "no chain in the store")
        elif st == "empty":
            out = (None, "empty chain")
        elif st != "complete":
            out = (None, f"chain {st}")
        else:
            spot, _ = _close_at(ds, cs, entry, exact=True)
            s_exp, stale = _close_at(ds, cs, expiry, exact=False)
            if spot is None:
                out = (None, "no entry close")
            elif s_exp is None:
                out = (None, "no expiry close")
            elif _any_in(splits, entry, expiry):
                out = (None, "excluded: split inside the trade")
            else:
                q = c.execute(
                    "SELECT strike, right, bid, ask FROM eod_chain_quotes "
                    "WHERE symbol = ? AND session = ? AND expiration = ?",
                    key,
                ).fetchall()
                ch = Chain(q, spot, entry, expiry)
                if ch.forward is None:
                    out = (None, "no parity forward")
                elif abs(ch.forward / spot - 1) > FWD_TOL:
                    out = (None, "parity forward more than 5% from the close")
                else:
                    out = (price_cells(ch, right, sym, s_exp), stale)
        chain_cache[ck] = out
        return out

    priced: list[dict] = []
    reported_far: list[dict] = []
    for t in job["trades"]:
        tag = f"{t['setup']}/{t['kind']}"
        if t["expiry"] != plan.expiry_for(t["entry"]):
            drops[f"{tag}: expiry not the declared monthly"] += 1
            continue
        if t["entry"] > job["end"] or t["expiry"] > job["end"]:
            drops[f"{tag}: after the window"] += 1
            continue
        right = "P" if t["side"] == "put" else "C"
        cells, extra = cells_for(t["entry"], t["expiry"], right)
        if cells is None:
            (empty if extra == "empty chain" else drops)[f"{tag}: {extra}"] += 1
            continue
        res = {}
        for cid, r in cells.items():
            if isinstance(r, str):
                drops[f"{tag} {cid}: {r}"] += 1
            else:
                res[cid] = _compact(r)
        if extra:
            drops[f"{tag}: settled at the last close (data ends before expiry; kept, flagged)"] += 1
        priced.append({**_ident(t), "stale": bool(extra), "cells": res})
        if t["kind"] == "trade":
            far = _far_trade(t, job, splits, exdiv, cells_for)
            if isinstance(far, str):
                drops[f"45-60 reported {t['setup']}: {far}"] += 1
            else:
                reported_far.append(far)
    c.close()
    return {"priced": priced, "far": reported_far, "drops": drops, "empty": empty}


def _far_trade(t, job, splits, exdiv, cells_for):
    """The same signal on the latest monthly 45-60 days out, where the store holds that chain:
    reported only. Earnings (the plan's rule), ex-dividend (calls) and splits re-applied."""
    exp = expiry_in(t["entry"], *REPORTED_DTE)
    if exp is None:
        return "no monthly 45-60 days out"
    if exp > job["end"]:
        return "after the window"
    reports = job.get("reports")
    if reports is None:
        return "earnings calendar not loaded"
    if plan.report_in_trade(t["symbol"], t["entry"], exp, reports, job.get("quarters", {})):
        return "excluded: earnings"
    if t["side"] == "call" and _any_in(exdiv, t["entry"], exp):
        return "excluded: ex-dividend inside a call spread"
    cells, extra = cells_for(t["entry"], exp, "P" if t["side"] == "put" else "C")
    if cells is None:
        return extra
    return {
        **_ident({**t, "expiry": exp}),
        "stale": bool(extra),
        "cells": {k: _compact(v) for k, v in cells.items() if not isinstance(v, str)},
    }


def _ident(t: dict) -> dict:
    return {k: t[k] for k in ("id", "kind", "of", "setup", "side", "symbol", "entry", "expiry")}


def _compact(r: dict) -> dict:
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()}


# --------------------------------------------------------------------------------------- statistics
def cluster_t(values: list[float], clusters: list[str]) -> float | None:
    """t of the mean with standard errors clustered by `clusters` (CR0, item 14's)."""
    n = len(values)
    if n < 2:
        return None
    m = statistics.fmean(values)
    g: dict[str, float] = defaultdict(float)
    for x, k in zip(values, clusters, strict=True):
        g[k] += x - m
    var = sum(v * v for v in g.values()) / (n * n)
    return m / math.sqrt(var) if var > 0 else None


def p_one(t: float | None) -> float:
    """One-sided normal tail of t; 1 when there is no t."""
    return 1.0 if t is None else 0.5 * math.erfc(t / math.sqrt(2))


def summarise(rows: list[dict]) -> dict:
    """The declared columns for a set of priced (trade, cell) results."""
    if not rows:
        return {"trades": 0}
    nets = [r["net"] for r in rows]
    credits = [r["credit"] * 100 for r in rows]
    return {
        "trades": len(rows),
        "mean_ret": statistics.fmean(r["ret"] for r in rows),
        "mean_net": statistics.fmean(nets),
        "credit_over_width": statistics.fmean(r["credit"] / r["width"] for r in rows),
        "credit_over_max_loss": statistics.fmean(r["credit"] * 100 / r["max_loss"] for r in rows),
        "win_rate": sum(1 for x in nets if x > 0) / len(rows),
        "worst_net": min(nets),
        "cost_drag": sum(r["costs"] for r in rows) / sum(credits) if sum(credits) > 0 else None,
        "slippage_per_trade": statistics.fmean(r["slippage"] for r in rows),
        "leg_bar_share": sum(1 for r in rows if r["liquid"]) / len(rows),
    }


# ------------------------------------------------------------------------------------- the rules
def index_baselines(priced: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for p in priced:
        if p["kind"] == "baseline":
            out[p["of"]].append(p)
    return out


def paired_diff(trade: dict, baselines: list[dict], cell: str) -> float | None:
    """The trade's ret less the mean ret of its baselines priced in the same cell; None when the trade
    or every baseline is unpriced there."""
    own = trade["cells"].get(cell)
    bs_ = [b["cells"][cell]["ret"] for b in baselines if cell in b["cells"]]
    if own is None or not bs_:
        return None
    return own["ret"] - statistics.fmean(bs_)


def best_cell(trades: list[dict], cells: list[str], min_n: int = 1) -> str | None:
    """The cell with the highest mean ret over `trades` (each counted where priced), among cells
    holding at least `min_n` of them; ties go to the cell declared first."""
    best, score = None, None
    for c in cells:
        xs = [t["cells"][c]["ret"] for t in trades if c in t["cells"]]
        if len(xs) < min_n:
            continue
        m = statistics.fmean(xs)
        if score is None or m > score + EPS:
            best, score = c, m
    return best


def assign_cells(pick: list[dict], cells: list[str], rule: str) -> tuple[str | None, dict[str, str]]:
    """(pooled cell, {symbol: cell}) picked on `pick`. Per symbol: the best cell on that symbol's own
    trades where a cell holds at least MIN_SYMBOL_TRADES of them, else the pooled cell."""
    pooled = best_cell(pick, cells)
    by_sym: dict[str, str] = {}
    if rule == "per_symbol" and pooled is not None:
        groups: dict[str, list[dict]] = defaultdict(list)
        for t in pick:
            groups[t["symbol"]].append(t)
        for s, ts in groups.items():
            c = best_cell(ts, cells, MIN_SYMBOL_TRADES)
            if c is not None:
                by_sym[s] = c
    return pooled, by_sym


def score_direction(trades, base_of, cells, rule, pick_half, score_half) -> dict:
    """One direction of one rule: pick on `pick_half`, score on `score_half`."""
    pick = [t for t in trades if t["half"] == pick_half]
    score = [t for t in trades if t["half"] == score_half]
    pooled, by_sym = assign_cells(pick, cells, rule)
    out = {"pick_half": pick_half, "score_half": score_half, "pooled_cell": pooled}
    if rule == "per_symbol":
        out["symbols_with_own_cell"] = len(by_sym)
    rows, unscored = [], Counter()
    for t in score:
        cell = by_sym.get(t["symbol"], pooled)
        if cell is None:
            unscored["no cell picked"] += 1
            continue
        if cell not in t["cells"]:
            unscored["not priced in its cell"] += 1
            continue
        d = paired_diff(t, base_of.get(t["id"], []), cell)
        if d is None:
            unscored["no baseline priced in its cell"] += 1
            continue
        r = t["cells"][cell]
        rows.append(
            {
                "id": t["id"],
                "symbol": t["symbol"],
                "entry": t["entry"],
                "expiry": t["expiry"],
                "cell": cell,
                "diff": d,
                "net": r["net"],
                "ret": r["ret"],
                "costs": r["costs"] + r["slippage"],
            }
        )
    # Planned MDE (B12, descriptive): the spread of the PICK half's paired diffs at its picked cells.
    past = []
    for t in pick:
        c = by_sym.get(t["symbol"], pooled)
        if c is not None:
            d = paired_diff(t, base_of.get(t["id"], []), c)
            if d is not None:
                past.append(d)
    out.update(direction_stats(rows))
    out["unscored"] = dict(unscored)
    out["planned_mde"] = robustness.planned_mde(past, max(len(rows), 1)) if len(past) >= 2 else None
    out["rows"] = rows
    return out


def direction_stats(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0, "mean_diff": None, "t": None, "own_mean_net": None, "own_mean_ret": None}
    diffs = [r["diff"] for r in rows]
    return {
        "n": n,
        "mean_diff": statistics.fmean(diffs),
        "t": cluster_t(diffs, [r["expiry"] for r in rows]),
        "own_mean_net": statistics.fmean(r["net"] for r in rows),
        "own_mean_ret": statistics.fmean(r["ret"] for r in rows),
    }


def judge(dirs: list[dict]) -> dict:
    """A test's verdict before Holm, from its two directions' stats."""
    ns = [d["n"] for d in dirs]
    if min(ns) < MIN_SCORED:
        return {"verdict": "no verdict", "why": f"scored trades per direction {ns}", "p": 1.0}
    ts = [d["t"] for d in dirs]
    t_ok = all(t is not None and t > T_BAR for t in ts)
    own_ok = all(d["own_mean_net"] > 0 for d in dirs)
    return {"verdict": None, "t_ok": t_ok, "own_ok": own_ok, "p": max(p_one(t) for t in ts)}


def apply_holm(tests: dict[str, dict]) -> None:
    holm = core_stats.holm({k: v["p"] for k, v in tests.items()}, alpha=ALPHA)
    for k, v in tests.items():
        v["holm_ok"] = holm[k]
        if v["verdict"] != "no verdict":
            v["verdict"] = "pass" if (v["t_ok"] and v["own_ok"] and v["holm_ok"]) else "fail"


def per_symbol_beats_pooled(ps_dirs: list[dict], pool_dirs: list[dict]) -> dict:
    """Paired on the same trades: per-symbol ret less pooled ret, t clustered by expiry, each way."""
    out = []
    for a, b in zip(ps_dirs, pool_dirs, strict=True):
        pooled = {r["id"]: r for r in b["rows"]}
        pairs = [(r["ret"] - pooled[r["id"]]["ret"], r["expiry"]) for r in a["rows"] if r["id"] in pooled]
        out.append(
            {
                "n": len(pairs),
                "mean": statistics.fmean(x for x, _ in pairs) if pairs else None,
                "t": cluster_t([x for x, _ in pairs], [e for _, e in pairs]),
            }
        )
    return {"directions": out, "beats": all(d["t"] is not None and d["t"] > T_BAR for d in out)}


def run_tests(priced: list[dict], symbols: set[str] | None = None) -> dict[str, dict]:
    """The 32 tests over `priced` (rows carrying `half`), optionally restricted to `symbols` (a name
    half: the cells are re-picked inside it)."""
    base_of = index_baselines(priced)
    tests: dict[str, dict] = {}
    for setup, side in SETUP_SIDES:
        sig = [p for p in priced if p["kind"] == "trade" and p["setup"] == setup]
        if symbols is not None:
            sig = [p for p in sig if p["symbol"] in symbols]
        for fam in FAMILIES:
            cells = [c for c, _, _ in CELLS[fam]]
            dirs_by_rule = {}
            for rule in RULES:
                dirs = [
                    score_direction(sig, base_of, cells, rule, "H1", "H2"),
                    score_direction(sig, base_of, cells, rule, "H2", "H1"),
                ]
                dirs_by_rule[rule] = dirs
                tests[f"{setup}|{side}|{fam}|{rule}"] = {
                    "setup": setup,
                    "side": side,
                    "family": fam,
                    "rule": rule,
                    "directions": dirs,
                    **judge(dirs),
                }
            tests[f"{setup}|{side}|{fam}|per_symbol"]["vs_pooled"] = per_symbol_beats_pooled(
                dirs_by_rule["per_symbol"], dirs_by_rule["pooled"]
            )
    apply_holm(tests)
    for v in tests.values():
        if v["rule"] == "per_symbol":
            v["earns_place"] = v["verdict"] == "pass" and v["vs_pooled"]["beats"]
    return tests


# --------------------------------------------------------------------------- descriptive additions
def b13_report(test: dict, priced: list[dict]) -> dict:
    """Beside a pass only, never re-choosing the setting: cost stress, neighbourhood, drawdown."""
    rows = sorted((r for d in test["directions"] for r in d["rows"]), key=lambda r: (r["entry"], r["id"]))
    out = {
        "cost_stress": robustness.cost_stress([r["net"] for r in rows], [r["costs"] for r in rows]),
        "drawdown": robustness.drawdown_range(
            [r["net"] for r in rows], sessions=len({r["entry"] for r in rows})
        ),
    }
    if test["rule"] == "pooled":
        sig = {p["id"]: p for p in priced if p["kind"] == "trade" and p["setup"] == test["setup"]}
        nb = []
        for d in test["directions"]:
            cell = d["pooled_cell"]
            fam, short, width = CELL_INFO[cell]
            scored = [p for p in sig.values() if p["half"] == d["score_half"]]

            def mean_at(c, scored=scored):
                xs = [p["cells"][c]["ret"] for p in scored if c in p["cells"]]
                return statistics.fmean(xs) if xs else None

            by_short = {s: mean_at(cell_id(s, fam, width)) for s in SHORTS}
            widths = STEPS if fam == "steps" else EM_FRACTIONS
            by_width = {float(w): mean_at(cell_id(short, fam, w)) for w in widths}
            nb.append(
                {
                    "score_half": d["score_half"],
                    "cell": cell,
                    "across_shorts": robustness.neighbourhood(by_short, short),
                    "across_widths": robustness.neighbourhood(by_width, float(width)),
                }
            )
        out["neighbourhood"] = nb
    else:
        out["neighbourhood"] = "not defined for per-symbol cells"
    return out


def describe(priced: list[dict]) -> dict:
    """Per setup-side and cell, pooled by period and by symbol over the deciding era. Descriptive."""
    pooled: dict = {}
    by_symbol: dict = {}
    for setup, side in SETUP_SIDES:
        for kind in ("trade", "baseline"):
            rows = [p for p in priced if p["setup"] == setup and p["kind"] == kind]
            for c in ALL_CELLS:
                for h in ("reported", "H1", "H2"):
                    sel = [p["cells"][c] for p in rows if p["half"] == h and c in p["cells"]]
                    pooled.setdefault(f"{setup}|{side}|{kind}", {}).setdefault(c, {})[h] = summarise(sel)
                if kind == "trade":
                    syms: dict[str, list[dict]] = defaultdict(list)
                    for p in rows:
                        if p["half"] in ("H1", "H2") and c in p["cells"]:
                            syms[p["symbol"]].append(p["cells"][c])
                    by_symbol.setdefault(f"{setup}|{side}", {})[c] = {
                        s: summarise(v) for s, v in sorted(syms.items())
                    }
    return {"pooled": pooled, "by_symbol": by_symbol}


def describe_far(far: list[dict]) -> dict:
    out: dict = {}
    for setup, side in SETUP_SIDES:
        rows = [p for p in far if p["setup"] == setup]
        for c in ALL_CELLS:
            for h in ("reported", "H1", "H2"):
                sel = [p["cells"][c] for p in rows if p["half"] == h and c in p["cells"]]
                out.setdefault(f"{setup}|{side}", {}).setdefault(c, {})[h] = summarise(sel)
    return out


def analyse(priced: list[dict], start: str, end: str) -> dict:
    """Everything the report holds, from priced rows. Pure."""
    mid = midpoint(start, end)
    for p in priced:
        p["half"] = period(p["entry"], start, mid)
    tests = run_tests(priced)
    halves = {}
    syms = sorted({p["symbol"] for p in priced})
    for i, names in enumerate(robustness.split_names(syms)):
        ht = run_tests(priced, set(names))
        halves[f"name_half_{i}"] = {
            "names": len(names),
            "verdicts": {k: {"verdict": v["verdict"], "p": v["p"]} for k, v in ht.items()},
        }
    b13 = {k: b13_report(v, priced) for k, v in tests.items() if v["verdict"] == "pass"}
    return {"midpoint": mid, "tests": tests, "name_halves": halves, "b13": b13}


def least_bad(tests: dict) -> dict | None:
    """The test with the highest smaller-direction own mean ret: what a no-pass tab lists."""
    best, score = None, None
    for k, v in tests.items():
        vals = [d["own_mean_ret"] for d in v["directions"]]
        if any(x is None for x in vals):
            continue
        s = min(vals)
        if score is None or s > score:
            best, score = k, s
    if best is None:
        return None
    return {
        "test": best,
        "cells": [d["pooled_cell"] for d in tests[best]["directions"]],
        "worse_half_ret": score,
    }


# ----------------------------------------------------------------------------------------- main
def licence_refusal(chains_db: Path, out: Path | None) -> str | None:
    """A result from the licensed store is written only under the backtest data dir."""
    vendor = "backtest" in Path(chains_db).as_posix()
    if vendor and out and not Path(out).resolve().is_relative_to(home.data_dir("backtest").resolve()):
        return "refused: a result from the licensed store is written only under the backtest data dir"
    return None


def _strip_rows(tests: dict) -> dict:
    return {
        k: {**v, "directions": [{kk: vv for kk, vv in d.items() if kk != "rows"} for d in v["directions"]]}
        for k, v in tests.items()
    }


def print_table(rep: dict) -> None:
    print(f"window {rep['window'][0]} .. {rep['window'][1]}, halves split at {rep['midpoint']}")
    print(f"{'setup':18s} {'side':4s} {'family':5s} {'rule':10s} {'n H1>H2':>8s} {'n H2>H1':>8s} "
          f"{'t':>6s} {'t':>6s} {'own$':>8s} {'own$':>8s}  verdict")  # fmt: skip
    for v in rep["tests"].values():
        d = v["directions"]

        def f(x, fmt):
            return format(x, fmt) if x is not None else "-"

        extra = ""
        if v["rule"] == "per_symbol":
            extra = " (earns its place)" if v.get("earns_place") else ""
        print(
            f"{v['setup']:18s} {v['side']:4s} {v['family']:5s} {v['rule']:10s} {d[0]['n']:8d} {d[1]['n']:8d} "
            f"{f(d[0]['t'], '6.2f'):>6s} {f(d[1]['t'], '6.2f'):>6s} {f(d[0]['own_mean_net'], '8.2f'):>8s} "
            f"{f(d[1]['own_mean_net'], '8.2f'):>8s}  {v['verdict']}{extra}"
        )
    print(f"least bad: {rep['least_bad']}")


def load_plan(path: Path) -> list[dict]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return [t for t in doc["trades"] if t["kind"] in ("trade", "baseline")]


def main(argv: list[str] | None = None) -> int:
    from cherrypick.backtest import chainfetch, settings
    from cherrypick.technicals import paths as tpaths

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default=DECLARED_START)
    ap.add_argument("--end", default=DECLARED_END)
    ap.add_argument("--json", type=Path, help="the full report")
    ap.add_argument("--plan", type=Path, help="trades.json (default: the item 17 plan)")
    ap.add_argument("--chains-db", type=Path)
    ap.add_argument("--history-db", type=Path)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit-names", type=int, help="price only the first N names (a trial, not the test)")
    ap.add_argument("--skip-45-60", action="store_true", help="skip the reported-only 45-60 day pricing")
    args = ap.parse_args(argv)

    chains_db = args.chains_db or chainfetch.db_path(settings.load())
    why = licence_refusal(chains_db, args.json)
    if why:
        print(why, file=sys.stderr)
        return 2
    history_db = args.history_db or tpaths.history_db()
    plan_path = args.plan or home.data_dir("backtest") / "results" / "research" / "item17" / "trades.json"
    trades = load_plan(plan_path)
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_sym[t["symbol"]].append(t)
    names = sorted(by_sym)
    if args.limit_names:
        names = names[: args.limit_names]
    reports, quarters = (None, {}) if args.skip_45_60 else plan._earnings()
    jobs = [
        {
            "symbol": s,
            "trades": by_sym[s],
            "chains_db": str(chains_db),
            "history_db": str(history_db),
            "start": args.start,
            "end": args.end,
            "reports": None if reports is None else {s: reports.get(s, [])},
            "quarters": {s: quarters.get(s, [])},
        }
        for s in names
    ]
    priced, far, drops, empty = [], [], Counter(), Counter()
    with ProcessPoolExecutor(args.workers) as ex:
        for part in ex.map(price_name, jobs, chunksize=8):
            priced.extend(part["priced"])
            far.extend(part["far"])
            drops.update(part["drops"])
            empty.update(part["empty"])

    rep = analyse(priced, args.start, args.end)
    mid = rep["midpoint"]
    for p in far:
        p["half"] = period(p["entry"], args.start, mid)
    out = {
        "licence": "ThetaData-derived. Local only; never into the repo, a PR, an issue or an artifact.",
        "item": 17,
        "window": [args.start, args.end],
        "midpoint": mid,
        "seed": SEED,
        "inputs": {"plan": str(plan_path), "chains_db": str(chains_db), "history_db": str(history_db)},
        "names": len(names),
        "trial": bool(args.limit_names),
        "tests": _strip_rows(rep["tests"]),
        "least_bad": least_bad(rep["tests"]),
        "descriptive": {
            "_note": "Descriptive only; never part of the verdict.",
            "name_halves_B9": rep["name_halves"],
            "planned_mde_B12": {
                k: [d["planned_mde"] for d in v["directions"]] for k, v in rep["tests"].items()
            },
            "robustness_B13": rep["b13"],
            "tables": describe(priced),
            "reported_45_60": describe_far(far),
        },
        "drops": dict(sorted(drops.items())),
        "empty_chains_B11": dict(sorted(empty.items())),
        "priced_counts": dict(Counter(f"{p['setup']}/{p['kind']}/{p['half']}" for p in priced)),
    }
    print_table({**out, "tests": rep["tests"]})
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(out, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
