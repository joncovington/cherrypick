"""Replay the stranded branch of legged flies arms against the rules proposed to shrink it (2026-09-28).

    python scripts/flies_stranded_replay.py                          # paper control since 2026-08-11
    python scripts/flies_stranded_replay.py --arm callwall --arm gex # several arms, one report each
    python scripts/flies_stranded_replay.py --all                    # every arm with settled rows
    python scripts/flies_stranded_replay.py --ledger live --since 2026-09-17

Read-only; runnable from anywhere; ledgers resolve off `$CHERRYPICK_HOME` (default `~/.cherrypick`).

**The question.** A legged entry that never completes is held to settlement as a short vertical,
and the uncompleted spreads at the edges of a session's forest were asked about as "full losses
at the end of the session" -- can a rule stop that? Five families were replayed, and this script
is the record of how, so the answer can be re-run on later sessions rather than re-derived:

1. `depth`   -- where each stranded spread settled against its own strikes (OTM / partial / full).
2. `paths`   -- the spot path from entry to completion or the bell, from the arm's own
               `fly_iterations`: minutes to cross the long wing, and how deep the flies that DID
               complete went against themselves first. This is the whole case against a stop.
3. `timing`  -- when a stranded spread's best completing debit came, in minutes after entry.
4. `salvage` -- a staged completion limit: `credit - fee_buffer` for T minutes, then
               `credit + x` (accept a known small loss). Pessimistic both ways: a completion that
               took longer than T is charged as filling at `credit + x`, and a stranded spread is
               rescued only when its best debit came at or after T and within `credit + x`, filled
               at `credit + x` and settled through `fly.position_pnl` at the real print.
5. `sameside` -- refuse an entry while an earlier spread on the same side is still uncompleted,
               replayed by dropping the refused rows (the `replay_gates.py` pattern, so a freed
               cadence slot is not modelled).

`miss_stop_minutes` and the hedge overlay are not re-implemented here -- `python run.py
replay-gates` and `python run.py hedge-overlay` already replay them.

Legged arms only for 2-5: debit-first and bwb have a different uncompleted branch (a long vertical,
an unrolled tail) and `depth` reports theirs by kind without the salvage/stop machinery.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from cherrypick.flies import fly

SALVAGE_X = (0.0, 0.1, 0.25, 0.5)
SALVAGE_T = (5, 15, 30, 45, 60, 90)


def _ledger(name: str) -> Path:
    home = os.environ.get("CHERRYPICK_HOME")
    root = (Path(home) if home else Path.home() / ".cherrypick") / "data" / "flies"
    return root / f"{name}_trades.db"


def _mins(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60


def load(conn: sqlite3.Connection, arm: str, symbol: str, since: str, until: str | None) -> list[dict]:
    q = """select * from fly_positions where arm=? and symbol=? and status='settled' and trade_date>=?
           and (? is null or trade_date<=?) and void_reason is null and coalesce(closed_before_expiry,0)=0
           order by entry_time"""
    return [dict(r) for r in conn.execute(q, (arm, symbol, since, until, until))]


def _session_stats(per_day: dict[str, float]) -> tuple[float, float, float, int]:
    vals = [per_day[d] for d in sorted(per_day)]
    cum = peak = dd = 0.0
    for v in vals:
        cum += v
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return sum(vals), (min(vals) if vals else 0.0), dd, sum(v < 0 for v in vals)


def depth(rows: list[dict]) -> None:
    done = [r for r in rows if r["kind"] == "fly"]
    left = [r for r in rows if r["kind"] != "fly"]
    print(f"  entries {len(rows)}  sessions {len({r['trade_date'] for r in rows})}  "
          f"completed {len(done)} ({len(done) / len(rows):.0%})  uncompleted {len(left)}")
    if done:
        print(f"  completed   avg {st.mean(r['pnl'] for r in done):8.2f}  net {sum(r['pnl'] for r in done):9.2f}")
    by = defaultdict(list)
    for r in left:
        if r["kind"] == "short_vertical":
            k, w = r["center"], r["wing_width"]
            d = (r["settlement_price"] - k) if r["side"] == "call" else (k - r["settlement_price"])
            by["short_vertical " + ("otm" if d <= 0 else "partial" if d < w else "full")].append(r["pnl"])
        else:
            by[r["kind"]].append(r["pnl"])
    for k in sorted(by):
        v = by[k]
        print(f"  {k:22} n {len(v):3d}  avg {st.mean(v):8.2f}  net {sum(v):9.2f}")
    if done and left:
        win, loss = st.mean(r["pnl"] for r in done), -st.mean(r["pnl"] for r in left)
        if loss > 0 and win > 0:
            print(f"  break-even completion {loss / (loss + win):.1%}  observed {len(done) / len(rows):.1%}")


def _spot_path(paper: sqlite3.Connection, arm: str, day: str, cache: dict) -> list[tuple[str, float]]:
    key = (arm, day)
    if key not in cache:
        q = """select iteration_ts, underlying_price from fly_iterations where arm=? and trade_date=?
               and underlying_price is not null order by iteration_ts"""
        path = list(paper.execute(q, (arm, day)))
        if not path and arm != "control":  # retired/advised arms: control saw the same tape
            path = list(paper.execute(q, ("control", day)))
        cache[key] = path
    return cache[key]


def paths(rows: list[dict], paper: sqlite3.Connection, arm: str) -> None:
    cache: dict = {}
    wing_mins, back, comp_depth = [], 0, Counter()
    for r in rows:
        if r["kind"] not in ("fly", "short_vertical") or r["credit"] is None:
            continue
        sgn = 1 if r["side"] == "call" else -1
        end = r["completed_at"] or r["entry_time"][:11] + "16:00:00" + r["entry_time"][19:]
        seg = [(t, sgn * (p - r["center"])) for t, p in _spot_path(paper, arm, r["trade_date"], cache)
               if r["entry_time"] <= t <= end]
        if not seg:
            continue
        w = r["wing_width"]
        if r["kind"] == "short_vertical":
            t_wing = next((t for t, d in seg if d >= w), None)
            if t_wing:
                wing_mins.append(_mins(r["entry_time"], t_wing))
                back += any(d <= 0 for t, d in seg if t > t_wing)
        else:
            m = max(d for _, d in seg)
            comp_depth["never ITM" if m <= 0 else "ITM, inside the wing" if m < w else "past the long wing"] += 1
    if wing_mins:
        print(f"  stranded: crossed the long wing {len(wing_mins)}x, median {st.median(wing_mins):.0f} min after "
              f"entry; came back OTM afterwards {back}x (and stranded anyway)")
    if comp_depth:
        print(f"  completed flies, deepest adverse excursion before completing: {dict(comp_depth)}")


def timing(rows: list[dict]) -> None:
    s = [_mins(r["entry_time"], r["best_debit_at"]) for r in rows
         if r["kind"] == "short_vertical" and r["best_debit_at"]]
    gaps = sorted(r["best_completing_debit"] - r["credit"] for r in rows
                  if r["kind"] == "short_vertical" and r["best_completing_debit"] is not None)
    if s:
        print(f"  stranded: best completing debit came a median {st.median(s):.1f} min after entry "
              f"({sum(v <= 5 for v in s)}/{len(s)} within 5 min)")
    if gaps:
        print("  best debit - credit reachable within: "
              + "  ".join(f"+{x}: {sum(g <= x for g in gaps)}" for x in (0, 0.1, 0.25, 0.5, 1.0))
              + f"  (of {len(gaps)})")


def _salvage(rows: list[dict], t: float, x: float) -> tuple:
    vfee = None
    per, rescued, charged = defaultdict(float), 0, 0
    for r in rows:
        p = r["pnl"]
        if r["kind"] == "fly":
            if (r["completion_latency_min"] or 0) > t:
                pen = max(0.0, r["credit"] + x - r["debit"]) * 100 * (r["quantity"] or 1)
                charged += pen > 0
                p -= pen
        elif r["kind"] == "short_vertical":
            b, at = r["best_completing_debit"], r["best_debit_at"]
            if b is not None and at and b <= r["credit"] + x and _mins(r["entry_time"], at) >= t:
                vfee = vfee if vfee is not None else fly.vertical_open_fee(r["symbol"])
                pos = dict(kind="fly", side=r["side"], center=r["center"], wing_width=r["wing_width"],
                           quantity=r["quantity"] or 1, net=-x, fees=2 * vfee * (r["quantity"] or 1), status="open")
                p = fly.position_pnl(pos, r["settlement_price"])
                rescued += 1
        per[r["trade_date"]] += p
    return (*_session_stats(per), rescued, charged)


def salvage(rows: list[dict]) -> None:
    legged = [r for r in rows if r["kind"] in ("fly", "short_vertical") and r["credit"] is not None]
    n, w, dd, lose, *_ = _salvage(legged, float("inf"), 0.0)
    print(f"  base          net {n:8.0f}  worst day {w:6.0f}  max DD {dd:7.0f}  losing days {lose}")
    for x in SALVAGE_X:
        for t in SALVAGE_T:
            n, w, dd, lose, res, ch = _salvage(legged, t, x)
            print(f"  x={x:<4} T={t:<3} net {n:8.0f}  worst day {w:6.0f}  max DD {dd:7.0f}  losing days {lose:2d}  "
                  f"rescued {res:2d}  completions charged {ch}")


def sameside(rows: list[dict]) -> None:
    legged = [r for r in rows if r["kind"] in ("fly", "short_vertical")]
    groups, base, kept = defaultdict(list), defaultdict(float), defaultdict(float)
    for r in legged:
        prior = [p for p in legged if p["trade_date"] == r["trade_date"] and p["entry_time"] < r["entry_time"]
                 and (p["completed_at"] is None or p["completed_at"] > r["entry_time"])]
        same = any(p["side"] == r["side"] for p in prior)
        groups["same side open" if same else "only opposite open" if prior else "nothing open"].append(r)
        base[r["trade_date"]] += r["pnl"]
        kept[r["trade_date"]] += 0 if same else r["pnl"]
    for k, v in groups.items():
        c = sum(x["kind"] == "fly" for x in v)
        print(f"  {k:19} n {len(v):3d}  completion {c / len(v):.0%}  avg {st.mean(x['pnl'] for x in v):7.1f}")
    b, k = _session_stats(base), _session_stats({d: kept.get(d, 0.0) for d in base})
    print(f"  refuse same-side: net {b[0]:.0f} -> {k[0]:.0f}, worst day {b[1]:.0f} -> {k[1]:.0f}, "
          f"max DD {b[2]:.0f} -> {k[2]:.0f}, losing days {b[3]} -> {k[3]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", choices=("paper", "live"), default="paper")
    ap.add_argument("--arm", action="append", help="repeatable; default control")
    ap.add_argument("--all", action="store_true", help="every arm with settled rows in the window")
    ap.add_argument("--symbol", default="SPX")
    ap.add_argument("--since", default="2026-08-11", help="default: the 2026-08-11 entry-rules break")
    ap.add_argument("--until")
    ap.add_argument("--min-sessions", type=int, default=3)
    ap.add_argument("--no-salvage", action="store_true", help="skip the 24-row salvage grid")
    a = ap.parse_args()

    conn = sqlite3.connect(_ledger(a.ledger))
    conn.row_factory = sqlite3.Row
    paper = sqlite3.connect(_ledger("paper"))  # spot paths: the paper loop records every tick
    arms = a.arm or ["control"]
    if a.all:
        arms = [r[0] for r in conn.execute(
            """select arm from fly_positions where symbol=? and status='settled' and trade_date>=?
               and void_reason is null group by arm having count(distinct trade_date)>=? order by count(*) desc""",
            (a.symbol, a.since, a.min_sessions))]
    for arm in arms:
        rows = load(conn, arm, a.symbol, a.since, a.until)
        if not rows:
            continue
        print(f"\n== {a.ledger} {arm}  {rows[0]['trade_date']}..{rows[-1]['trade_date']}")
        depth(rows)
        if not any(r["kind"] == "short_vertical" for r in rows):
            continue
        paths(rows, paper, arm)
        timing(rows)
        sameside(rows)
        if not a.no_salvage:
            salvage(rows)


if __name__ == "__main__":
    main()
