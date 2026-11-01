"""How far MEIC's stops fill past their trigger, in its live ledger, its paper ledger and a replay study,
side by side: the check that tells whether the 60 s replay's stop model matches real fills.

    python scripts/meic_stop_overshoot.py [--study item23] [--arm live-1400] [--json PATH]

A stopped side's overshoot is its cost to close over its trigger level, the trigger being the condor's
credit times the stop ratio in force (`stop_trigger_current`, else `stop_trigger_original`): 1.00 filled
at the trigger, 1.29 filled 29% past it. The sizing check's stop-plus-overshoot risk reads the replay's
95th percentile (`cards.OVERSHOOT_QUANTILE`), so a live distribution well away from the replay's would
change the size it allows; one that matches leaves it, and finer history would add little.

Paper prices stops off the same live quotes the replay's model uses, so paper agreeing with the replay
says the models match, not that real fills do: only the live rows answer that, and they need about 30
stopped sides before the comparison means anything (2026-10-31: 2).

Read-only on every ledger. Writes under the backtest data dir (results/research/stop-overshoot/), never
into the repo: the replay's figures are licensed.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date
from pathlib import Path

from cherrypick.backtest import paths
from cherrypick.core import db as core_db
from cherrypick.core import home

#: Stopped sides the live ledger needs before its distribution is compared at all.
MIN_LIVE = 30
QUANTILES = (0.5, 0.9, 0.95, 0.99)


def overshoots(conn: sqlite3.Connection, arm: str | None = None) -> list[dict]:
    """Every stopped side in a MEIC ledger: its overshoot, arm and entry day. `arm` keeps one arm's."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ic_trades)")}
    need = {"ic_order_id", "net_credit", "stop_trigger_original", "put_stop_cost", "call_stop_cost"}
    have_legs = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'ic_spread_legs'").fetchone()
    if not need <= cols or not have_legs:
        return []
    arm_col = core_db.arm_column(conn, "ic_trades")
    trig = (
        "COALESCE(t.stop_trigger_current, t.stop_trigger_original)"
        if "stop_trigger_current" in cols
        else "t.stop_trigger_original"
    )
    day = "substr(t.entry_time, 1, 10)" if "entry_time" in cols else "NULL"
    out = []
    for side in ("put", "call"):
        for cost, trigger, credit, who, when in conn.execute(
            f"SELECT t.{side}_stop_cost, {trig}, t.net_credit, t.{arm_col}, {day} FROM ic_trades t "
            "JOIN ic_spread_legs l ON l.ic_order_id = t.ic_order_id AND l.side = ? "
            "WHERE l.exit_reason = 'per_side_stop'",
            (side,),
        ):
            if not cost or not trigger or not credit or (arm is not None and who != arm):
                continue
            out.append({"overshoot": cost / (trigger * credit), "arm": who, "day": when, "side": side})
    return out


def summary(rows: list[dict]) -> dict:
    """The distribution of a set of stopped sides: count, quantiles, how many filled under the trigger."""
    v = sorted(r["overshoot"] for r in rows)
    if not v:
        return {"n": 0}
    days = sorted(r["day"] for r in rows if r.get("day"))
    return {
        "n": len(v),
        **{f"p{round(q * 100)}": round(v[min(len(v) - 1, int(q * len(v)))], 3) for q in QUANTILES},
        "under_trigger": sum(1 for x in v if x < 0.999),
        "span": [days[0], days[-1]] if days else None,
    }


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _read(path: Path, arm: str | None = None) -> list[dict]:
    if not path.exists():
        return []
    conn = _ro(path)
    try:
        return overshoots(conn, arm)
    finally:
        conn.close()


def replay_rows(study: str, arm: str) -> list[dict]:
    """One arm's stopped sides across every run a study's result files name."""
    rows = []
    for f in sorted((paths.results_dir() / "research" / study).glob("study-*.json")):
        run_id = json.loads(f.read_text(encoding="utf-8")).get("run_id")
        if not run_id:
            continue
        for db in sorted((paths.results_dir() / "runs" / run_id).glob("*.db")):
            rows += _read(db, arm)
    return rows


def _line(name: str, s: dict) -> str:
    if not s["n"]:
        return f"{name:<38} n=0"
    span = "..".join(s["span"]) if s.get("span") else "?"
    return (
        f"{name:<38} n={s['n']:<5} p50 {s['p50']:.3f}  p90 {s['p90']:.3f}  p95 {s['p95']:.3f}  "
        f"p99 {s['p99']:.3f}  under trigger {s['under_trigger']}  {span}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--study", default="item23", help="the replay study to compare (default item23)")
    ap.add_argument(
        "--arm", default="live-1400", help="the study's arm for the live rule (default live-1400)"
    )
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    meic = home.data_dir("meic")
    sources = {
        "live": summary(_read(meic / "meic_trades.db")),
        "paper (all arms)": summary(_read(meic / "paper_trades.db")),
        f"replay {args.study} {args.arm}": summary(replay_rows(args.study, args.arm)),
    }
    for name, s in sources.items():
        print(_line(name, s))
    live_n = sources["live"]["n"]
    if live_n < MIN_LIVE:
        print(f"\nLive has {live_n} stopped sides; the comparison means something from {MIN_LIVE}.")

    out_dir = paths.results_dir() / "research" / "stop-overshoot"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = Path(args.json) if args.json else out_dir / f"{date.today().isoformat()}.json"
    doc = {"study": args.study, "arm": args.arm, "min_live": MIN_LIVE, "sources": sources}
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"written {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
