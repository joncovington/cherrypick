"""Item 82: MEIC's live rule skipping days whose first hour is wide against its recent median.

The declaration is research/edge-hypotheses-plan.md section 82; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

Item 81 found that a first hour wide against its own 20-session median forecasts a wider rest of the
day. MEIC's live arm (`live-1400`: one condor entered 14:00-14:30) sells against that range. This asks
whether skipping its entry on such days raises what it earns.

- **No new replay, no holdout spent.** The trades are `live-1400`'s in two stored MEIC runs of one
  spec (`RUNS`): the design window's and the holdout window's, read from `session_results` (the net,
  paper's arithmetic) and the run's own ledgers (each condor's buying power). Together they are one
  run over 2022-05-16..2026-10-12, cut at 2025-07-01.
- **The feature** is item 81's `w`, from SPX's one-minute prices in the licensed store, known at 10:30.
- **Two literals decide it.** `RULE` (the kit's three bars, on the gated arm `skip_wide`) and
  `GATE_RULE` (the paired question: do the skipped days' trades earn less than the kept ones). The
  pure helpers are copied from item 81's script (a study does not load another study's script).

- `study`: writes `{results}/results.json` with `verdict` (`{"gate": word, "skip_wide": word}`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sqlite3
import statistics
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.backtest.study import rule as kit_rule

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The kit's three bars, on the gated arm, over one run cut at the split.
RULE = {
    "t_bar": 2.0,
    "min_n": 200,
    "holdout_min_n": 60,
    "tail": {"per_year": "sample", "resamples": 10000, "seed": 82},
    "delta": False,
    "splits": ["2025-07-01"],
}

#: The paired question, fixed by the declaration before any run.
GATE_RULE = {
    "arm": "live-1400",
    "skip_w_at_or_above": 0.405465,  # ln 1.5: a first hour half again as wide as its 20-session median
    "neighbours": [0.223144, 0.693147],  # ln 1.25 and ln 2, reported only
    "min_skipped": 40,
    "holdout_min_skipped": 15,
    "draws": 10000,
    "seed": 82,
    "alpha": 0.05,
    "halves": "2023-12-01",
    "split": "2025-07-01",
    "symbol": "SPX",
    "min_samples": 380,
    "max_gap_days": 4,
    "ib_end": "10:30",
    "ib_median_sessions": 20,
}

#: The stored runs read (one spec of live-1400 across both windows).
RUNS = {"design": "20261028-065012-0174bd", "holdout": "20261028-173006-c9b430"}

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`. Nothing is fetched
#: and nothing is replayed.
CHAIN = ({"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},)

ET = ZoneInfo("America/New_York")
PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"
RATE_STALE_DAYS = 10


# --------------------------------------------------------------------------- the feature (item 81's)


def hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def minute_of(ts: float) -> int:
    t = datetime.fromtimestamp(ts, ET)
    return t.hour * 60 + t.minute


def first_hour_widths(sessions: dict[str, list[tuple[float, float]]], gate: dict) -> dict[str, float]:
    """Each session's `w`: the log of its first hour's range over the prior close, over the median of
    the same ratio across the prior `ib_median_sessions` full sessions. A short session or a calendar
    gap restarts the history, as in item 81."""
    ib_end = hhmm(gate["ib_end"])
    n = gate["ib_median_sessions"]
    out: dict[str, float] = {}
    hist: list[float] = []
    prev: str | None = None
    for d in sorted(sessions):
        s = sessions[d]
        if len(s) < gate["min_samples"]:
            prev, hist = None, []
            continue
        if (
            prev is not None
            and (date.fromisoformat(d) - date.fromisoformat(prev)).days > gate["max_gap_days"]
        ):
            prev, hist = None, []
        ib = [p for ts, p in s if minute_of(ts) < ib_end]
        if prev is not None and ib:
            frac = (max(ib) - min(ib)) / sessions[prev][-1][1]
            if len(hist) >= n:
                med = statistics.median(hist[-n:])
                if frac > 0 and med > 0:
                    out[d] = math.log(frac / med)
            hist.append(frac)
        prev = d
    return out


# --------------------------------------------------------------------------- the gate test (pure)


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def gap(trades: list[dict], skip: set[str]) -> float | None:
    """The kept trades' mean net less the skipped trades' mean net (positive: skipping helps)."""
    kept = [t["net"] for t in trades if t["date"] not in skip]
    gone = [t["net"] for t in trades if t["date"] in skip]
    if not kept or not gone:
        return None
    return _mean(kept) - _mean(gone)


def permutation_p(trades: list[dict], skip: set[str], *, draws: int, seed: int) -> float | None:
    """One-sided: the share of random skip sets of the same size whose gap is at least the observed."""
    observed = gap(trades, skip)
    k = sum(1 for t in trades if t["date"] in skip)
    if observed is None or k == 0 or k == len(trades):
        return None
    rng = random.Random(seed)
    nets = [t["net"] for t in trades]
    total, n = sum(nets), len(nets)
    hits = 0
    for _ in range(draws):
        pick = rng.sample(range(n), k)
        gone = sum(nets[i] for i in pick)
        g = (total - gone) / (n - k) - gone / k
        if g >= observed - 1e-12:
            hits += 1
    return (hits + 1) / (draws + 1)


def judge_gate(trades: list[dict], w: dict[str, float], gate: dict, *, draws: int | None = None) -> dict:
    """The design part (skipped mean below zero and below the kept mean, permutation p, both halves),
    then the holdout (skipped mean below the kept mean), read only after a design pass."""
    cut = gate["skip_w_at_or_above"]
    skip = {d for d, v in w.items() if v >= cut}
    judged = [t for t in trades if t["date"] in w]
    design = [t for t in judged if t["date"] < gate["split"]]
    holdout = [t for t in judged if t["date"] >= gate["split"]]
    n_skip = sum(1 for t in design if t["date"] in skip)
    out: dict = {
        "unjudged": len(trades) - len(judged),
        "design": {"n": len(design), "skipped": n_skip},
        "holdout": {"n": len(holdout), "skipped": sum(1 for t in holdout if t["date"] in skip)},
    }
    if n_skip < gate["min_skipped"]:
        out["design"]["verdict"] = NO_VERDICT
        out["holdout"]["verdict"] = NOT_READ
        return {**out, "verdict": NO_VERDICT}
    skipped_mean = _mean([t["net"] for t in design if t["date"] in skip])
    g = gap(design, skip)
    p = permutation_p(design, skip, draws=draws or gate["draws"], seed=gate["seed"])
    halves = {
        "first": gap([t for t in design if t["date"] < gate["halves"]], skip),
        "second": gap([t for t in design if t["date"] >= gate["halves"]], skip),
    }
    checks = {
        "skipped_below_zero": skipped_mean is not None and skipped_mean < 0,
        "p": p is not None and p < gate["alpha"],
        "halves": all(v is not None and v > 0 for v in halves.values()),
    }
    out["design"].update({"skipped_mean": skipped_mean, "gap": g, "p": p, "halves": halves, "checks": checks})
    if not all(checks.values()):
        out["design"]["verdict"] = FAIL
        out["holdout"]["verdict"] = NOT_READ
        return {**out, "verdict": FAIL}
    out["design"]["verdict"] = PASS
    hg = gap(holdout, skip)
    word = PASS if hg is not None and hg > 0 else FAIL
    provisional = word == PASS and out["holdout"]["skipped"] < gate["holdout_min_skipped"]
    out["holdout"].update({"gap": hg, "verdict": word, "provisional": provisional})
    return {**out, "verdict": word, "provisional": provisional}


def gated(trades: list[dict], w: dict[str, float], cut: float) -> list[dict]:
    """The gated arm's trades: every trade on a day whose `w` is known and under the cut."""
    return [t for t in trades if t["date"] in w and w[t["date"]] < cut]


# --------------------------------------------------------------------------- the stores (read-only)


def fingerprint(sessions: dict[str, list]) -> dict:
    """What the run read from the minute store, as counts and a hash of them: never a price."""
    lines = "\n".join(f"{d}:{len(s)}" for d, s in sorted(sessions.items()))
    return {
        "sessions": len(sessions),
        "stamps": sum(len(s) for s in sessions.values()),
        "first": min(sessions) if sessions else None,
        "last": max(sessions) if sessions else None,
        "sha256": hashlib.sha256(lines.encode()).hexdigest(),
    }


def load_sessions(
    conn: sqlite3.Connection, symbol: str, through: str
) -> dict[str, list[tuple[float, float]]]:
    out: dict[str, list[tuple[float, float]]] = {}
    for d, ts, spot in conn.execute(
        "SELECT trade_date, ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date <= ? AND spot > 0 "
        "ORDER BY trade_date, ts",
        (symbol, through),
    ):
        out.setdefault(d, []).append((float(ts), float(spot)))
    return out


def condor_bp(rows: list[sqlite3.Row]) -> float | None:
    """A session's buying power: each condor's wider wing less its credit, times its multiplier and
    quantity (one side can lose at expiry), summed."""
    total = 0.0
    for r in rows:
        wing = max(r["put_wing"] or r["wing_width"] or 0.0, r["call_wing"] or r["wing_width"] or 0.0)
        if not wing or r["net_credit"] is None:
            return None
        total += (wing - r["net_credit"]) * (r["dollar_multiplier"] or 100.0) * (r["quantity"] or 1)
    return total if total > 0 else None


def run_trades(results_conn: sqlite3.Connection, runs_dir: Path, run_id: str, arm: str, rate) -> list[dict]:
    """One run's trade days for the arm: the session's net from `session_results`, its buying power
    from the run's own ledger, one day held, the bill before the session."""
    out = []
    for session, net in results_conn.execute(
        "SELECT session, net FROM session_results WHERE run_id = ? AND arm = ? AND trades > 0 "
        "AND net IS NOT NULL ORDER BY session",
        (run_id, arm),
    ):
        db = runs_dir / run_id / f"{session}.db"
        lc = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        lc.row_factory = sqlite3.Row
        try:
            rows = lc.execute("SELECT * FROM ic_trades WHERE arm = ?", (arm,)).fetchall()
        finally:
            lc.close()
        bp = condor_bp(rows)
        if bp is None:
            continue
        out.append({"date": session, "net": float(net), "bp": bp, "days": 1, "rate": rate(session)})
    return out


def _rate_reader():
    from cherrypick.core import treasury

    seen: dict[str, float] = {}

    def rate(session: str) -> float:
        if session not in seen:
            got = treasury.yield_before(session, "3m")
            if got is None or (date.fromisoformat(session) - got[0]).days > RATE_STALE_DAYS:
                raise SystemExit(f"no Treasury 3-month yield within {RATE_STALE_DAYS} days before {session}")
            seen[session] = got[1] / 100.0
        return seen[session]

    return rate


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item82"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_study(args) -> int:
    from cherrypick.backtest import paths, results, settings

    s = settings.load(None)
    gate = GATE_RULE
    rconn = results.connect()
    try:
        for w, run_id in RUNS.items():
            if rconn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is None:
                raise SystemExit(f"stored run {run_id} ({w}) is missing")
        rate = _rate_reader()
        runs_dir = paths.results_dir() / "runs"
        trades = [
            t for run_id in RUNS.values() for t in run_trades(rconn, runs_dir, run_id, gate["arm"], rate)
        ]
        specs = {
            w: sorted(
                {
                    r[0]
                    for r in rconn.execute(
                        "SELECT DISTINCT spec_hash FROM session_results WHERE run_id = ? AND arm = ?",
                        (rid, gate["arm"]),
                    )
                }
            )
            for w, rid in RUNS.items()
        }
    finally:
        rconn.close()
    if len({tuple(v) for v in specs.values()}) != 1:
        raise SystemExit(f"the runs do not share one spec of {gate['arm']}: {specs}")
    mconn = sqlite3.connect(f"file:{paths.vendor_dir(s) / 'SPX-60s.db'}?mode=ro", uri=True)
    try:
        sessions = load_sessions(mconn, gate["symbol"], max(t["date"] for t in trades))
    finally:
        mconn.close()
    w = first_hour_widths(sessions, gate)
    cut = gate["skip_w_at_or_above"]
    block = judge_gate(trades, w, gate)
    judged = [t for t in trades if t["date"] in w]
    bars_skip = kit_rule.evaluate(RULE, {"skip_wide": gated(trades, w, cut)})
    bars_control = kit_rule.evaluate(RULE, {"control": judged})
    reported = {
        "control_bars": bars_control,
        "neighbours": {
            str(c): {
                "gap": gap(
                    [t for t in judged if t["date"] < gate["split"]], {d for d, v in w.items() if v >= c}
                )
            }
            for c in gate["neighbours"]
        },
    }
    doc = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 82,
        "provisional": bool(block.get("provisional")),
        "rule": RULE,
        "gate_rule": gate,
        "runs": RUNS,
        "spec": specs,
        "window": {
            "from": judged[0]["date"] if judged else None,
            "through": judged[-1]["date"] if judged else None,
            "splits": RULE["splits"],
        },
        "gate": block,
        "arms": bars_skip["arms"],
        "bars": bars_skip,
        "verdict": {"gate": block["verdict"], "skip_wide": bars_skip["verdict"]["skip_wide"]},
        "reported": reported,
        "inputs": fingerprint(sessions),
    }
    path = Path(args.out) if args.out else out_dir() / "results.json"
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"], "provisional": doc["provisional"]}))
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
