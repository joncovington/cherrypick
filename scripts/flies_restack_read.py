"""Read the flies `restack` arm against control: what a second contract on a completed centre earned.

    python scripts/flies_restack_read.py                   # restack vs control, from the arm's first session
    python scripts/flies_restack_read.py --since 2026-10-12 --until 2026-10-30

Read-only; runnable from anywhere; the ledger resolves off `$CHERRYPICK_HOME` (default `~/.cherrypick`).

**The question** (declared 2026-10-11, docs/experiment-log.md). Control never enters a structure it
already holds that day. Spot came back to 127 of its 196 completed flies' centres over 08-21..10-09,
and the rule refused each return before pricing it, so what a second contract there earns was never
recorded. `restack` is control with the rule ignoring a structure once it is completed risk-free.

**The split.** Every restack entry is either a FIRST entry at its centre that day (the trade control
would also have made, give or take the path) or a RESTACK (an earlier same-day row of the arm holds the
same centre, wings and far wing). Restacks are read on their own: completion rate, net, net per entry,
against the arm's first entries and control's entries over the same sessions. The arm's book is read
against control's per session, sign test included, and flagged underpowered below `MIN_EFFECTIVE_N`
sessions. Stack depth (the most contracts the arm held on one centre at the close) is shown because the
cost of the arm is concentration: every contract on a centre settles to the same payoff.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

from cherrypick.core.metrics import MIN_EFFECTIVE_N
from cherrypick.core.regimecuts import sign_test_p

ARM_FROM = "2026-10-12"


def _ledger() -> Path:
    home = os.environ.get("CHERRYPICK_HOME")
    return (Path(home) if home else Path.home() / ".cherrypick") / "data" / "flies" / "paper_trades.db"


def load(path: Path, arm: str, since: str, until: str | None) -> list[dict]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    q = """select trade_date, entry_time, center, wing_width, far_width, completed_at, pnl
           from fly_positions where arm=? and symbol='SPX' and status='settled' and void_reason is null
           and entry_mode='legged' and trade_date>=? and (? is null or trade_date<=?) order by entry_time"""
    return [dict(r) for r in conn.execute(q, (arm, since, until, until))]


def classify(rows: list[dict]) -> list[dict]:
    """Stamp each row `restack` (an earlier same-day row holds its structure) and `depth` (its place
    in that structure's stack, 1 for the first). Rows must be in entry order."""
    seen: dict[tuple, int] = defaultdict(int)
    out = []
    for r in rows:
        key = (r["trade_date"], r["center"], r["wing_width"], r["far_width"])
        seen[key] += 1
        out.append({**r, "depth": seen[key], "restack": seen[key] > 1})
    return out


def summary(rows: list[dict]) -> dict:
    n = len(rows)
    net = round(sum(r["pnl"] or 0.0 for r in rows), 2)
    done = sum(1 for r in rows if r["completed_at"])
    return {
        "n": n,
        "completed": done,
        "completion_rate": round(done / n, 3) if n else None,
        "net": net,
        "per_entry": round(net / n, 2) if n else None,
    }


def per_session(rows: list[dict]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for r in rows:
        out[r["trade_date"]] += r["pnl"] or 0.0
    return dict(out)


def read(arm_rows: list[dict], control_rows: list[dict]) -> dict:
    tagged = classify(arm_rows)
    sessions = sorted(set(per_session(arm_rows)) | set(per_session(control_rows)))
    a, c = per_session(arm_rows), per_session(control_rows)
    diffs = [a.get(d, 0.0) - c.get(d, 0.0) for d in sessions]
    better, worse = sum(1 for x in diffs if x > 0.005), sum(1 for x in diffs if x < -0.005)
    depth: dict[str, int] = defaultdict(int)
    for r in tagged:
        depth[r["trade_date"]] = max(depth[r["trade_date"]], r["depth"])
    return {
        "sessions": len(sessions),
        "underpowered": len(sessions) < MIN_EFFECTIVE_N,
        "first_entries": summary([r for r in tagged if not r["restack"]]),
        "restacks": summary([r for r in tagged if r["restack"]]),
        "control": summary(control_rows),
        "arm_net": round(sum(a.values()), 2),
        "control_net": round(sum(c.values()), 2),
        "better_days": better,
        "worse_days": worse,
        "sign_p": sign_test_p(better, worse),
        "max_stack_depth": max(depth.values()) if depth else 0,
        "per_session": {d: (round(a.get(d, 0.0), 2), round(c.get(d, 0.0), 2)) for d in sessions},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", default="restack")
    ap.add_argument("--control", default="control")
    ap.add_argument("--since", default=ARM_FROM, help="default: the arm's first session")
    ap.add_argument("--until")
    ap.add_argument("--db", type=Path, default=None, help="default: the paper ledger")
    a = ap.parse_args()
    path = a.db or _ledger()
    out = read(load(path, a.arm, a.since, a.until), load(path, a.control, a.since, a.until))
    if not out["sessions"]:
        print(f"no settled {a.arm} or {a.control} entries since {a.since}")
        return
    flag = f"  (underpowered: under {MIN_EFFECTIVE_N} sessions)" if out["underpowered"] else ""
    print(f"{a.arm} vs {a.control}, {out['sessions']} sessions{flag}")
    for label in ("first_entries", "restacks", "control"):
        s = out[label]
        rate = "n/a" if s["completion_rate"] is None else f"{s['completion_rate']:.0%}"
        per = "n/a" if s["per_entry"] is None else f"{s['per_entry']:+.2f}"
        print(f"  {label:<14} n {s['n']:>4}  completed {rate:>4}  net {s['net']:+10.2f}  per entry {per}")
    print(
        f"  book net {out['arm_net']:+.2f} vs {out['control_net']:+.2f}; better {out['better_days']}, "
        f"worse {out['worse_days']}, sign p {out['sign_p']}; deepest stack {out['max_stack_depth']}"
    )
    for d, (arm_net, ctl_net) in out["per_session"].items():
        print(f"    {d}  {a.arm} {arm_net:+9.2f}   {a.control} {ctl_net:+9.2f}")


if __name__ == "__main__":
    main()
