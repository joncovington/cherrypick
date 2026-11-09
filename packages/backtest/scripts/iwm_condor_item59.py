"""Item 59, the 45-day IWM condor (research/edge-hypotheses-plan.md): an IWM iron condor sold on the
monthly cycle at about 45 days, shorts at 16 delta and longs at 5, closed at half its credit or at
21 days.

    python packages/backtest/scripts/iwm_condor_item59.py [--start 2016-01-01] [--end 2021-12-31]
    [--allow-holdout]
        [--account N] [--json PATH]

Reads only `eod_chains.db` (IWM's end-of-day chains and each monthly expiry's daily path, fetched by
item 59's `fetch-chain-paths` row) and Cboe's published VIX history (the local market-files copy).
Writes its result under the backtest data dir (results/research/item59/), never into the repo: the
inputs are licensed.

**The rule, as declared.** For each standard monthly expiry E (the third Friday, or the Thursday
before it when that Friday is a holiday), on the session whose close is nearest 45 calendar days
before E and 42 to 48 days out:
- the forward is put-call parity at r = 0 (the median of K + C - P over the three strikes nearest
  the strike where the call's and put's mids are closest, each two-sided both sides); deltas are
  Black-76 at r = 0 from each strike's own mid, as items 14-17;
- short the out-of-the-money put and call (below and above the forward) whose |delta| is nearest
  0.16; long the put and call further out whose |delta| is nearest 0.05;
- one contract, sold at the working price (halfway from mid to natural); a non-positive working
  credit is no trade.
Each later session's close marks the buy-back at the same fill. The condor is closed on the first
session whose buy-back costs half the credit or less, else on the first session 21 days or fewer
from E. A session whose legs do not all price is passed over; a condor that reaches E without a
priced exit is skipped and counted.

**The verdict:** the mean net per trade above zero with a one-sided t above 2, both time-halves of
the window positive, and the mean still positive at 1.5x costs. Fewer than 50 trades in a window,
or more than 5% of its entries skipped for want of an exit mark, is no verdict. The holdout (entries
after 2021-12-31) is refused without --allow-holdout, and the declaration reads it only after a
design pass.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from cherrypick.core import bs, fees, home, legbars, robustness
from cherrypick.core import stats as core_stats

from cherrypick.backtest import paths
from cherrypick.backtest import settings as _settings

SYMBOL = "IWM"
DESIGN = ("2016-01-01", "2021-12-31")
ENTRY_DTE = 45
ENTRY_BAND = (42, 48)
SHORT_DELTA = 0.16
LONG_DELTA = 0.05
TARGET = 0.50
TIME_EXIT_DTE = 21
VIX_BANDS = (15.0, 20.0, 25.0, math.inf)
MIN_TRADES = 50
MAX_NO_EXIT_SHARE = 0.05
T_BAR = 2.0
PASS_STRESS = 1.5
FILLS = ("working", "mid", "natural")
WORST_N = 5


# --------------------------------------------------------------------------- inputs
def vix_closes(path: Path) -> dict[str, float]:
    out = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            m, d, y = row["DATE"].split("/")
            out[f"{y}-{m}-{d}"] = float(row["CLOSE"])
    return out


def prior_close(closes: dict[str, float], day: str) -> float | None:
    earlier = [d for d in closes if d < day]
    return closes[max(earlier)] if earlier else None


def vix_band(vix: float) -> str:
    low = 0.0
    for top in VIX_BANDS:
        if vix < top:
            return f"{low:g}-{top:g}" if top != math.inf else f"{low:g}+"
        low = top
    raise AssertionError("unreachable")


def is_monthly(e: str) -> bool:
    """The third Friday, or the Thursday before it (an exchange holiday moved the expiry)."""
    d = date.fromisoformat(e)
    return (d.weekday() == 4 and 15 <= d.day <= 21) or (d.weekday() == 3 and 14 <= d.day <= 20)


def monthlies(expirations: list[str]) -> list[str]:
    """One standard monthly per month: its Friday, or the Thursday when no Friday is listed."""
    chosen: dict[str, str] = {}
    for e in sorted(expirations):
        if not is_monthly(e):
            continue
        prev = chosen.get(e[:7])
        if prev is None or date.fromisoformat(e).weekday() == 4:
            chosen[e[:7]] = e
    return sorted(chosen.values())


def days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def entry_session(sessions: list[str], expiry: str, target: int = ENTRY_DTE, band=ENTRY_BAND) -> str | None:
    """The session nearest `target` calendar days before `expiry`, inside `band`; the earlier on a tie."""
    inside = [s for s in sessions if band[0] <= days_between(s, expiry) <= band[1]]
    if not inside:
        return None
    return min(inside, key=lambda s: (abs(days_between(s, expiry) - target), s))


# --------------------------------------------------------------------------- chain
def parity_forward(calls: dict, puts: dict) -> float | None:
    """r = 0 parity: the median of K + C_mid - P_mid over the three strikes nearest the strike where
    the call's and put's mids are closest, every one two-sided both sides. None without one."""
    both = [k for k in calls if k in puts and legbars.two_sided(calls[k]) and legbars.two_sided(puts[k])]
    if not both:
        return None

    def mid(q):
        return (q["bid"] + q["ask"]) / 2

    seed = min(both, key=lambda k: (abs(mid(calls[k]) - mid(puts[k])), k))
    near = sorted(both, key=lambda k: (abs(k - seed), k))[:3]
    return statistics.median(k + mid(calls[k]) - mid(puts[k]) for k in near)


class Chain:
    """One end-of-day chain for one expiry: quotes by right and strike, its forward and deltas."""

    def __init__(self, rows, session: str, expiry: str):
        self.years = max(days_between(session, expiry), 1) / 365.0
        self.by: dict[str, dict[float, dict]] = {"C": {}, "P": {}}
        for k, r, b, a in rows:
            r = "C" if str(r).upper() in ("C", "CALL") else "P"
            self.by[r][float(k)] = {"strike": float(k), "right": r, "bid": b, "ask": a}
        self.forward = parity_forward(self.by["C"], self.by["P"])

    def delta(self, q: dict) -> float | None:
        if not self.forward or not legbars.two_sided(q):
            return None
        iv = bs.implied_vol(self.forward, q["strike"], self.years, (q["bid"] + q["ask"]) / 2, q["right"], 0.0)
        if not iv or iv <= 0.0002:
            return None
        return bs.delta(self.forward, q["strike"], self.years, iv, q["right"], 0.0)

    def otm(self, right: str) -> list[dict]:
        if right == "P":
            ks = sorted((k for k in self.by["P"] if k < self.forward), reverse=True)
        else:
            ks = sorted(k for k in self.by["C"] if k > self.forward)
        return [self.by[right][k] for k in ks]

    def nearest(self, right: str, target: float, beyond: float | None = None) -> dict | None:
        """The OTM strike on `right` whose |delta| is nearest `target`, strictly beyond `beyond`."""
        best = None
        for q in self.otm(right):
            if beyond is not None and (q["strike"] >= beyond if right == "P" else q["strike"] <= beyond):
                continue
            d = self.delta(q)
            if d is None:
                continue
            gap = abs(abs(d) - target)
            if best is None or gap < best[0]:
                best = (gap, q)
        return None if best is None else best[1]


def pick_condor(
    chain: Chain, short_delta: float = SHORT_DELTA, long_delta: float = LONG_DELTA
) -> list | None:
    """The four strikes as ((strike, right), signed quantity), sold -1. None when a leg is missing."""
    if not chain.forward:
        return None
    sp = chain.nearest("P", short_delta)
    sc = chain.nearest("C", short_delta)
    if sp is None or sc is None:
        return None
    lp = chain.nearest("P", long_delta, beyond=sp["strike"])
    lc = chain.nearest("C", long_delta, beyond=sc["strike"])
    if lp is None or lc is None:
        return None
    return [
        ((sp["strike"], "P"), -1),
        ((sc["strike"], "C"), -1),
        ((lp["strike"], "P"), 1),
        ((lc["strike"], "C"), 1),
    ]


def price(chain: Chain, legs: list, fill: str, *, close: bool = False) -> float | None:
    """Per share: the credit a sale brings, or (close=True) the debit a buy-back costs. None when a leg
    does not price."""
    qs = []
    for (k, r), n in legs:
        q = chain.by[r].get(k)
        if q is None:
            return None
        qs.append((q, -n if close else n))
    p = legbars.structure_prices(qs)
    if p is None:
        return None
    return round(-p[fill], 4) if close else p[fill]


# --------------------------------------------------------------------------- one trade
def side_value(chain: Chain, legs: list, right: str, fill: str) -> float | None:
    side = [leg for leg in legs if leg[0][1] == right]
    return price(chain, side, fill, close=True)


def run_trade(
    entry: Chain,
    path: list[tuple[str, Chain]],
    expiry: str,
    *,
    fill: str,
    short_delta: float = SHORT_DELTA,
    target: float = TARGET,
    time_exit: int = TIME_EXIT_DTE,
) -> dict:
    legs = pick_condor(entry, short_delta)
    if legs is None:
        return {"skip": "no condor strikes"}
    credit = price(entry, legs, fill)
    if credit is None or credit <= 0:
        return {"skip": "no positive credit"}
    side_credit = {r: price(entry, [leg for leg in legs if leg[0][1] == r], fill) for r in ("P", "C")}
    for session, chain in path:
        debit = price(chain, legs, fill, close=True)
        if debit is None:
            continue
        dte = days_between(session, expiry)
        how = "target" if debit <= target * credit else ("time" if dte <= time_exit else None)
        if how is None:
            continue
        open_fee = fees.ic_open_fee(SYMBOL, 1, legs=4, sell_legs=2)
        close_fee = fees.ic_close_fee(SYMBOL, 1, legs=4, sell_legs=2)
        gross = round((credit - debit) * 100, 2)
        cost = round(open_fee + close_fee, 2)
        sides = {}
        for r in ("P", "C"):
            v = side_value(chain, legs, r, fill)
            c = side_credit[r]
            sides[r] = None if v is None or c is None else round((c - v) * 100, 2)
        return {
            "legs": [[k, r, n] for (k, r), n in legs],
            "credit": credit,
            "debit": debit,
            "exit": session,
            "how": how,
            "days_held": days_between(path[0][0], session) + 1 if path else None,
            "gross": gross,
            "cost": cost,
            "net": round(gross - cost, 2),
            "risk": round(
                (max(legs[0][0][0] - legs[2][0][0], legs[3][0][0] - legs[1][0][0]) - credit) * 100, 2
            ),
            "sides_gross": sides,
        }
    return {"skip": "no exit mark"}


# --------------------------------------------------------------------------- the store
def load_chain(db: sqlite3.Connection, session: str, expiry: str) -> Chain:
    rows = db.execute(
        "SELECT strike, right, bid, ask FROM eod_chain_quotes "
        "WHERE symbol = ? AND session = ? AND expiration = ?",
        (SYMBOL, session, expiry),
    ).fetchall()
    return Chain(rows, session, expiry)


def sessions_for(db: sqlite3.Connection, expiry: str) -> list[str]:
    return [
        s
        for (s,) in db.execute(
            "SELECT DISTINCT session FROM eod_chain_quotes "
            "WHERE symbol = ? AND expiration = ? ORDER BY session",
            (SYMBOL, expiry),
        )
    ]


def trades(
    db: sqlite3.Connection, start: str, end: str, vix: dict, *, fill: str, entry_dte: int = ENTRY_DTE, **kw
) -> tuple[list[dict], Counter]:
    exps = monthlies(
        [
            e
            for (e,) in db.execute(
                "SELECT DISTINCT expiration FROM eod_chain_quotes WHERE symbol = ?", (SYMBOL,)
            )
        ]
    )
    (last,) = db.execute("SELECT max(session) FROM eod_chain_quotes WHERE symbol = ?", (SYMBOL,)).fetchone()
    rows, skips = [], Counter()
    for e in exps:
        planned = date.fromordinal(date.fromisoformat(e).toordinal() - entry_dte).isoformat()
        if not (start <= planned <= end):
            continue
        if e > last:
            skips["cycle not finished"] += 1
            continue
        sess = sessions_for(db, e)
        s0 = entry_session(sess, e, entry_dte, (entry_dte - 3, entry_dte + 3))
        if s0 is None:
            skips["no entry chain"] += 1
            continue
        entry = load_chain(db, s0, e)
        path = [(s, load_chain(db, s, e)) for s in sess if s > s0]
        r = run_trade(entry, path, e, fill=fill, **kw)
        if "skip" in r:
            skips[r["skip"]] += 1
            continue
        v = prior_close(vix, s0)
        rows.append({"entry": s0, "expiry": e, "vix": v, **r})
    return rows, skips


# --------------------------------------------------------------------------- statistics
def t_stat(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    sd = statistics.stdev(xs)
    return statistics.fmean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else None


def p_one(t: float | None, n: int) -> float:
    return 1.0 if t is None or n < 2 else 1.0 - core_stats.t_cdf(t, n - 1)


def midpoint(start: str, end: str) -> str:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return date.fromordinal((a.toordinal() + b.toordinal()) // 2).isoformat()


def score(rows: list[dict], skips: Counter, start: str, end: str) -> dict:
    nets = [r["net"] for r in rows]
    costs = [r["cost"] for r in rows]
    mid = midpoint(start, end)
    halves = {
        h: [r["net"] for r in rows if (r["entry"] < mid) == (h == "first")] for h in ("first", "second")
    }
    no_exit = skips.get("no exit mark", 0)
    entered = len(rows) + no_exit
    t = t_stat(nets)
    out = {
        "n": len(nets),
        "mean_net": round(statistics.fmean(nets), 2) if nets else None,
        "mean_gross": round(statistics.fmean(r["gross"] for r in rows), 2) if rows else None,
        "mean_cost": round(statistics.fmean(costs), 2) if costs else None,
        "mean_risk": round(statistics.fmean(r["risk"] for r in rows), 2) if rows else None,
        "t": None if t is None else round(t, 3),
        "p": p_one(t, len(nets)),
        "halves": {h: round(statistics.fmean(v), 2) if v else None for h, v in halves.items()},
        "halves_n": {h: len(v) for h, v in halves.items()},
        "no_exit_share": round(no_exit / entered, 3) if entered else None,
        "cost_stress": robustness.cost_stress(nets, costs, pass_stress=PASS_STRESS) if nets else None,
        "win_rate": round(sum(x > 0 for x in nets) / len(nets), 3) if nets else None,
        "mean_credit": round(statistics.fmean(r["credit"] for r in rows) * 100, 2) if rows else None,
    }
    if len(nets) < MIN_TRADES:
        out["verdict"], out["why"] = "no verdict", f"{len(nets)} trades < {MIN_TRADES}"
    elif entered and no_exit / entered > MAX_NO_EXIT_SHARE:
        out["verdict"], out["why"] = "no verdict", f"{no_exit} of {entered} entries had no exit mark"
    return out


def judge(test: dict) -> None:
    if test.get("verdict") == "no verdict":
        return
    checks = {
        "mean_positive": (test["mean_net"] or 0) > 0,
        "t_above_bar": (test["t"] or 0) > T_BAR,
        "both_halves_positive": all((x or 0) > 0 for x in test["halves"].values()),
        "survives_1_5x_costs": bool(test["cost_stress"] and test["cost_stress"]["survives"]),
    }
    test["checks"] = checks
    test["verdict"] = "pass" if all(checks.values()) else "fail"


def describe(rows: list[dict]) -> dict:
    """Description only, never the verdict: by side (drift), exit kind, VIX band, year, worst trades."""

    def m(xs):
        xs = [x for x in xs if x is not None]
        return {"n": len(xs), "mean": round(statistics.fmean(xs), 2) if xs else None}

    def split(key):
        g: dict = defaultdict(list)
        for r in rows:
            g[key(r)].append(r["net"])
        return {str(k): m(v) for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))}

    return {
        "sides_gross": {r: m([x["sides_gross"][r] for x in rows]) for r in ("P", "C")},
        "by_exit": split(lambda r: r["how"]),
        "by_vix_band": split(lambda r: vix_band(r["vix"]) if r["vix"] is not None else "unknown"),
        "by_year": split(lambda r: r["entry"][:4]),
        "worst_trades": [
            {"entry": r["entry"], "expiry": r["expiry"], "net": r["net"]}
            for r in sorted(rows, key=lambda r: r["net"])[:WORST_N]
        ],
    }


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start", default=DESIGN[0])
    ap.add_argument("--end", default=DESIGN[1])
    ap.add_argument(
        "--allow-holdout", action="store_true", help="an --end past the design window reads the holdout"
    )
    ap.add_argument(
        "--account", type=float, help="B13's size line, passed at run time (never stored in the repo)"
    )
    ap.add_argument("--json", type=Path, help="default: results/research/item59/study-<window>.json")
    args = ap.parse_args(argv)
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
    vix = vix_closes(home.data_dir("market-files") / "cboe" / "VIX.csv")

    by_fill = {f: trades(db, args.start, args.end, vix, fill=f) for f in FILLS}
    rows, skips = by_fill["working"]
    test = score(rows, skips, args.start, args.end)
    judge(test)
    out = {
        "licence": "derived from licensed vendor history: stays under ~/.cherrypick, never in the repo",
        "item": 59,
        "window": [args.start, args.end],
        "allow_holdout": args.allow_holdout,
        "verdict": test.get("verdict"),
        "test": test,
        "skips": dict(skips),
        "describe": describe(rows),
        "other_fills": {f: score(*by_fill[f], args.start, args.end) for f in FILLS if f != "working"},
        "planned_mde_B12": robustness.planned_mde([r["net"] for r in rows], len(rows)) if rows else None,
    }
    if test.get("verdict") == "pass":

        def mean_at(**over):
            rs, _ = trades(db, args.start, args.end, vix, fill="working", **over)
            return round(statistics.fmean(r["net"] for r in rs), 2) if rs else None

        rep = robustness.report(
            [r["net"] for r in rows],
            [r["cost"] for r in rows],
            means_by_setting={d: mean_at(short_delta=d) for d in (0.12, SHORT_DELTA, 0.20)},
            declared=SHORT_DELTA,
            sessions=len(rows),
            account=args.account,
            account_share=0.10 if args.account else None,
            pass_stress=PASS_STRESS,
        )
        out["robustness_B13"] = {
            **rep,
            "entry_dte_means": {d: mean_at(entry_dte=d) for d in (40, ENTRY_DTE, 50)},
            "target_means": {t: mean_at(target=t) for t in (0.40, TARGET, 0.60)},
        }

    target = args.json or (
        home.data_dir("backtest") / "results" / "research" / "item59" / f"study-{args.start}-{args.end}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    (target.parent / f"trades-{args.start}-{args.end}.json").write_text(
        json.dumps(rows, default=str), encoding="utf-8"
    )
    print(json.dumps({"verdict": out["verdict"], "written": str(target), "at": datetime.now().isoformat()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
