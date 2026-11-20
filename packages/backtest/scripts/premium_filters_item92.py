"""Item 92: IV rank and realised-under-implied vol as entry filters for monthly short premium.

The declaration is research/edge-hypotheses-plan.md section 92; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

Only the filters' cycle counts were computed before declaring, to set the floors (no trade, price
path or outcome was read). The paths are item 46's, so nothing new is fetched.

- `plan`: write the chain-path requests this study needs (`{results}/path_requests.json`).
- `study`: one run over the whole history, judged by the declared RULE (the study kit's three bars,
  cut at its splits), written to `{results}/results.json` as the kit's verdict block.
"""

from __future__ import annotations

import argparse
import json
import math
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

#: The pass rule as data, filled by the declaration before any run. Every None must become a value:
#: the kit refuses a rule with one, so nothing is judged until the declaration says how.
#: Rows are one per monthly cycle: the entries the arm took that cycle, the funds pooled.
RULE = {
    "t_bar": 2.4,
    "min_n": 30,
    "holdout_min_n": 30,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 92},
    "delta": True,
    "splits": ["2021-06-01"],
}

#: The second rule, the filter's own information: per entry, net over buying power, the entries the
#: filter keeps against the ones it skips on the same structure. One-sided t on the difference of
#: means, standard errors clustered by cycle. Design: t >= t_bar. Holdout, read only after a design
#: pass of both rules: the difference above zero. Either side under its floor is no verdict.
FILTER_RULE = {
    "t_bar": 2.4,
    "min_entries": 20,
    "holdout_min_entries": 20,
    "cluster": "cycle",
    "split": "2021-06-01",
}

#: The base trade, declared (the plan's section 92 states each in words).
SPEC = {
    "funds": {"SPY": "VIX", "QQQ": "VXN", "IWM": "RVX", "GLD": "GVZ"},
    "expiry": "the monthly (third-Friday) expiry",
    "entry": "the first trading session 60 calendar days before the expiry, or the next; none under 45",
    "first_expiry": "2016-03-18",
    "last_expiry": "2026-10-16",
    "structures": {
        "put": {"short_delta": 0.16},
        "spread": {"short_delta": 0.20, "long_delta": 0.10},
    },
    "exit": {
        "profit_target_pct_of_credit": 50,
        "stop_loss_multiple_of_credit": 1.0,
        "spread_stop_pct_of_max_loss_when_credit_stop_cannot_fire": 50,
        "close_days_to_expiry": 21,
        "marks": "each session's close; a session without a mark holds",
    },
    "fills": "mid with core's slippage model (verdict), core option fees",
    "buying_power": {"put": "strike x 100 (cash-secured)", "spread": "width x 100 less the credit"},
    "ivr_lookback": 252,
    "hv_returns": 30,
}

#: The judged arms: each structure with each filter, read on the session before entry.
FILTERS = {"ivr30": "vol index IV rank >= 30", "hv_lt_iv": "30-day realised vol < the vol index"}
ARMS = tuple(f"{s}-{f}" for s in SPEC["structures"] for f in FILTERS)
#: Reported, never judged: the base (no filter), the recipe's own thresholds, and the low-rank side.
REPORTED = ("put-base", "spread-base", "put-ivr50", "put-ivr70", "spread-ivr50", "spread-ivr70")

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
    },
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item92"
    d.mkdir(parents=True, exist_ok=True)
    return d


def item46_dir() -> Path:
    from cherrypick.backtest import paths

    return paths.results_dir() / "research" / "item46"


def requests(source: Path | None = None) -> list[dict]:
    """Item 46's requested paths for the four funds, each once: every monthly expiry from 2016-03-18
    to 2026-10-16, from 66 days before it through the expiry, so the entries 45-60 days out and every
    daily mark to the exit are inside them and nothing new is fetched."""
    f = source or item46_dir() / "path_requests.json"
    if not f.exists():
        raise SystemExit(f"no item 46 requests at {f}: item 46's plan has not run")
    seen, out = set(), []
    for r in json.loads(f.read_text(encoding="utf-8"))["requests"]:
        if r["symbol"] not in SPEC["funds"]:
            continue
        if not SPEC["first_expiry"] <= r["expiration"] <= SPEC["last_expiry"]:
            continue
        key = (r["symbol"], r["expiration"], r["start"], r["through"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return sorted(out, key=lambda r: (r["expiration"], r["symbol"]))


def chain_store():
    import sqlite3

    from cherrypick.backtest import paths, settings

    db = paths.vendor_dir(settings.load()) / "eod_chains.db"
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


# ------------------------------------------------------------------------------------------------
# Pure: the entry, the filters, the legs, the marks and the exit.

TARGET_DAYS, MIN_DAYS = 60, 45


def entry_day(expiry: str) -> str | None:
    """The first trading session 60 calendar days before `expiry`, or the next; None under 45 days."""
    d = date.fromisoformat(expiry) - timedelta(days=TARGET_DAYS)
    if not cal.is_trading_day(d):
        d = cal.next_trading_day(d)
    return d.isoformat() if (date.fromisoformat(expiry) - d).days >= MIN_DAYS else None


def iv_rank(closes: list[float], lookback: int) -> float | None:
    """The last close's place in the `lookback` closes ending with it, 0-100; None when short or flat."""
    if len(closes) < lookback:
        return None
    window = closes[-lookback:]
    lo, hi = min(window), max(window)
    return None if hi == lo else 100.0 * (window[-1] - lo) / (hi - lo)


def realised_vol(closes: list[float], returns: int) -> float | None:
    """The sample standard deviation of the last `returns` daily log returns, annualised by sqrt(252),
    in vol points; None when short."""
    if len(closes) < returns + 1:
        return None
    c = closes[-(returns + 1) :]
    r = [math.log(b / a) for a, b in zip(c, c[1:], strict=False)]
    return statistics.stdev(r) * math.sqrt(252) * 100.0


def filters(rank: float | None, hv: float | None, iv: float | None) -> dict[str, bool] | None:
    """Every filter's state on one entry, judged and reported; None when an input is missing."""
    if rank is None or hv is None or iv is None:
        return None
    return {"ivr30": rank >= 30, "ivr50": rank >= 50, "ivr70": rank >= 70, "hv_lt_iv": hv < iv}


def _mid(q: dict) -> float:
    return (q["bid"] + q["ask"]) / 2.0


def open_legs(chain, structure: str) -> list[dict] | str:
    """The structure's legs at entry (the short put first), or why there are none."""
    if chain is None or chain.forward is None:
        return "no entry forward"
    deltas = SPEC["structures"][structure]
    short = chain.nearest("P", deltas["short_delta"])
    if short is None or not short["bid"]:
        return "no short leg"
    if "long_delta" not in deltas:
        return [short]
    long = chain.nearest("P", deltas["long_delta"], beyond=short["strike"])
    if long is None or not long["ask"]:
        return "no long leg"
    return [short, long]


def close_cost(chain, legs: list[dict]) -> float | None:
    """What buying the position back costs at mid, whole-position dollars; None without a two-sided
    quote on every leg (no mark that session)."""
    if chain is None:
        return None
    total = 0.0
    for i, leg in enumerate(legs):
        q = chain.by["P"].get(leg["strike"])
        if (
            q is None
            or q["bid"] is None
            or q["ask"] is None
            or q["ask"] <= 0
            or q["bid"] < 0
            or q["bid"] > q["ask"]
        ):
            return None
        total += (1.0 if i == 0 else -1.0) * _mid(q) * 100.0
    return total


def exit_rule(credit: float, width: float | None, cost: float, days_left: int) -> str | None:
    """The owner's credit-trade rules on one mark: the stop first, then the target, then the time."""
    ex = SPEC["exit"]
    loss = cost - credit
    stop_at = ex["stop_loss_multiple_of_credit"] * credit
    if width is not None and credit + stop_at > width:  # the credit stop cannot fire on this spread
        stop_at = ex["spread_stop_pct_of_max_loss_when_credit_stop_cannot_fire"] / 100.0 * (width - credit)
    if loss >= stop_at:
        return "stop"
    if cost <= (1 - ex["profit_target_pct_of_credit"] / 100.0) * credit:
        return "target"
    if days_left <= ex["close_days_to_expiry"]:
        return "time"
    return None


def settle_forward(chain) -> float | None:
    """The expiry close's parity forward, a zero bid counted (item 78's rule): K + C - P at the strike
    whose call and put mids are closest."""
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


def trade(symbol: str, structure: str, expiry: str, path: list[tuple[str, object]]) -> dict:
    """One entry managed to its exit, or {"skip": why}. `path` is [(session, Chain)] from the entry
    session through the expiry, in order, the entry first."""
    entry_session, c0 = path[0]
    legs = open_legs(c0, structure)
    if isinstance(legs, str):
        return {"skip": legs}
    credit = 100.0 * (_mid(legs[0]) - sum(_mid(q) for q in legs[1:]))
    if credit <= 0:
        return {"skip": "no credit"}
    width = 100.0 * (legs[0]["strike"] - legs[1]["strike"]) if len(legs) == 2 else None
    sells = 1
    slip_open = fees.slippage_dollars(legs, 1, {}, symbol=symbol)
    fee_open = fees.ic_open_fee(symbol, 1, legs=len(legs), sell_legs=sells, ndigits=4)
    exit_session, kind, cost, f1, settlement, slip_close, fee_close = None, None, None, None, 0.0, 0.0, 0.0
    for session, chain in path[1:]:
        if session >= expiry:
            break
        mark = close_cost(chain, legs)
        if mark is None:
            continue
        why = exit_rule(credit, width, mark, (date.fromisoformat(expiry) - date.fromisoformat(session)).days)
        if why:
            quotes = [chain.by["P"][q["strike"]] for q in legs]
            exit_session, kind, cost, f1 = session, why, mark, chain.forward
            slip_close = fees.slippage_dollars(quotes, 1, {}, symbol=symbol)
            fee_close = fees.ic_close_fee(symbol, 1, legs=len(legs), sell_legs=len(legs) - 1, ndigits=4)
            break
    if exit_session is None:
        last_session, last_chain = path[-1]
        f1 = settle_forward(last_chain) if last_session == expiry else None
        if f1 is None:
            return {"skip": "no exit mark and no expiry forward"}
        owed = bs.intrinsic(f1, legs[0]["strike"], "P") - sum(
            bs.intrinsic(f1, q["strike"], "P") for q in legs[1:]
        )
        exit_session, kind, cost = expiry, "settled", 100.0 * owed
        itm = [q for q in legs if bs.intrinsic(f1, q["strike"], "P") > 0]
        settlement = sum(
            fees.assignment_round_trip_fee(100, q["strike"], f1, direction="long", ndigits=4) for q in itm
        )
    f0 = c0.forward
    gross = credit - cost
    slippage = slip_open + slip_close
    fee = fee_open + fee_close
    net = gross - fee - settlement - slippage
    bp = width - credit if width is not None else 100.0 * legs[0]["strike"]
    delta = -100.0 * c0.delta(legs[0]) + sum(100.0 * c0.delta(q) for q in legs[1:])
    return {
        "symbol": symbol,
        "structure": structure,
        "cycle": expiry,
        "entry": entry_session,
        "exit": exit_session,
        "exit_kind": kind,
        "strikes": [q["strike"] for q in legs],
        "credit": round(credit, 4),
        "gross": round(gross, 4),
        "fees": round(fee, 4),
        "settlement": round(settlement, 4),
        "slippage": round(slippage, 4),
        "net": round(net, 4),
        "bp": round(bp, 4),
        "delta": delta,
        "f0": f0,
        "f1": f1 if f1 is not None else f0,
        "days": max((date.fromisoformat(exit_session) - date.fromisoformat(entry_session)).days, 1),
    }


def cycle_rows(entries: list[dict], rate) -> list[dict]:
    """The kit's rows, one per cycle: the arm's entries that cycle across the funds pooled, net and
    buying power summed, the days held weighted by buying power, each entry's delta financed on its
    own buying power and summed."""
    by: dict[str, list[dict]] = defaultdict(list)
    for t in entries:
        by[t["cycle"]].append(t)
    out = []
    for c in sorted(by):
        ts = by[c]
        r = rate(ts[0]["entry"])
        bp = sum(t["bp"] for t in ts)
        financed = sum(ck.financed_delta(t["delta"], t["f0"], t["f1"], t["bp"], r, t["days"]) for t in ts)
        out.append({
            "date": ts[0]["entry"],
            "cycle": c,
            "net": sum(t["net"] for t in ts),
            "bp": bp,
            "days": sum(t["days"] * t["bp"] for t in ts) / bp,
            "rate": r,
            "financed": financed,
            "funds": len(ts),
        })  # fmt: skip
    return out


def clustered_difference(kept: list[dict], skipped: list[dict]) -> dict:
    """Mean net over buying power of the kept entries less the skipped ones', with a one-sided t from
    standard errors clustered by cycle (a two-parameter regression on a kept indicator, CR1)."""
    xs = [(t["net"] / t["bp"], 1.0, t["cycle"]) for t in kept] + [
        (t["net"] / t["bp"], 0.0, t["cycle"]) for t in skipped
    ]
    n, nk = len(xs), len(kept)
    if nk == 0 or nk == n:
        return {"kept": nk, "skipped": n - nk, "difference": None, "t": None}
    mk = statistics.fmean(x for x, d, _ in xs if d)
    ms = statistics.fmean(x for x, d, _ in xs if not d)
    b = mk - ms
    # X = [1, d]; (X'X)^-1 for a binary regressor; residuals around each group's mean.
    sxx = [[n, nk], [nk, nk]]
    det = sxx[0][0] * sxx[1][1] - sxx[0][1] * sxx[1][0]
    inv = [[sxx[1][1] / det, -sxx[0][1] / det], [-sxx[1][0] / det, sxx[0][0] / det]]
    groups: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for x, d, g in xs:
        u = x - (mk if d else ms)
        groups[g][0] += u
        groups[g][1] += u * d
    meat = [[0.0, 0.0], [0.0, 0.0]]
    for s0, s1 in groups.values():
        meat[0][0] += s0 * s0
        meat[0][1] += s0 * s1
        meat[1][0] += s1 * s0
        meat[1][1] += s1 * s1
    G = len(groups)
    scale = (G / (G - 1)) * ((n - 1) / (n - 2)) if G > 1 and n > 2 else float("nan")
    # Var(b) = [inv . meat . inv]_{11}
    row = [inv[1][0], inv[1][1]]
    var = scale * sum(row[i] * meat[i][j] * row[j] for i in range(2) for j in range(2))
    se = math.sqrt(var) if var == var and var > 0 else None
    return {"kept": nk, "skipped": n - nk, "clusters": G, "difference": b, "t": (b / se) if se else None}


def filter_verdict(design: dict, holdout: dict, rule_design_passed: bool) -> dict:
    """FILTER_RULE on one arm: the design difference at t >= t_bar with both sides at their floor;
    the holdout, read only after a design pass of both rules, above zero with both sides at theirs."""
    fr = FILTER_RULE
    if min(design["kept"], design["skipped"]) < fr["min_entries"] or design["t"] is None:
        d_word = ck.NO_VERDICT
    else:
        d_word = ck.PASS if design["t"] >= fr["t_bar"] else ck.FAIL
    out = {"design": {**design, "verdict": d_word}}
    if d_word != ck.PASS or not rule_design_passed:
        # The holdout is read only after a design pass of both rules; the arm's own verdict joins them.
        out["holdout"] = {"verdict": kit_rule.NOT_READ}
        out["verdict"] = d_word
        return out
    if min(holdout["kept"], holdout["skipped"]) < fr["holdout_min_entries"] or holdout["difference"] is None:
        h_word = ck.NO_VERDICT
    else:
        h_word = ck.PASS if holdout["difference"] > 0 else ck.FAIL
    out["holdout"] = {**holdout, "verdict": h_word}
    out["verdict"] = h_word
    return out


# ------------------------------------------------------------------------------------------------
# The run.

STRUCTURES = tuple(SPEC["structures"])
ALL_FILTERS = ("ivr30", "ivr50", "ivr70", "hv_lt_iv")


def vol_closes(index: str) -> list[tuple[str, float]]:
    from cherrypick.core import volindex

    return sorted(volindex.closes(index).items())


def fund_closes(symbol: str) -> list[tuple[str, float]]:
    """The fund's daily closes from the stock history (raw), read-only."""
    import sqlite3

    from cherrypick.core import home

    db = home.data_dir() / "technicals" / "history.db"
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT date, close FROM bars WHERE symbol = ? ORDER BY date", (symbol,)
        ).fetchall()
    finally:
        conn.close()


def before(series: list[tuple[str, float]], session: str) -> list[float]:
    """The values strictly before `session`, oldest first."""
    return [v for d, v in series if d < session and v is not None and v > 0]


def collect(inputs: Inputs, conn, vols: dict, closes: dict, rep=None) -> tuple[list[dict], dict]:
    """Every entry of both structures over every declared path, each with its filter states, and the
    skips by structure. `rep` is a progress Reporter, when the caller keeps one."""
    keys = sorted({(s, e) for s, e, _, _ in inputs.keys}, key=lambda k: (k[1], k[0]))
    entries, skips = [], {s: defaultdict(int) for s in STRUCTURES}
    if rep is not None:
        rep.split("paths", [sym for sym, _ in keys], unit="paths")
    for sym, exp in keys:
        if rep is not None:
            rep.advance("paths", key=sym, current=exp)
        day = entry_day(exp)
        sessions = inputs.sessions(conn, sym, exp)
        if day is None or day not in sessions:
            for s in STRUCTURES:
                skips[s]["no entry session"] += 1
            continue
        index = SPEC["funds"][sym]
        ix = before(vols[index], day)
        states = filters(
            iv_rank(ix, SPEC["ivr_lookback"]),
            realised_vol(before(closes[sym], day), SPEC["hv_returns"]),
            ix[-1] if ix else None,
        )
        if states is None:
            for s in STRUCTURES:
                skips[s]["no filter inputs"] += 1
            continue
        path = [(s, inputs.chain(conn, sym, s, exp)) for s in sessions if s >= day]
        for s in STRUCTURES:
            t = trade(sym, s, exp, path)
            if "skip" in t:
                skips[s][t["skip"]] += 1
            else:
                entries.append({**t, "filters": states})
    return entries, {s: dict(v) for s, v in skips.items()}


def arm_entries(entries: list[dict], arm: str) -> list[dict]:
    structure, flt = arm.split("-", 1)
    return [t for t in entries if t["structure"] == structure and (flt == "base" or t["filters"][flt])]


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


RATE_STALE_DAYS = 10
CRASH = ("2020-02-01", "2020-04-30")


def summary(ts: list[dict]) -> dict:
    if not ts:
        return {"entries": 0}
    kinds: dict[str, int] = defaultdict(int)
    for t in ts:
        kinds[t["exit_kind"]] += 1
    return {
        "entries": len(ts),
        "win_rate": sum(1 for t in ts if t["net"] > 0) / len(ts),
        "net": sum(t["net"] for t in ts),
        "mean_net": statistics.fmean(t["net"] for t in ts),
        "mean_return_on_bp": statistics.fmean(t["net"] / t["bp"] for t in ts),
        "credit": statistics.fmean(t["credit"] for t in ts),
        "days": statistics.fmean(t["days"] for t in ts),
        "exits": dict(kinds),
    }


def study(inputs: Inputs, conn, rate, vols: dict, closes: dict, rep=None) -> dict:
    entries, skips = collect(inputs, conn, vols, closes, rep)
    split = RULE["splits"][0]
    names = [*ARMS, *REPORTED, "put-ivr_lt30", "spread-ivr_lt30"]
    by_arm = {}
    for a in names:
        if a.endswith("ivr_lt30"):
            by_arm[a] = [
                t for t in entries if t["structure"] == a.split("-")[0] and not t["filters"]["ivr30"]
            ]
        else:
            by_arm[a] = arm_entries(entries, a)
    rows = {a: cycle_rows(ts, rate) for a, ts in by_arm.items()}
    block = kit_rule.evaluate(RULE, {a: rows[a] for a in ARMS})
    reported = kit_rule.evaluate(RULE, {a: rows[a] for a in names if a not in ARMS})

    filter_rule = {}
    for a in ARMS:
        structure, flt = a.split("-", 1)
        base = [t for t in entries if t["structure"] == structure]
        part = {}
        for name, keep in (
            ("design", lambda t: t["entry"] < split),
            ("holdout", lambda t: t["entry"] >= split),
        ):
            ts = [t for t in base if keep(t)]
            part[name] = clustered_difference(
                [t for t in ts if t["filters"][flt]], [t for t in ts if not t["filters"][flt]]
            )
        rule_design = block["arms"][a]["parts"][0]["verdict"] == ck.PASS
        filter_rule[a] = filter_verdict(part["design"], part["holdout"], rule_design)

    verdicts = {}
    for a in ARMS:
        words = (block["verdict"][a], filter_rule[a]["verdict"])
        verdicts[a] = (
            ck.PASS if words == (ck.PASS, ck.PASS) else ck.FAIL if ck.FAIL in words else ck.NO_VERDICT
        )

    def beside(a: str) -> dict:
        ts = by_arm[a]
        return {
            "design": summary([t for t in ts if t["entry"] < split]),
            "holdout": summary([t for t in ts if t["entry"] >= split]),
            "funds": {f: summary([t for t in ts if t["symbol"] == f]) for f in SPEC["funds"]},
            "years": {
                y: summary([t for t in ts if t["entry"][:4] == y])
                for y in sorted({t["entry"][:4] for t in ts})
            },
            "without_2020_crash": summary([t for t in ts if not CRASH[0] <= t["entry"] <= CRASH[1]]),
        }

    return {
        **block,
        "filter_rule": filter_rule,
        "verdict_rule_only": block["verdict"],
        "verdict": verdicts,
        "reported": {"rule": reported, "beside": {a: beside(a) for a in names}},
        "skips": skips,
        "corrections": list(REPLAY_CORRECTIONS),
    }


def cmd_plan(args) -> int:
    path = Path(args.out) if args.out else out_dir() / "path_requests.json"
    path.write_text(json.dumps({"requests": requests()}, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(path)}))
    return 0


def cmd_study(args) -> int:
    inputs = Inputs(out_dir() / "path_requests.json")
    conn = chain_store()
    vols = {ix: vol_closes(ix) for ix in set(SPEC["funds"].values())}
    closes = {f: fund_closes(f) for f in SPEC["funds"]}
    try:
        with core_progress.Reporter("premium_filters_item92-study") as rep:
            block = study(inputs, conn, rate_reader(), vols, closes, rep)
        read = inputs.fingerprint(conn)
    finally:
        conn.close()
    doc = {"licence": "derived from licensed vendor data: never commit or publish", "item": 92, **block}
    doc["inputs"] = {
        **read,
        "vol_indexes": {
            ix: [len(v), v[0][0] if v else None, v[-1][0] if v else None] for ix, v in vols.items()
        },
        "fund_closes": {
            f: [len(v), v[0][0] if v else None, v[-1][0] if v else None] for f, v in closes.items()
        },
    }
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
