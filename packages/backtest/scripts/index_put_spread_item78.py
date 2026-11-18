"""Item 78: item 57's weekly index put as a defined-risk put credit spread, under the three bars.

The declaration is research/edge-hypotheses-plan.md section 78; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

Item 77 found item 57's naked 10-delta put a tail-risk trade: a bad year is not earned back within
a year. This buys the tail back. On item 57's weekly expiries and names (SPX, SPY, QQQ, IWM), the put
nearest 10 delta is sold 7 days before expiry, as item 57 does, and a lower put is bought with it;
the spread is held to expiry. Two arms by the long leg: `long5` (the put nearest 5 delta below the
short) and `long3` (nearest 3 delta). A spread holds the same buying power in a cash account and
under Reg T: its width less the credit. The measure is each trade's return on that buying power, the
equal-weight mean over the names present in a cycle, one cycle one observation.

- `plan`: the chain paths item 57 requested (its recorded requests files), written as this study's
  own (`{results}/path_requests.json`). Every one is already stored, so the chain fetches nothing.
- `study`: one run over the whole sample, judged by the declared RULE (the study kit's three bars,
  no splits), written to `{results}/results.json` as the kit's verdict block, with what is reported
  beside it: the naked short put by this same code on Reg T margin (item 77's model), each half
  (split 2021-06-01), each name, and the forward cycles after 2026-10-30.

The sample was read by items 57 and 77 for the short leg, so the verdict is provisional by
declaration; the forward cycles are the fresh evidence.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import bs, fees, treasury
from cherrypick.core import calendar as cal
from cherrypick.core import progress as core_progress

from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.inputs import Inputs

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

RULE = {
    "t_bar": 2.0,
    "min_n": 80,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 78},
    "delta": True,
    "splits": [],
}

#: The research-queue chain. Every path is already in the store (item 57 fetched them, and its
#: forward retest fetches the forward weeks), so there is no fetch step: the plan, then the study.
CHAIN = (
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/path_requests.json"},
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)

#: Item 57's requests files: its sample, and the forward plan its monthly retest writes.
SOURCES = ("path_requests.json", "path_requests-forward.json")
SHORT_DELTA = 0.10
ARMS = {"long5": 0.05, "long3": 0.03}
TENOR = 7
#: Item 57's judged window and its forward read.
LAST_JUDGED = "2026-10-16"
FORWARD_AFTER = "2026-10-30"
SPLIT = "2021-06-01"
#: The store spells SPX's weekly root SPXW; it is cash settled and pays the index exchange fee.
CASH_SETTLED = {"SPXW"}
FEE_SYMBOL = {"SPXW": "SPX"}
#: Item 77's Reg T model for the naked put reported beside: share of the underlying, floor on the strike.
MARGIN_SHARE = {"SPXW": 0.15}
MARGIN_SHARE_DEFAULT = 0.20
MARGIN_FLOOR = 0.10
RATE_STALE_DAYS = 10


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item78"
    d.mkdir(parents=True, exist_ok=True)
    return d


def item57_dir() -> Path:
    from cherrypick.backtest import paths

    return paths.results_dir() / "research" / "item57"


def requests(source_dir: Path | None = None) -> list[dict]:
    """Item 57's requested paths, from its recorded requests files, each once, in order."""
    source_dir = source_dir or item57_dir()
    seen, out = set(), []
    for name in SOURCES:
        f = source_dir / name
        if not f.exists():
            continue
        for r in json.loads(f.read_text(encoding="utf-8"))["requests"]:
            key = (r["symbol"], r["expiration"], r["start"], r["through"])
            if key not in seen:
                seen.add(key)
                out.append(r)
    if not out:
        raise SystemExit(f"no item 57 requests under {source_dir}: item 57's plan has not run")
    return sorted(out, key=lambda r: (r["expiration"], r["symbol"]))


def chain_store():
    import sqlite3

    from cherrypick.backtest import paths, settings

    db = paths.vendor_dir(settings.load()) / "eod_chains.db"
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


# ------------------------------------------------------------------------------------------------
# Pure.


def entry_day(expiry: str) -> str:
    """Item 46's entry: the session 7 calendar days before expiry, or the next one."""
    d = date.fromisoformat(expiry) - timedelta(days=TENOR)
    return (d if cal.is_trading_day(d) else cal.next_trading_day(d)).isoformat()


def _mid(q: dict) -> float:
    return (q["bid"] + q["ask"]) / 2.0


def settle_forward(chain) -> float | None:
    """The expiry close's parity forward on item 46's rule, a zero bid counted: K + C - P at the
    strike whose call and put mids are closest. At the expiry close one side of every strike is
    nearly worthless, so the kit's two-sided forward would drop most expiries."""
    if chain is None:
        return None
    pairs = []
    for k, c in chain.by["C"].items():
        p = chain.by["P"].get(k)
        if p is None:
            continue
        mids = []
        for q in (c, p):
            b, a = q["bid"], q["ask"]
            if b is None or a is None or a <= 0 or b < 0 or b > a:
                break
            mids.append((b + a) / 2.0)
        if len(mids) == 2:
            pairs.append((abs(mids[0] - mids[1]), k, k + mids[0] - mids[1]))
    return min(pairs)[2] if pairs else None


def settle_fee(symbol: str, legs: list[dict], f_exp: float) -> float:
    """Item 46's settlement: a cash-settled leg in the money pays the settlement fee; a fund's pays an
    assignment round trip on 100 shares (an upper bound when both legs land in the money)."""
    itm = [q for q in legs if bs.intrinsic(f_exp, q["strike"], "P") > 0]
    if symbol in CASH_SETTLED:
        return fees.ic_expire_fee(len(itm))
    return sum(
        fees.assignment_round_trip_fee(100, q["strike"], f_exp, direction="long", ndigits=4) for q in itm
    )


def margin_bp(symbol: str, fwd: float, strike: float, premium: float) -> float:
    """Item 77's Reg T on a naked put, the forward standing in for the spot."""
    share = MARGIN_SHARE.get(symbol, MARGIN_SHARE_DEFAULT)
    otm = 100.0 * max(fwd - strike, 0.0)
    return max(share * 100.0 * fwd - otm, MARGIN_FLOOR * 100.0 * strike) + premium


def spread_trade(symbol: str, entry, expiry_chain, long_delta: float | None) -> dict | None:
    """One trade, or {"skip": why}. `entry` and `expiry_chain` are the kit's `Chain`s at the entry
    and expiry sessions. `long_delta` None is the naked short put (reported, on margin)."""
    if entry.forward is None:
        return {"skip": "no entry forward"}
    f1 = settle_forward(expiry_chain)
    if f1 is None:
        return {"skip": "no expiry forward"}
    short = entry.nearest("P", SHORT_DELTA)
    if short is None or not short["bid"]:
        return {"skip": "no short leg"}
    legs = [short]
    if long_delta is not None:
        long = entry.nearest("P", long_delta, beyond=short["strike"])
        if long is None:
            return {"skip": "no long leg"}
        legs.append(long)
    f0 = entry.forward
    credit = 100.0 * (_mid(short) - sum(_mid(q) for q in legs[1:]))
    if credit <= 0:
        return {"skip": "no credit"}
    owed = bs.intrinsic(f1, short["strike"], "P") - sum(bs.intrinsic(f1, q["strike"], "P") for q in legs[1:])
    payoff = 100.0 * owed
    slip = fees.slippage_dollars(legs, 1, {})
    fee = fees.ic_open_fee(FEE_SYMBOL.get(symbol, symbol), 1, legs=len(legs), sell_legs=1, ndigits=4)
    costs = slip + fee + settle_fee(symbol, legs, f1)
    net = credit - payoff - costs
    if long_delta is None:
        bp = margin_bp(symbol, f0, short["strike"], credit)
    else:
        bp = 100.0 * (short["strike"] - legs[1]["strike"]) - credit
    # Position delta in shares: short the short put, long the long one.
    delta = -100.0 * entry.delta(short) + sum(100.0 * entry.delta(q) for q in legs[1:])
    days = (date.fromisoformat(expiry_chain.session) - date.fromisoformat(entry.session)).days
    return {
        "symbol": symbol,
        "entry": entry.session,
        "expiry": expiry_chain.session,
        "strikes": [q["strike"] for q in legs],
        "credit": round(credit, 4),
        "payoff": round(payoff, 4),
        "costs": round(costs, 4),
        "net": round(net, 4),
        "bp": round(bp, 4),
        "delta": delta,
        "f0": f0,
        "f1": f1,
        "days": days,
    }


def cycle_rows(trades: list[dict], rate) -> list[dict]:
    """Per expiry, the equal-weight mean over the names present of each trade's return on its buying
    power, with its financed delta on the same capital: the kit's trade rows, one cycle each."""
    by: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by[t["expiry"]].append(t)
    out = []
    for e in sorted(by):
        ts = by[e]
        r = rate(ts[0]["entry"])
        out.append({
            "date": ts[0]["entry"],
            "expiry": e,
            "net": statistics.fmean(t["net"] / t["bp"] for t in ts),
            "bp": 1.0,
            "days": ts[0]["days"],
            "rate": r,
            "financed": statistics.fmean(
                ck.financed_delta(t["delta"], t["f0"], t["f1"], t["bp"], r, t["days"]) / t["bp"] for t in ts
            ),
            "names": len(ts),
        })  # fmt: skip
    return out


def mean_excess(rows: list[dict]) -> dict:
    if not rows:
        return {"cycles": 0}
    ex = [r["net"] - ck.bill_on(1.0, r["rate"], r["days"]) for r in rows]
    return {"cycles": len(rows), "mean_excess": statistics.fmean(ex)}


# ------------------------------------------------------------------------------------------------
# The run.


def collect(
    inputs: Inputs, conn, reporter: core_progress.Reporter | None = None
) -> tuple[dict[str, list[dict]], dict[str, dict[str, int]]]:
    """Every arm's trades (and the naked put's) over every declared path, and the skips per arm."""
    keys = sorted({(s, e) for s, e, _, _ in inputs.keys}, key=lambda k: (k[1], k[0]))
    if reporter is not None:
        keys = core_progress.track(
            keys, job="index_put_spread_item78-study", phase="paths", unit="paths",
            key=lambda k: k[0], current=lambda k: k[1], reporter=reporter,
        )  # fmt: skip
    arms = {**ARMS, "naked": None}
    trades: dict[str, list[dict]] = {a: [] for a in arms}
    skips: dict[str, dict[str, int]] = {a: {} for a in arms}
    for sym, exp in keys:
        sessions = inputs.sessions(conn, sym, exp)
        entry = entry_day(exp)
        if entry not in sessions or exp not in sessions:
            for a in arms:
                skips[a]["no entry or expiry chain"] = skips[a].get("no entry or expiry chain", 0) + 1
            continue
        c0, c1 = inputs.chain(conn, sym, entry, exp), inputs.chain(conn, sym, exp, exp)
        for a, d in arms.items():
            t = spread_trade(sym, c0, c1, d) if c0 is not None else {"skip": "no entry chain"}
            if "skip" in t:
                skips[a][t["skip"]] = skips[a].get(t["skip"], 0) + 1
            else:
                trades[a].append(t)
    return trades, skips


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


def study(inputs: Inputs, conn, rate, reporter: core_progress.Reporter | None = None) -> dict:
    trades, skips = collect(inputs, conn, reporter)
    rows = {a: cycle_rows(ts, rate) for a, ts in trades.items()}
    judged = {a: [r for r in rs if r["expiry"] <= LAST_JUDGED] for a, rs in rows.items()}
    block = kit_rule.evaluate(RULE, {a: judged[a] for a in ARMS})
    naked = kit_rule.evaluate(RULE, {"naked": judged["naked"]})["arms"]["naked"]

    def beside(a: str) -> dict:
        js = judged[a]
        names = {}
        for sym in sorted({t["symbol"] for t in trades[a]}):
            own = [t for t in trades[a] if t["symbol"] == sym and t["expiry"] <= LAST_JUDGED]
            names[sym] = mean_excess(cycle_rows(own, rate))
        return {
            "halves": {
                "first": mean_excess([r for r in js if r["expiry"] < SPLIT]),
                "second": mean_excess([r for r in js if r["expiry"] >= SPLIT]),
            },
            "names": names,
            "forward": mean_excess([r for r in rows[a] if r["expiry"] > FORWARD_AFTER]),
            "mean_bp_per_trade": statistics.fmean(t["bp"] for t in trades[a]) if trades[a] else None,
            "skips": skips[a],
        }

    return {
        **block,
        "provisional": True,
        "window": [min((r["expiry"] for r in judged["long5"]), default=None), LAST_JUDGED],
        "reported": {a: beside(a) for a in [*ARMS, "naked"]},
        "naked_margin": naked,
        "corrections": list(REPLAY_CORRECTIONS),
    }


def cmd_plan(args) -> int:
    path = Path(args.out) if args.out else out_dir() / "path_requests.json"
    reqs = requests()
    path.write_text(json.dumps({"requests": reqs}, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(path), "requests": len(reqs)}))
    return 0


def cmd_study(args) -> int:
    inputs = Inputs(out_dir() / "path_requests.json")
    conn = chain_store()
    try:
        with core_progress.Reporter("index_put_spread_item78-study") as rep:
            block = study(inputs, conn, rate_reader(), rep)
        read = inputs.fingerprint(conn)
    finally:
        conn.close()
    doc = {"licence": "derived from licensed vendor data: never commit or publish", "item": 78, **block}
    doc["inputs"] = read
    if args.smoke:
        # A look at the sample, never a verdict: the queue fails it on an arm with no trades.
        doc = {**doc, "smoke": True, "verdict": "smoke"}
    name = "smoke.json" if args.smoke else "results.json"
    path = Path(args.out) if args.out else out_dir() / name
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    for name, fn in (("plan", cmd_plan), ("study", cmd_study)):
        p = sub.add_parser(name)
        p.add_argument("--out", default=None)
        if name == "study":
            p.add_argument("--smoke", action="store_true", help="run on the sample fetched so far")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
