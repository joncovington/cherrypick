"""Item 55, the defined-risk index collar as a hedge for a long-delta book (research/edge-hypotheses-plan.md):
the coverage plan, the design run and the holdout run.

    python packages/backtest/scripts/index_collar_item55.py plan [--out-dir DIR]
    python packages/backtest/scripts/index_collar_item55.py design [--out-dir DIR]
    python packages/backtest/scripts/index_collar_item55.py holdout [--out-dir DIR]

The manage package offers three hedges when the book is too long (`cherrypick.manage.hedge`): a call
credit spread, a put debit spread, and since 2026-11-06 a collar, the put debit spread funded by a call
credit spread whose strike brings the net nearest zero cost. The collar is labelled "no tested edge,
research item 55". This study asks whether it improves a long-delta book after costs, against no hedge
and against each single vertical.

The book is a stand-in for a long-delta options book: 100 SPY shares (delta one), held from the stored
session nearest 45 days before each standard monthly expiry to the first session three or fewer days
before it. Four arms on the same cycles, the three hedges each scaled to cut 20 shares of the book's
delta at entry (fractional lots, so the arms differ in shape and cost and not in size):

- N: no hedge (the control);
- C: a call credit spread, the short nearest 0.30 delta, the long five points beyond it;
- D: a put debit spread, the long nearest 0.40 delta, the short five points below it;
- K: the collar: D plus a call credit spread five points wide whose short is the strike with |delta|
  from 0.05 to under 0.5 that brings the whole to the net nearest zero at mid (a tie goes to the one
  further out); no collar within 0.25 a share of zero is no cycle.

Strikes, widths and deltas are the ones `hedge.py` uses. SPY stands in for XSP and SPX (the same index,
and the one stored chain history with deltas back to 2017). Marks are the store's 15:45 mids; a hedge
leg is sold or bought at mid with the suite's slippage (`fees.slippage_dollars`) and per-leg fees both
ways (`fees.ic_open_fee`, `fees.ic_close_fee`); the stress takes 1.5x fees and a quarter of the spread.
A leg without a two-sided quote at the exit is priced adversely (a short bought at intrinsic plus its
entry spread, a long sold at intrinsic less it, never below zero).

The measure is the cycle's net in basis points of the book's notional (100 x the entry spot): the
share move plus the hedge's net of costs. An arm is scored by its return per unit of tail risk, the
mean over the absolute mean of its worst 20% of cycles (the denominator never under one basis point),
and an arm beats another when that ratio is higher, judged by a seeded stationary bootstrap of the
paired cycles (neighbouring cycles overlap by weeks). Five tests, K against N, C against N, D against
N, K against C and K against D, Holm at 0.05. Everything written goes under the backtest data dir
(results/research/item55/), never into the repo: the store is licensed, and so is everything derived
from it.
"""

from __future__ import annotations

import argparse
import json
import math
import random
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
ENTRY_DTE = 45
ENTRY_SLACK = 3  # an entry may be up to this many days further out than its target
CLOSE_DTE = 3
WIDTH = 5.0
CREDIT_SHORT_DELTA = 0.30
DEBIT_LONG_DELTA = 0.40
MIN_SHORT_DELTA = 0.05  # the collar's sold call: |delta| from here up to (not including) 0.5
MAX_NET = 0.25  # the collar's net at mid, a share, credit or debit
HEDGE_CUT = 20.0  # shares of delta each hedge removes from the 100-share book
BOOK_SHARES = 100
ARMS = ("N", "C", "D", "K")
HEDGES = ("C", "D", "K")
TESTS = (("K", "N"), ("C", "N"), ("D", "N"), ("K", "C"), ("K", "D"))
DESIGN = (date(2017, 1, 1), date(2021, 12, 31))
HOLDOUT = (date(2022, 1, 1), date(2026, 12, 31))
MIN_CYCLES = {"design": 40, "holdout": 40}
ALPHA = 0.05
TAIL_SHARE = 0.20
TAIL_FLOOR_BP = 1.0
BEST_REMOVED = 5
BLOCK = 2.0  # mean block length of the stationary bootstrap, in cycles
RESAMPLES = 10000
SEED = 55
STRESS_FEES = 1.5
STRESS_COSTS = {"tastytrade_costs": {"slippage_frac_of_spread": 0.25}}

Quote = tuple  # (bid, ask, delta)
Chain = dict  # {(strike, right): Quote}
Leg = tuple  # (quantity, right, strike)


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


def signed_delta(right: str, q: Quote) -> float:
    """The store's delta with the sign a call or a put must have, whatever the store wrote."""
    return abs(q[2]) if right == "C" else -abs(q[2])


def nearest_delta(chain: Chain, right: str, target: float) -> float | None:
    """The strike of `right` whose |delta| is nearest `target`, two-sided only; a tie goes to the
    lower strike (so the same answer twice)."""
    pool = [
        (abs(abs(q[2]) - target), k)
        for (k, r), q in chain.items()
        if r == right and two_sided(q) and q[2] is not None
    ]
    return min(pool)[1] if pool else None


def wing(chain: Chain, right: str, anchor: float, width: float, outward: float) -> float | None:
    """The listed two-sided strike nearest `width` from `anchor` in the direction `outward` (+1
    higher), never nearer than that."""
    want = anchor + outward * width
    ks = [k for (k, r), q in chain.items() if r == right and two_sided(q) and (k - want) * outward >= -1e-9]
    return min(ks, key=lambda k: (abs(k - want), k)) if ks else None


def net_credit(chain: Chain, legs: list[Leg]) -> float:
    """Per share at mid, + a credit received."""
    return -sum(q * mid(chain[(k, r)]) for q, r, k in legs)


def credit_legs(chain: Chain) -> list[Leg] | None:
    """C: sell the call nearest 0.30 delta, buy the one `WIDTH` above."""
    short = nearest_delta(chain, "C", CREDIT_SHORT_DELTA)
    long_ = wing(chain, "C", short, WIDTH, 1.0) if short is not None else None
    return [(-1, "C", short), (1, "C", long_)] if short is not None and long_ is not None else None


def debit_legs(chain: Chain) -> list[Leg] | None:
    """D: buy the put nearest 0.40 delta, sell the one `WIDTH` below."""
    long_ = nearest_delta(chain, "P", DEBIT_LONG_DELTA)
    short = wing(chain, "P", long_, WIDTH, -1.0) if long_ is not None else None
    return [(1, "P", long_), (-1, "P", short)] if long_ is not None and short is not None else None


def collar_legs(chain: Chain) -> list[Leg] | None:
    """K: D plus the call credit spread that brings the net at mid nearest zero (a tie to the one
    further out); None when D cannot be built or the nearest net is past `MAX_NET`."""
    debit = debit_legs(chain)
    if debit is None:
        return None
    paid = net_credit(chain, debit)
    best = None
    for (k, r), q in chain.items():
        if r != "C" or not two_sided(q) or q[2] is None or not MIN_SHORT_DELTA <= abs(q[2]) < 0.5:
            continue
        w = wing(chain, "C", k, WIDTH, 1.0)
        if w is None:
            continue
        credit_half = [(-1, "C", k), (1, "C", w)]
        net = paid + net_credit(chain, credit_half)
        key = (round(abs(net), 6), abs(q[2]), k)
        if best is None or key < best[0]:
            best = (key, credit_half, net)
    if best is None or abs(best[2]) > MAX_NET:
        return None
    return [*debit, *best[1]]


def build(chain: Chain) -> dict[str, list[Leg]] | str:
    """{arm: legs} for the three hedges, or the arm that could not be built."""
    out = {}
    for arm, fn in (("C", credit_legs), ("D", debit_legs), ("K", collar_legs)):
        legs = fn(chain)
        if legs is None:
            return arm
        out[arm] = legs
    return out


def unit_delta(chain: Chain, legs: list[Leg]) -> float:
    """Shares of delta of one unit of the legs."""
    return sum(q * 100.0 * signed_delta(r, chain[(k, r)]) for q, r, k in legs)


def hedge_scale(chain: Chain, legs: list[Leg]) -> float | None:
    """Units of the legs that cut `HEDGE_CUT` shares of delta; None when they do not cut it."""
    d = unit_delta(chain, legs)
    return HEDGE_CUT / -d if d < 0 else None


def intrinsic(right: str, strike: float, spot: float) -> float:
    return max(spot - strike, 0.0) if right == "C" else max(strike - spot, 0.0)


def forced_quotes(chain: Chain, legs: list[Leg], entry: Chain, spot: float) -> list[dict]:
    """Exit quotes: a leg's own when two-sided, else adverse (a short at intrinsic plus its entry
    spread, a long at intrinsic less it, never below zero), as a zero-width quote."""
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


def hedge_net(
    entry_quotes: list[dict], exit_quotes: list[dict], legs: list[Leg], spot: float, scale: float
) -> dict:
    """The hedge's net in dollars and in basis points of 100 x `spot`, at the working costs and the
    stress, for `scale` units. Entry and exit at mid; slippage and fees on both sides."""
    sells = sum(1 for q, _, _ in legs if q < 0)
    n = len(legs)
    gross = 100.0 * sum(
        qty * ((x["bid"] + x["ask"]) / 2 - (e["bid"] + e["ask"]) / 2)
        for (qty, _, _), e, x in zip(legs, entry_quotes, exit_quotes, strict=True)
    )
    fee = fees.ic_open_fee(SYMBOL, 1, legs=n, sell_legs=sells, ndigits=4) + fees.ic_close_fee(
        SYMBOL, 1, legs=n, sell_legs=n - sells, ndigits=4
    )
    two = [{"bid": q["bid"], "ask": q["ask"]} for q in exit_quotes if not q.get("filled")]
    out = {"gross": scale * gross, "fees": scale * fee}
    for label, cfg, f in (("working", {}, 1.0), ("stress", STRESS_COSTS, STRESS_FEES)):
        slip = fees.slippage_dollars([{"bid": q["bid"], "ask": q["ask"]} for q in entry_quotes], 1, cfg)
        slip += fees.slippage_dollars(two, 1, cfg) if two else 0.0
        value = scale * (gross - slip - f * fee)
        out[label] = {"net": value, "bp": 1e4 * value / (BOOK_SHARES * spot)}
    return out


def tail(values: list[float], share: float = TAIL_SHARE) -> float | None:
    """The mean of the worst `share` of the values (at least one)."""
    if not values:
        return None
    k = max(1, int(math.floor(share * len(values))))
    return statistics.fmean(sorted(values)[:k])


def ratio(values: list[float]) -> float | None:
    """Return per unit of tail risk: the mean over the absolute mean of the worst 20%, which is never
    taken as under `TAIL_FLOOR_BP`."""
    if not values:
        return None
    return statistics.fmean(values) / max(-tail(values), TAIL_FLOOR_BP)


def ratio_diff_p(xs: list[float], rs: list[float], resamples: int = RESAMPLES, seed: int = SEED) -> float:
    """The one-sided p that arm x's ratio is not above arm r's, by a seeded stationary bootstrap of
    the paired cycles: the share of resamples with x not above r (the observed one counted)."""
    n = len(xs)
    if n < 2:
        return 1.0
    rng = random.Random(seed)
    at_most = 0
    for _ in range(resamples):
        idx = core_stats.stationary_indices(n, BLOCK, rng)
        if ratio([xs[i] for i in idx]) - ratio([rs[i] for i in idx]) <= 0:
            at_most += 1
    return (at_most + 1) / (resamples + 1)


def drop_best(cycles: list[dict], x: str, ref: str, k: int = BEST_REMOVED) -> list[dict]:
    """The cycles without the `k` on which x gained most over ref."""
    ranked = sorted(cycles, key=lambda c: c["arms"][x]["bp"] - c["arms"][ref]["bp"])
    return ranked[:-k] if len(ranked) > k else []


def _mean(xs) -> float | None:
    xs = list(xs)
    return statistics.fmean(xs) if xs else None


def compare(cycles: list[dict], x: str, ref: str, *, resamples: int = RESAMPLES, seed: int = SEED) -> dict:
    """The paired reading of arm x against arm ref on the same cycles."""
    xs = [c["arms"][x]["bp"] for c in cycles]
    rs = [c["arms"][ref]["bp"] for c in cycles]
    sx = [c["arms"][x]["stress_bp"] for c in cycles]
    sr = [c["arms"][ref]["stress_bp"] for c in cycles]
    kept = drop_best(cycles, x, ref)
    kx, kr = [c["arms"][x]["bp"] for c in kept], [c["arms"][ref]["bp"] for c in kept]
    down = [c["arms"][x]["bp"] - c["arms"][ref]["bp"] for c in cycles if c["arms"]["N"]["bp"] < 0]
    r1, r0 = ratio(xs), ratio(rs)
    return {
        "cycles": len(cycles),
        "ratio_x": r1,
        "ratio_ref": r0,
        "ratio_diff": None if r1 is None else r1 - r0,
        "p": ratio_diff_p(xs, rs, resamples, seed) if len(cycles) >= 2 else 1.0,
        "stress_ratio_diff": None if not cycles else ratio(sx) - ratio(sr),
        "ratio_diff_without_best_five": None if not kept else ratio(kx) - ratio(kr),
        "tail_x": tail(xs),
        "tail_ref": tail(rs),
        "mean_x": _mean(xs),
        "mean_ref": _mean(rs),
        "mean_diff": _mean(x_ - r_ for x_, r_ in zip(xs, rs, strict=True)),
        "mean_diff_when_book_lost": _mean(down),
    }


def design_decision(cmp: dict, holm_reject: bool, cycles: int) -> dict:
    """The declared design rule for one test."""
    if cycles < MIN_CYCLES["design"]:
        return {"decision": "no verdict", "why": f"{cycles} design cycles < {MIN_CYCLES['design']}"}
    d = cmp["ratio_diff"]
    checks = {
        "ratio_holm": d is not None and d > 0 and holm_reject,
        "stress_ratio_positive": cmp["stress_ratio_diff"] is not None and cmp["stress_ratio_diff"] > 0,
        "best_five_removed_positive": cmp["ratio_diff_without_best_five"] is not None
        and cmp["ratio_diff_without_best_five"] > 0,
        "tail_no_worse": cmp["tail_x"] is not None
        and cmp["tail_ref"] is not None
        and cmp["tail_x"] >= cmp["tail_ref"],
    }
    return {"decision": "pass" if all(checks.values()) else "fail", "checks": checks}


def holdout_decision(design: dict, cmp: dict, cycles: int) -> dict:
    """A design pass is confirmed by the same sign at p < 0.05 with the tail no worse."""
    if design.get("decision") != "pass":
        return {"decision": "not judged", "why": "no design pass"}
    if cycles < MIN_CYCLES["holdout"]:
        return {"decision": "no verdict", "why": f"{cycles} holdout cycles < {MIN_CYCLES['holdout']}"}
    d = cmp["ratio_diff"]
    ok = d is not None and d > 0 and cmp["p"] < ALPHA and cmp["tail_x"] >= cmp["tail_ref"]
    return {"decision": "pass" if ok else "fail"}


def collar_label_lifts(confirmed: list[str]) -> bool:
    """The collar's 'no tested edge' label comes off only when K is confirmed against no hedge and
    against both verticals."""
    return all(f"{x}|{ref}" in confirmed for x, ref in (("K", "N"), ("K", "C"), ("K", "D")))


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

        d = paths.results_dir() / "research" / "item55"
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


def simulate(sessions: dict, expiry: date) -> tuple[dict | None, str | None]:
    """Every arm on one expiry, or (None, why)."""
    days = sorted(sessions)
    start = entry_session(expiry, ENTRY_DTE, days)
    if start is None:
        return None, "no_entry_session"
    spot, chain = sessions[start]
    if not spot:
        return None, "no_spot"
    hedges = build(chain)
    if isinstance(hedges, str):
        return None, f"no_hedge_{hedges}"
    scales = {arm: hedge_scale(chain, legs) for arm, legs in hedges.items()}
    bad = next((a for a, s in scales.items() if s is None), None)
    if bad:
        return None, f"no_cut_{bad}"
    exit_day = next((d for d in days if d > start and (expiry - d).days <= CLOSE_DTE), None)
    if exit_day is None:
        return None, "no_exit_mark"
    x_spot, x_chain = sessions[exit_day]
    if not x_spot:
        return None, "no_exit_spot"
    book = 1e4 * (x_spot - spot) / spot
    out = {
        "expiry": expiry.isoformat(),
        "entry": start.isoformat(),
        "exit": exit_day.isoformat(),
        "entry_dte": (expiry - start).days,
        "spot": spot,
        "collar_net": net_credit(chain, hedges["K"]),
        "collar_call_delta": abs(chain[(hedges["K"][2][2], "C")][2]),
        "collar_put_delta": abs(chain[(hedges["K"][0][2], "P")][2]),
        "arms": {
            "N": {"bp": book, "stress_bp": book, "net": BOOK_SHARES * (x_spot - spot), "forced_legs": 0}
        },
    }
    for arm, legs in hedges.items():
        entry_quotes = [{"bid": chain[(k, r)][0], "ask": chain[(k, r)][1]} for _, r, k in legs]
        quotes = forced_quotes(x_chain, legs, chain, x_spot)
        n = hedge_net(entry_quotes, quotes, legs, spot, scales[arm])
        out["arms"][arm] = {
            "bp": book + n["working"]["bp"],
            "stress_bp": book + n["stress"]["bp"],
            "net": BOOK_SHARES * (x_spot - spot) + n["working"]["net"],
            "hedge_bp": n["working"]["bp"],
            "fees": n["fees"],
            "units": scales[arm],
            "forced_legs": sum(1 for q in quotes if q.get("filled")),
        }
    return out, None


def run_all(conn) -> tuple[list[dict], dict]:
    cycles, drops = [], {}
    for exp in expiries(conn):
        t, why = simulate(load_expiry(conn, exp), exp)
        if t is None:
            drops[why] = drops.get(why, 0) + 1
            continue
        t["window"] = window_of(date.fromisoformat(t["entry"]))
        cycles.append(t)
    return cycles, drops


def in_window(cycles: list[dict], window: str) -> list[dict]:
    return [c for c in cycles if c["window"] == window]


def arm_summary(cycles: list[dict], arm: str) -> dict:
    v = [c["arms"][arm]["bp"] for c in cycles]
    return {
        "cycles": len(v),
        "mean": _mean(v),
        "stress_mean": _mean(c["arms"][arm]["stress_bp"] for c in cycles),
        "mean_dollars": _mean(c["arms"][arm]["net"] for c in cycles),
        "tail": tail(v),
        "worst": min(v) if v else None,
        "ratio": ratio(v),
        "win_share": _mean(1.0 if x > 0 else 0.0 for x in v),
        "forced_legs": sum(c["arms"][arm]["forced_legs"] for c in cycles),
    }


# ------------------------------------------------------------------------------------------------
# The steps.


def cmd_plan(args) -> int:
    conn = _store()
    exps = expiries(conn)
    cycles, drops = run_all(conn)
    conn.close()
    counts = {w: len(in_window(cycles, w)) for w in ("design", "holdout")}
    short = {w: n for w, n in counts.items() if n < MIN_CYCLES[w]}
    plan = {
        "expiries": len(exps),
        "first_expiry": exps[0].isoformat() if exps else None,
        "last_expiry": exps[-1].isoformat() if exps else None,
        "cycles": counts,
        "under_floor": short,
        "drops": drops,
        "tests": len(TESTS),
    }
    d = _dir(args.out_dir)
    (d / "plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    print(
        json.dumps(
            {
                "tests": plan["tests"],
                "cycles": counts,
                "under_floor": len(short),
                "file": str(d / "plan.json"),
            }
        )
    )
    return 0


def cmd_design(args) -> int:
    conn = _store()
    cycles, drops = run_all(conn)
    conn.close()
    d = _dir(args.out_dir)
    (d / "cycles.json").write_text(json.dumps(cycles), encoding="utf-8")
    rows = in_window(cycles, "design")
    readings = {f"{x}|{ref}": compare(rows, x, ref, seed=SEED + i) for i, (x, ref) in enumerate(TESTS)}
    pvalues = {k: v["p"] for k, v in readings.items()}
    reject = core_stats.holm(pvalues, ALPHA)
    adjusted = core_stats.holm_adjusted(pvalues)
    result = {"drops": drops, "tests": {}, "arms": {a: arm_summary(rows, a) for a in ARMS}}
    for key, cmp in readings.items():
        result["tests"][key] = {
            **cmp,
            "holm_p": adjusted[key],
            **design_decision(cmp, reject[key], len(rows)),
        }
    (d / "design.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    passed = sorted(k for k, v in result["tests"].items() if v["decision"] == "pass")
    print(json.dumps({"design_passes": passed, "file": str(d / "design.json")}))
    return 0


def overall_verdict(design_tests: dict, confirmed: list) -> str:
    """The study's one word: passed when a test is confirmed; no verdict when every design test was
    no verdict (too few trades, nothing judged); failed otherwise. Before 2026-11-07 a study that
    judged nothing wrote failed, which read as a rejected rule."""
    if confirmed:
        return "passed"
    if design_tests and all(t.get("decision") == "no verdict" for t in design_tests.values()):
        return "no verdict"
    return "failed"


def cmd_holdout(args) -> int:
    d = _dir(args.out_dir)
    design = json.loads((d / "design.json").read_text(encoding="utf-8"))
    cycles = json.loads((d / "cycles.json").read_text(encoding="utf-8"))
    rows = in_window(cycles, "holdout")
    result = {"tests": {}, "arms": {a: arm_summary(rows, a) for a in ARMS}}
    for i, (x, ref) in enumerate(TESTS):
        key = f"{x}|{ref}"
        cmp = compare(rows, x, ref, seed=SEED + i)
        result["tests"][key] = {**cmp, **holdout_decision(design["tests"][key], cmp, len(rows))}
    confirmed = sorted(k for k, v in result["tests"].items() if v["decision"] == "pass")
    result["verdict"] = overall_verdict(design["tests"], confirmed)
    result["confirmed"] = confirmed
    result["collar_label_lifts"] = collar_label_lifts(confirmed)
    (d / "holdout.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "confirmed": confirmed,
                "collar_label_lifts": result["collar_label_lifts"],
                "file": str(d / "holdout.json"),
            }
        )
    )
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
