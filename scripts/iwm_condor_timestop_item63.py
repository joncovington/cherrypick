"""Item 63, a 7-day time stop on item 59's condor (docs/edge-hypotheses-plan.md): every cycle item 59
enters, run twice on the same entry and the same daily marks, once under item 59's exit and once
with a time stop that closes the condor 7 days after entry when the half-credit target has not come.

    python scripts/iwm_condor_timestop_item63.py [--start 2016-01-01] [--end 2021-12-31]
        [--allow-holdout] [--json PATH]

Reads what item 59 reads (`eod_chains.db`, IWM's chains and each monthly expiry's daily path, and
Cboe's VIX history), through item 59's own script, so the entry, strikes, fills and fees cannot
drift from it. Writes under the backtest data dir (results/research/item63/), never into the repo:
the inputs are licensed.

**The two arms.**
- `base`: item 59 unchanged. Close on the first session whose buy-back costs half the credit or
  less, else on the first session 21 days or fewer from the expiry.
- `stop7`: the same half-credit target, else close on the first priced session 7 or more calendar
  days after entry. Item 59's 21-day exit cannot come first: the entry is 42 to 48 days out.

**The verdict**, all must hold, on the trades both arms closed:
- the paired difference (stop7 net less base net, per trade) has a mean above zero with a one-sided
  t above 2;
- that mean is above zero in each time-half of the window;
- stop7's own mean net is still positive at 1.5x costs (a better loser is not a trade).
Fewer than 50 paired trades, or more than 5% of entries without an exit mark in either arm, is no
verdict. The holdout (entries after 2021-12-31) is refused without --allow-holdout, and the
declaration reads it only after a design pass.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from cherrypick.backtest import paths
from cherrypick.backtest import settings as _settings
from cherrypick.core import fees, home, robustness

_ITEM59 = Path(__file__).resolve().parent / "iwm_condor_item59.py"


def _load_item59():
    spec = importlib.util.spec_from_file_location("iwm_condor_item59", _ITEM59)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ic = _load_item59()

DESIGN = ic.DESIGN
STOP_DAYS = 7
NEIGHBOUR_STOPS = (5, 10, 14)
ARMS = ("base", "stop7")


# --------------------------------------------------------------------------- one trade
def run_stop(
    entry,
    path: list,
    expiry: str,
    *,
    fill: str,
    entry_session: str,
    stop_days: int = STOP_DAYS,
    target: float = ic.TARGET,
) -> dict:
    """Item 59's trade with a time stop: the half-credit target, else the first priced session
    `stop_days` or more calendar days after `entry_session`. The same legs, fill and fees as item 59."""
    legs = ic.pick_condor(entry)
    if legs is None:
        return {"skip": "no condor strikes"}
    credit = ic.price(entry, legs, fill)
    if credit is None or credit <= 0:
        return {"skip": "no positive credit"}
    for session, chain in path:
        debit = ic.price(chain, legs, fill, close=True)
        if debit is None:
            continue
        held = ic.days_between(entry_session, session)
        how = "target" if debit <= target * credit else ("time stop" if held >= stop_days else None)
        if how is None:
            continue
        gross = round((credit - debit) * 100, 2)
        cost = round(
            fees.ic_open_fee(ic.SYMBOL, 1, legs=4, sell_legs=2)
            + fees.ic_close_fee(ic.SYMBOL, 1, legs=4, sell_legs=2),
            2,
        )
        return {
            "legs": [[k, r, n] for (k, r), n in legs],
            "credit": credit,
            "debit": debit,
            "exit": session,
            "how": how,
            "days_held": held,
            "gross": gross,
            "cost": cost,
            "net": round(gross - cost, 2),
        }
    return {"skip": "no exit mark"}


# --------------------------------------------------------------------------- the store
def paired_trades(db: sqlite3.Connection, start: str, end: str, *, fill: str, stop_days: int = STOP_DAYS):
    """Both arms on every cycle item 59 would enter: (pairs, skips by arm). A pair needs both arms
    closed; an entry one arm could not close is counted under that arm and left out of the pairs."""
    exps = ic.monthlies(
        [
            e
            for (e,) in db.execute(
                "SELECT DISTINCT expiration FROM eod_chain_quotes WHERE symbol = ?", (ic.SYMBOL,)
            )
        ]
    )
    (last,) = db.execute(
        "SELECT max(session) FROM eod_chain_quotes WHERE symbol = ?", (ic.SYMBOL,)
    ).fetchone()
    pairs, skips = [], {a: Counter() for a in ARMS}
    for e in exps:
        planned = date.fromordinal(date.fromisoformat(e).toordinal() - ic.ENTRY_DTE).isoformat()
        if not (start <= planned <= end):
            continue
        if e > last:
            for a in ARMS:
                skips[a]["cycle not finished"] += 1
            continue
        sess = ic.sessions_for(db, e)
        s0 = ic.entry_session(sess, e)
        if s0 is None:
            for a in ARMS:
                skips[a]["no entry chain"] += 1
            continue
        entry = ic.load_chain(db, s0, e)
        path = [(s, ic.load_chain(db, s, e)) for s in sess if s > s0]
        pair = pair_trade(entry, path, e, s0, fill=fill, stop_days=stop_days)
        for a in ARMS:
            if "skip" in pair[a]:
                skips[a][pair[a]["skip"]] += 1
        if all("skip" not in pair[a] for a in ARMS):
            pairs.append({"entry": s0, "expiry": e, **pair})
    return pairs, skips


def pair_trade(
    entry, path, expiry: str, entry_session: str, *, fill: str, stop_days: int = STOP_DAYS
) -> dict:
    return {
        "base": ic.run_trade(entry, path, expiry, fill=fill),
        "stop7": run_stop(entry, path, expiry, fill=fill, entry_session=entry_session, stop_days=stop_days),
    }


# --------------------------------------------------------------------------- statistics
def score(pairs: list[dict], skips: dict, start: str, end: str) -> dict:
    diffs = [p["stop7"]["net"] - p["base"]["net"] for p in pairs]
    stop_nets = [p["stop7"]["net"] for p in pairs]
    stop_costs = [p["stop7"]["cost"] for p in pairs]
    mid = ic.midpoint(start, end)
    halves = {
        h: [d for p, d in zip(pairs, diffs, strict=True) if (p["entry"] < mid) == (h == "first")]
        for h in ("first", "second")
    }
    t = ic.t_stat(diffs)

    def arm(a):
        nets = [p[a]["net"] for p in pairs]
        return {
            "mean_net": round(statistics.fmean(nets), 2) if nets else None,
            "win_rate": round(sum(x > 0 for x in nets) / len(nets), 3) if nets else None,
            "worst": min(nets) if nets else None,
            # Calendar days from entry to exit, the same way for both arms.
            "mean_days_held": round(
                statistics.fmean(ic.days_between(p["entry"], p[a]["exit"]) for p in pairs), 1
            )
            if pairs
            else None,
            "by_exit": dict(Counter(p[a]["how"] for p in pairs)),
        }

    out = {
        "n": len(pairs),
        "mean_diff": round(statistics.fmean(diffs), 2) if diffs else None,
        "t": None if t is None else round(t, 3),
        "p": ic.p_one(t, len(diffs)),
        "halves": {h: round(statistics.fmean(v), 2) if v else None for h, v in halves.items()},
        "halves_n": {h: len(v) for h, v in halves.items()},
        "stop7_cost_stress": robustness.cost_stress(stop_nets, stop_costs, pass_stress=ic.PASS_STRESS)
        if stop_nets
        else None,
        "arms": {a: arm(a) for a in ARMS},
        "no_exit_share": {},
    }
    for a in ARMS:
        no_exit = skips[a].get("no exit mark", 0)
        entered = len(pairs) + no_exit
        out["no_exit_share"][a] = round(no_exit / entered, 3) if entered else None
    if len(pairs) < ic.MIN_TRADES:
        out["verdict"], out["why"] = "no verdict", f"{len(pairs)} paired trades < {ic.MIN_TRADES}"
    elif any((s or 0) > ic.MAX_NO_EXIT_SHARE for s in out["no_exit_share"].values()):
        out["verdict"], out["why"] = "no verdict", f"entries without an exit mark: {out['no_exit_share']}"
    return out


def judge(test: dict) -> None:
    if test.get("verdict") == "no verdict":
        return
    checks = {
        "diff_mean_positive": (test["mean_diff"] or 0) > 0,
        "diff_t_above_bar": (test["t"] or 0) > ic.T_BAR,
        "diff_both_halves_positive": all((x or 0) > 0 for x in test["halves"].values()),
        "stop7_positive_at_1_5x_costs": bool(
            test["stop7_cost_stress"] and test["stop7_cost_stress"]["survives"]
        ),
    }
    test["checks"] = checks
    test["verdict"] = "pass" if all(checks.values()) else "fail"


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start", default=DESIGN[0])
    ap.add_argument("--end", default=DESIGN[1])
    ap.add_argument(
        "--allow-holdout", action="store_true", help="an --end past the design window reads the holdout"
    )
    ap.add_argument("--json", type=Path, help="default: results/research/item63/study-<window>.json")
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

    pairs, skips = paired_trades(db, args.start, args.end, fill="working")
    test = score(pairs, skips, args.start, args.end)
    judge(test)
    out = {
        "licence": "derived from licensed vendor history: stays under ~/.cherrypick, never in the repo",
        "item": 63,
        "window": [args.start, args.end],
        "allow_holdout": args.allow_holdout,
        "verdict": test.get("verdict"),
        "test": test,
        "skips": {a: dict(s) for a, s in skips.items()},
        "other_fills": {
            f: score(*paired_trades(db, args.start, args.end, fill=f), args.start, args.end)
            for f in ("mid", "natural")
        },
        "planned_mde_B12": robustness.planned_mde(
            [p["stop7"]["net"] - p["base"]["net"] for p in pairs], len(pairs)
        )
        if pairs
        else None,
    }
    if test.get("verdict") == "pass":
        # B13, beside a pass and never used to re-choose: the stop at its neighbours.
        def mean_diff_at(days: int):
            ps, _ = paired_trades(db, args.start, args.end, fill="working", stop_days=days)
            return (
                round(statistics.fmean(p["stop7"]["net"] - p["base"]["net"] for p in ps), 2) if ps else None
            )

        out["robustness_B13"] = {"stop_day_mean_diffs": {d: mean_diff_at(d) for d in NEIGHBOUR_STOPS}}

    target = args.json or (
        home.data_dir("backtest") / "results" / "research" / "item63" / f"study-{args.start}-{args.end}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    (target.parent / f"pairs-{args.start}-{args.end}.json").write_text(
        json.dumps(pairs, default=str), encoding="utf-8"
    )
    print(json.dumps({"verdict": out["verdict"], "written": str(target), "at": datetime.now().isoformat()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
