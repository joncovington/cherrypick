"""Item 89: the weekly SPY at-the-money call calendar, 60/90 days, out at +30% or the short's expiry.

The declaration is research/edge-hypotheses-plan.md section 89; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

A published trader's rule: every Friday, sell the at-the-money SPY call about 60 days out and buy the
same strike about 90 days out; take profit at 30% of the debit; no stop; close at the short's
expiry. Live, the trader's 60/90 is the monthly nearest 60 days and the next monthly, which is how
this reads it. The source tuned every setting on 2019-2026, so the years before it are the test.

- `plan`: the chain paths every arm and reported variant needs (`{results}/path_requests.json`), one
  per monthly, oldest first.
- `study`: one run over every entry, judged by RULE (the study kit's three bars) and OVERLAP_RULE
  (the same excesses by a moving-block bootstrap, since a week's trades overlap the next five or so),
  cut at the split. `--smoke` reads what is stored so far. Variants are reported only.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import calendar as cal
from cherrypick.core import fees, treasury

from cherrypick.backtest.study import calendars
from cherrypick.backtest.study import checks as kit_checks
from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.inputs import Inputs

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The trade as declared (the source's live rule: the monthly nearest 60 days short, the next long).
SPEC = {
    "symbol": "SPY",
    "right": "C",
    "entry_weekday": 4,  # Friday; a Friday holiday has no entry
    "short_target_days": 60,
    "corner_target_days": 21,  # reported: the corner the source's heatmap scores near zero
    "first_entry": "2016-01-08",
    "data_end": "2026-11-13",  # the store's last session when declared; later shorts are not taken
    "target_frac": 0.30,  # of the entry debit's mid, read at each session's close
}

#: The kit's pass rule on one row per entry (Friday). The design part is every entry before the
#: source's backtests begin; the holdout is the source's own window, read only after a design pass.
RULE = {
    "t_bar": 2.0,
    "min_n": 120,
    "holdout_min_n": 60,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 89},
    "delta": True,
    "splits": ["2019-01-01"],
}

#: The second rule: the kit's t treats overlapping trades as independent, so the capital and delta
#: excesses must also clear a moving-block bootstrap whose block spans a trade's whole life.
OVERLAP_RULE = {"block": 13, "resamples": 10_000, "seed": 89, "p_bar": 0.025}

#: Reported only, never judged.
STRESS = {"fees": 1.5, "slippage_frac_of_spread": 0.25}
VARIANTS = {
    "day-before": {"exit_day_before": True},  # the trader's live habit
    "no-target": {"target_frac": None},
    "stop-50": {"stop_frac": 0.50},
    "stop-75": {"stop_frac": 0.75},
}
WEEKDAYS = {0: "monday", 1: "tuesday", 2: "wednesday", 3: "thursday"}
BEST_REMOVED = 5

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`.
#: A sample of the paths is fetched first and the study runs on it (`smoke`), so a crash or an arm that
#: judges no trades shows in minutes, not after the whole fetch (#1072); the queue's study-first
#: rule runs the smoke as soon as the sample is in.
CHAIN = (
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/path_requests.json"},
    {
        "step": "fetch-sample",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/path_requests.json", "limit": 20},
    },
    {"step": "smoke", "kind": "script", "args": ["study", "--smoke"], "output": "{results}/smoke.json"},
    {
        "step": "fetch-chains",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/path_requests.json"},
        "repeat": 2,
    },
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)

PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"
RATE_STALE_DAYS = 10


# --------------------------------------------------------------------------- pure: the dates


def short_expiry(entry: date, monthlies: list[date], target: int) -> date | None:
    """The standard monthly nearest `target` calendar days after `entry`; the earlier on a tie."""
    ahead = [m for m in monthlies if m > entry]
    return min(ahead, key=lambda m: (abs((m - entry).days - target), m)) if ahead else None


def trade_dates(spec: dict = SPEC, *, weekday: int | None = None, target: int | None = None) -> list[dict]:
    """Every entry the rule takes, oldest first: each trading day on `weekday` (the spec's Friday by
    default) from the first entry, its short the monthly nearest `target` days out and its long the
    next monthly. An entry whose short expires after the data's last session is not taken."""
    weekday = spec["entry_weekday"] if weekday is None else weekday
    target = spec["short_target_days"] if target is None else target
    first, end = date.fromisoformat(spec["first_entry"]), date.fromisoformat(spec["data_end"])
    monthlies = calendars.monthlies_between(first, end + timedelta(days=200))
    out, day = [], first
    while day <= end:
        if day.weekday() == weekday and cal.is_trading_day(day):
            s = short_expiry(day, monthlies, target)
            if s is not None and s <= end:
                long_ = monthlies[monthlies.index(s) + 1]
                out.append({"entry": day, "short": s, "long": long_})
        day += timedelta(days=1)
    return out


def all_trade_dates(spec: dict = SPEC) -> list[dict]:
    """The dates of every arm and variant: the five weekdays at the stated target, and the short corner."""
    out = []
    for wd in range(5):
        out += trade_dates(spec, weekday=wd)
    out += trade_dates(spec, target=spec["corner_target_days"])
    return out


def requests(spec: dict = SPEC) -> list[dict]:
    """The chain paths the study reads: one request per monthly, from the first entry that holds it
    (as either leg) through the last session any trade holding it can need (the short's expiry),
    oldest first, so a fetch that is cut short has the earliest years."""
    span: dict[date, list[date]] = {}
    for t in all_trade_dates(spec):
        for leg in (t["short"], t["long"]):
            lo, hi = span.get(leg, [t["entry"], t["short"]])
            span[leg] = [min(lo, t["entry"]), max(hi, t["short"])]
    return [
        {
            "symbol": spec["symbol"],
            "expiration": e.isoformat(),
            "start": lo.isoformat(),
            "through": hi.isoformat(),
        }
        for e, (lo, hi) in sorted(span.items())
    ]


# --------------------------------------------------------------------------- pure: one trade


def two_sided(q: dict | None) -> bool:
    return bool(q) and q["bid"] is not None and q["ask"] is not None and q["bid"] > 0 and q["ask"] >= q["bid"]


def mid(q: dict) -> float:
    return (q["bid"] + q["ask"]) / 2.0


def atm_strike(short, long_, right: str) -> float | None:
    """The strike nearest the short expiry's parity forward (the long's when the short has none),
    quoted two-sided in both expiries; the lower on a tie."""
    fwd = short.forward or long_.forward
    if not fwd:
        return None
    ks = [
        k for k in short.by[right] if two_sided(short.by[right].get(k)) and two_sided(long_.by[right].get(k))
    ]
    return min(ks, key=lambda k: (abs(k - fwd), k)) if ks else None


def costs_cfg(stress: dict | None) -> tuple[dict, float]:
    if not stress:
        return {}, 1.0
    return {"tastytrade_costs": {"slippage_frac_of_spread": stress["slippage_frac_of_spread"]}}, stress[
        "fees"
    ]


def price_trade(
    symbol: str,
    right: str,
    strike: float,
    path: dict[date, tuple],
    closes: dict[date, float],
    t: dict,
    *,
    target_frac: float | None,
    stop_frac: float | None = None,
    exit_day_before: bool = False,
    stress: dict | None = None,
) -> dict:
    """One calendar: bought at the entry session's mids plus the suite's slippage and fees, then at each
    later session's close sold at the first of the spread's mid at or above (1 + target_frac) of its
    entry mid, at or under (1 - stop_frac) of it, or the time exit. The time exit is the short's expiry
    session: the long sold at its mid less slippage, the short settled at its intrinsic value on SPY's
    close (an in-the-money short is bought back there, with its fee). `exit_day_before` moves the time
    exit to the session before, both legs sold at their mids. `path` is {session: (short quote, long
    quote)} at the strike, either None where not two-sided. A target or stop on an unquoted session
    waits for the next quoted one; a time exit without a quote is {"skip": ...}."""
    cfg, fee_x = costs_cfg(stress)
    first = path.get(t["entry"])
    if first is None or first[0] is None or first[1] is None:
        return {"skip": "entry_unquoted"}
    m0 = mid(first[1]) - mid(first[0])
    if m0 <= 0:
        return {"skip": "no_debit"}
    open_cost = fees.slippage_dollars(list(first), 1, cfg, symbol=symbol) + fee_x * fees.ic_open_fee(
        symbol, 1, legs=2, sell_legs=1, ndigits=4
    )
    paid = 100.0 * m0 + open_cost
    last = cal.previous_trading_day(t["short"]) if exit_day_before else t["short"]
    day = cal.next_trading_day(t["entry"])
    while day <= last:
        q = path.get(day)
        quoted = q is not None and q[0] is not None and q[1] is not None
        why = None
        if quoted:
            m = mid(q[1]) - mid(q[0])
            if target_frac is not None and m >= (1.0 + target_frac) * m0:
                why = "target"
            elif stop_frac is not None and m <= (1.0 - stop_frac) * m0:
                why = "stop"
        if why is None and day == last:
            why = "time"
        if why is not None:
            if why == "time" and not exit_day_before:
                got, close_cost = settle_at_expiry(symbol, right, strike, q, closes.get(day), cfg, fee_x)
                if got is None:
                    return {"skip": "expiry_unpriced"}
            elif quoted:
                got = 100.0 * (mid(q[1]) - mid(q[0]))
                close_cost = fees.slippage_dollars(
                    list(q), 1, cfg, symbol=symbol
                ) + fee_x * fees.ic_close_fee(symbol, 1, legs=2, sell_legs=1, ndigits=4)
            elif why == "time":
                return {"skip": "exit_unquoted"}
            else:
                day = cal.next_trading_day(day)
                continue
            net = got - close_cost - paid
            return {
                "why": why,
                "exit": day,
                "paid": paid,
                "net": net,
                "ret": net / paid,
                "costs": open_cost + close_cost,
                "days": max((day - t["entry"]).days, 1),
            }
        day = cal.next_trading_day(day)
    return {"skip": "exit_unpriced"}


def settle_at_expiry(
    symbol: str, right: str, strike: float, q: tuple | None, close: float | None, cfg: dict, fee_x: float
) -> tuple[float | None, float]:
    """The short's expiry session: the long sold at its mid less slippage and the short at its intrinsic
    value on SPY's close. Returns (proceeds, costs), proceeds None without a long quote or a close."""
    long_q = q[1] if q is not None else None
    if long_q is None or close is None:
        return None, 0.0
    intrinsic = max(0.0, close - strike) if right == "C" else max(0.0, strike - close)
    legs = 2 if intrinsic > 0 else 1
    cost = fees.slippage_dollars([long_q], 1, cfg, symbol=symbol) + fee_x * fees.ic_close_fee(
        symbol, 1, legs=legs, sell_legs=1, ndigits=4
    )
    return 100.0 * (mid(long_q) - intrinsic), cost


def position_delta(short, long_, right: str, strike: float) -> float | None:
    """The calendar's entry delta in shares: 100 x (long delta - short delta), the kit's Black-76."""
    ds, dl = short.delta(short.by[right][strike]), long_.delta(long_.by[right][strike])
    return None if ds is None or dl is None else 100.0 * (dl - ds)


# --------------------------------------------------------------------------- pure: the rules


def block_bootstrap_p(values: list[float], *, block: int, resamples: int, seed: int) -> float | None:
    """One-sided p that the mean is not above zero: the share of circular moving-block resamples (in
    time order, blocks of `block` rows) whose mean is at or under zero. None for fewer rows than a block."""
    n = len(values)
    if n < block:
        return None
    rng = random.Random(seed)
    hits = 0
    for _ in range(resamples):
        total, taken = 0.0, 0
        while taken < n:
            start = rng.randrange(n)
            for j in range(min(block, n - taken)):
                total += values[(start + j) % n]
            taken += min(block, n - taken)
        if total / n <= 0:
            hits += 1
    return hits / resamples


def overlap_part(rows: list[dict], rule: dict = OVERLAP_RULE) -> dict:
    """The bootstrap p of the capital and delta excesses of one part's rows, in entry order."""
    rows = sorted(rows, key=lambda r: r["date"])
    capital = [r["net"] - kit_checks.bill_on(r["bp"], r["rate"], r["days"]) for r in rows]
    delta = [r["net"] - r["financed"] for r in rows]
    kw = {"block": rule["block"], "resamples": rule["resamples"], "seed": rule["seed"]}
    p = {"capital": block_bootstrap_p(capital, **kw), "delta": block_bootstrap_p(delta, **kw)}
    ok = all(v is not None and v <= rule["p_bar"] for v in p.values())
    return {"n": len(rows), "p": p, "verdict": PASS if ok else FAIL}


def judge_overlap(rows: list[dict], splits: list[str], rule: dict = OVERLAP_RULE) -> dict:
    """OVERLAP_RULE per part, a later part read only after every part before it passed."""
    out, word = [], None
    for part in kit_rule.cut(rows, splits):
        if word is not None and word != PASS:
            out.append({"n": len(part), "verdict": NOT_READ})
            continue
        block = overlap_part(part, rule)
        out.append(block)
        word = block["verdict"]
    return {"parts": out, "verdict": word}


def both(a: str, b: str) -> str:
    if a == PASS and b == PASS:
        return PASS
    if FAIL in (a, b):
        return FAIL
    return NO_VERDICT


def kit_rows(trades: list[dict]) -> list[dict]:
    return [
        {
            "date": t["entry"].isoformat(),
            "net": t["net"],
            "bp": t["paid"],
            "days": t["days"],
            "rate": t["rate"],
            "financed": t["financed"],
        }
        for t in trades
    ]


def judge(arm: str, trades: list[dict], rule: dict = RULE, overlap: dict = OVERLAP_RULE) -> dict:
    """Both rules for the judged arm, from one run cut at the split."""
    rows = kit_rows(trades)
    bars = kit_rule.evaluate(rule, {arm: rows})
    ov = judge_overlap(rows, rule["splits"], overlap)
    return {**bars, "overlap": {arm: ov}, "verdict": {arm: both(bars["verdict"][arm], ov["verdict"])}}


def _mean(xs) -> float | None:
    xs = list(xs)
    return statistics.fmean(xs) if xs else None


def summary(trades: list[dict], split: str) -> dict:
    """What every arm and variant reports: counts, mean net and return, exits, by year, by part and
    without the best (and the worst) five. Never a verdict."""
    nets = [t["net"] for t in trades]
    by_year: dict[str, list[float]] = {}
    for t in trades:
        by_year.setdefault(str(t["entry"].year), []).append(t["net"])
    exits: dict[str, int] = {}
    for t in trades:
        exits[t["why"]] = exits.get(t["why"], 0) + 1
    s = sorted(nets)
    return {
        "trades": len(trades),
        "mean_net": _mean(nets),
        "mean_ret": _mean(t["ret"] for t in trades),
        "win_share": _mean(1.0 if n > 0 else 0.0 for n in nets),
        "mean_costs": _mean(t["costs"] for t in trades),
        "mean_paid": _mean(t["paid"] for t in trades),
        "mean_days": _mean(t["days"] for t in trades),
        "exits": exits,
        "parts": {
            "before": _mean(t["net"] for t in trades if t["entry"].isoformat() < split),
            "from": _mean(t["net"] for t in trades if t["entry"].isoformat() >= split),
        },
        "without_best": _mean(s[:-BEST_REMOVED]) if len(s) > BEST_REMOVED else None,
        "without_worst": _mean(s[BEST_REMOVED:]) if len(s) > BEST_REMOVED else None,
        "by_year": {y: {"n": len(v), "net": sum(v)} for y, v in sorted(by_year.items())},
    }


# --------------------------------------------------------------------------- the store and the bars


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item89"
    d.mkdir(parents=True, exist_ok=True)
    return d


def chain_store():
    from cherrypick.backtest import chainfetch, settings

    store = chainfetch.db_path(settings.load(None))
    return sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True)


def spy_closes(symbol: str) -> dict[date, float]:
    """Raw daily closes from the stock history (technicals' `history.db`): the prices strikes settle on."""
    from cherrypick.core import home

    db = home.data_dir("technicals") / "history.db"
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT date, close FROM bars WHERE symbol = ?", (symbol,)).fetchall()
    finally:
        conn.close()
    return {date.fromisoformat(d[:10]): float(c) for d, c in rows if c is not None}


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


class Chains:
    """Each (session, expiry) chain read once, through the study's own `Inputs`."""

    def __init__(self, inputs: Inputs, conn, symbol: str):
        self.inputs, self.conn, self.symbol, self.memo = inputs, conn, symbol, {}

    def get(self, session: date, expiry: date):
        key = (session, expiry)
        if key not in self.memo:
            self.memo[key] = self.inputs.chain(
                self.conn, self.symbol, session.isoformat(), expiry.isoformat()
            )
        return self.memo[key]


def replay(
    chains: Chains, closes: dict[date, float], rate, dates: list[dict], spec: dict = SPEC, **kw
) -> dict:
    """Every entry of one arm or variant: {"trades": [...], "skipped": {why: count}}."""
    right, trades, skipped = spec["right"], [], {}
    kw.setdefault("target_frac", spec["target_frac"])
    for t in dates:
        short0, long0 = chains.get(t["entry"], t["short"]), chains.get(t["entry"], t["long"])
        why = None
        if short0 is None or long0 is None:
            why = "entry_unstored"
        else:
            strike = atm_strike(short0, long0, right)
            if strike is None:
                why = "no_strike"
        if why is None:
            path, day = {}, t["entry"]
            while day <= t["short"]:
                s, lg = chains.get(day, t["short"]), chains.get(day, t["long"])
                qs = s.by[right].get(strike) if s else None
                ql = lg.by[right].get(strike) if lg else None
                path[day] = (qs if two_sided(qs) else None, ql if two_sided(ql) else None)
                day = cal.next_trading_day(day)
            res = price_trade(spec["symbol"], right, strike, path, closes, t, **kw)
            why = res.get("skip")
        if why is None:
            delta = position_delta(short0, long0, right, strike)
            long1 = chains.get(res["exit"], t["long"])
            if delta is None or not long0.forward or long1 is None or not long1.forward:
                why = "no_forward"
        if why is not None:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        r = rate(t["entry"].isoformat())
        trades.append(
            {
                **t,
                **res,
                "strike": strike,
                "delta": delta,
                "rate": r,
                "financed": kit_checks.financed_delta(
                    delta, long0.forward, long1.forward, res["paid"], r, res["days"]
                ),
            }
        )
    return {"trades": trades, "skipped": skipped}


# --------------------------------------------------------------------------- the commands


def cmd_plan(args) -> int:
    path = Path(args.out) if args.out else out_dir() / "path_requests.json"
    path.write_text(json.dumps({"requests": requests()}, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(path), "paths": len(requests())}))
    return 0


def cmd_study(args) -> int:
    inputs = Inputs(out_dir() / "path_requests.json")
    conn = chain_store()
    split = RULE["splits"][0]
    try:
        chains = Chains(inputs, conn, SPEC["symbol"])
        closes, rate = spy_closes(SPEC["symbol"]), rate_reader()
        fridays = trade_dates()
        stated = replay(chains, closes, rate, fridays)
        reported = {name: replay(chains, closes, rate, fridays, **kw) for name, kw in VARIANTS.items()}
        reported["stress"] = replay(chains, closes, rate, fridays, stress=STRESS)
        for wd, name in WEEKDAYS.items():
            reported[name] = replay(chains, closes, rate, trade_dates(weekday=wd))
        reported["corner-21"] = replay(chains, closes, rate, trade_dates(target=SPEC["corner_target_days"]))
        read = inputs.fingerprint(conn)
    finally:
        conn.close()
    doc = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 89,
        **judge("stated", stated["trades"]),
        "skipped": {"stated": stated["skipped"], **{k: v["skipped"] for k, v in reported.items()}},
        "summary": {
            "stated": summary(stated["trades"], split),
            **{k: summary(v["trades"], split) for k, v in reported.items()},
        },
        "inputs": read,
    }
    if args.smoke:
        # A look at what is stored so far, never a verdict: the queue fails it on an arm with no trades.
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
            p.add_argument("--smoke", action="store_true", help="run on the paths stored so far")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
