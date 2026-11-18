"""Item 79: item 64's four bullish structures re-judged on the three bars, over all their history.

The declaration is research/edge-hypotheses-plan.md section 79; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

A retest of item 64, said out loud: the same trades (a call backspread, two front call credit spreads
with a back-month long call, a 1-3-2 put broken-wing butterfly, and the 1-3-2 calendarized, on ten
slow large caps), built by item 64's own `trades` over its stored chain paths. Item 64 judged each
against the same delta held in shares; shares are not the suite's control any more (the capital-
efficiency baseline is the delta financed on the same capital). This judges each structure through
the study kit's `rule` on the three bars:

- **capital:** each cycle's return on the capital the broker holds, less the 3-month bill over the
  trade's days, above zero;
- **tail:** the 99th-percentile one-year drawdown of the cycles under one year of mean return;
- **delta:** the return less the structure's entry delta financed on the same capital.

One run from item 64's first entries through the last cycle closed in the store, judged whole: the
tail bar's bootstrap needs 100 cycles, which item 64's design window (about 70) cannot reach alone, so
a split at item 64's boundary would leave the design part without a verdict and the rest unread. Item
64 read the cycles before 2022-01-01, so the verdict is provisional by declaration; the cycles from
then on (item 64's holdout, never read) are reported apart as the fresh evidence. Loading item 64's
script is the point of a retest of its trades, not a shared piece the kit should hold.

- `study`: writes `{results}/results.json`, the kit's verdict block per structure, with item 64's
  data rule applied: more than 5% of a structure's entries with no exit mark is no verdict for it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from cherrypick.core import progress, treasury

from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as kit_rule

_HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


i64 = _load("backspread_bwb_item64")

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: t of 2.3 keeps a Holm step across the four structures from binding: at 100 cycles or more its
#: one-sided p is under 0.0125, Holm's strictest bar for four. 100 cycles is the tail bar's own floor.
RULE = {
    "t_bar": 2.3,
    "min_n": 100,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 79},
    "delta": True,
    "splits": [],
}
#: Item 64's design/holdout boundary: the cycles from it on were never read, reported apart.
FRESH_FROM = "2022-01-01"

CHAIN = ({"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},)

#: Item 64's first planned entry; the last planned entry whose cycle has closed in the store (the
#: 2026-11-20 front, entered about 2026-10-06, time-exited by 2026-10-30).
START = "2016-01-01"
END = "2026-10-31"
STRUCTURES = i64.STRUCTURES
MAX_NO_EXIT_SHARE = i64.MAX_NO_EXIT_SHARE
RATE_STALE_DAYS = 10


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item79"
    d.mkdir(parents=True, exist_ok=True)
    return d


def days_held(t: dict) -> int:
    return max((date.fromisoformat(t["exit"]) - date.fromisoformat(t["entry"])).days, 1)


def cycle_rows(trades: list[dict], rate) -> list[dict]:
    """Per front expiry, the equal-weight mean over the names that entered of each trade's return on
    its capital, and of its entry delta financed on the same capital (item 64's `shares` is that delta
    times the forward's change; the capital earns the bill): the kit's rows, one cycle each."""
    by: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by[t["front"]].append(t)
    out = []
    for f in sorted(by):
        ts = by[f]
        first = min(t["entry"] for t in ts)
        r = rate(first)
        out.append({
            "date": first,
            "front": f,
            "net": statistics.fmean(t["net"] / t["capital"] for t in ts),
            "bp": 1.0,
            "days": statistics.fmean(days_held(t) for t in ts),
            "rate": r,
            "financed": statistics.fmean(
                (t["shares"] + ck.bill_on(t["capital"], r, days_held(t))) / t["capital"] for t in ts
            ),
            "names": len(ts),
        })  # fmt: skip
    return out


def no_exit_share(trades: list[dict], skips: dict) -> float:
    """Item 64's data rule: entries without an exit mark over all entries that priced."""
    missing = skips.get("no exit mark", 0)
    total = len(trades) + missing
    return missing / total if total else 0.0


def judge(trades: list[dict], skips: dict[str, dict], rate) -> dict:
    by = {k: [t for t in trades if t["structure"] == k] for k in STRUCTURES}
    rows = {k: cycle_rows(v, rate) for k, v in by.items()}
    block = kit_rule.evaluate(RULE, rows)
    data = {}
    for k in STRUCTURES:
        share = no_exit_share(by[k], skips.get(k, {}))
        data[k] = {"no_exit_share": round(share, 4), "within_rule": share <= MAX_NO_EXIT_SHARE}
        if share > MAX_NO_EXIT_SHARE:
            block["arms"][k]["verdict"] = block["verdict"][k] = ck.NO_VERDICT
    reported = {k: {"read_by_item64": mean_excess([r for r in rows[k] if r["date"] < FRESH_FROM]),
                    "fresh": mean_excess([r for r in rows[k] if r["date"] >= FRESH_FROM])}
                for k in STRUCTURES}  # fmt: skip
    return {**block, "data_rule": data, "reported": reported}


def mean_excess(rows: list[dict]) -> dict:
    """Mean return over the bill, and over the financed delta, of some cycles: reported, never judged."""
    if not rows:
        return {"cycles": 0}
    return {
        "cycles": len(rows),
        "over_bill": statistics.fmean(r["net"] - ck.bill_on(1.0, r["rate"], r["days"]) for r in rows),
        "over_financed_delta": statistics.fmean(r["net"] - r["financed"] for r in rows),
    }


def rate_reader():
    seen: dict[str, float] = {}

    def rate(session: str) -> float:
        if session not in seen:
            got = treasury.yield_before(session, "3m")
            if got is None or (date.fromisoformat(session) - got[0]).days > RATE_STALE_DAYS:
                raise SystemExit(f"no Treasury 3-month yield within {RATE_STALE_DAYS} days before {session}")
            seen[session] = got[1] / 100.0
        return seen[session]

    return rate


def cmd_study(args) -> int:
    from cherrypick.backtest import paths
    from cherrypick.backtest import settings as _settings

    vendor = paths.ensure_vendor_dir(_settings.load())
    db = sqlite3.connect(f"file:{vendor / 'eod_chains.db'}?mode=ro", uri=True)
    try:
        with progress.Reporter("bullish_structures_bars_item79-study") as rep:
            trades, skips = i64.trades(db, START, END, reporter=rep)
    finally:
        db.close()
    skips = {k: dict(v) for k, v in skips.items()}
    doc = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 79,
        "retest_of": 64,
        "window": [START, END],
        "provisional_part": "before 2022-01-01 (item 64's design window, read by item 64)",
        **judge(trades, skips, rate_reader()),
        "skips": skips,
        "trades": len(trades),
        "corrections": list(REPLAY_CORRECTIONS),
    }
    path = Path(args.out) if args.out else out_dir() / "results.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("study")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_study)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
