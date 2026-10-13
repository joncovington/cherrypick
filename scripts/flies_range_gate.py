"""Would gating flies entries on where spot sits in the session's range so far have helped?

The 2026-10-09 study behind the `range-high` paper arm (`engine.range_high_refusal`), recovered from
the session scratchpad (`flies_range_gate.py`, `flies_range_gate2.py`, `rg_extra.py`) into one
script. Read-only: the flies ledger and the gex recorder's history are opened `mode=ro`.

For each settled SPX entry of an arm: the session high/low from 09:30 to the entry instant, from the
gex recorder's SPX spot trail (no look-ahead), p = (spot - low) / (high - low). Outcomes: completed
(a fly) or stranded (a short vertical at settlement), and net P&L. Every gate is scored as the P&L of
the entries it would have refused (negative refused = the gate helps), per half of the era and per
session. The rows the arm was built from:

- both ends (p >= X or p <= 1 - X) and each end alone;
- near an end, completing toward it / away from it;
- the overlap with the existing up-from-open read (drift > 20 points since the first trail print);
- p >= 0.9 split by the range so far (the arm's cut: under 30 points);
- p >= 0.9 by minutes since the open;
- the narrow-range at-high rows by session: the worst and best sessions, and the net without the
  worst two (is the result carried by one or two days?).

**Validation** (`--validate`): the study reads the range off the gex spot trail; the arm reads
`stream_summary`'s live day_high/day_low. For every `range-high` entry attempt and decision on
file, the trail's p and range at that instant are put through `engine.range_high_refusal` with the
arm's own params, and its verdict is compared with what the arm did (filled = not refused). A
disagreement means the study measured a different range from the one the arm acts on. The
control totals are also reconciled with the ledger's own sum.

Usage:
    python scripts/flies_range_gate.py                        # control, paper, all sessions
    python scripts/flies_range_gate.py control paper --until 2026-10-09   # the 10-09 study as run
    python scripts/flies_range_gate.py control live
    python scripts/flies_range_gate.py --validate
"""

from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import home as _home

ET = ZoneInfo("America/New_York")
DRIFT_UP = 20.0  # points above the first trail print = up from the open


def ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_trail(gex_db: Path) -> dict[str, tuple[list[float], list[float]]]:
    """SPX spot prints from 09:30 on, per session: (timestamps, spots)."""
    trail: dict[str, tuple[list[float], list[float]]] = {}
    for td, ts, spot in ro(gex_db).execute(
        "SELECT trade_date, ts, spot FROM gex_spot_history WHERE symbol='SPX' ORDER BY ts"
    ):
        if not spot:
            continue
        t = datetime.fromtimestamp(ts, ET)
        if t.date().isoformat() != td or t.time() < time(9, 30):
            continue
        trail.setdefault(td, ([], []))
        trail[td][0].append(ts)
        trail[td][1].append(spot)
    return trail


def range_at(trail, td: str, ts: float, spot: float | None = None) -> dict | None:
    """p, range, drift and minutes since the open at `ts`, from the prints up to it; None when the
    trail has fewer than ten prints by then or no range."""
    if td not in trail:
        return None
    ts_list, spots = trail[td]
    k = bisect.bisect_right(ts_list, ts)
    if k < 10:
        return None
    hi, lo = max(spots[:k]), min(spots[:k])
    spot = spot or spots[k - 1]
    if hi - lo <= 0:
        return None
    return {
        "p": min(1.0, max(0.0, (spot - lo) / (hi - lo))),
        "rng": hi - lo,
        "high": hi,
        "low": lo,
        "spot": spot,
        "drift": spot - spots[0],
        "mins": (ts - ts_list[0]) / 60,
    }


def load_entries(flies_db: Path, trail, arm: str, since: str | None, until: str | None):
    rows = (
        ro(flies_db)
        .execute(
            "SELECT trade_date, entry_time, kind, completing_direction, underlying_at_entry, pnl "
            "FROM fly_positions WHERE symbol='SPX' AND arm=? AND status='settled' AND pnl IS NOT NULL "
            "AND (? IS NULL OR trade_date >= ?) AND (? IS NULL OR trade_date <= ?)",
            (arm, since, since, until, until),
        )
        .fetchall()
    )
    entries, skipped = [], defaultdict(int)
    for r in rows:
        td = r["trade_date"]
        if td not in trail:
            skipped[f"no_spot_trail {td}"] += float(r["pnl"])
            continue
        at = range_at(
            trail, td, datetime.fromisoformat(r["entry_time"]).timestamp(), r["underlying_at_entry"]
        )
        if at is None:
            skipped[f"too_early_or_zero_range {td}"] += float(r["pnl"])
            continue
        entries.append(
            {
                **at,
                "day": td,
                "dir": r["completing_direction"],
                "done": r["kind"] == "fly",
                "pnl": float(r["pnl"]),
            }
        )
    return entries, dict(skipped), sum(float(r["pnl"]) for r in rows), len(rows)


def report(entries: list[dict]) -> None:
    days = sorted({e["day"] for e in entries})
    half = days[len(days) // 2]
    tot = sum(e["pnl"] for e in entries)
    print(f"all: completion {sum(e['done'] for e in entries) / len(entries):.0%}, net ${tot:,.0f}\n")

    print("by position in the session range so far (0 = at the low, 1 = at the high)")
    for a, b in [(0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 0.9), (0.9, 1.01)]:
        es = [e for e in entries if a <= e["p"] < b]
        if not es:
            continue
        toward = [e for e in es if (e["dir"] == "up") == (e["p"] >= 0.5)]
        print(
            f"  p {a:.2f}-{min(b, 1):.2f}: n {len(es):3d}  "
            f"completion {sum(e['done'] for e in es) / len(es):4.0%}  "
            f"net ${sum(e['pnl'] for e in es):8,.0f}  per ${sum(e['pnl'] for e in es) / len(es):6.1f}  "
            f"| completing toward the near extreme: n {len(toward)}, "
            f"{(sum(e['done'] for e in toward) / len(toward)) if toward else 0:.0%}, "
            f"${sum(e['pnl'] for e in toward):,.0f}"
        )

    def score(name, refuse):
        out = [e for e in entries if refuse(e)]
        if not out:
            return
        first = sum(e["pnl"] for e in out if e["day"] < half)
        second = sum(e["pnl"] for e in out if e["day"] >= half)
        per_day = defaultdict(float)
        for e in out:
            per_day[e["day"]] += e["pnl"]
        better = sum(1 for v in per_day.values() if v < 0)
        worse = sum(1 for v in per_day.values() if v > 0)
        print(
            f"  {name:44s} refuses {len(out):3d} (completion {sum(e['done'] for e in out) / len(out):4.0%}), "
            f"their net ${sum(e['pnl'] for e in out):7,.0f} -> gate {-sum(e['pnl'] for e in out):+7,.0f}  "
            f"halves {-first:+6,.0f} / {-second:+6,.0f}  sessions better {better} worse {worse}"
        )

    print(f"\ngates (gate = P&L change from refusing these; + helps; halves split at {half})")
    for x in (0.75, 0.8, 0.85, 0.9, 0.95):
        score(f"both ends: p >= {x} or p <= {1 - x:.2f}", lambda e, x=x: e["p"] >= x or e["p"] <= 1 - x)
    for x in (0.8, 0.9):
        score(f"at the high only: p >= {x}", lambda e, x=x: e["p"] >= x)
        score(f"at the low only: p <= {1 - x:.2f}", lambda e, x=x: e["p"] <= 1 - x)
        score(
            f"near an end, completing TOWARD it (x={x})",
            lambda e, x=x: (e["p"] >= x and e["dir"] == "up") or (e["p"] <= 1 - x and e["dir"] == "down"),
        )
        score(
            f"near an end, completing AWAY from it (x={x})",
            lambda e, x=x: (e["p"] >= x and e["dir"] == "down") or (e["p"] <= 1 - x and e["dir"] == "up"),
        )
    print(f"\noverlap with trend-from-open (drift > {DRIFT_UP:g} = up_from_open)")
    for name, f in [
        ("up_from_open (existing gate)", lambda e: e["drift"] > DRIFT_UP),
        ("p >= 0.9", lambda e: e["p"] >= 0.9),
        ("p >= 0.9 AND up_from_open", lambda e: e["p"] >= 0.9 and e["drift"] > DRIFT_UP),
        ("p >= 0.9 NOT up_from_open", lambda e: e["p"] >= 0.9 and e["drift"] <= DRIFT_UP),
        ("up_from_open NOT p >= 0.9", lambda e: e["drift"] > DRIFT_UP and e["p"] < 0.9),
    ]:
        score(name, f)
    print("\nthe high gate by the size of the range so far")
    for r in (10, 20, 30, 40):
        score(f"p >= 0.9 and range >= {r}", lambda e, r=r: e["p"] >= 0.9 and e["rng"] >= r)
        score(f"p >= 0.9 and range <  {r}", lambda e, r=r: e["p"] >= 0.9 and e["rng"] < r)
    print("\nby time since the open, p >= 0.9")
    for a, b in ((0, 60), (60, 150), (150, 400)):
        score(f"p >= 0.9, {a}-{b} min after 09:30", lambda e, a=a, b=b: e["p"] >= 0.9 and a <= e["mins"] < b)

    narrow = [e for e in entries if e["p"] >= 0.9 and e["rng"] < 30]
    per = defaultdict(lambda: [0, 0.0])
    for e in narrow:
        per[e["day"]][0] += 1
        per[e["day"]][1] += e["pnl"]
    top = sorted(per.items(), key=lambda kv: kv[1][1])
    print(f"\nthe arm's cut (p >= 0.9, range < 30) by session: {len(per)} sessions")
    print("  worst:", [(d, n, round(v)) for d, (n, v) in top[:5]])
    print("  best: ", [(d, n, round(v)) for d, (n, v) in top[-4:]])
    print(f"  net without the worst two sessions: ${sum(v for _, (n, v) in top[2:]):,.0f}")


def validate(flies_db: Path, trail, config_path: Path) -> int:
    """The arm's verdicts against the study's trail-derived range, attempt by attempt."""
    from cherrypick.flies import engine

    params = {"range_high_pct": 0.9, "range_high_max_points": 30.0}
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        params.update(
            {
                k: v
                for k, v in (cfg.get("arms", {}).get("range-high") or {}).items()
                if k.startswith("range_high")
            }
        )
    except (OSError, ValueError):
        print(f"  (no readable config at {config_path}; using the declared 0.9 / 30)")
    conn = ro(flies_db)
    rows = conn.execute(
        "SELECT ts, trade_date, outcome, block_detail, spot FROM fly_entry_attempts "
        "WHERE arm='range-high' AND symbol='SPX' AND (outcome='filled' OR block_detail LIKE '%session_high%' "
        "OR outcome LIKE '%range%') ORDER BY ts"
    ).fetchall()
    print(
        f"Validation: {len(rows)} range-high attempts that filled or were refused at the high; "
        f"params {params}"
    )
    agree = disagree = unread = 0
    for r in rows:
        ts = r["ts"] if isinstance(r["ts"], (int, float)) else datetime.fromisoformat(r["ts"]).timestamp()
        at = range_at(trail, r["trade_date"], ts, r["spot"])
        if at is None:
            unread += 1
            continue
        snap = {"underlying_price": at["spot"], "session": {"day_high": at["high"], "day_low": at["low"]}}
        study_refuses = engine.range_high_refusal(snap, params) is not None
        arm_refused = r["outcome"] != "filled"
        ok = study_refuses == arm_refused
        agree += ok
        disagree += not ok
        if not ok:
            print(
                f"  DISAGREE {r['trade_date']} {datetime.fromtimestamp(ts, ET):%H:%M:%S} arm {r['outcome']} "
                f"trail p {at['p']:.2f} range {at['rng']:.1f} (high {at['high']:.2f} low {at['low']:.2f})"
            )
    print(f"  agree {agree}, disagree {disagree}, trail unreadable {unread}")
    decisions = conn.execute(
        "SELECT trade_date, reason, occurrences FROM fly_decisions "
        "WHERE arm='range-high' AND reason LIKE '%high%'"
    ).fetchall()
    print(f"  range-high decisions refused at the high so far: {[tuple(d) for d in decisions] or 'none'}")
    return 0 if disagree == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("arm", nargs="?", default="control")
    ap.add_argument("ledger", nargs="?", default="paper", choices=("paper", "live"))
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--validate", action="store_true")
    args = ap.parse_args(argv)
    data = _home.data_dir()
    flies_db = data / "flies" / f"{args.ledger}_trades.db"
    trail = load_trail(data / "gex" / "gex_history.db")
    if args.validate:
        return validate(data / "flies" / "paper_trades.db", trail, _home.config_dir() / "flies.json")
    entries, skipped, ledger_net, ledger_n = load_entries(flies_db, trail, args.arm, args.since, args.until)
    print(
        f"{args.arm} ({args.ledger}): {len(entries)} entries over "
        f"{len({e['day'] for e in entries})} sessions; "
        f"skipped {skipped}"
    )
    print(
        f"ledger reconciliation: {ledger_n} settled SPX rows net ${ledger_net:,.2f}; scored "
        f"${sum(e['pnl'] for e in entries):,.2f} + skipped (net by session) "
        f"${ledger_net - sum(e['pnl'] for e in entries):,.2f}"
    )
    if entries:
        report(entries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
