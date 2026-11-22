"""Item 108: credit spreads held across an earnings report: do they lose more of their risk than the rest?

The declaration is research/edge-hypotheses-plan.md section 108; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

It reads item 109's candidates (`candidates.json`, every condor its rule offered, per contract) and
report dates (`earnings.json`), so it runs after item 109's study and fetches nothing.

- `study`: the single-name candidates entered from 2020-02-01, split by whether a report falls inside
  their life, judged by RULE and cut at the declared split (`results.json`).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from cherrypick.core import progress as core_progress
from cherrypick.core.stats import stationary_indices

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The pass rule as data, fixed by the declaration before any run. Not the kit's three bars (this
#: compares two groups of one rule's trades), so the queue leaves its verdict to be recorded by hand.
RULE = {
    "kind": "group_difference",
    "statistic": "excess_loss",
    "max_excess": -0.03,
    "t_bar": -2.0,
    "min_n": 150,
    "min_names": 10,
    "holdout_min_n": 100,
    "block_months": 3,
    "resamples": 10_000,
    "seed": 108,
    "from": "2020-02-01",
    "splits": ["2023-07-01"],
}

#: Funds have no reports; they are item 109's `funds`, left out here.
FUNDS = ("SPY", "QQQ", "IWM", "GLD", "TLT", "SLV")
AFTER_CLOSE = "After market close"

#: Queued after item 109's study row (`--after`), which writes this item's inputs.
CHAIN = ({"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},)

PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no_verdict", "not_read"


def results_dir(item: str) -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / item
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------------------------------------
# Pure.


def spans(t: dict, reports: dict[str, list[list[str]]]) -> bool:
    """A report after the entry session (or on it, after the close) and on or before the expiry."""
    for d, when in reports.get(t["symbol"], []):
        if t["entry"] < d <= t["expiry"] or (d == t["entry"] and when == AFTER_CLOSE):
            return True
    return False


def days_to_report(t: dict, reports: dict) -> int | None:
    from datetime import date

    ds = [d for d, w in reports.get(t["symbol"], []) if t["entry"] <= d <= t["expiry"]]
    return (date.fromisoformat(min(ds)) - date.fromisoformat(t["entry"])).days if ds else None


def outcome(t: dict, key: str = "net") -> float:
    """Net P&L after costs over max loss at entry, per contract."""
    return t[key] / t["max_loss"]


def excess_loss(trades: list[dict], key: str = "net") -> dict:
    """The spanning trades' mean outcome less the others', within each name, weighted by each name's
    spanning count. A name with no trade in either group is left out."""
    by: dict[str, dict[bool, list[float]]] = defaultdict(lambda: {True: [], False: []})
    for t in trades:
        by[t["symbol"]][t["span"]].append(outcome(t, key))
    num = den = 0
    names = 0
    for groups in by.values():
        if groups[True] and groups[False]:
            n = len(groups[True])
            num += n * (statistics.fmean(groups[True]) - statistics.fmean(groups[False]))
            den += n
            names += 1
    return {
        "excess": num / den if den else None,
        "spanning": den,
        "names": names,
        "others": sum(len(g[False]) for g in by.values() if g[True] and g[False]),
    }


def bootstrap(trades: list[dict], rule: dict, *, resamples: int | None = None, key: str = "net") -> dict:
    """`excess_loss` with a standard error from a stationary block bootstrap over entry months in
    order, each month's trades drawn together."""
    est = excess_loss(trades, key)
    months = sorted({t["entry"][:7] for t in trades})
    by_month = defaultdict(list)
    for t in trades:
        by_month[t["entry"][:7]].append(t)
    if est["excess"] is None or len(months) < 6:
        return {**est, "t": None}
    rng = random.Random(rule["seed"])
    draws = []
    for _ in range(resamples or rule["resamples"]):
        idx = stationary_indices(len(months), rule["block_months"], rng)
        sample = [t for i in idx for t in by_month[months[i]]]
        x = excess_loss(sample, key)["excess"]
        if x is not None:
            draws.append(x)
    se = statistics.stdev(draws) if len(draws) > 1 else None
    return {**est, "se": se, "t": est["excess"] / se if se else None, "months": len(months)}


def judge(design: dict, holdout: dict, rule: dict) -> dict:
    d, h = dict(design), dict(holdout)
    if d["spanning"] < rule["min_n"] or d["names"] < rule["min_names"] or d.get("t") is None:
        d["verdict"] = NO_VERDICT
    else:
        d["verdict"] = PASS if d["excess"] <= rule["max_excess"] and d["t"] <= rule["t_bar"] else FAIL
    n_holdout = h.get("spanning", 0)
    if d["verdict"] != PASS:
        h = {"verdict": NOT_READ}
    elif h.get("excess") is None:
        h["verdict"] = NO_VERDICT
    else:
        h["verdict"] = PASS if h["excess"] < 0 else FAIL
        h["provisional"] = n_holdout < rule["holdout_min_n"]
    word = h["verdict"] if d["verdict"] == PASS else d["verdict"]
    arm = {"n": d["spanning"] + n_holdout, "parts": [d, h], "verdict": word}
    return {"arms": {"spanning": arm}, "verdict": {"spanning": word}}


def group_summary(ts: list[dict], key: str = "net") -> dict:
    if not ts:
        return {"n": 0}
    xs = sorted(outcome(t, key) for t in ts)
    k = max(1, math.ceil(0.05 * len(xs)))
    return {
        "n": len(xs),
        "mean": statistics.fmean(xs),
        "lost_half_or_more": sum(1 for x in xs if x <= -0.5) / len(xs),
        "es5": -statistics.fmean(xs[:k]),
        "entry_spread": statistics.fmean(t["entry_spread"] for t in ts),
    }


def study(cands: list[dict], reports: dict, *, resamples: int | None = None, rep=None) -> dict:
    trades = [
        {**t, "span": spans(t, reports)}
        for t in cands
        if t["symbol"] not in FUNDS and t["entry"] >= RULE["from"]
    ]
    split = RULE["splits"][0]
    parts = {
        "design": [t for t in trades if t["entry"] < split],
        "holdout": [t for t in trades if t["entry"] >= split],
    }
    if rep is not None:
        rep.split("parts", list(parts), unit="parts")
    stats = {}
    for name, ts in parts.items():
        stats[name] = bootstrap(ts, RULE, resamples=resamples)
        if rep is not None:
            rep.advance("parts", key=name, current=name)
    block = judge(stats["design"], stats["holdout"], RULE)

    def beside(ts: list[dict]) -> dict:
        span, other = [t for t in ts if t["span"]], [t for t in ts if not t["span"]]
        buckets = {"under_14": (0, 13), "14_to_28": (14, 28), "over_28": (29, 999)}
        by_days = {}
        for b, (lo, hi) in buckets.items():
            pick = [t for t in span if lo <= (days_to_report(t, reports) or -1) <= hi]
            by_days[b] = group_summary(pick)
        by_quarter = {
            q: excess_loss([t for t in ts if f"{t['entry'][:4]}-Q{(int(t['entry'][5:7]) - 1) // 3 + 1}" == q])
            for q in sorted({f"{t['entry'][:4]}-Q{(int(t['entry'][5:7]) - 1) // 3 + 1}" for t in ts})
        }
        return {
            "spanning": group_summary(span),
            "others": group_summary(other),
            "spanning_by_days_to_report": by_days,
            "stop_off": excess_loss(ts, "net_no_stop"),
            "by_quarter": by_quarter,
        }

    return {
        "rule": RULE,
        **block,
        "reported": {name: beside(ts) for name, ts in parts.items()},
        "trades": len(trades),
    }


def cmd_study(args) -> int:
    src = results_dir("item109")
    cands = json.loads((src / "candidates.json").read_text(encoding="utf-8"))
    reports = json.loads((src / "earnings.json").read_text(encoding="utf-8"))
    with core_progress.Reporter("earnings_span_item108-study") as rep:
        block = study(cands, reports, resamples=200 if args.smoke else None, rep=rep)
    doc = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 108,
        **block,
        "inputs": {"candidates": len(cands), "reported_names": len(reports)},
        "corrections": list(REPLAY_CORRECTIONS),
    }
    if args.smoke:
        doc = {**doc, "smoke": True, "verdict": "smoke"}
    out = (
        Path(args.out)
        if args.out
        else results_dir("item108") / ("smoke.json" if args.smoke else "results.json")
    )
    out.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(out), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    s = sub.add_parser("study")
    s.add_argument("--out", default=None)
    s.add_argument("--smoke", action="store_true", help="200 draws")
    s.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
