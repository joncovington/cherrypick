"""Item 74, the super bull as leverage (research/edge-hypotheses-plan.md): a short put spread under a
long call spread, opened near even money, on the same expiry and with its long call one monthly later
(the diagonal form), judged per dollar of capital against the same delta held on that capital and
financed at the bill.

    python packages/backtest/scripts/super_bull_item74.py requests [--out PATH]
    python packages/backtest/scripts/super_bull_item74.py study [--start 2016-01-01] [--end 2021-12-31]
        [--allow-holdout] [--json PATH]

`requests` writes the path fetch: for each name and each standard monthly expiry, its daily chain from
100 days out to the expiry (a monthly is read as the front F and, for the diagonal, as the next one B).
`study` reads only `eod_chains.db` and the Treasury curve files. Results go under the backtest data
dir (results/research/item74/), never into the repo: the inputs are licensed.

**The cycle.** For each name and each standard monthly expiry F, with B the next standard monthly:
enter on the session nearest `dte` calendar days before F, for two entries: 35 (28 to 42) and 52 (45
to 60). Strikes by |delta| (Black-76 at r = 0 off each leg's mid and its chain's parity forward): F's
short put nearest 0.30 and long put nearest 0.20 below it; the long call nearest 0.20, in F (`A`, one
expiry) or in B (`B`, the diagonal); F's short call nearest 0.10 above the long call. One contract
each. Four arms: A35, B35, A52, B52.

**Capital** is the put spread's width less the net credit (plus the net debit): the expiry's max loss
for A, and for B the same with the long call counted at nothing, which is what an IRA holds. A trade
is kept only when its net premium is under a quarter of its capital (manage's directional test) and
its capital is positive.

**Exits, manage's directional rules as live from 2026-11-13:** the whole structure, at the working
price, on the first close whose gross gain reaches the lesser of half the max gain and 1x the capital
(the max gain: the call spread's width plus the net credit, B's long call again counted at its
intrinsic only), whose gross loss reaches half the capital, or else 3 days or fewer before F (the
expiry close). No time exit. `hold`, reported only: the 3-day close alone.

**The rival, `financed`:** the structure's entry delta (shares) held on the same capital, the rest
borrowed at the 3-month bill, over the same days: delta x the change in F's parity forward (which
carries the borrowing and the dividend) plus the capital's own interest at the bill. Excess is the
trade's net less that.

**The verdict**, per arm, on cycle means (one observation a cycle month, equal weight across names):
net return on capital > 0 with its stationary-bootstrap one-sided 95% lower bound > 0; excess return
on capital > 0 with its lower bound > 0 and Holm at 5% across the four arms; excess positive in both
time-halves; net and excess still positive at 1.5x costs. Fewer than 50 cycles, or more than 5% of
entries without an exit mark, is no verdict. The holdout (entries after 2021-12-31) is refused
without --allow-holdout.

Reported, never judged: the put spread alone and the call side alone per dollar of their own capital;
shares (delta x the forward's change, no financing); `hold`; B less A per cycle; entry put skew in
terciles (Holm across three, decides nothing).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from cherrypick.core import bs, fees, home, legbars, robustness, treasury
from cherrypick.core import calendar as cal
from cherrypick.core import stats as core_stats

from cherrypick.backtest import paths
from cherrypick.backtest import settings as _settings

_ITEM59 = Path(__file__).resolve().parent / "iwm_condor_item59.py"


def _load_item59():
    spec = importlib.util.spec_from_file_location("iwm_condor_item59", _ITEM59)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ic = _load_item59()

# Declared with hindsight (B11): three index funds and twenty large caps across sectors, each with
# weekly options and deep monthly books today.
NAMES = (
    "SPY", "QQQ", "IWM",
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "JPM", "BAC", "XOM", "CVX",
    "UNH", "JNJ", "PG", "KO", "WMT", "HD", "COST", "V", "DIS", "CAT",
)  # fmt: skip
DESIGN = ("2016-01-01", "2021-12-31")
LAST_FRONT = date(2026, 11, 20)
PATH_DAYS = 100
ENTRIES = {"35": (35, (28, 42)), "52": (52, (45, 60))}
ARMS = ("A35", "B35", "A52", "B52")
SHORT_PUT, LONG_PUT, LONG_CALL, SHORT_CALL = 0.30, 0.20, 0.20, 0.10
NEAR_EVEN = 0.25
TP_SHARE, TP_CAP, STOP_SHARE = 0.50, 1.0, 0.50
EXPIRY_CLOSE_DAYS = 3
MIN_CYCLES = 50
MAX_NO_EXIT_SHARE = 0.05
ALPHA = 0.05
LEVEL = 0.95
DRAWS = 20_000
BLOCK = 3
SEED = 74
PASS_STRESS = 1.5
RATE_STALE_DAYS = 10
SKEW_DELTA = 0.25


# --------------------------------------------------------------------------- calendar
def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(4 - d.weekday()) % 7)
    return d + timedelta(days=14)


def monthly_expiry(year: int, month: int) -> date:
    d = third_friday(year, month)
    return d if cal.is_trading_day(d) else cal.previous_trading_day(d)


def monthlies(first: date, last: date) -> list[date]:
    out, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        e = monthly_expiry(y, m)
        if first <= e <= last:
            out.append(e)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def requests(
    names=NAMES, first: date = date(2016, 1, 1), last: date | None = None, today: date | None = None
):
    """Each monthly's path from PATH_DAYS out: as F from its entry, and as B from F's earliest entry."""
    today = today or date.today()
    last = last or LAST_FRONT + timedelta(days=40)
    return [
        {
            "symbol": name,
            "expiration": e.isoformat(),
            "start": (e - timedelta(days=PATH_DAYS)).isoformat(),
            "through": min(e, today).isoformat(),
        }
        for name in names
        for e in monthlies(first, last)
        if e - timedelta(days=PATH_DAYS) <= today
    ]


# --------------------------------------------------------------------------- strikes and legs
def pick_legs(entry: dict, diagonal: bool) -> list | None:
    """[(expiry tag, strike, right, signed contracts)]: F -1 put, F +1 put below it, +1 call (F, or B
    for the diagonal), F -1 call above it. None when a leg is missing or the puts are not below the
    calls."""
    front = entry.get("F")
    calls = entry.get("B") if diagonal else front
    if front is None or calls is None or not front.forward or not calls.forward:
        return None
    sp = front.nearest("P", SHORT_PUT)
    lp = front.nearest("P", LONG_PUT, beyond=sp["strike"]) if sp else None
    lc = calls.nearest("C", LONG_CALL)
    sc = front.nearest("C", SHORT_CALL, beyond=lc["strike"]) if lc else None
    if None in (sp, lp, lc, sc) or not lp["strike"] < sp["strike"] <= lc["strike"] < sc["strike"]:
        return None
    return [
        ("F", sp["strike"], "P", -1),
        ("F", lp["strike"], "P", 1),
        ("B" if diagonal else "F", lc["strike"], "C", 1),
        ("F", sc["strike"], "C", -1),
    ]


def puts_of(legs: list) -> list:
    return [x for x in legs if x[2] == "P"]


def calls_of(legs: list) -> list:
    return [x for x in legs if x[2] == "C"]


def quotes(chains: dict, legs: list, *, close: bool = False) -> list | None:
    out = []
    for tag, k, r, n in legs:
        q = chains[tag].by[r].get(k) if chains.get(tag) else None
        if q is None:
            return None
        out.append((q, -n if close else n))
    return out


def price(chains: dict, legs: list, fill: str = "working", *, close: bool = False) -> float | None:
    """Per share, a credit +: what opening (or, close=True, closing) the legs brings in. On a close, a
    long with no bid is left to expire worthless: it is counted at nothing rather than refusing the
    close, as a far wing near expiry always is."""
    qs = quotes(chains, legs, close=close)
    if qs is None:
        return None
    if close:
        qs = [(q, n) for q, n in qs if not (n < 0 and not q.get("bid"))]
        if not qs:
            return 0.0
    p = legbars.structure_prices(qs)
    return None if p is None else p[fill]


def width(legs: list) -> float:
    ks = [k for _, k, _, _ in legs]
    return max(ks) - min(ks)


def capital(legs: list, credit: float) -> float | None:
    """The put spread's width less the net credit, in dollars; None when not positive."""
    cap = (width(puts_of(legs)) - credit) * 100
    return round(cap, 2) if cap > 0 else None


def max_gain(legs: list, credit: float) -> float:
    """The call spread's width plus the net credit, in dollars (B's long call at intrinsic only)."""
    return round((width(calls_of(legs)) + credit) * 100, 2)


def near_even(credit: float, cap: float) -> bool:
    return abs(credit * 100) < NEAR_EVEN * cap


def fee_pair(symbol: str, legs: list) -> tuple[float, float]:
    total, sold = sum(abs(n) for *_, n in legs), sum(-n for *_, n in legs if n < 0)
    return (
        fees.ic_open_fee(symbol, 1, legs=total, sell_legs=sold),
        fees.ic_close_fee(symbol, 1, legs=total, sell_legs=sold),
    )


def entry_delta(chains: dict, legs: list) -> float | None:
    """Share-equivalent delta: signed contracts x 100 x each leg's delta off its own chain."""
    total = 0.0
    for tag, k, r, n in legs:
        q = chains[tag].by[r].get(k)
        d = chains[tag].delta(q) if q else None
        if d is None:
            return None
        total += n * 100 * d
    return total


def skew(chain) -> float | None:
    """F's implied volatility at the 0.25-delta put less at the 0.25-delta call."""
    out = []
    for r in ("P", "C"):
        q = chain.nearest(r, SKEW_DELTA)
        if q is None:
            return None
        iv = bs.implied_vol(chain.forward, q["strike"], chain.years, (q["bid"] + q["ask"]) / 2, r, 0.0)
        if not iv:
            return None
        out.append(iv)
    return round(out[0] - out[1], 5)


def financed(delta: float, fwd0: float, fwd1: float, cap: float, rate: float, days: int) -> float:
    """The entry delta held on `cap` with the rest borrowed at the bill: the forward's change carries the
    borrowing and the dividend; the capital earns the bill."""
    return round(delta * (fwd1 - fwd0) + cap * rate * days / 365.0, 2)


# --------------------------------------------------------------------------- one trade
def exit_reason(gross: float, cap: float, gain: float, days_left: int, *, hold: bool = False) -> str | None:
    if not hold:
        if gross >= min(TP_SHARE * gain, TP_CAP * cap):
            return "target"
        if -gross >= STOP_SHARE * cap:
            return "stop"
    return "expiry" if days_left <= EXPIRY_CLOSE_DAYS else None


def run_trade(
    symbol: str,
    legs: list,
    entry: dict,
    path: list,
    front: str,
    opened: str,
    rate: float,
    *,
    hold: bool = False,
) -> dict:
    """`entry` is {"F": chain, "B": chain} on session `opened`; `path` is [(session, {"F": chain, "B":
    chain})] after it; `rate` is the bill on `opened`."""
    credit = price(entry, legs)
    if credit is None:
        return {"skip": "entry does not price"}
    cap = capital(legs, credit)
    if cap is None:
        return {"skip": "no capital"}
    if not near_even(credit, cap):
        return {"skip": "not near even money"}
    gain = max_gain(legs, credit)
    if gain <= 0:
        return {"skip": "no gain"}
    delta = entry_delta(entry, legs)
    if delta is None:
        return {"skip": "no entry delta"}
    fee_open, fee_close = fee_pair(symbol, legs)
    put_credit, call_credit = price(entry, puts_of(legs)), price(entry, calls_of(legs))
    for session, chains in path:
        proceeds = price(chains, legs, close=True)
        if proceeds is None or not chains["F"].forward:
            continue
        gross = round((credit + proceeds) * 100, 2)
        how = exit_reason(gross, cap, gain, ic.days_between(session, front), hold=hold)
        if how is None:
            continue
        cost = round(fee_open + fee_close, 2)
        net = round(gross - cost, 2)
        days = ic.days_between(opened, session)
        fin = financed(delta, entry["F"].forward, chains["F"].forward, cap, rate, days)
        sides = {}
        for side, part, opened in (
            ("puts", puts_of(legs), put_credit),
            ("calls", calls_of(legs), call_credit),
        ):
            closed = price(chains, part, close=True)
            if opened is None or closed is None:
                sides[side] = None
                continue
            side_cap = width(part) * 100 - opened * 100 if side == "puts" else -opened * 100
            side_net = (opened + closed) * 100 - cost / 2
            sides[side] = round(side_net / side_cap, 5) if side_cap > 0 else None
        return {
            "credit": credit,
            "exit": session,
            "how": how,
            "days": days,
            "gross": gross,
            "cost": cost,
            "net": net,
            "capital": cap,
            "max_gain": gain,
            "delta": round(delta, 2),
            "financed": fin,
            "shares": round(delta * (chains["F"].forward - entry["F"].forward), 2),
            "excess": round(net - fin, 2),
            "puts_roc": sides["puts"],
            "calls_roc": sides["calls"],
        }
    return {"skip": "no exit mark"}


# --------------------------------------------------------------------------- the store
def load_chain(db: sqlite3.Connection, symbol: str, session: str, expiry: str):
    rows = db.execute(
        "SELECT strike, right, bid, ask FROM eod_chain_quotes "
        "WHERE symbol = ? AND session = ? AND expiration = ?",
        (symbol, session, expiry),
    ).fetchall()
    return ic.Chain(rows, session, expiry) if rows else None


def sessions_for(db: sqlite3.Connection, symbol: str, expiry: str) -> list[str]:
    return [
        s
        for (s,) in db.execute(
            "SELECT DISTINCT session FROM eod_chain_quotes "
            "WHERE symbol = ? AND expiration = ? ORDER BY session",
            (symbol, expiry),
        )
    ]


def rate_reader(lookup=None):
    """The session's 3-month bill, strictly before it; a gap of more than RATE_STALE_DAYS stops the run."""
    lookup = lookup or treasury.yield_before
    seen: dict[str, float] = {}

    def rate(session: str) -> float:
        if session not in seen:
            got = lookup(session, "3m")
            if got is None or (date.fromisoformat(session) - got[0]).days > RATE_STALE_DAYS:
                raise SystemExit(
                    f"no Treasury 3-month yield within {RATE_STALE_DAYS} days before {session}; seed the "
                    "curve files: python scripts/fetch_market_files.py treasury-history --since 2015"
                )
            seen[session] = got[1] / 100.0
        return seen[session]

    return rate


def trades(db, start: str, end: str, rate) -> tuple[list, dict]:
    """Every (name, front expiry) cycle in the window, all four arms and their `hold` twins."""
    rows, skips = [], {a: Counter() for a in ARMS}
    exps = monthlies(date(2015, 12, 1), LAST_FRONT + timedelta(days=40))
    for symbol in NAMES:
        for f, b in zip(exps, exps[1:], strict=False):
            if f > LAST_FRONT:
                continue
            fs, bs_ = f.isoformat(), b.isoformat()
            sess = sessions_for(db, symbol, fs)
            for tag, (dte, band) in ENTRIES.items():
                planned = (f - timedelta(days=dte)).isoformat()
                if not (start <= planned <= end):
                    continue
                arms = [f"A{tag}", f"B{tag}"]
                s0 = ic.entry_session(sess, fs, dte, band)
                entry = (
                    {"F": load_chain(db, symbol, s0, fs), "B": load_chain(db, symbol, s0, bs_)}
                    if s0
                    else None
                )
                if not entry or entry["F"] is None:
                    for a in arms:
                        skips[a]["no entry chain"] += 1
                    continue
                path = [
                    (s, {"F": load_chain(db, symbol, s, fs), "B": load_chain(db, symbol, s, bs_)})
                    for s in sess
                    if s > s0
                ]
                path = [(s, c) for s, c in path if c["F"] is not None]
                sk = skew(entry["F"])
                for a in arms:
                    legs = pick_legs(entry, diagonal=a.startswith("B"))
                    if legs is None:
                        skips[a]["no strikes"] += 1
                        continue
                    r = run_trade(symbol, legs, entry, path, fs, s0, rate(s0))
                    if "skip" in r:
                        skips[a][r["skip"]] += 1
                        continue
                    held = run_trade(symbol, legs, entry, path, fs, s0, rate(s0), hold=True)
                    rows.append(
                        {
                            "symbol": symbol,
                            "arm": a,
                            "front": fs,
                            "entry": s0,
                            "skew": sk,
                            "legs": legs,
                            **r,
                            "hold_net": held.get("net"),
                        }
                    )
    return rows, skips


# --------------------------------------------------------------------------- statistics
def cycle_means(rows: list, key) -> dict[str, float]:
    g: dict = defaultdict(list)
    for r in rows:
        v = key(r)
        if v is not None:
            g[r["front"][:7]].append(v)
    return {m: statistics.fmean(v) for m, v in sorted(g.items())}


def lower_bound(xs: list[float]) -> tuple[float | None, float]:
    """Stationary bootstrap of the mean: (one-sided LEVEL lower bound, p = share of draws at or below 0)."""
    if len(xs) < 2:
        return None, 1.0
    rng = random.Random(SEED)
    draws = sorted(
        statistics.fmean(xs[i] for i in core_stats.stationary_indices(len(xs), BLOCK, rng))
        for _ in range(DRAWS)
    )
    lo = draws[int((1 - LEVEL) * DRAWS)]
    p = sum(d <= 0 for d in draws) / DRAWS
    return round(lo, 5), p


def _summary(series: dict[str, float], start: str, end: str) -> dict:
    xs = list(series.values())
    lo, p = lower_bound(xs)
    mid = ic.midpoint(start, end)[:7]
    halves = {h: [v for m, v in series.items() if (m < mid) == (h == "first")] for h in ("first", "second")}
    return {
        "cycles": len(xs),
        "mean": round(statistics.fmean(xs), 5) if xs else None,
        "lower_bound": lo,
        "p": p,
        "halves": {h: round(statistics.fmean(v), 5) if v else None for h, v in halves.items()},
    }


def score(rows: list, skips: dict, start: str, end: str) -> dict:
    out: dict = {}
    for a in ARMS:
        rs = [r for r in rows if r["arm"] == a]
        net = _summary(cycle_means(rs, lambda r: r["net"] / r["capital"]), start, end)
        exc = _summary(cycle_means(rs, lambda r: r["excess"] / r["capital"]), start, end)
        cost = cycle_means(rs, lambda r: r["cost"] / r["capital"])
        months = sorted(cost)
        net_m = cycle_means(rs, lambda r: r["net"] / r["capital"])
        exc_m = cycle_means(rs, lambda r: r["excess"] / r["capital"])
        stress = {
            name: robustness.cost_stress(
                [s[m] for m in months], [cost[m] for m in months], pass_stress=PASS_STRESS
            )
            if months
            else None
            for name, s in (("net", net_m), ("excess", exc_m))
        }
        no_exit = skips[a].get("no exit mark", 0)
        entered = len(rs) + no_exit
        out[a] = {
            "net": net,
            "excess": exc,
            "cost_stress": stress,
            "trades": len(rs),
            "no_exit_share": round(no_exit / entered, 3) if entered else None,
            "win_rate": round(sum(r["net"] > 0 for r in rs) / len(rs), 3) if rs else None,
            "by_exit": dict(Counter(r["how"] for r in rs)),
        }
    return out


def judge(tests: dict) -> None:
    adjusted = core_stats.holm({a: tests[a]["excess"]["p"] for a in ARMS}, alpha=ALPHA)
    for a in ARMS:
        t = tests[a]
        if t["excess"]["cycles"] < MIN_CYCLES:
            t["verdict"], t["why"] = "no verdict", f"{t['excess']['cycles']} cycles < {MIN_CYCLES}"
            continue
        if (t.get("no_exit_share") or 0) > MAX_NO_EXIT_SHARE:
            t["verdict"], t["why"] = "no verdict", f"{t['no_exit_share']} of entries had no exit mark"
            continue
        stress = t["cost_stress"]
        checks = {
            "net_positive": (t["net"]["mean"] or 0) > 0 and (t["net"]["lower_bound"] or 0) > 0,
            "excess_positive": (t["excess"]["mean"] or 0) > 0 and (t["excess"]["lower_bound"] or 0) > 0,
            "holm": bool(adjusted.get(a)),
            "excess_both_halves": all((x or 0) > 0 for x in t["excess"]["halves"].values()),
            "net_at_1_5x_costs": bool(stress["net"] and stress["net"]["survives"]),
            "excess_at_1_5x_costs": bool(stress["excess"] and stress["excess"]["survives"]),
        }
        t["checks"] = checks
        t["verdict"] = "pass" if all(checks.values()) else "fail"


def skew_terciles(rows: list, arm: str, start: str, end: str) -> dict:
    """Within each cycle, the names' entry skew cut in thirds; each third's excess return on capital.
    Holm across the three. Reported: it decides nothing."""
    by_cycle: dict = defaultdict(list)
    for r in rows:
        if r["arm"] == arm and r["skew"] is not None:
            by_cycle[r["front"]].append(r)
    third: dict = {"low": [], "mid": [], "high": []}
    for rs in by_cycle.values():
        if len(rs) < 3:
            continue
        rs = sorted(rs, key=lambda r: r["skew"])
        n = len(rs)
        for i, r in enumerate(rs):
            third["low" if i < n / 3 else ("mid" if i < 2 * n / 3 else "high")].append(r)
    out = {
        k: _summary(cycle_means(v, lambda r: r["excess"] / r["capital"]), start, end)
        for k, v in third.items()
    }
    adjusted = core_stats.holm({k: out[k]["p"] for k in out}, alpha=ALPHA)
    for k in out:
        out[k]["holm"] = bool(adjusted.get(k))
    return out


def reported(rows: list, start: str, end: str) -> dict:
    out: dict = {}
    for a in ARMS:
        rs = [r for r in rows if r["arm"] == a]
        out[a] = {
            "puts_alone": _summary(cycle_means(rs, lambda r: r["puts_roc"]), start, end),
            "calls_alone": _summary(cycle_means(rs, lambda r: r["calls_roc"]), start, end),
            "shares": _summary(cycle_means(rs, lambda r: r["shares"] / r["capital"]), start, end),
            "financed": _summary(cycle_means(rs, lambda r: r["financed"] / r["capital"]), start, end),
            "hold": _summary(
                cycle_means(rs, lambda r: None if r["hold_net"] is None else r["hold_net"] / r["capital"]),
                start,
                end,
            ),
            "skew_terciles": skew_terciles(rows, a, start, end),
            "by_year": {
                y: round(statistics.fmean(r["net"] / r["capital"] for r in rs if r["entry"][:4] == y), 5)
                for y in sorted({r["entry"][:4] for r in rs})
            },
            "by_name": {
                s: round(statistics.fmean(r["net"] / r["capital"] for r in rs if r["symbol"] == s), 5)
                for s in NAMES
                if any(r["symbol"] == s for r in rs)
            },
        }
    for tag in ENTRIES:
        pair: dict = defaultdict(dict)
        for r in rows:
            if r["arm"][1:] == tag:
                pair[(r["front"], r["symbol"])][r["arm"][0]] = r["net"] / r["capital"]
        g: dict = defaultdict(list)
        for (front, _), v in pair.items():
            if "A" in v and "B" in v:
                g[front[:7]].append(v["B"] - v["A"])
        out[f"B-A{tag}"] = _summary({m: statistics.fmean(v) for m, v in sorted(g.items())}, start, end)
    return out


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rq = sub.add_parser("requests")
    rq.add_argument("--out", type=Path)
    st = sub.add_parser("study")
    st.add_argument("--start", default=DESIGN[0])
    st.add_argument("--end", default=DESIGN[1])
    st.add_argument("--allow-holdout", action="store_true")
    st.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    base = home.data_dir("backtest") / "results" / "research" / "item74"

    if args.cmd == "requests":
        target = args.out or base / "requests-paths.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        reqs = requests()
        target.write_text(json.dumps({"requests": reqs}, indent=1), encoding="utf-8")
        print(json.dumps({"requests": len(reqs), "written": str(target)}))
        return 0

    if args.end > DESIGN[1] and not args.allow_holdout:
        print(
            f"refused: --end {args.end} is past the design window's end {DESIGN[1]}, into the holdout; "
            "pass --allow-holdout",
            file=sys.stderr,
        )
        return 2
    cfg = _settings.load()
    vendor = paths.ensure_vendor_dir(cfg)
    db = sqlite3.connect(f"file:{vendor / 'eod_chains.db'}?mode=ro", uri=True)
    rows, skips = trades(db, args.start, args.end, rate_reader())
    tests = score(rows, skips, args.start, args.end)
    judge(tests)
    out = {
        "licence": "derived from licensed vendor history: stays under ~/.cherrypick, never in the repo",
        "item": 74,
        "window": [args.start, args.end],
        "allow_holdout": args.allow_holdout,
        "verdicts": {a: tests[a].get("verdict") for a in ARMS},
        "tests": tests,
        "reported": reported(rows, args.start, args.end),
        "skips": {a: dict(v) for a, v in skips.items()},
        "planned_mde_B12": {
            a: robustness.planned_mde(v, len(v)) if v else None
            for a, v in ((a, [r["net"] for r in rows if r["arm"] == a]) for a in ARMS)
        },
    }
    target = args.json or base / f"study-{args.start}-{args.end}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    (target.parent / f"trades-{args.start}-{args.end}.json").write_text(
        json.dumps(rows, default=str), encoding="utf-8"
    )
    print(json.dumps({"verdicts": out["verdicts"], "written": str(target), "at": datetime.now().isoformat()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
