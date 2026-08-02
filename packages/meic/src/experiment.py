"""GEX study read-out: does the GEX entry gate earn the samples it cuts?

Read-only over the paper ledger. No broker, no network, no writes.

**The primary read is a WITHIN-ARM counterfactual, not an A/B comparison.** All three GEX gates are
pure entry filters, and every fill stamps the GEX state it saw (`gex_net_at_entry`,
`gex_positive_at_entry`, `gamma_flip_at_entry`, `gex_spot_at_entry`). So the ungated arm's own trades
contain both sides of the question: split them by the recorded state and you learn exactly what each
gate would have blocked, on the same days, with every trade informative. That is far more efficient
than comparing two arms' P&L -- which matters here, because sessions are the unit of independence and
there will only ever be a handful of them.

The A/B read (`compare_arms`) is secondary. It is the only thing that can see the path-dependent
portfolio effect -- a blocked entry frees a concurrency slot and changes what trades later -- which
the within-arm split structurally cannot.

**Sessions, not trades, are the sample.** Same-day trades share a regime, so they are not independent
observations; MEIC's own experiment docs already say the effective N is the day count. Every function
here reports `sessions` beside `trades`, and `bootstrap_difference` resamples whole SESSIONS rather
than individual trades. Below ~30 clusters even cluster-robust intervals run optimistic, so treat a
14-session reading as directional and nothing more.
"""

from __future__ import annotations

import os
import random
import sqlite3
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from cherrypick.core.metrics import calibration_reading  # noqa: E402

import paths as _paths  # noqa: E402

CONTROL_ARM = "gex-open"  # ungated: takes the entries the live policy refuses
TREATMENT_ARM = "gex-blocked"  # gated: runs the live policy

# Below this many sessions no interval is quoted. PROMOTION_RULE.min_days is 14 and MEIC's own
# experiment docs put the bar at 14-20 sessions for a regime-level claim; quoting a bootstrap
# interval over 3 clusters would dress up noise as a measurement.
MIN_SESSIONS_FOR_INTERVAL = 14


def _connect(db_path=None) -> sqlite3.Connection:
    path = db_path or str(_paths.paper_db_path())
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _records(conn, arm: str, start: str | None = None) -> list[dict]:
    """Closed trades for one arm, in the shape `calibration_reading` expects.

    `session` is the exit date, matching how the orchestrator's `meic_ic` reader defines a session,
    so a reading here and a reading from `cherrypick calibrate` can never disagree about which day
    a trade belongs to.
    """
    where = ["risk_profile = ?", "exit_time IS NOT NULL"]
    params: list = [arm]
    if start:
        where.append("substr(exit_time, 1, 10) >= ?")
        params.append(start)
    rows = conn.execute(
        f"SELECT symbol, risk_profile, pnl, fees, exit_time, slippage_dollars, "  # noqa: S608 -- fixed clauses
        f"gex_net_at_entry, gex_positive_at_entry, gamma_flip_at_entry, gex_spot_at_entry "
        f"FROM ic_trades WHERE {' AND '.join(where)}",
        params,
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "session": (r["exit_time"] or "")[:10],
                "symbol": r["symbol"],
                "net_pnl": (r["pnl"] or 0.0) - (r["fees"] or 0.0),
                "slippage": r["slippage_dollars"],
                "gex_net": r["gex_net_at_entry"],
                "gex_positive": r["gex_positive_at_entry"],
                "gamma_flip": r["gamma_flip_at_entry"],
                "gex_spot": r["gex_spot_at_entry"],
            }
        )
    return out


# --------------------------------------------------------------------------- gate predicates
def _split_block_negative(rec: dict) -> str:
    """The live default: refuse when net GEX is CONFIRMED negative. Unknown GEX is not blocked."""
    if rec["gex_positive"] is None:
        return "unknown"
    return "allowed" if rec["gex_positive"] else "blocked"


def _split_require_positive(rec: dict) -> str:
    """The strict variant: refuse unless GEX is confirmed POSITIVE, so unknown is blocked too."""
    if rec["gex_positive"] is None:
        return "blocked"
    return "allowed" if rec["gex_positive"] else "blocked"


def _split_flip_distance(rec: dict, min_pct: float) -> str:
    """The magnitude variant: require positive GEX AND spot at least `min_pct` from the gamma flip."""
    if rec["gex_positive"] is None or rec["gamma_flip"] is None or not rec["gex_spot"]:
        return "unknown"
    if not rec["gex_positive"]:
        return "blocked"
    return "allowed" if abs(rec["gex_spot"] - rec["gamma_flip"]) / rec["gex_spot"] >= min_pct else "blocked"


def _reading(group: list[dict]) -> dict:
    r = calibration_reading(group)
    r["sessions"] = len({g["session"] for g in group if g["session"]})
    return r


def counterfactual(records: list[dict], gate: str = "block_negative", min_pct: float = 0.005) -> dict:
    """Split ONE arm's own trades into what `gate` would have blocked vs allowed.

    This is the primary read. `unknown` is carried as its own bucket and never folded into either
    side -- a trade whose GEX we could not read is a fact about instrumentation, and silently
    counting it as "allowed" would let a coverage gap masquerade as a result.
    """
    splitter = {
        "block_negative": _split_block_negative,
        "require_positive": _split_require_positive,
        "flip_distance": lambda rec: _split_flip_distance(rec, min_pct),
    }.get(gate)
    if splitter is None:
        raise ValueError(f"counterfactual: unknown gate {gate!r}")

    groups: dict[str, list] = {"blocked": [], "allowed": [], "unknown": []}
    for rec in records:
        groups[splitter(rec)].append(rec)

    out = {"gate": gate, "buckets": {k: _reading(v) for k, v in groups.items() if v}}
    if gate == "flip_distance":
        out["min_flip_distance_pct"] = min_pct
    blocked, allowed = groups["blocked"], groups["allowed"]
    if blocked and allowed:
        b = sum(r["net_pnl"] for r in blocked) / len(blocked)
        a = sum(r["net_pnl"] for r in allowed) / len(allowed)
        # Positive => the gate is REMOVING trades that were worse than the ones it keeps, i.e. it is
        # earning its cut. Negative => it is cutting trades that were better, and costing money.
        out["mean_advantage_per_trade"] = round(a - b, 2)
        out["verdict_direction"] = "gate helps" if a > b else "gate hurts"
    out["sessions"] = len({r["session"] for r in records if r["session"]})
    out["trades"] = len(records)
    return out


def flip_distance_sweep(records: list[dict], steps=(0.001, 0.002, 0.003, 0.005, 0.0075, 0.01)) -> list[dict]:
    """`flip_distance` across a range of thresholds, so the parameter is read off a curve rather than
    guessed at. A single hand-picked value is how the uncalibrated thresholds elsewhere in this suite
    got there."""
    return [counterfactual(records, "flip_distance", min_pct=s) for s in steps]


# --------------------------------------------------------------------------- A/B (secondary)
def compare_arms(conn, start: str | None = None) -> dict:
    """gex-open vs gex-blocked. Secondary to `counterfactual`, and the only read that can see the
    path-dependent portfolio effect the within-arm split cannot."""
    return {arm: _reading(_records(conn, arm, start)) for arm in (CONTROL_ARM, TREATMENT_ARM)}


def bootstrap_difference(group_a: list[dict], group_b: list[dict], iterations=2000, seed=0) -> dict | None:
    """Percentile interval for (mean net P&L of A) − (mean of B), resampling whole SESSIONS.

    Sessions are the resampling unit because same-day trades share a regime and are not independent;
    treating each trade as its own observation is what makes a 6-session result look like a
    120-observation one. Returns None below `MIN_SESSIONS_FOR_INTERVAL` rather than quoting an
    interval nothing supports -- and even at 14 sessions, cluster counts under ~30 are known to give
    optimistic coverage, so read the result as directional.
    """
    by_session: dict[str, dict[str, list]] = {}
    for tag, group in (("a", group_a), ("b", group_b)):
        for r in group:
            by_session.setdefault(r["session"], {"a": [], "b": []})[tag].append(r["net_pnl"])
    sessions = [s for s in by_session if s]
    if len(sessions) < MIN_SESSIONS_FOR_INTERVAL:
        return {
            "ok": False,
            "reason": f"{len(sessions)} sessions < {MIN_SESSIONS_FOR_INTERVAL} required",
            "sessions": len(sessions),
        }

    rng = random.Random(seed)
    diffs = []
    for _ in range(iterations):
        picked = [by_session[rng.choice(sessions)] for _ in sessions]
        a_vals = [v for p in picked for v in p["a"]]
        b_vals = [v for p in picked for v in p["b"]]
        if not a_vals or not b_vals:
            continue
        diffs.append(sum(a_vals) / len(a_vals) - sum(b_vals) / len(b_vals))
    if not diffs:
        return {"ok": False, "reason": "no resample produced both groups", "sessions": len(sessions)}
    diffs.sort()
    lo = diffs[int(0.025 * len(diffs))]
    hi = diffs[min(int(0.975 * len(diffs)), len(diffs) - 1)]
    return {
        "ok": True,
        "sessions": len(sessions),
        "point_estimate": round(sum(diffs) / len(diffs), 2),
        "ci95": [round(lo, 2), round(hi, 2)],
        # An interval spanning zero means the data does not distinguish the two, which at these
        # sample sizes is the expected answer for a long while.
        "excludes_zero": (lo > 0) or (hi < 0),
    }


def run(db_path: str | None = None, start: str | None = None) -> dict:
    """The whole read-out: coverage, the three within-arm counterfactuals, the sweep, and the A/B."""
    conn = _connect(db_path)
    try:
        control = _records(conn, CONTROL_ARM, start)
        treated = _records(conn, TREATMENT_ARM, start)
        with_gex = [r for r in control if r["gex_positive"] is not None]
        return {
            "ok": True,
            "control_arm": CONTROL_ARM,
            "treatment_arm": TREATMENT_ARM,
            "start": start,
            "coverage": {
                "control_trades": len(control),
                "control_sessions": len({r["session"] for r in control if r["session"]}),
                "control_with_gex": len(with_gex),
                "treatment_trades": len(treated),
                "min_sessions_for_interval": MIN_SESSIONS_FOR_INTERVAL,
            },
            "counterfactuals": {
                g: counterfactual(control, g) for g in ("block_negative", "require_positive")
            },
            "flip_distance_sweep": flip_distance_sweep(control),
            "arms": compare_arms(conn, start),
            "bootstrap": bootstrap_difference(control, treated),
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="MEIC GEX study read-out (read-only)")
    ap.add_argument("--db", help=f"paper DB (default: {os.path.basename(str(_paths.paper_db_path()))})")
    ap.add_argument("--start", help="only sessions on/after this date (YYYY-MM-DD)")
    args = ap.parse_args()
    print(json.dumps(run(args.db, args.start), indent=2, default=str))
