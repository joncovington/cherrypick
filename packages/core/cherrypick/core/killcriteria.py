"""cherrypick.core.killcriteria — a live arm's written, frozen condition for being wrong.

`docs/trading-book-review-plan.md` B1. Every live loop states, dated and before it trades, the
drawdown and losing streak that declare its arm invalid. Crossing them never closes anything and
never flips a gate: it reduces the arm's entry size, then blocks its entries, and a block latches
until a person clears it. Paper is unchanged.

**Where the thresholds come from.** A bootstrap of the arm's own tested trades (the paper or replay
history it went live on): thousands of resampled sequences of `horizon` trades, read at the 95th
(reduce) and 99th (block) percentile of max drawdown and longest losing streak. The resampling is
the stationary block bootstrap (`core.stats.stationary_indices`) with a mean block of a week of
sessions (`BLOCK_SESSIONS` x the evidence's trades per session), because trades inside one session
share a tape, neighbouring sessions share a regime, and real streaks run longer than an
independent-trade table says. `derive` prints the block; a person writes it into the module's
`live.kill` config with its date, and it is never re-derived while the arm trades.

**Too little evidence.** Under `MIN_EVIDENCE` tested trades there is nothing worth bootstrapping;
the arm may still trade at its minimum size under a hand-written `hand_limit_dollars` drawdown.

**Missing criteria refuse.** A live arm with no `live.kill` block, or one that does not parse, is
blocked (`kill_criteria_unwritten`): missing data can block an entry, never allow one.

`derive` and `evaluate` are pure functions over numbers; the latch is one JSON file per module
under `state/kill/`, which the watchdog reports.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from cherrypick.core import home as _home
from cherrypick.core import ledgers
from cherrypick.core.stats import stationary_indices

MIN_EVIDENCE = 100
BLOCK_SESSIONS = 5
DEFAULT_HORIZON = 100
DEFAULT_RESAMPLES = 10_000
DEFAULT_SEED = 20261024

NORMAL, REDUCED, BLOCKED = "normal", "reduced", "blocked"
UNWRITTEN = "kill_criteria_unwritten"
LATCHED = "kill_criteria_latched"


# --------------------------------------------------------------------------- the measures
def drawdown_and_streak(nets: list[float]) -> tuple[float, int]:
    """Max peak-to-trough drawdown (dollars, >= 0) and the longest run of losing trades, over
    `nets` in order. The peak starts at zero, so a book that loses from its first trade draws down."""
    equity = peak = dd = 0.0
    run = longest = 0
    for x in nets:
        equity += x
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
        run = run + 1 if x < 0 else 0
        longest = max(longest, run)
    return round(dd, 2), longest


def current_drawdown_and_streak(nets: list[float]) -> tuple[float, int]:
    """Where the book stands NOW: drawdown below its running peak, and the losing run it ends on."""
    equity = peak = 0.0
    run = 0
    for x in nets:
        equity += x
        peak = max(peak, equity)
        run = run + 1 if x < 0 else 0
    return round(peak - equity, 2), run


def _percentile(sorted_values: list[float], q: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))]


def derive(
    nets: list[float],
    *,
    sessions: int | None = None,
    horizon: int = DEFAULT_HORIZON,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> dict:
    """The kill thresholds an arm's evidence supports. `nets` are its tested trades in time order;
    `sessions` how many sessions they span (sets the mean block; trades are independent without it).
    Returns `{"insufficient": True, ...}` under `MIN_EVIDENCE` trades."""
    n = len(nets)
    if n < MIN_EVIDENCE:
        return {"insufficient": True, "evidence_trades": n, "min_evidence": MIN_EVIDENCE}
    mean_block = BLOCK_SESSIONS * max(1.0, n / sessions) if sessions else 1.0
    rng = random.Random(seed)
    dds, streaks = [], []
    for _ in range(resamples):
        idx = stationary_indices(n, mean_block, rng, length=horizon)
        dd, st = drawdown_and_streak([nets[i] for i in idx])
        dds.append(dd)
        streaks.append(st)
    dds.sort()
    streaks.sort()
    return {
        "evidence_trades": n,
        "evidence_sessions": sessions,
        "horizon": horizon,
        "mean_block": round(mean_block, 2),
        "resamples": resamples,
        "seed": seed,
        "drawdown_reduce": _percentile(dds, 0.95),
        "drawdown_block": _percentile(dds, 0.99),
        "streak_reduce": int(_percentile(streaks, 0.95)),
        "streak_block": int(_percentile(streaks, 0.99)),
    }


# --------------------------------------------------------------------------- the verdict
def _written(criteria) -> str | None:
    if not isinstance(criteria, dict):
        return None
    w = str(criteria.get("written") or "").strip()
    try:
        date.fromisoformat(w[:10])
    except ValueError:
        return None
    return w[:10]


def evaluate(live_nets: list[float], criteria) -> dict:
    """`{state, reasons, drawdown, streak}` for a live arm whose trades SINCE its criteria were
    written are `live_nets`, in order. Blocked when the criteria are missing or malformed."""
    dd, streak = current_drawdown_and_streak(live_nets)
    out = {"state": NORMAL, "reasons": [], "drawdown": dd, "streak": streak}
    if _written(criteria) is None:
        return {**out, "state": BLOCKED, "reasons": [UNWRITTEN]}
    try:
        if criteria.get("hand_limit_dollars") is not None:
            limit = abs(float(criteria["hand_limit_dollars"]))
            if dd >= limit:
                return {**out, "state": BLOCKED, "reasons": [f"drawdown {dd:.2f} >= hand limit {limit:.2f}"]}
            return out
        bands = {
            k: float(criteria[k])
            for k in ("drawdown_reduce", "drawdown_block", "streak_reduce", "streak_block")
        }
    except (KeyError, TypeError, ValueError):
        return {**out, "state": BLOCKED, "reasons": [UNWRITTEN]}
    blocked = [f"drawdown {dd:.2f} >= {bands['drawdown_block']:.2f}"] if dd >= bands["drawdown_block"] else []
    blocked += (
        [f"losing streak {streak} >= {bands['streak_block']:g}"] if streak >= bands["streak_block"] else []
    )
    if blocked:
        return {**out, "state": BLOCKED, "reasons": blocked}
    reduced = (
        [f"drawdown {dd:.2f} >= {bands['drawdown_reduce']:.2f}"] if dd >= bands["drawdown_reduce"] else []
    )
    reduced += (
        [f"losing streak {streak} >= {bands['streak_reduce']:g}"] if streak >= bands["streak_reduce"] else []
    )
    if reduced:
        return {**out, "state": REDUCED, "reasons": reduced}
    return out


def sized(quantity: int, verdict: dict, criteria) -> int:
    """The entry size a verdict allows: half (at least one) when reduced, and the minimum of one
    under a hand-written limit, where there is no evidence to size from."""
    if isinstance(criteria, dict) and criteria.get("hand_limit_dollars") is not None:
        return 1
    return max(1, int(quantity) // 2) if verdict.get("state") == REDUCED else int(quantity)


def live_nets(records: list[dict], arm: str, criteria) -> list[float]:
    """An arm's closed live nets (`core.ledgers` records) since its criteria were written, in session
    order. A row with no net is skipped: n/r is not a zero."""
    since = _written(criteria) or "9999-99-99"
    rows = [r for r in records if r.get("arm") == arm and (r.get("session") or "") >= since]
    return [
        float(r["net_pnl"])
        for r in sorted(rows, key=lambda r: r.get("session") or "")
        if r.get("net_pnl") is not None
    ]


# --------------------------------------------------------------------------- the latch
def latch_path(module: str) -> Path:
    return _home.state_dir() / "kill" / f"{module}.json"


def latched(module: str) -> dict | None:
    """The module's latched block, or None. An unreadable latch is still a latch."""
    p = latch_path(module)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"reasons": ["latch file unreadable"]}


def latch(module: str, arm: str, verdict: dict) -> None:
    """Write the block, once. Only a person clears it (`clear`)."""
    p = latch_path(module)
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "module": module,
        "arm": arm,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **verdict,
    }
    p.write_text(json.dumps(body, indent=2), encoding="utf-8")


def gate(module: str, arm: str, records: list[dict], criteria) -> dict:
    """The one call a live loop makes before an entry: a latched block refuses; otherwise evaluate
    and, on a fresh block, latch it. Returns the verdict (`state`, `reasons`, ...)."""
    held = latched(module)
    if held is not None:
        return {"state": BLOCKED, "reasons": [LATCHED, *held.get("reasons", [])], "latched": held}
    verdict = evaluate(live_nets(records, arm, criteria), criteria)
    # Unwritten criteria refuse every tick but do not latch: writing them is the fix.
    if verdict["state"] == BLOCKED and verdict["reasons"] != [UNWRITTEN]:
        latch(module, arm, verdict)
    return verdict


def clear(module: str) -> bool:
    p = latch_path(module)
    if not p.exists():
        return False
    p.unlink()
    return True


# --------------------------------------------------------------------------- CLI
def evidence_records(db: Path, schema: str) -> list[dict]:
    """Closed records from one ledger, or from every `*.db` in a directory (a replay writes one
    ledger per session)."""
    reader = ledgers.READERS[ledgers.canonical_schema(schema)]
    out = []
    for path in sorted(db.glob("*.db")) if db.is_dir() else [db]:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            out += reader(conn)
        finally:
            conn.close()
    return out


def _cmd_derive(args) -> int:
    rows = sorted(
        (
            r
            for r in evidence_records(Path(args.db), args.schema)
            if r.get("arm") == args.arm and r.get("net_pnl") is not None
        ),
        key=lambda r: r.get("session") or "",
    )
    nets = [float(r["net_pnl"]) for r in rows]
    result = derive(nets, sessions=len({r.get("session") for r in rows}) or None, horizon=args.horizon)
    if result.get("insufficient"):
        result["hand_limit_dollars"] = None
        result["note"] = "under MIN_EVIDENCE: write hand_limit_dollars by hand; the arm trades at size 1"
    span = f"{rows[0].get('session')}..{rows[-1].get('session')}" if rows else "none"
    evidence = f"{args.schema} arm {args.arm}, {Path(args.db).name}, {span}"
    block = {"written": date.today().isoformat(), "evidence": evidence, **result}
    print(json.dumps(block, indent=2))
    return 0


def _cmd_clear(args) -> int:
    print("cleared" if clear(args.module) else "no latch")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m cherrypick.core.killcriteria", description=__doc__.splitlines()[0]
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("derive", help="print a live.kill block from an arm's paper evidence")
    d.add_argument(
        "--db", required=True, help="the ledger the arm was tested in, or a replay's directory of them"
    )
    d.add_argument("--schema", required=True, help="its trade schema (meic_ic, bwb_132, ...)")
    d.add_argument("--arm", required=True)
    d.add_argument("--horizon", type=int, default=DEFAULT_HORIZON, help="trades per resampled sequence")
    d.set_defaults(fn=_cmd_derive)
    c = sub.add_parser("clear", help="clear a latched block (a person, deliberately)")
    c.add_argument("--module", required=True)
    c.set_defaults(fn=_cmd_clear)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
