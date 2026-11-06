"""Item 53, the time exit for short premium opened inside 30 DTE (docs/edge-hypotheses-plan.md): the
coverage plan, the design run and the holdout run.

    python scripts/time_exit_entry_dte_item53.py plan [--out-dir DIR]
    python scripts/time_exit_entry_dte_item53.py design [--out-dir DIR]
    python scripts/time_exit_entry_dte_item53.py holdout [--out-dir DIR]

The manage package skips its 21-day time exit for a trade opened 21 days or fewer from expiry (user,
2026-11-06): such a trade is managed by a 50% take profit, the stop and the close three days out (arm
A). This study asks whether anything does better for trades opened at 7, 14, 21 and 28 DTE:

- A: 50% take profit + manage's credit stop + the close at 3 DTE (today's default);
- B: A plus a time exit scaled to the entry, at the share of life the 21-of-45 rule keeps (close
  when the days left reach round(entry DTE x 21/45)); at 7 DTE that is the 3-DTE close, so B is
  judged at 14, 21 and 28 only;
- C: 50% take profit and the 3-DTE close, no stop;
- D: hold to the 3-DTE close (the control).

Each arm manages the same trades: a one-lot SPY short strangle (the listed calls and puts nearest
16 delta) and a one-lot iron condor (the same shorts, long wings at the listed strike nearest 1% of
the entry spot beyond each short), opened at 15:45 ET on the stored session closest to the entry DTE
(the target to 3 days more), on each standard monthly expiry. Marks are the store's 15:45 mids, one a
session; a trigger fires on the first mark that reaches it and fills at that mark. Fills take the
suite's slippage (`fees.slippage_dollars`) and per-leg fees both ways (`fees.ic_open_fee`,
`fees.ic_close_fee`); the stress takes 1.5x fees and a quarter of the spread. A leg without a
two-sided quote on a mark day holds every trigger that day; at a forced close it is priced adversely
(a short bought at intrinsic plus its entry spread, a long sold at intrinsic less its entry spread,
never below zero).

The measure is the net in basis points of the notional (100 x the entry spot), so 2017 and 2026 add
up. Every challenger is judged against A on the same trades (the paired difference), Holm across the
22 (structure, entry DTE, challenger) tests. Everything written goes under the backtest data dir
(results/research/item53/), never into the repo: the store is licensed, and so is everything derived
from it.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import calendar as cal
from cherrypick.core import fees
from cherrypick.core import stats as core_stats

SYMBOL = "SPY"
STORE = "SPY-monthly-1545.db"
ENTRY_DTES = (7, 14, 21, 28)
ENTRY_SLACK = 3  # an entry may be up to this many days further out than its target
TARGET_DELTA = 0.16
WING_SHARE = 0.01
TAKE_PROFIT = 0.50
STOP_MULTIPLE = 1.0  # manage's credit.stop: loss at 1x the credit
STOP_FALLBACK = 0.5  # ... or half the max loss, when that is smaller (a defined-risk trade)
CLOSE_DTE = 3
SCALE = 21 / 45
STRUCTURES = ("strangle", "condor")
ARMS = ("A", "B", "C", "D")
CHALLENGERS = ("B", "C", "D")
DESIGN = (date(2017, 1, 1), date(2021, 12, 31))
HOLDOUT = (date(2022, 1, 1), date(2026, 12, 31))
MIN_TRADES = {"design": 40, "holdout": 40}
ALPHA = 0.05
TAIL_SHARE = 0.05
BEST_REMOVED = 5
STRESS_FEES = 1.5
STRESS_COSTS = {"tastytrade_costs": {"slippage_frac_of_spread": 0.25}}

Quote = tuple  # (bid, ask, delta)
Chain = dict  # {(strike, right): Quote}


# ------------------------------------------------------------------------------------------------
# Pure.


def two_sided(q) -> bool:
    return q is not None and q[0] is not None and q[1] is not None and q[0] >= 0 and q[1] > 0 and q[1] >= q[0]


def mid(q) -> float:
    return (q[0] + q[1]) / 2.0


def monthly(y: int, m: int) -> date:
    """The standard monthly: the third Friday, the trading day before it when it is a holiday."""
    first = date(y, m, 1)
    fri = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
    return fri if cal.is_trading_day(fri) else cal.previous_trading_day(fri)


def entry_session(expiry: date, dte: int, sessions: list[date]) -> date | None:
    """The stored session whose days to `expiry` are `dte` to `dte + ENTRY_SLACK`, the nearest to
    `dte`; None when no stored session falls there."""
    fits = [s for s in sessions if dte <= (expiry - s).days <= dte + ENTRY_SLACK]
    return min(fits, key=lambda s: (expiry - s).days) if fits else None


def scaled_exit_dte(entry_dte: int) -> int:
    """Arm B's time exit: the days left at the share of life the 21-of-45 rule keeps."""
    return int(round(entry_dte * SCALE))


def pick_short(chain: Chain, right: str, spot: float, target: float = TARGET_DELTA) -> float | None:
    """The out-of-the-money listed strike whose |delta| is nearest `target`, two-sided only."""
    best = None
    for (k, r), q in chain.items():
        if r != right or not two_sided(q) or q[2] is None:
            continue
        if (right == "C" and k <= spot) or (right == "P" and k >= spot):
            continue
        gap = abs(abs(q[2]) - target)
        if best is None or gap < best[0] or (gap == best[0] and abs(k - spot) > abs(best[1] - spot)):
            best = (gap, k)
    return best[1] if best else None


def pick_wing(chain: Chain, right: str, short: float, spot: float, share: float = WING_SHARE) -> float | None:
    """The listed strike beyond `short` nearest `share` of spot away from it, two-sided only."""
    want = short + share * spot if right == "C" else short - share * spot
    ks = [
        k
        for (k, r), q in chain.items()
        if r == right and two_sided(q) and ((k > short) if right == "C" else (k < short))
    ]
    return min(ks, key=lambda k: (abs(k - want), abs(k - short))) if ks else None


def build(chain: Chain, spot: float, structure: str) -> list[tuple[int, str, float]] | None:
    """The legs as (quantity, right, strike), or None when a leg cannot be picked."""
    kp, kc = pick_short(chain, "P", spot), pick_short(chain, "C", spot)
    if kp is None or kc is None:
        return None
    legs = [(-1, "P", kp), (-1, "C", kc)]
    if structure == "condor":
        wp, wc = pick_wing(chain, "P", kp, spot), pick_wing(chain, "C", kc, spot)
        if wp is None or wc is None:
            return None
        legs += [(1, "P", wp), (1, "C", wc)]
    return legs


def intrinsic(right: str, strike: float, spot: float) -> float:
    return max(spot - strike, 0.0) if right == "C" else max(strike - spot, 0.0)


def close_cost(chain: Chain, legs: list) -> float | None:
    """What buying the position back costs at mid, per share (shorts paid, longs received); None
    when any leg is not two-sided."""
    total = 0.0
    for qty, right, k in legs:
        q = chain.get((k, right))
        if not two_sided(q):
            return None
        total -= qty * mid(q)
    return total


def forced_quotes(chain: Chain, legs: list, entry: Chain, spot: float) -> list[dict]:
    """Exit quotes for a forced close: a leg's own when two-sided, else adverse (a short at intrinsic
    plus its entry spread, a long at intrinsic less it, never below zero), as a zero-width quote."""
    out = []
    for qty, right, k in legs:
        q = chain.get((k, right))
        if two_sided(q):
            out.append({"bid": q[0], "ask": q[1], "filled": False})
            continue
        e = entry[(k, right)]
        spread = e[1] - e[0]
        px = intrinsic(right, k, spot) + spread if qty < 0 else max(0.0, intrinsic(right, k, spot) - spread)
        out.append({"bid": px, "ask": px, "filled": True})
    return out


def stop_level(credit: float, max_loss: float | None) -> float:
    """manage's credit.stop, per share: the loss at 1x the credit, or half the max loss when that is
    smaller (a defined-risk trade)."""
    stop = STOP_MULTIPLE * credit
    if max_loss is not None and max_loss < stop * 0.999:
        stop = STOP_FALLBACK * max_loss
    return stop


def exit_for(
    arm: str, entry_dte: int, credit: float, max_loss: float | None, path: list
) -> tuple[int, str] | None:
    """(index into `path`, reason) of the arm's exit; None when the path ends before any exit.

    `path` is [(days left, close cost or None)] for each mark after the entry, oldest first. Checked on
    each mark in this order: the 3-DTE close, the stop, the take profit, arm B's scaled exit. A mark
    with no cost (a leg not two-sided) can only take the 3-DTE close."""
    stop = stop_level(credit, max_loss) if arm in ("A", "B") else None
    target = (1 - TAKE_PROFIT) * credit if arm in ("A", "B", "C") else None
    scaled = scaled_exit_dte(entry_dte) if arm == "B" else None
    for i, (left, cost) in enumerate(path):
        if left <= CLOSE_DTE:
            return i, "close_dte"
        if cost is None:
            continue
        if stop is not None and cost - credit >= stop:
            return i, "stop"
        if target is not None and cost <= target:
            return i, "take_profit"
        if scaled is not None and left <= scaled:
            return i, "scaled_exit"
    return None


def net(entry_quotes: list[dict], exit_quotes: list[dict], legs: list, spot: float) -> dict:
    """One trade's net in dollars and in basis points of 100 x spot, at the working costs and the
    stress. Sold at mid, bought back at mid; slippage and fees on both sides."""
    sells = sum(1 for q, _, _ in legs if q < 0)
    n = len(legs)
    credit = 100.0 * sum(
        -qty * (q["bid"] + q["ask"]) / 2 for (qty, _, _), q in zip(legs, entry_quotes, strict=True)
    )
    back = 100.0 * sum(
        -qty * (q["bid"] + q["ask"]) / 2 for (qty, _, _), q in zip(legs, exit_quotes, strict=True)
    )
    fee = fees.ic_open_fee(SYMBOL, 1, legs=n, sell_legs=sells, ndigits=4) + fees.ic_close_fee(
        SYMBOL, 1, legs=n, sell_legs=n - sells, ndigits=4
    )
    out = {"credit": credit, "exit_cost": back, "fees": fee}
    two = [{"bid": q["bid"], "ask": q["ask"]} for q in exit_quotes if not q.get("filled")]
    for label, cfg, f in (("working", {}, 1.0), ("stress", STRESS_COSTS, STRESS_FEES)):
        slip = fees.slippage_dollars([{"bid": q["bid"], "ask": q["ask"]} for q in entry_quotes], 1, cfg)
        slip += fees.slippage_dollars(two, 1, cfg) if two else 0.0
        value = credit - back - slip - f * fee
        out[label] = {"net": value, "bp": 1e4 * value / (100.0 * spot)}
    return out


def tail(values: list[float], share: float = TAIL_SHARE) -> float | None:
    """The mean of the worst `share` of the values (at least one)."""
    if not values:
        return None
    k = max(1, int(math.floor(share * len(values))))
    return statistics.fmean(sorted(values)[:k])


def one_sided_p(values: list[float]) -> dict:
    """Mean, t and the one-sided p that the mean is not above zero (Student t, n - 1 df)."""
    n = len(values)
    if n < 2:
        return {"n": n, "mean": values[0] if values else None, "t": None, "p": 1.0}
    m, sd = statistics.fmean(values), statistics.stdev(values)
    if sd < 1e-12:
        t = math.inf if m > 0 else -math.inf if m < 0 else 0.0
    else:
        t = m / (sd / math.sqrt(n))
    p = 0.0 if t == math.inf else 1.0 if t == -math.inf else 1.0 - core_stats.t_cdf(t, n - 1)
    return {"n": n, "mean": m, "t": t, "p": p}


def drop_best(values: list[float], k: int = BEST_REMOVED) -> list[float]:
    return sorted(values)[:-k] if len(values) > k else []


def _mean(xs) -> float | None:
    xs = list(xs)
    return statistics.fmean(xs) if xs else None


def tests_family() -> list[tuple[str, int, str]]:
    """The declared tests: (structure, entry DTE, challenger); B only where it differs from A."""
    return [
        (s, d, c)
        for s in STRUCTURES
        for d in ENTRY_DTES
        for c in CHALLENGERS
        if not (c == "B" and scaled_exit_dte(d) <= CLOSE_DTE)
    ]


def compare(trades: list[dict], challenger: str) -> dict:
    """The paired reading of one challenger against A on the same trades."""
    diffs = [t["arms"][challenger]["bp"] - t["arms"]["A"]["bp"] for t in trades]
    stress = [t["arms"][challenger]["stress_bp"] - t["arms"]["A"]["stress_bp"] for t in trades]
    a = [t["arms"]["A"]["bp"] for t in trades]
    c = [t["arms"][challenger]["bp"] for t in trades]
    return {
        "diff": one_sided_p(diffs),
        "stress_diff_mean": _mean(stress),
        "diff_without_best_five": _mean(drop_best(diffs)),
        "tail_a": tail(a),
        "tail_challenger": tail(c),
        "worst_a": min(a) if a else None,
        "worst_challenger": min(c) if c else None,
    }


def arm_summary(trades: list[dict], arm: str) -> dict:
    v = [t["arms"][arm]["bp"] for t in trades]
    return {
        "trades": len(v),
        "net": one_sided_p(v),
        "stress_mean": _mean(t["arms"][arm]["stress_bp"] for t in trades),
        "mean_dollars": _mean(t["arms"][arm]["net"] for t in trades),
        "worst": min(v) if v else None,
        "tail": tail(v),
        "win_share": _mean(1.0 if x > 0 else 0.0 for x in v),
        "exits": {
            r: sum(1 for t in trades if t["arms"][arm]["reason"] == r)
            for r in ("close_dte", "stop", "take_profit", "scaled_exit")
        },
    }


def design_decision(cmp: dict, holm_reject: bool, trades: int) -> dict:
    """The declared design rule for one test."""
    if trades < MIN_TRADES["design"]:
        return {"decision": "no verdict", "why": f"{trades} design trades < {MIN_TRADES['design']}"}
    d = cmp["diff"]["mean"]
    checks = {
        "diff_holm": d is not None and d > 0 and holm_reject,
        "stress_diff_positive": cmp["stress_diff_mean"] is not None and cmp["stress_diff_mean"] > 0,
        "best_five_removed_positive": cmp["diff_without_best_five"] is not None
        and cmp["diff_without_best_five"] > 0,
        "tail_no_worse": cmp["tail_challenger"] is not None
        and cmp["tail_a"] is not None
        and cmp["tail_challenger"] >= cmp["tail_a"],
    }
    return {"decision": "pass" if all(checks.values()) else "fail", "checks": checks}


def holdout_decision(design: dict, cmp: dict, trades: int) -> dict:
    """A design pass is confirmed by a holdout paired difference above zero with its tail no worse."""
    if design.get("decision") != "pass":
        return {"decision": "not judged", "why": "no design pass"}
    if trades < MIN_TRADES["holdout"]:
        return {"decision": "no verdict", "why": f"{trades} holdout trades < {MIN_TRADES['holdout']}"}
    d = cmp["diff"]["mean"]
    ok = d is not None and d > 0 and cmp["tail_challenger"] >= cmp["tail_a"]
    return {"decision": "pass" if ok else "fail"}


def window_of(day: date) -> str | None:
    for name, (a, b) in (("design", DESIGN), ("holdout", HOLDOUT)):
        if a <= day <= b:
            return name
    return None


# ------------------------------------------------------------------------------------------------
# The store.


def _dir(override: str | None) -> Path:
    if override:
        d = Path(override)
    else:
        from cherrypick.backtest import paths

        d = paths.results_dir() / "research" / "item53"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _store() -> sqlite3.Connection:
    from cherrypick.backtest import paths
    from cherrypick.backtest import settings as _settings

    path = paths.vendor_dir(_settings.load()) / STORE
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def load_expiry(conn, expiry: date) -> dict[date, tuple[float, Chain]]:
    """{session: (spot, chain)} for one expiry, every stored session."""
    out: dict[date, tuple[float, Chain]] = {}
    for day, k, r, b, a, dl, u in conn.execute(
        "SELECT day, strike, right, bid, ask, delta, underlying FROM quotes WHERE expiration = ?",
        (expiry.isoformat(),),
    ):
        d = date.fromisoformat(day)
        spot, chain = out.get(d, (u, {}))
        chain[(float(k), str(r).upper()[:1])] = (b, a, dl)
        out[d] = (spot if spot is not None else u, chain)
    return out


def expiries(conn) -> list[date]:
    return sorted(date.fromisoformat(e) for (e,) in conn.execute("SELECT DISTINCT expiration FROM quotes"))


def simulate(sessions: dict, expiry: date, entry_dte: int, structure: str) -> tuple[dict | None, str | None]:
    """Every arm on one (expiry, entry DTE, structure), or (None, why)."""
    days = sorted(sessions)
    start = entry_session(expiry, entry_dte, days)
    if start is None:
        return None, "no_entry_session"
    spot, chain = sessions[start]
    if not spot:
        return None, "no_spot"
    legs = build(chain, spot, structure)
    if legs is None:
        return None, "no_legs"
    entry_quotes = [{"bid": chain[(k, r)][0], "ask": chain[(k, r)][1]} for _, r, k in legs]
    credit = sum(-q * mid(chain[(k, r)]) for q, r, k in legs)
    if credit <= 0:
        return None, "no_credit"
    max_loss = None
    if structure == "condor":
        widths = [abs(legs[2][2] - legs[0][2]), abs(legs[3][2] - legs[1][2])]
        max_loss = max(widths) - credit
    later = [d for d in days if d > start]
    path = [((expiry - d).days, close_cost(sessions[d][1], legs)) for d in later]
    out = {
        "expiry": expiry.isoformat(),
        "entry": start.isoformat(),
        "entry_dte": (expiry - start).days,
        "target_dte": entry_dte,
        "structure": structure,
        "spot": spot,
        "credit_per_share": credit,
        "arms": {},
    }
    for arm in ARMS:
        if arm == "B" and scaled_exit_dte(entry_dte) <= CLOSE_DTE:
            out["arms"]["B"] = None
            continue
        hit = exit_for(arm, entry_dte, credit, max_loss, path)
        if hit is None:
            return None, "no_exit_mark"
        i, reason = hit
        x_spot, x_chain = sessions[later[i]]
        quotes = forced_quotes(x_chain, legs, chain, x_spot or spot)
        n = net(entry_quotes, quotes, legs, spot)
        out["arms"][arm] = {
            "reason": reason,
            "exit": later[i].isoformat(),
            "net": n["working"]["net"],
            "bp": n["working"]["bp"],
            "stress_bp": n["stress"]["bp"],
            "forced_legs": sum(1 for q in quotes if q.get("filled")),
        }
    return out, None


def run_all(conn) -> tuple[list[dict], dict]:
    trades, drops = [], {}
    for exp in expiries(conn):
        sessions = load_expiry(conn, exp)
        for dte in ENTRY_DTES:
            for s in STRUCTURES:
                t, why = simulate(sessions, exp, dte, s)
                if t is None:
                    key = f"{s}_{dte}_{why}"
                    drops[key] = drops.get(key, 0) + 1
                    continue
                t["window"] = window_of(date.fromisoformat(t["entry"]))
                trades.append(t)
    return trades, drops


def cell(trades: list[dict], window: str, structure: str, dte: int) -> list[dict]:
    return [
        t for t in trades if t["window"] == window and t["structure"] == structure and t["target_dte"] == dte
    ]


# ------------------------------------------------------------------------------------------------
# The steps.


def cmd_plan(args) -> int:
    conn = _store()
    exps = expiries(conn)
    trades, drops = run_all(conn)
    conn.close()
    counts = {
        f"{s}_{d}_{w}": len(cell(trades, w, s, d))
        for s in STRUCTURES
        for d in ENTRY_DTES
        for w in ("design", "holdout")
    }
    short = {k: v for k, v in counts.items() if v < MIN_TRADES[k.rsplit("_", 1)[1]]}
    plan = {
        "expiries": len(exps),
        "first_expiry": exps[0].isoformat() if exps else None,
        "last_expiry": exps[-1].isoformat() if exps else None,
        "cells": counts,
        "under_floor": short,
        "drops": drops,
        "tests": len(tests_family()),
    }
    d = _dir(args.out_dir)
    (d / "plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    print(json.dumps({"tests": plan["tests"], "under_floor": len(short), "file": str(d / "plan.json")}))
    return 0


def cmd_design(args) -> int:
    conn = _store()
    trades, drops = run_all(conn)
    conn.close()
    d = _dir(args.out_dir)
    (d / "trades.json").write_text(json.dumps(trades), encoding="utf-8")
    readings, pvalues = {}, {}
    for s, dte, c in tests_family():
        key = f"{s}|{dte}|{c}"
        cmp = compare(cell(trades, "design", s, dte), c)
        readings[key] = cmp
        pvalues[key] = cmp["diff"]["p"]
    reject = core_stats.holm(pvalues, ALPHA)
    adjusted = core_stats.holm_adjusted(pvalues)
    result = {"drops": drops, "tests": {}, "arms": {}}
    for s, dte, c in tests_family():
        key = f"{s}|{dte}|{c}"
        n = len(cell(trades, "design", s, dte))
        result["tests"][key] = {
            **readings[key],
            "holm_p": adjusted[key],
            **design_decision(readings[key], reject[key], n),
        }
    for s in STRUCTURES:
        for dte in ENTRY_DTES:
            for arm in ARMS:
                rows = [t for t in cell(trades, "design", s, dte) if t["arms"].get(arm)]
                result["arms"][f"{s}|{dte}|{arm}"] = arm_summary(rows, arm) if rows else None
    (d / "design.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    passed = sorted(k for k, v in result["tests"].items() if v["decision"] == "pass")
    print(json.dumps({"design_passes": passed, "file": str(d / "design.json")}))
    return 0


def cmd_holdout(args) -> int:
    d = _dir(args.out_dir)
    design = json.loads((d / "design.json").read_text(encoding="utf-8"))
    trades = json.loads((d / "trades.json").read_text(encoding="utf-8"))
    result = {"tests": {}, "arms": {}}
    for s, dte, c in tests_family():
        key = f"{s}|{dte}|{c}"
        rows = cell(trades, "holdout", s, dte)
        cmp = compare(rows, c)
        result["tests"][key] = {**cmp, **holdout_decision(design["tests"][key], cmp, len(rows))}
    for s in STRUCTURES:
        for dte in ENTRY_DTES:
            for arm in ARMS:
                rows = [t for t in cell(trades, "holdout", s, dte) if t["arms"].get(arm)]
                result["arms"][f"{s}|{dte}|{arm}"] = arm_summary(rows, arm) if rows else None
    confirmed = sorted(k for k, v in result["tests"].items() if v["decision"] == "pass")
    result["verdict"] = "passed" if confirmed else "failed"
    result["confirmed"] = confirmed
    (d / "holdout.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"verdict": result["verdict"], "confirmed": confirmed, "file": str(d / "holdout.json")}))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("plan", cmd_plan), ("design", cmd_design), ("holdout", cmd_holdout)):
        sp = sub.add_parser(name)
        sp.add_argument("--out-dir")
        sp.set_defaults(fn=fn)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
