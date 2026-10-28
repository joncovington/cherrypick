"""Item 22, the overnight condor (docs/edge-hypotheses-plan.md): a 1DTE SPX iron condor sold at 15:50
and bought back the next morning, scored on two exit arms.

    python scripts/overnight_condor_item22.py [--start 2022-05-16] [--end 2024-12-31] [--allow-holdout]
        [--account N] [--json PATH]

Reads only `SPX-time-warp.db` (scripts/fetch_spx_time_warp.py), `SPX-60s.db`'s daily closes and
Cboe's published VIX history (the local market-files copy). Writes its result under the backtest data
dir (results/research/item22/), never into the repo: the inputs are licensed.

**The rule, as declared.** On session D at 15:50, with E the next trading day, sell the SPXW condor
expiring E: the short call is the call above spot whose delta is nearest +0.10, the short put the
put below spot whose delta is nearest -0.10, each wing 25 points further out, one contract a side.
Every leg must be two-sided; a session missing a side is skipped and counted. On E, arm A buys the
condor back at the 09:35 mark (the first mark to 09:50 when 09:35 has no quote); arm B settles it at
E's close. Nothing is managed in between.

**The verdict**, per arm: the mean net per session above zero with a one-sided t above 2, Holm
across the two arms, both time-halves of the window positive, and the mean still positive at 1.5x
costs. Fewer than 100 entered sessions is no verdict. The holdout (past 2024-12-31) is refused
without --allow-holdout; the user asked for it to be run after the design whatever the design shows,
and it counts toward a pass only after a design pass.
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

from cherrypick.backtest import paths
from cherrypick.backtest import settings as _settings
from cherrypick.core import fees, home, robustness
from cherrypick.core import stats as core_stats

DESIGN = ("2022-05-16", "2024-12-31")
HOLDOUT_END = "2026-10-23"
ENTRY_TIME = "15:50"
SHORT_DELTA = 0.10
WING = 25
#: Arm A buys back at the first of these marks that quotes every leg.
BUYBACK_MARKS = ("09:35", "09:40", "09:45", "09:50")
#: VIX bands for the description (D-1's close), never a filter.
VIX_BANDS = (15.0, 20.0, 25.0, math.inf)
MIN_SESSIONS = 100
T_BAR = 2.0
ALPHA = 0.05
PASS_STRESS = 1.5
ARMS = ("buyback_0935", "settle")
FILLS = ("working", "mid", "mid_0.05")
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


def spx_closes(db: sqlite3.Connection) -> dict[str, tuple[float, str]]:
    rows = db.execute("SELECT trade_date, close, source FROM daily_closes WHERE symbol = 'SPX'")
    return {d: (c, s) for d, c, s in rows}


def hhmm(ts: str) -> str:
    return ts[11:16]


def load_session(db: sqlite3.Connection, day: str) -> tuple[str, dict, dict] | None:
    """(expiration, {hhmm: {(strike, right): quote}} for D's entry bars, the same for E's path)."""
    rows = db.execute(
        "SELECT expiration, leg, ts, strike, right, bid, ask, delta, underlying FROM quotes WHERE day = ?",
        (day,),
    )
    exp = None
    legs: dict[str, dict] = {"entry": defaultdict(dict), "path": defaultdict(dict)}
    for e, leg, ts, k, r, bid, ask, delta, und in rows:
        exp = e
        legs[leg][hhmm(ts)][(k, r)] = {"bid": bid, "ask": ask, "delta": delta, "underlying": und}
    if exp is None:
        return None
    return exp, legs["entry"], legs["path"]


# --------------------------------------------------------------------------- pricing
def two_sided(q: dict | None) -> bool:
    return (
        bool(q)
        and q["bid"] is not None
        and q["ask"] is not None
        and q["ask"] >= q["bid"] >= 0
        and q["ask"] > 0
    )


def side_legs(short: float, wing: int, right: str) -> list[tuple[tuple[float, str], int]]:
    """One vertical of the condor: short the inner strike, long the wing further out of the money."""
    far = short + wing if right == "C" else short - wing
    return [((short, right), -1), ((far, right), 1)]


def price(chain: dict, legs: list, *, sell: bool, fill: str) -> float | None:
    """Per share, what the legs bring when sold to open (`sell`) or cost to buy back. `working` is
    halfway between the mid and the natural fill; `mid_0.05` is the mid conceded by $0.05 a
    structure. None when any leg is not two-sided."""
    qs = [(chain.get(k), n) for k, n in legs]
    if not all(two_sided(q) for q, _ in qs):
        return None
    # Legs are signed as held (short -1, long +1), so the holder's value is negative and its size is
    # both the credit the sale brings and the debit the buy-back costs. Only the natural fill differs:
    # the sale hits the short's bid and the long's ask, the buy-back the short's ask and the long's bid.
    mid = -sum(n * (q["bid"] + q["ask"]) / 2 for q, n in qs)
    if sell:
        natural = -sum(n * (q["bid"] if n < 0 else q["ask"]) for q, n in qs)
    else:
        natural = -sum(n * (q["ask"] if n < 0 else q["bid"]) for q, n in qs)
    if fill == "mid":
        p = mid
    elif fill == "mid_0.05":
        p = mid - 0.05 if sell else mid + 0.05
    else:
        p = (mid + natural) / 2
    return round(max(0.0, p) if sell else p, 4)


def intrinsic(legs: list, s: float) -> tuple[float, int]:
    """What the legs are worth to their holder at settlement per share (a short condor's holder owes
    it), and how many distinct legs finish in the money."""
    value, itm = 0.0, 0
    for (k, r), n in legs:
        v = max(0.0, s - k) if r == "C" else max(0.0, k - s)
        value += n * v
        itm += v > 0
    return round(value, 4), itm


def pick_short(chain: dict, spot: float, right: str, target: float) -> float | None:
    """The out-of-the-money strike whose delta is nearest `target` in size, among two-sided quotes."""
    best = None
    for (k, r), q in chain.items():
        if r != right or q.get("delta") is None or not two_sided(q):
            continue
        if (right == "C" and k <= spot) or (right == "P" and k >= spot):
            continue
        gap = abs(abs(q["delta"]) - target)
        if best is None or gap < best[0]:
            best = (gap, k)
    return None if best is None else best[1]


# --------------------------------------------------------------------------- exits
def exit_buyback(path: dict, legs: list, fill: str, marks: tuple = BUYBACK_MARKS) -> tuple[float, str] | None:
    for t in marks:
        chain = path.get(t)
        v = None if chain is None else price(chain, legs, sell=False, fill=fill)
        if v is not None:
            return v, t
    return None


def side_result(legs: list, credit: float, buyback: tuple | None, settle_at: float) -> dict:
    """Whole-position dollars at one contract for one vertical: gross, fees, settlement, net. The
    condor's fees are charged once; a vertical carries half of each four-leg fee."""
    open_fee = fees.ic_open_fee("SPX", 1, legs=4, sell_legs=2, ndigits=4) / 2
    if buyback is not None:
        value, when = buyback
        close_fee, settle_fee, how = (
            fees.ic_close_fee("SPX", 1, legs=4, sell_legs=2, ndigits=4) / 2,
            0.0,
            "closed",
        )
    else:
        owed, itm = intrinsic(legs, settle_at)
        # The sale's legs are signed -1 short / +1 long, so `owed` is the holder's negative value.
        value, close_fee, settle_fee, how, when = -owed, 0.0, fees.ic_expire_fee(itm), "settled", "16:00"
    gross = round((credit - value) * 100, 2)
    cost = round(open_fee + close_fee + settle_fee, 2)
    return {
        "credit": credit,
        "exit": value,
        "how": how,
        "when": when,
        "gross": gross,
        "cost": cost,
        "net": round(gross - cost, 2),
    }


# --------------------------------------------------------------------------- sessions
def run_session(
    entry: dict,
    path: dict,
    settle_at: float,
    *,
    at: str,
    delta: float,
    wing: int,
    fill: str,
    buyback: tuple = BUYBACK_MARKS,
) -> dict:
    chain = entry.get(at)
    if not chain:
        return {"skip": "no entry quote"}
    spot = next(iter(chain.values()))["underlying"]
    out: dict = {"spot": spot, "sides": {}}
    for right in ("C", "P"):
        short = pick_short(chain, spot, right, delta)
        if short is None:
            return {"skip": f"no {right} short"}
        legs = side_legs(short, wing, right)
        credit = price(chain, legs, sell=True, fill=fill)
        if credit is None:
            return {"skip": f"{right} wing not two-sided"}
        out["sides"][right] = {
            "short": short,
            "delta": chain[(short, right)]["delta"],
            "arms": {
                "buyback_0935": side_result(legs, credit, exit_buyback(path, legs, fill, buyback), settle_at),
                "settle": side_result(legs, credit, None, settle_at),
            },
        }
    for arm in ARMS:
        out[arm] = {
            k: round(sum(s["arms"][arm][k] for s in out["sides"].values()), 2) for k in ("net", "cost")
        }
        out[arm]["when"] = out["sides"]["C"]["arms"][arm]["when"]
    out["credit"] = round(sum(next(iter(s["arms"].values()))["credit"] for s in out["sides"].values()), 4)
    return out


def closure_kind(day: str, expiration: str) -> str:
    """One night, or a weekend or holiday closure (more than one calendar day to the expiry)."""
    return "weeknight" if (date.fromisoformat(expiration) - date.fromisoformat(day)).days == 1 else "weekend"


def sessions(
    tw: sqlite3.Connection, days: list[str], vix: dict, closes: dict, **kw
) -> tuple[list[dict], Counter]:
    rows, skips = [], Counter()
    for day in days:
        loaded = load_session(tw, day)
        if loaded is None:
            skips["not fetched"] += 1
            continue
        exp, entry, path = loaded
        v = prior_close(vix, day)
        settle = closes.get(exp)
        if v is None or settle is None:
            skips["no VIX or settlement close"] += 1
            continue
        r = run_session(entry, path, settle[0], **kw)
        if "skip" in r:
            skips[r["skip"]] += 1
            continue
        rows.append(
            {
                "day": day,
                "expiration": exp,
                "closure": closure_kind(day, exp),
                "vix": v,
                "settle_source": settle[1],
                **r,
            }
        )
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


def score(rows: list[dict], arm: str, start: str, end: str) -> dict:
    nets = [r[arm]["net"] for r in rows]
    costs = [r[arm]["cost"] for r in rows]
    mid = midpoint(start, end)
    halves = {
        h: [r[arm]["net"] for r in rows if (r["day"] < mid) == (h == "first")] for h in ("first", "second")
    }
    t = t_stat(nets)
    stress = robustness.cost_stress(nets, costs, pass_stress=PASS_STRESS)
    out = {
        "n": len(nets),
        "mean_net": round(statistics.fmean(nets), 2) if nets else None,
        "t": None if t is None else round(t, 3),
        "p": p_one(t, len(nets)),
        "halves": {h: round(statistics.fmean(v), 2) if v else None for h, v in halves.items()},
        "halves_n": {h: len(v) for h, v in halves.items()},
        "cost_stress": stress,
        "win_rate": round(sum(x > 0 for x in nets) / len(nets), 3) if nets else None,
        "mean_credit": round(statistics.fmean(r["credit"] for r in rows) * 100, 2) if rows else None,
        "mean_net_over_credit": round(statistics.fmean(r[arm]["net"] / (r["credit"] * 100) for r in rows), 4)
        if rows
        else None,
    }
    if len(nets) < MIN_SESSIONS:
        out["verdict"] = "no verdict"
        out["why"] = f"{len(nets)} entered sessions < {MIN_SESSIONS}"
    return out


def judge(tests: dict[str, dict]) -> None:
    holm = core_stats.holm({k: v["p"] for k, v in tests.items()}, alpha=ALPHA)
    for k, v in tests.items():
        v["holm_ok"] = holm[k]
        if v.get("verdict") == "no verdict":
            continue
        checks = {
            "mean_positive": (v["mean_net"] or 0) > 0,
            "t_above_bar": (v["t"] or 0) > T_BAR,
            "holm": v["holm_ok"],
            "both_halves_positive": all((x or 0) > 0 for x in v["halves"].values()),
            "survives_1_5x_costs": v["cost_stress"]["survives"],
        }
        v["checks"] = checks
        v["verdict"] = "pass" if all(checks.values()) else "fail"


def describe(rows: list[dict], arm: str) -> dict:
    """Description only, never the verdict: by side, by closure (the mechanism's own split), by VIX
    band, by settlement source, the worst sessions, how positions end."""
    by_side: dict = defaultdict(list)
    ends = Counter()
    for r in rows:
        for side, s in r["sides"].items():
            by_side[side].append(s["arms"][arm]["net"])
            ends[s["arms"][arm]["how"]] += 1
    by_closure: dict = defaultdict(list)
    by_band: dict = defaultdict(list)
    by_src: dict = defaultdict(list)
    for r in rows:
        by_closure[r["closure"]].append(r[arm]["net"])
        by_band[vix_band(r["vix"])].append(r[arm]["net"])
        by_src[r["settle_source"]].append(r[arm]["net"])

    def m(xs):
        return {"n": len(xs), "mean": round(statistics.fmean(xs), 2) if xs else None}

    worst = sorted(rows, key=lambda r: r[arm]["net"])[:WORST_N]
    return {
        "by_side": {k: m(v) for k, v in by_side.items()},
        "by_closure": {k: m(v) for k, v in sorted(by_closure.items())},
        "by_vix_band": {k: m(v) for k, v in sorted(by_band.items())},
        "by_settlement_source": {k: m(v) for k, v in by_src.items()},
        "worst_sessions": [{"day": r["day"], "net": r[arm]["net"]} for r in worst],
        "side_endings": dict(ends),
    }


# --------------------------------------------------------------------------- main
def trading_days(tw: sqlite3.Connection, start: str, end: str) -> list[str]:
    return [
        d
        for (d,) in tw.execute(
            "SELECT DISTINCT day FROM fetched WHERE day BETWEEN ? AND ? ORDER BY day", (start, end)
        )
    ]


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
    ap.add_argument("--json", type=Path, help="default: results/research/item22/<window>.json")
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
    tw = sqlite3.connect(f"file:{vendor / 'SPX-time-warp.db'}?mode=ro", uri=True)
    spx = sqlite3.connect(f"file:{vendor / 'SPX-60s.db'}?mode=ro", uri=True)
    vix = vix_closes(home.data_dir("market-files") / "cboe" / "VIX.csv")
    closes = spx_closes(spx)
    days = trading_days(tw, args.start, args.end)
    declared = {"at": ENTRY_TIME, "delta": SHORT_DELTA, "wing": WING}

    results: dict = {}
    for fill in FILLS:
        rows, skips = sessions(tw, days, vix, closes, fill=fill, **declared)
        tests = {arm: score(rows, arm, args.start, args.end) for arm in ARMS}
        if fill == "working":
            judge(tests)
        results[fill] = {
            "tests": tests,
            "skips": dict(skips),
            "describe": {a: describe(rows, a) for a in ARMS},
        }
        if fill == "working":
            working_rows = rows

    verdict = results["working"]["tests"]
    out = {
        "licence": "derived from licensed vendor history: stays under ~/.cherrypick, never in the repo",
        "item": 22,
        "window": [args.start, args.end],
        "allow_holdout": args.allow_holdout,
        "sessions_in_window": len(days),
        "verdict": {a: verdict[a].get("verdict") for a in ARMS},
        "tests": verdict,
        "skips": results["working"]["skips"],
        "describe": results["working"]["describe"],
        "other_fills": {f: results[f]["tests"] for f in FILLS if f != "working"},
        "planned_mde_B12": {
            a: robustness.planned_mde([r[a]["net"] for r in working_rows], len(working_rows)) for a in ARMS
        },
    }
    passing = [a for a in ARMS if verdict[a].get("verdict") == "pass"]
    if passing:
        b13 = {}
        for a in passing:

            def mean_at(a=a, **over):
                rows, _ = sessions(tw, days, vix, closes, fill="working", **{**declared, **over})
                return round(statistics.fmean(r[a]["net"] for r in rows), 2) if rows else None

            deltas = {d: mean_at(delta=d) for d in (0.05, SHORT_DELTA, 0.15)}
            rep = robustness.report(
                [r[a]["net"] for r in working_rows],
                [r[a]["cost"] for r in working_rows],
                means_by_setting=deltas,
                declared=SHORT_DELTA,
                sessions=len(working_rows),
                account=args.account,
                account_share=0.10 if args.account else None,
                pass_stress=PASS_STRESS,
            )
            b13[a] = {
                **rep,
                "entry_time_means": {t: mean_at(at=t) for t in ("15:45", ENTRY_TIME, "15:55")},
                "wing_means": {w: mean_at(wing=w) for w in (15, WING, 50)},
                "buyback_means": {
                    m[0]: mean_at(buyback=m) for m in (BUYBACK_MARKS, ("09:45", "09:50"), ("10:30",))
                },
            }
        out["robustness_B13"] = b13

    target = args.json or (
        home.data_dir("backtest") / "results" / "research" / "item22" / f"study-{args.start}-{args.end}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    (target.parent / f"sessions-{args.start}-{args.end}.json").write_text(
        json.dumps(working_rows, default=str), encoding="utf-8"
    )
    print(json.dumps({"verdict": out["verdict"], "written": str(target), "at": datetime.now().isoformat()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
