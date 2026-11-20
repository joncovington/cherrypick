"""Item 94: the mean-reversion signal as a short-put entry time, a retest of items 17 and 18.

The declaration is research/edge-hypotheses-plan.md section 94; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

- `plan`: find every `mr-300m` signal and its random-day baselines (item 14's rules, restated here),
  and write the entry-day chain requests (`{results}/path_requests.json`) and the trades
  (`{results}/trades.json`).
- `study`: one run over the whole window, judged by the declared RULE (the study kit's three bars,
  cut at its split) and TIMING_RULE (signal entries against their baselines), written to
  `{results}/results.json` as the kit's verdict block.

Each trade reads one end-of-day chain, its entry session's, and is held to expiry, settled at the
name's raw close on the expiry session (the technicals stock history), so no other session is fetched.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import bs, fees, legbars, treasury
from cherrypick.core import calendar as cal
from cherrypick.core import progress as core_progress
from cherrypick.core.procs import default_workers

from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.inputs import Inputs

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The pass rule as data. Rows are one per monthly expiry: every signal entry on that expiry across
#: the names pooled (net and buying power summed), so names entered the same week are not counted as
#: independent trades.
RULE = {
    "t_bar": 2.0,
    "min_n": 40,
    "holdout_min_n": 12,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 94},
    "delta": True,
    "splits": ["2025-07-01"],
}

#: The second rule, the timing's own information: per entry, net over buying power, the signal entries
#: against their baselines (the same name, a random eligible session of the same year, the same rules).
#: One-sided t on the difference of means, standard errors clustered by expiry. Design: t >= t_bar.
#: Holdout, read only after a design pass of both rules: the difference above zero. Either side under
#: its floor is no verdict.
TIMING_RULE = {
    "t_bar": 2.0,
    "min_entries": 100,
    "holdout_min_entries": 30,
    "cluster": "expiry",
    "split": "2025-07-01",
}

#: The trade, declared (the plan's section 94 states each in words).
SPEC = {
    "setup": "reversion",
    "dollar_floor": 300e6,
    "first_entry": "2021-01-04",
    "last_expiry": "2026-11-20",
    "dte": (30, 45),
    "baselines": 5,
    "seed": 20261119,
    "short_delta": 0.25,
    "reported_delta": 0.16,
    "fills": "mid with core's slippage model (verdict), core option fees, core assignment fee",
    "buying_power": "strike x 100 (cash-secured)",
    "exit": "held to expiry, settled at the name's raw close on the expiry session",
}

#: The judged arm, and what is reported beside it, never judged.
ARMS = ("put25",)
REPORTED = ("put16", "put25-tight", "base25", "base16")

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

    d = paths.results_dir() / "research" / "item94"
    d.mkdir(parents=True, exist_ok=True)
    return d


def chain_store():
    from cherrypick.backtest import paths, settings

    db = paths.vendor_dir(settings.load()) / "eod_chains.db"
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


# ------------------------------------------------------------------------------------------------
# Pure: the expiry, the exclusions, the requests.


def monthly(y: int, m: int) -> date:
    """The standard monthly expiry: the third Friday, or the trading day before it on a holiday."""
    d = date(y, m, 15)
    d += timedelta(days=(4 - d.weekday()) % 7)
    return d if cal.is_trading_day(d) else cal.previous_trading_day(d)


MONTHLIES = sorted(monthly(y, m) for y in range(2020, 2028) for m in range(1, 13))


def expiry_for(entry: str) -> str | None:
    """The latest standard monthly 30-45 calendar days after `entry`, or None."""
    d = date.fromisoformat(entry)
    lo, hi = SPEC["dte"]
    inside = [e for e in MONTHLIES if lo <= (e - d).days <= hi]
    return inside[-1].isoformat() if inside else None


def any_between(sorted_days: list[str], lo: str, hi: str) -> bool:
    """Whether a day in `sorted_days` falls in [lo, hi]."""
    i = bisect.bisect_left(sorted_days, lo)
    return i < len(sorted_days) and sorted_days[i] <= hi


def why_not(entry: str, suspects_between, reports: list[str]) -> tuple[str | None, str | None]:
    """(expiry, None) when a put can be entered on `entry`, else (None, why). `suspects_between(entry,
    expiry)` says whether a suspected unadjusted split falls inside the trade."""
    exp = expiry_for(entry)
    if exp is None:
        return None, "no monthly 30-45 days out"
    if exp > SPEC["last_expiry"]:
        return None, "expiry after the window"
    if any_between(reports, entry, exp):
        return None, "earnings report inside the trade"
    if suspects_between(entry, exp):
        return None, "suspected unadjusted split inside the trade"
    return exp, None


def path_requests(rows: list[dict]) -> list[dict]:
    """One request per distinct (symbol, expiry, entry): the entry session's chain alone. The signal
    entries come first, so the chain's 20-path sample holds signal trades for the smoke run."""
    first: dict[tuple[str, str, str], int] = {}
    for r in rows:
        k = (r["symbol"], r["expiry"], r["entry"])
        first[k] = min(first.get(k, 1), 0 if r["kind"] == "signal" else 1)
    keys = sorted(first, key=lambda k: (first[k], k[2], k[0], k[1]))
    return [{"symbol": s, "expiration": e, "start": d, "through": d} for s, e, d in keys]


# ------------------------------------------------------------------------------------------------
# Planning (a worker process per batch of names): item 14's signal and baseline rules, restated.

_ctx: dict = {}


def _init(reports: dict) -> None:
    from cherrypick.technicals import paths, store

    conn = store.connect(paths.history_db())
    _ctx.update(conn=conn, data_end=conn.execute("SELECT MAX(date) FROM bars").fetchone()[0], reports=reports)


def plan_name(sym: str) -> dict:
    from cherrypick.technicals import setups, study

    out: dict = {"symbol": sym, "rows": [], "dropped": Counter(), "short": 0}
    nm = study.load(_ctx["conn"], sym, _ctx["data_end"])
    if nm is None:
        return out
    reports = _ctx["reports"].get(sym, [])
    floor = SPEC["dollar_floor"]

    def suspects_between(entry: str, exp: str) -> bool:
        if not nm.suspects:
            return False
        a, b = bisect.bisect_left(nm.dates, entry), bisect.bisect_right(nm.dates, exp)
        return any(a <= s < b for s in nm.suspects)

    def eligible(j: int) -> bool:
        """In the universe at $300M a day the session before entry, outside a corporate-action shadow."""
        return (
            j >= 1
            and nm.member[j - 1]
            and (nm.dollar_volume[j - 1] or 0.0) >= floor
            and not (nm.shadow and nm.shadow[j])
        )

    by_year: dict[str, list[int]] = defaultdict(list)
    for j in range(1, len(nm.dates)):
        if nm.dates[j] >= SPEC["first_entry"] and eligible(j):
            by_year[nm.dates[j][:4]].append(j)
    allow = lambda i: (nm.dollar_volume[i] or 0.0) >= floor  # noqa: E731
    for t in setups.run(SPEC["setup"], nm.readings, allow=allow):
        i, j = t.entry, t.entry + 1
        if j >= len(nm.dates) or nm.dates[j] < SPEC["first_entry"]:
            continue
        if not study.counted(nm, SPEC["setup"], i):
            continue
        exp, why = why_not(nm.dates[j], suspects_between, reports)
        if why:
            out["dropped"][why] += 1
            continue
        tid = f"{sym}:{nm.dates[i]}"
        out["rows"].append(_row(tid, "signal", None, sym, nm.dates[i], nm.dates[j], exp))
        rng = random.Random(f"{SPEC['seed']}:{tid}")
        pool = [k for k in by_year.get(nm.dates[j][:4], []) if k != j]
        rng.shuffle(pool)
        drawn = 0
        for k in pool:
            if drawn == SPEC["baselines"]:
                break
            bexp, bwhy = why_not(nm.dates[k], suspects_between, reports)
            if bwhy:
                continue
            drawn += 1
            out["rows"].append(_row(f"{tid}#b{drawn}", "baseline", tid, sym, None, nm.dates[k], bexp))
        if drawn < SPEC["baselines"]:
            out["short"] += 1
    return out


def _row(tid, kind, of, sym, signal, entry, exp) -> dict:
    return {"id": tid, "kind": kind, "of": of, "symbol": sym, "signal": signal, "entry": entry, "expiry": exp}


def _earnings() -> dict:
    """{symbol: sorted report dates} from the local Dolt clone's earnings calendar (exact from 2020)."""
    from cherrypick.technicals import land

    db = land._connect(land.DEFAULTS, "earnings")
    try:
        cur = db.cursor()
        cur.execute("SELECT act_symbol, date FROM earnings_calendar")
        reports: dict[str, list[str]] = {}
        for sym, d in cur.fetchall():
            reports.setdefault(str(sym).replace("/", "."), []).append(str(d)[:10])
    finally:
        db.close()
    return {k: sorted(v) for k, v in reports.items()}


def universe_names() -> list[str]:
    """Every name that ever traded $300M a day, less the leveraged, inverse and volatility funds (a
    long setup on an inverse fund is a short; item 17's exclusion)."""
    from cherrypick.technicals import paths
    from cherrypick.technicals.skate import leveraged_funds

    c = sqlite3.connect(f"file:{paths.history_db().as_posix()}?mode=ro", uri=True)
    try:
        rows = c.execute(
            "SELECT symbol FROM peaks WHERE max_dollar_volume >= ? ORDER BY symbol", (SPEC["dollar_floor"],)
        )
        names = {r[0] for r in rows}
    finally:
        c.close()
    return sorted(names - {s.replace("/", ".") for s in leveraged_funds()})


def plan(workers: int, rep=None) -> dict:
    reports = _earnings()
    names = universe_names()
    rows: list[dict] = []
    dropped: Counter = Counter()
    short = 0
    if rep is not None:
        rep.split("names", names, unit="names")
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(reports,)) as ex:
        futs = {ex.submit(plan_name, s): s for s in names}
        for f in as_completed(futs):
            got = f.result()
            rows += got["rows"]
            dropped.update(got["dropped"])
            short += got["short"]
            if rep is not None:
                rep.advance("names", key=futs[f])
    rows.sort(key=lambda r: (r["entry"], r["symbol"], r["id"]))
    return {
        "spec": SPEC,
        "names": len(names),
        "signals": sum(1 for r in rows if r["kind"] == "signal"),
        "baselines": sum(1 for r in rows if r["kind"] == "baseline"),
        "short_of_baselines": short,
        "dropped": dict(dropped),
        "rows": rows,
    }


# ------------------------------------------------------------------------------------------------
# Pure: one put, entered at the entry chain, held to expiry.


def _mid(q: dict) -> float:
    return (q["bid"] + q["ask"]) / 2.0


def tight(q: dict) -> bool:
    """The suite's spread bar on one leg (`core.legbars`): at most 10% of its mid or a nickel. The
    end-of-day store holds no open interest, so that bar is not read."""
    bars = legbars.LEG_BARS
    return legbars.two_sided(q) and legbars._tight(q["ask"] - q["bid"], _mid(q), bars)


def put_trade(symbol: str, expiry: str, chain, settle: float | None, short_delta: float) -> dict:
    """One cash-secured short put nearest `short_delta`, or {"skip": why}. `settle` is the name's raw
    close on the expiry session."""
    if chain is None:
        return {"skip": "no entry chain"}
    if chain.forward is None:
        return {"skip": "no entry forward"}
    q = chain.nearest("P", short_delta)
    if q is None or not legbars.two_sided(q):
        return {"skip": "no short put"}
    if settle is None:
        return {"skip": "no expiry close"}
    credit = 100.0 * _mid(q)
    owed = 100.0 * bs.intrinsic(settle, q["strike"], "P")
    slip = fees.slippage_dollars([q], 1, {}, symbol=symbol)
    fee = fees.ic_open_fee(symbol, 1, legs=1, sell_legs=1, ndigits=4)
    settlement = (
        fees.assignment_round_trip_fee(100, q["strike"], settle, direction="long", ndigits=4)
        if owed > 0
        else 0.0
    )
    net = credit - owed - fee - settlement - slip
    return {
        "symbol": symbol,
        "expiry": expiry,
        "entry": chain.session,
        "strike": q["strike"],
        "credit": round(credit, 4),
        "gross": round(credit - owed, 4),
        "fees": round(fee, 4),
        "settlement": round(settlement, 4),
        "slippage": round(slip, 4),
        "net": round(net, 4),
        "bp": round(100.0 * q["strike"], 4),
        "delta": -100.0 * chain.delta(q),
        "f0": chain.forward,
        "f1": settle,
        "assigned": owed > 0,
        "tight": tight(q),
        "days": max((date.fromisoformat(expiry) - date.fromisoformat(chain.session)).days, 1),
    }


def cycle_rows(entries: list[dict], rate) -> list[dict]:
    """The kit's rows, one per expiry: the arm's entries on it pooled, net and buying power summed,
    the days weighted by buying power, each entry's delta financed on its own buying power, summed.
    A row is dated by its earliest entry."""
    by: dict[str, list[dict]] = defaultdict(list)
    for t in entries:
        by[t["expiry"]].append(t)
    out = []
    for e in sorted(by):
        ts = sorted(by[e], key=lambda t: t["entry"])
        r = rate(ts[0]["entry"])
        bp = sum(t["bp"] for t in ts)
        financed = sum(ck.financed_delta(t["delta"], t["f0"], t["f1"], t["bp"], r, t["days"]) for t in ts)
        out.append({
            "date": ts[0]["entry"],
            "expiry": e,
            "net": sum(t["net"] for t in ts),
            "bp": bp,
            "days": sum(t["days"] * t["bp"] for t in ts) / bp,
            "rate": r,
            "financed": financed,
            "entries": len(ts),
        })  # fmt: skip
    return out


def clustered_difference(signal: list[dict], base: list[dict]) -> dict:
    """Mean net over buying power of the signal entries less the baselines', with a one-sided t from
    standard errors clustered by expiry (a two-parameter regression on a signal indicator, CR1).
    Copied from item 92's, the cluster key named."""
    xs = [(t["net"] / t["bp"], 1.0, t["expiry"]) for t in signal] + [
        (t["net"] / t["bp"], 0.0, t["expiry"]) for t in base
    ]
    n, nk = len(xs), len(signal)
    if nk == 0 or nk == n:
        return {"signal": nk, "baseline": n - nk, "difference": None, "t": None}
    mk = statistics.fmean(x for x, d, _ in xs if d)
    ms = statistics.fmean(x for x, d, _ in xs if not d)
    b = mk - ms
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
    row = [inv[1][0], inv[1][1]]
    var = scale * sum(row[i] * meat[i][j] * row[j] for i in range(2) for j in range(2))
    se = math.sqrt(var) if var == var and var > 0 else None
    return {"signal": nk, "baseline": n - nk, "clusters": G, "difference": b, "t": (b / se) if se else None}


def timing_verdict(design: dict, holdout: dict, rule_design_passed: bool) -> dict:
    """TIMING_RULE: the design difference at t >= t_bar with both sides at their floor; the holdout,
    read only after a design pass of both rules, above zero with both sides at theirs."""
    tr = TIMING_RULE
    if min(design["signal"], design["baseline"]) < tr["min_entries"] or design["t"] is None:
        d_word = ck.NO_VERDICT
    else:
        d_word = ck.PASS if design["t"] >= tr["t_bar"] else ck.FAIL
    out = {"design": {**design, "verdict": d_word}}
    if d_word != ck.PASS or not rule_design_passed:
        out["holdout"] = {"verdict": kit_rule.NOT_READ}
        out["verdict"] = d_word
        return out
    if (
        min(holdout["signal"], holdout["baseline"]) < tr["holdout_min_entries"]
        or holdout["difference"] is None
    ):
        h_word = ck.NO_VERDICT
    else:
        h_word = ck.PASS if holdout["difference"] > 0 else ck.FAIL
    out["holdout"] = {**holdout, "verdict": h_word}
    out["verdict"] = h_word
    return out


def summary(ts: list[dict]) -> dict:
    if not ts:
        return {"entries": 0}
    return {
        "entries": len(ts),
        "win_rate": sum(1 for t in ts if t["net"] > 0) / len(ts),
        "assigned": sum(1 for t in ts if t["assigned"]),
        "net": sum(t["net"] for t in ts),
        "mean_net": statistics.fmean(t["net"] for t in ts),
        "mean_return_on_bp": statistics.fmean(t["net"] / t["bp"] for t in ts),
        "credit": statistics.fmean(t["credit"] for t in ts),
        "slippage": statistics.fmean(t["slippage"] for t in ts),
        "tight": sum(1 for t in ts if t["tight"]),
    }


def judge(entries: dict[str, list[dict]], rate) -> dict:
    """The verdict block from every arm's entries: RULE on the judged arm's expiry rows, TIMING_RULE on
    its entries against the baselines', both needed for a pass. Reported arms are judged by RULE beside,
    never counted."""
    split = RULE["splits"][0]
    rows = {a: cycle_rows(ts, rate) for a, ts in entries.items()}
    block = kit_rule.evaluate(RULE, {a: rows[a] for a in ARMS})
    reported = kit_rule.evaluate(RULE, {a: rows[a] for a in REPORTED})
    timing, verdicts = {}, {}
    for a in ARMS:
        sig, base = entries[a], entries["base" + a[3:]]
        parts = {
            "design": clustered_difference(
                [t for t in sig if t["entry"] < split], [t for t in base if t["entry"] < split]
            ),
            "holdout": clustered_difference(
                [t for t in sig if t["entry"] >= split], [t for t in base if t["entry"] >= split]
            ),
        }
        design_pass = block["arms"][a]["parts"][0]["verdict"] == ck.PASS
        timing[a] = timing_verdict(parts["design"], parts["holdout"], design_pass)
        words = (block["verdict"][a], timing[a]["verdict"])
        verdicts[a] = (
            ck.PASS if words == (ck.PASS, ck.PASS) else ck.FAIL if ck.FAIL in words else ck.NO_VERDICT
        )

    def beside(a: str) -> dict:
        ts = entries[a]
        return {
            "design": summary([t for t in ts if t["entry"] < split]),
            "holdout": summary([t for t in ts if t["entry"] >= split]),
            "years": {
                y: summary([t for t in ts if t["entry"][:4] == y])
                for y in sorted({t["entry"][:4] for t in ts})
            },
        }

    return {
        **block,
        "timing_rule": timing,
        "verdict_rule_only": block["verdict"],
        "verdict": verdicts,
        "reported": {"rule": reported, "beside": {a: beside(a) for a in (*ARMS, *REPORTED)}},
    }


# ------------------------------------------------------------------------------------------------
# The run.


def raw_closes(symbols: list[str]) -> dict[str, dict[str, float]]:
    """Each name's raw daily closes from the technicals stock history, read-only."""
    from cherrypick.technicals import paths

    c = sqlite3.connect(f"file:{paths.history_db().as_posix()}?mode=ro", uri=True)
    try:
        out: dict[str, dict[str, float]] = {}
        for s in symbols:
            out[s] = {d: v for d, v in c.execute("SELECT date, close FROM bars WHERE symbol = ?", (s,)) if v}
        return out
    finally:
        c.close()


RATE_STALE_DAYS = 10


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


def trades(inputs: Inputs, conn, plan_rows: list[dict], closes: dict, rep=None) -> tuple[dict, dict]:
    """Every arm's entries, and the skips by arm. Only rows whose entry chain the requests file names
    are read, so a smoke run on the sample reads what the sample holds."""
    declared = {(s, e, d) for s, e, d, _ in inputs.keys}
    rows = [r for r in plan_rows if (r["symbol"], r["expiry"], r["entry"]) in declared]
    entries: dict[str, list[dict]] = {a: [] for a in (*ARMS, *REPORTED)}
    skips: dict[str, Counter] = {a: Counter() for a in entries}
    if rep is not None:
        rep.split("entries", [r["symbol"] for r in rows], unit="entries")
    for r in rows:
        chain = inputs.chain(conn, r["symbol"], r["entry"], r["expiry"])
        settle = closes.get(r["symbol"], {}).get(r["expiry"])
        for delta, sig_arm, base_arm in (
            (SPEC["short_delta"], "put25", "base25"),
            (SPEC["reported_delta"], "put16", "base16"),
        ):
            arm = sig_arm if r["kind"] == "signal" else base_arm
            t = put_trade(r["symbol"], r["expiry"], chain, settle, delta)
            if "skip" in t:
                skips[arm][t["skip"]] += 1
                continue
            t = {**t, "id": r["id"], "of": r["of"]}
            entries[arm].append(t)
            if arm == "put25" and t["tight"]:
                entries["put25-tight"].append(t)
        if rep is not None:
            rep.advance("entries", key=r["symbol"], current=r["entry"])
    return entries, {a: dict(v) for a, v in skips.items()}


def cmd_plan(args) -> int:
    with core_progress.Reporter("item94-plan") as rep:
        doc = plan(args.workers, rep)
    (out_dir() / "trades.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    path = Path(args.out) if args.out else out_dir() / "path_requests.json"
    path.write_text(json.dumps({"requests": path_requests(doc["rows"])}, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(path), "signals": doc["signals"], "baselines": doc["baselines"]}))
    return 0


def cmd_study(args) -> int:
    plan_doc = json.loads((out_dir() / "trades.json").read_text(encoding="utf-8"))
    inputs = Inputs(out_dir() / "path_requests.json")
    closes = raw_closes(sorted({r["symbol"] for r in plan_doc["rows"]}))
    conn = chain_store()
    job = "item94-smoke" if args.smoke else "item94-study"
    try:
        with core_progress.Reporter(job) as rep:
            entries, skips = trades(inputs, conn, plan_doc["rows"], closes, rep)
        block = judge(entries, rate_reader())
        read = inputs.fingerprint(conn)
    finally:
        conn.close()
    doc = {"licence": "derived from licensed vendor data: never commit or publish", "item": 94, **block}
    doc["skips"] = skips
    doc["plan"] = {k: v for k, v in plan_doc.items() if k != "rows"}
    doc["corrections"] = list(REPLAY_CORRECTIONS)
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
        if name == "plan":
            p.add_argument("--workers", type=int, default=default_workers())
        if name == "study":
            p.add_argument("--smoke", action="store_true", help="run on the sample fetched so far")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
