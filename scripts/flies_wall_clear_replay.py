"""Flies legged entries against the GEX wall on their completing side, and the wall-clear gate (2026-10-05).

    python scripts/flies_wall_clear_replay.py                       # control, the SPX era to 10-04
    python scripts/flies_wall_clear_replay.py --arm time_window --arm vol-floor
    python scripts/flies_wall_clear_replay.py --ahead 10 --past 5 --json
    python scripts/flies_wall_clear_replay.py --since 2026-10-05    # completion-rule era; never pooled

Read-only; runnable from anywhere; ledgers resolve off `$CHERRYPICK_HOME` (default `~/.cherrypick`).

**The question.** A legged entry sells a credit spread and completes into a fly only if spot moves
AWAY from it: a put spread on a rally, a call spread on a selloff. Does the GEX wall on that side
-- the call wall for a put spread, the put wall for a call spread -- strand the spread when it sits
just ahead of spot?

**How.** Each settled legged row of the chosen arms is joined to the gex recorder's latest RTH row
at or before its `entry_time`, at most 600 s old, through `cherrypick.core.regime.gex_at` -- the same
reader, staleness and refusals the live gate uses (`flies.provider.RECORDED_GEX_MAX_AGE_SECONDS`).
`room` is how far spot (`underlying_at_entry`) may travel in the completing direction before the
wall: negative once through it. Rows are bucketed by room per side, with completion rate, net per
position and completion latency (`completion_latency_min`, completed rows only). The gate's verdict
on each row is `engine.wall_clearance_refusal` itself, called on a snapshot of that row's spot and
joined walls, so the replay cannot disagree with the arm about a row.

**Statistics.** Rows are not draws -- every position on a day observes one market. The p-value
shuffles the refused label WITHIN each session (one-sided: refused rows worse), and `paired` counts
the sessions holding both kinds in which the refused rows averaged worse.

**What it is not.**
- **Not the arm's book.** A refused tick is dropped here; the arm enters at a later tick instead,
  at a price no ledger recorded. That is why `wall-clear` is an arm and not a replayed verdict.
- **Not out of sample** on its own era: the declared thresholds (10 ahead, 5 past) were read off
  these rows. Only sessions from the arm's start (2026-10-19) judge it.
- **Not the snapshot's own GEX walls**, which are gross call/put peaks; the recorder's are net-GEX
  walls, a different definition (provider.py beside `RECORDED_GEX_MAX_AGE_SECONDS`).
- **Pools nothing across 2026-10-05**, when paper completions began paying the live limit; the
  default window ends the day before.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from cherrypick.core import regime
from cherrypick.flies import engine, provider

BUCKETS = (
    (-1e9, -5, "past 5+"),
    (-5, 0, "past 0-5"),
    (0, 10, "ahead 0-10"),
    (10, 20, "ahead 10-20"),
    (20, 35, "ahead 20-35"),
    (35, 60, "ahead 35-60"),
    (60, 1e9, "ahead 60+"),
)


def ledger_path() -> Path:
    home = Path(os.environ.get("CHERRYPICK_HOME") or Path.home() / ".cherrypick")
    return home / "data" / "flies" / "paper_trades.db"


def load_rows(
    arms: list[str], since: str | None, until: str | None, ahead: float, past: float
) -> tuple[list, int]:
    conn = sqlite3.connect(f"file:{ledger_path().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    sql = (
        "SELECT trade_date, arm, side, entry_time, underlying_at_entry, completed_at, "
        "completion_latency_min, pnl "
        "FROM fly_positions WHERE symbol = 'SPX' AND entry_mode = 'legged' AND status = 'settled' "
        "AND void_reason IS NULL AND COALESCE(closed_before_expiry, 0) = 0 "
        f"AND arm IN ({','.join('?' * len(arms))})"
    )
    args: list = list(arms)
    if since:
        sql += " AND trade_date >= ?"
        args.append(since)
    if until:
        sql += " AND trade_date <= ?"
        args.append(until)
    params = {"wall_clear_ahead_points": ahead, "wall_clear_past_points": past}
    rows, unjoined = [], 0
    for r in conn.execute(sql + " ORDER BY entry_time", args):
        ts = datetime.fromisoformat(r["entry_time"]).timestamp()
        rec = regime.gex_at(ts, "SPX", max_staleness_seconds=provider.RECORDED_GEX_MAX_AGE_SECONDS)
        spot = r["underlying_at_entry"]
        wall = rec.get("call_wall") if r["side"] == engine.PUT else rec.get("put_wall")
        if rec.get("status") != "measured" or wall is None or spot is None:
            unjoined += 1
            continue
        snap = {"underlying_price": spot, "recorded_gex": rec}
        rows.append(
            {
                "date": r["trade_date"],
                "arm": r["arm"],
                "side": r["side"],
                "room": wall - spot if r["side"] == engine.PUT else spot - wall,
                "done": r["completed_at"] is not None,
                "latency": r["completion_latency_min"],
                "pnl": r["pnl"],
                "refused": engine.wall_clearance_refusal(snap, params, r["side"]) is not None,
            }
        )
    conn.close()
    return rows, unjoined


def quantiles(xs: list[float]) -> list[float] | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    pick = lambda q: xs[min(len(xs) - 1, int(q * len(xs)))]  # noqa: E731
    return [round(pick(0.25), 1), round(pick(0.5), 1), round(pick(0.75), 1)]


def summary(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    return {
        "n": len(rows),
        "sessions": len({r["date"] for r in rows}),
        "completion": round(sum(r["done"] for r in rows) / len(rows), 3),
        "net_per_position": round(st.mean(r["pnl"] for r in rows), 2),
        "net": round(sum(r["pnl"] for r in rows), 2),
        "latency_min_p25_p50_p75": quantiles([r["latency"] for r in rows if r["done"]]),
    }


def gate(rows: list[dict], shuffles: int = 4000) -> dict:
    refused = [r for r in rows if r["refused"]]
    kept = [r for r in rows if not r["refused"]]
    out = {"refused": summary(refused), "kept": summary(kept)}
    if not refused or not kept:
        return out
    diff = st.mean(r["pnl"] for r in refused) - st.mean(r["pnl"] for r in kept)
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
    rng, extreme = random.Random(11), 0
    for _ in range(shuffles):
        a, b = [], []
        for day in by_day.values():
            labels = [r["refused"] for r in day]
            rng.shuffle(labels)
            for r, lab in zip(day, labels, strict=True):
                (a if lab else b).append(r["pnl"])
        extreme += (st.mean(a) - st.mean(b)) <= diff
    pairs = [
        st.mean(r["pnl"] for r in day if r["refused"]) - st.mean(r["pnl"] for r in day if not r["refused"])
        for day in by_day.values()
        if any(r["refused"] for r in day) and not all(r["refused"] for r in day)
    ]
    out.update(
        {
            "mean_diff_refused_minus_kept": round(diff, 2),
            "p_within_session": round(extreme / shuffles, 3),
            "paired_sessions_refused_worse": f"{sum(p < 0 for p in pairs)}/{len(pairs)}",
        }
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", action="append", help="arm(s) to read (default: control)")
    ap.add_argument("--since", default="2026-08-01")
    ap.add_argument("--until", default="2026-10-04")
    ap.add_argument("--ahead", type=float, default=10.0, help="wall_clear_ahead_points (declared: 10)")
    ap.add_argument("--past", type=float, default=5.0, help="wall_clear_past_points (declared: 5)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    arms = a.arm or ["control"]
    rows, unjoined = load_rows(arms, a.since, a.until, a.ahead, a.past)
    if not rows:
        sys.exit(f"no joinable rows for {arms} in {a.since}..{a.until} ({unjoined} without recorded walls)")
    dates = sorted({r["date"] for r in rows})
    mid = dates[len(dates) // 2]
    report = {
        "arms": arms,
        "window": [a.since, a.until],
        "rows": len(rows),
        "unjoined": unjoined,
        "sessions": len(dates),
        "ahead": a.ahead,
        "past": a.past,
        "by_side": {
            side: {
                label: summary([r for r in rows if r["side"] == side and lo <= r["room"] < hi])
                for lo, hi, label in BUCKETS
            }
            for side in (engine.PUT, engine.CALL)
        },
        "gate": gate(rows),
        "gate_halves": {
            f"before {mid}": gate([r for r in rows if r["date"] < mid]),
            f"from {mid}": gate([r for r in rows if r["date"] >= mid]),
        },
    }
    if a.json:
        print(json.dumps(report, indent=2))
        return
    print(
        f"{', '.join(arms)}  {dates[0]}..{dates[-1]}  rows {len(rows)} over {len(dates)} sessions"
        f"  ({unjoined} without recorded walls)"
    )
    for side, wall in ((engine.PUT, "call"), (engine.CALL, "put")):
        head = "n  sess  compl   net/pos   latency p25/p50/p75"
        print(f"\n{side} spread -- room to the {wall} wall (pts)      {head}")
        for label, s in report["by_side"][side].items():
            if s["n"]:
                print(
                    f"  {label:12s}                  {s['n']:5d} {s['sessions']:5d}  {s['completion']:5.0%}"
                    f"  {s['net_per_position']:8.2f}   {s['latency_min_p25_p50_p75']}"
                )
    print(f"\ngate: refuse while -{a.past:g} < room < {a.ahead:g}")
    for name, g in [("all", report["gate"]), *report["gate_halves"].items()]:
        r, k = g["refused"], g["kept"]
        if not r.get("n") or not k.get("n"):
            continue
        print(
            f"  {name:18s} refused {r['n']:3d} ({r['sessions']} sess) net {r['net']:9.2f}"
            f" compl {r['completion']:.0%} latency {r['latency_min_p25_p50_p75']}"
            f" | kept {k['net_per_position']:.2f}/pos compl {k['completion']:.0%}"
            f" latency {k['latency_min_p25_p50_p75']}"
            f" | p {g.get('p_within_session')} paired {g.get('paired_sessions_refused_worse')}"
        )


if __name__ == "__main__":
    main()
