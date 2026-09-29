"""Replay paper control's entries under the flies live pilot's rules, varying only the start (2026-09-28).

    python scripts/flies_live_start_replay.py                      # 10:00 / 10:15 / 10:30 over the era
    python scripts/flies_live_start_replay.py --start 10:15 --start 10:45
    python scripts/flies_live_start_replay.py --validate --since 2026-09-29 --start 10:15

Read-only; runnable from anywhere; ledgers resolve off `$CHERRYPICK_HOME` (default `~/.cherrypick`).

**The question.** `live.no_entry_before` moved to 10:30 on 2026-09-17 because live then held ONE
incomplete position at a time, so a weak 10:00 entry could sit on the only slot through the 10:15
and 10:30 entries that made the money. That rule was removed on 2026-09-25, leaving
`live.max_open_margin_dollars` as the only sizing gate -- so the start was replayed again under the
cap. Answer on 25 era sessions: 10:15 +$4,758 against 10:30's +$2,735 and 10:00's +$3,614, better
than 10:30 on 19 of 21 differing days (sign p 0.0002), in both halves of the era. The 10:00-10:14
slot is weak AND crowds the cap (29 entries blocked at 10:00, 11 at 10:15): the old reasoning held,
the cap just does the crowding now. Live moved to 10:15 for 2026-09-29, journaled as a live
`entry_rules` break; `--validate` is the ten-session check against this replay.

**The model** -- live_loop's own arithmetic, applied to paper control's recorded entries in time
order. An entry is admitted only while the open worst-case exposure plus its own (width - credit)
x100 fits under the cap (`margin_cap_exceeded`). An admitted position counts until settlement:
before its completion as a short vertical at its entry credit (entry fees taken as half the row's
total), after it at its completed floor, which is 0 once risk-free (`open_margin_dollars`). No
count limit. Dropping rows is the `replay_gates` pattern: a freed slot is not re-filled by an entry
paper never recorded, and paper's modelled fills stand in for live's -- which is why this compares
starts with one another and does not forecast live P&L. Checked on 09-25..09-28: the replay admitted
10 structures to live's 10, but 5/5 by day against live's 4/6, while live netted -$53 against the
replay's +$768 -- the admission rule is approximated, the fills are not modelled at all.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import statistics as st
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from cherrypick.core.regimecuts import sign_test_p
from cherrypick.flies import fly

DEFAULT_STARTS = ("10:00", "10:15", "10:30")


def _ledger(name: str) -> Path:
    home = os.environ.get("CHERRYPICK_HOME")
    root = (Path(home) if home else Path.home() / ".cherrypick") / "data" / "flies"
    return root / f"{name}_trades.db"


def load(name: str, arm: str, since: str, until: str | None) -> list[dict]:
    conn = sqlite3.connect(f"file:{_ledger(name)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    q = """select trade_date, entry_time, completed_at, kind, wing_width, quantity, net, credit, fees, pnl
           from fly_positions where arm=? and symbol='SPX' and status='settled' and void_reason is null
           and entry_mode='legged' and trade_date>=? and (? is null or trade_date<=?) order by entry_time"""
    return [dict(r) for r in conn.execute(q, (arm, since, until, until))]


def exposure_at(p: dict, t: datetime) -> float:
    """Worst-case dollars one admitted position commits at time t."""
    if p["kind"] == "fly" and p["completed_at"] and datetime.fromisoformat(p["completed_at"]) <= t:
        return max(0.0, -fly.position_floor(p))
    entry_fees = (p["fees"] or 0.0) / (2 if p["kind"] == "fly" else 1)
    return max(0.0, -fly.position_floor({**p, "kind": "short_vertical", "net": p["credit"], "fees": entry_fees}))


def replay(rows: list[dict], start: str, cap: float) -> dict:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[r["trade_date"]].append(r)
    per_day, kept_per_day, kept, by_start, by_cap = {}, {}, 0, 0, 0
    for day, rs in sorted(by_day.items()):
        taken: list[dict] = []
        for r in rs:
            t = datetime.fromisoformat(r["entry_time"])
            if t.strftime("%H:%M") < start:
                by_start += 1
                continue
            proposed = max(0.0, (r["wing_width"] - r["credit"]) * fly.CONTRACT_MULTIPLIER * (r["quantity"] or 1))
            if sum(exposure_at(p, t) for p in taken) + proposed > cap:
                by_cap += 1
                continue
            taken.append(r)
        kept += len(taken)
        kept_per_day[day] = len(taken)
        per_day[day] = round(sum(p["pnl"] for p in taken), 2)
    vals = list(per_day.values())
    return {
        "kept": kept,
        "blocked_by_start": by_start,
        "blocked_by_cap": by_cap,
        "net": round(sum(vals), 2),
        "days": len(vals),
        "losing_days": sum(1 for v in vals if v < 0),
        "worst_day": min(vals) if vals else None,
        "median_day": round(st.median(vals), 2) if vals else None,
        "per_day": per_day,
        "kept_per_day": kept_per_day,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", default="control")
    ap.add_argument("--start", action="append", help="HH:MM, repeatable; default 10:00, 10:15, 10:30")
    ap.add_argument("--baseline", default="10:30", help="the start every other one is compared against")
    ap.add_argument("--cap", type=float, default=1000.0, help="live.max_open_margin_dollars")
    ap.add_argument("--since", default="2026-08-21", help="default: the advisor-era cutover")
    ap.add_argument("--until")
    ap.add_argument("--validate", action="store_true", help="live's actual sessions beside the replay of them")
    a = ap.parse_args()
    starts = a.start or list(DEFAULT_STARTS)

    if a.validate:
        # Structures per day are the check: the replay models live's admission rule, not its fills,
        # so the counts should track and the money need not.
        live = load("live", a.arm, a.since, a.until)
        live_day: dict[str, float] = defaultdict(float)
        live_n: dict[str, int] = defaultdict(int)
        for r in live:
            live_day[r["trade_date"]] += r["pnl"]
            live_n[r["trade_date"]] += 1
        rep = replay(load("paper", a.arm, a.since, a.until), starts[0], a.cap)
        print(f"live actual: {len(live)} structures, net {sum(live_day.values()):+.2f}")
        print(f"replay {starts[0]}: {rep['kept']} structures, net {rep['net']:+.2f}")
        for d in sorted(set(live_day) | set(rep["per_day"])):
            print(
                f"  {d}  live {live_n.get(d, 0):>3} / {live_day.get(d, 0.0):+9.2f}"
                f"   replay {rep['kept_per_day'].get(d, 0):>3} / {rep['per_day'].get(d, 0.0):+9.2f}"
            )
        return

    rows = load("paper", a.arm, a.since, a.until)
    base = replay(rows, a.baseline, a.cap)
    for start in sorted(set(starts) | {a.baseline}):
        out = replay(rows, start, a.cap)
        line = {k: v for k, v in out.items() if k not in ("per_day", "kept_per_day")}
        if start != a.baseline:
            diffs = [out["per_day"][d] - base["per_day"][d] for d in out["per_day"]]
            better, worse = sum(1 for x in diffs if x > 0.01), sum(1 for x in diffs if x < -0.01)
            line["vs_baseline"] = f"{better}-{worse} days, p {sign_test_p(better, worse)}, net {sum(diffs):+.0f}"
        print(start, line)


if __name__ == "__main__":
    main()
