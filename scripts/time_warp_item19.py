"""Item 19, the Time Warp (docs/edge-hypotheses-plan.md): two out-of-the-money SPX flies bought at
15:50 and sold the next session, scored on two exit arms.

    python scripts/time_warp_item19.py [--start 2022-05-16] [--end 2024-12-31] [--allow-holdout]
        [--account N] [--json PATH]

Reads only `SPX-time-warp.db` (scripts/fetch_spx_time_warp.py), `SPX-60s.db`'s daily closes and
Cboe's published VIX history (the local market-files copy). Writes its result under the backtest data
dir (results/research/item19/), never into the repo: the inputs are licensed.

**The rule, as declared.** On session D at 15:50, with E the next trading day and `w` set by D-1's
VIX close (20 below 17, 30 to 32, 50 above), each side's body is the listed strike nearest spot
whose fly (long 1 / short 2 / long 1, wings `w` from the body) costs at most 5% of `w` at the working
price, walking out one strike at a time; every leg must be two-sided. On E, arm A sells a fly on the
host's trail (once above its debit, give back g of the gain from the high-water mark: 0.75 before
12:00, 0.50 to 14:00, 0.20 after) and arm B at the 10:30 mark; a fly not sold settles at E's close.

**The verdict**, per arm: the mean pair net per session above zero with a one-sided t above 2, Holm
across the two arms, both time-halves of the window positive, and the mean still positive at 1.5x
costs. Fewer than 100 entered sessions is no verdict. The holdout (past 2024-12-31) is refused
without --allow-holdout, and is read once, only after a design pass.
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
DEBIT_SHARE = 0.05
#: (VIX below, wing width). The last band has no ceiling.
WIDTH_BANDS = ((17.0, 20), (32.0, 30), (math.inf, 50))
#: The robustness ladder for "one band narrower and wider" (description only).
WIDTH_LADDER = (15, 20, 30, 50, 70)
#: (until HH:MM, give-back share of the gain from the high-water mark).
TRAIL = (("12:00", 0.75), ("14:00", 0.50), ("99:99", 0.20))
TRAIL_FROM = "09:35"
FIXED_EXIT = "10:30"
MIN_SESSIONS = 100
T_BAR = 2.0
ALPHA = 0.05
PASS_STRESS = 1.5
ARMS = ("trail", "fixed_1030")
FILLS = ("working", "mid", "mid_0.05")


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


def width_for(vix: float) -> int:
    return next(w for top, w in WIDTH_BANDS if vix < top)


def spx_closes(db: sqlite3.Connection) -> dict[str, tuple[float, str]]:
    rows = db.execute("SELECT trade_date, close, source FROM daily_closes WHERE symbol = 'SPX'")
    return {d: (c, s) for d, c, s in rows}


def hhmm(ts: str) -> str:
    return ts[11:16]


def load_session(db: sqlite3.Connection, day: str) -> tuple[str, dict, dict] | None:
    """(expiration, {hhmm: {(strike, right): quote}} for D's entry bars, the same for E's path)."""
    rows = db.execute(
        "SELECT expiration, leg, ts, strike, right, bid, ask, underlying FROM quotes WHERE day = ?", (day,)
    )
    exp = None
    legs: dict[str, dict] = {"entry": defaultdict(dict), "path": defaultdict(dict)}
    for e, leg, ts, k, r, bid, ask, und in rows:
        exp = e
        legs[leg][hhmm(ts)][(k, r)] = {"bid": bid, "ask": ask, "underlying": und}
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


def fly_legs(body: float, w: int, right: str) -> list[tuple[tuple[float, str], int]]:
    return [((body - w, right), 1), ((body, right), -2), ((body + w, right), 1)]


def price(chain: dict, legs: list, *, buy: bool, fill: str) -> float | None:
    """Per share, what the fly costs to buy (`buy`) or brings to sell. `working` is halfway between
    the mid and the natural fill; `mid_0.05` is the mid conceded by $0.05 a fly. None when a leg is
    not two-sided (entry) or has no ask (exit: a wing bid at zero sells for zero)."""
    qs = [(chain.get(k), n) for k, n in legs]
    if buy and not all(two_sided(q) for q, _ in qs):
        return None
    if not all(q and q["ask"] is not None for q, _ in qs):
        return None
    sign = 1 if buy else -1
    mid = sum(sign * n * ((q["bid"] or 0) + q["ask"]) / 2 for q, n in qs)
    natural = sum(sign * n * (q["ask"] if sign * n > 0 else (q["bid"] or 0)) for q, n in qs)
    if fill == "mid":
        p = mid
    elif fill == "mid_0.05":
        p = mid + 0.05
    else:
        p = (mid + natural) / 2
    # The buy side's price is a debit; the sell side's is what is received, so flip it back.
    return round(p if buy else max(0.0, -p), 4)


def intrinsic(legs: list, s: float) -> tuple[float, int]:
    """The fly's settlement value per share, and how many distinct legs finish in the money."""
    value, itm = 0.0, 0
    for (k, r), n in legs:
        v = max(0.0, s - k) if r == "C" else max(0.0, k - s)
        value += n * v
        itm += v > 0
    return round(value, 4), itm


def pick_body(chain: dict, spot: float, w: int, right: str, share: float, fill: str) -> tuple | None:
    """The listed strike nearest spot, walking away from it, whose fly costs at most share x w."""
    strikes = sorted({k for k, r in chain if r == right})
    bodies = [k for k in strikes if k > spot] if right == "C" else [k for k in reversed(strikes) if k < spot]
    for body in bodies:
        legs = fly_legs(body, w, right)
        debit = price(chain, legs, buy=True, fill=fill)
        if debit is not None and 0 < debit <= share * w:
            return body, legs, debit
    return None


# --------------------------------------------------------------------------- exits
def give_back(t: str) -> float:
    return next(g for until, g in TRAIL if t < until)


def exit_trail(path: dict, legs: list, debit: float, fill: str) -> tuple[float, str] | None:
    hwm = None
    for t in sorted(path):
        if t < TRAIL_FROM:
            continue
        v = price(path[t], legs, buy=False, fill=fill)
        if v is None:
            continue
        if hwm is None:
            if v > debit:
                hwm = v
            continue
        hwm = max(hwm, v)
        if v < debit + (1 - give_back(t)) * (hwm - debit):
            return v, t
    return None


def exit_fixed(path: dict, legs: list, fill: str) -> tuple[float, str] | None:
    chain = path.get(FIXED_EXIT)
    v = None if chain is None else price(chain, legs, buy=False, fill=fill)
    return None if v is None else (v, FIXED_EXIT)


def fly_result(legs: list, debit: float, sale: tuple | None, settle_at: float) -> dict:
    """Whole-position dollars at one contract: gross, fees, settlement, net."""
    open_fee = fees.ic_open_fee("SPX", 1, legs=4, sell_legs=2, ndigits=4)
    if sale is not None:
        value, when = sale
        close_fee, settle_fee, how = (
            fees.ic_close_fee("SPX", 1, legs=4, sell_legs=2, ndigits=4),
            0.0,
            "closed",
        )
    else:
        value, itm = intrinsic(legs, settle_at)
        close_fee, settle_fee, how, when = 0.0, fees.ic_expire_fee(itm), "settled", "16:00"
    gross = round((value - debit) * 100, 2)
    cost = round(open_fee + close_fee + settle_fee, 2)
    return {
        "debit": debit,
        "exit": value,
        "how": how,
        "when": when,
        "gross": gross,
        "cost": cost,
        "net": round(gross - cost, 2),
    }


# --------------------------------------------------------------------------- sessions
def run_session(
    entry: dict, path: dict, w: int, settle_at: float, *, at: str, share: float, fill: str
) -> dict:
    chain = entry.get(at)
    if not chain:
        return {"skip": "no entry quote"}
    spot = next(iter(chain.values()))["underlying"]
    out: dict = {"spot": spot, "w": w, "sides": {}}
    for right in ("C", "P"):
        pick = pick_body(chain, spot, w, right, share, fill)
        if pick is None:
            continue
        body, legs, debit = pick
        out["sides"][right] = {
            "body": body,
            "arms": {
                "trail": fly_result(legs, debit, exit_trail(path, legs, debit, fill), settle_at),
                "fixed_1030": fly_result(legs, debit, exit_fixed(path, legs, fill), settle_at),
            },
        }
    if not out["sides"]:
        return {"skip": "no affordable body"}
    for arm in ARMS:
        out[arm] = {
            k: round(sum(s["arms"][arm][k] for s in out["sides"].values()), 2) for k in ("net", "cost")
        }
    out["debit"] = round(sum(next(iter(s["arms"].values()))["debit"] for s in out["sides"].values()), 4)
    return out


def sessions(
    tw: sqlite3.Connection, days: list[str], vix: dict, closes: dict, **kw
) -> tuple[list[dict], Counter]:
    rows, skips = [], Counter()
    width_shift = kw.pop("width_shift", 0)
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
        w = width_for(v)
        if width_shift:
            i = WIDTH_LADDER.index(w) + width_shift
            w = WIDTH_LADDER[max(0, min(i, len(WIDTH_LADDER) - 1))]
        r = run_session(entry, path, w, settle[0], **kw)
        if "skip" in r:
            skips[r["skip"]] += 1
            continue
        rows.append({"day": day, "expiration": exp, "vix": v, "settle_source": settle[1], **r})
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
        "mean_debit": round(statistics.fmean(r["debit"] for r in rows) * 100, 2) if rows else None,
        "mean_net_over_debit": round(statistics.fmean(r[arm]["net"] / (r["debit"] * 100) for r in rows), 4)
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
    """Description only, never the verdict: by side, by VIX band, by settlement source, how flies end."""
    by_side: dict = defaultdict(list)
    ends = Counter()
    for r in rows:
        for side, s in r["sides"].items():
            by_side[side].append(s["arms"][arm]["net"])
            ends[s["arms"][arm]["how"]] += 1
    by_band: dict = defaultdict(list)
    by_src: dict = defaultdict(list)
    for r in rows:
        by_band[r["w"]].append(r[arm]["net"])
        by_src[r["settle_source"]].append(r[arm]["net"])

    def m(xs):
        return {"n": len(xs), "mean": round(statistics.fmean(xs), 2) if xs else None}

    return {
        "by_side": {k: m(v) for k, v in by_side.items()},
        "by_width": {str(k): m(v) for k, v in sorted(by_band.items())},
        "by_settlement_source": {k: m(v) for k, v in by_src.items()},
        "fly_endings": dict(ends),
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
    ap.add_argument("--json", type=Path, help="default: results/research/item19/<window>.json")
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

    results: dict = {}
    for fill in FILLS:
        rows, skips = sessions(tw, days, vix, closes, at=ENTRY_TIME, share=DEBIT_SHARE, fill=fill)
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
        "item": 19,
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
            means = {}
            for share in (0.04, DEBIT_SHARE, 0.06):
                rows, _ = sessions(tw, days, vix, closes, at=ENTRY_TIME, share=share, fill="working")
                means[share] = statistics.fmean(r[a]["net"] for r in rows) if rows else None
            times = {}
            for at in ("15:45", ENTRY_TIME, "15:55"):
                rows, _ = sessions(tw, days, vix, closes, at=at, share=DEBIT_SHARE, fill="working")
                times[at] = round(statistics.fmean(r[a]["net"] for r in rows), 2) if rows else None
            widths = {}
            for shift in (-1, 0, 1):
                rows, _ = sessions(
                    tw, days, vix, closes, at=ENTRY_TIME, share=DEBIT_SHARE, fill="working", width_shift=shift
                )
                widths[shift] = round(statistics.fmean(r[a]["net"] for r in rows), 2) if rows else None
            rep = robustness.report(
                [r[a]["net"] for r in working_rows],
                [r[a]["cost"] for r in working_rows],
                means_by_setting=means,
                declared=DEBIT_SHARE,
                sessions=len(working_rows),
                account=args.account,
                account_share=0.10 if args.account else None,
                pass_stress=PASS_STRESS,
            )
            b13[a] = {**rep, "entry_time_means": times, "width_shift_means": widths}
        out["robustness_B13"] = b13

    target = args.json or (
        home.data_dir("backtest") / "results" / "research" / "item19" / f"study-{args.start}-{args.end}.json"
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
