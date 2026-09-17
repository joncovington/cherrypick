"""Read-side replay of the two 2026-09-17 entry gates over rows already recorded.

Both gates are pure functions of facts every position row already carries -- the fill time, the
completion time (or its absence), and the trend bucket stamped at entry -- so a session can be
re-run under either rule exactly, by dropping the entries the rule would have refused and summing
what is left. Structures are independent (a fly never affects another's outcome), which is what
makes dropping rows a valid replay rather than a simulation. This is the cheap first answer; the
advised twin's forward A/B is the confirmation.

Read-only over the paper ledger. Never writes, never reaches a broker.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import mean, median

MISS_STOP_SWEEP = (15, 30, 45, 60, 90)
TREND_BUCKETS = ("up_from_open", "down_from_open")


def load_rows(
    conn, *, start: str, end: str | None = None, arm: str = "control", symbol: str = "SPX"
) -> list[dict]:
    q = (
        "SELECT trade_date, kind, entry_time, completed_at, pnl, entry_trend_bucket FROM fly_positions "
        "WHERE arm = ? AND symbol = ? AND status = 'settled' AND trade_date >= ?"
    )
    args: list = [arm, symbol, start]
    if end:
        q += " AND trade_date <= ?"
        args.append(end)
    q += " ORDER BY trade_date, entry_time"
    return [dict(r) for r in conn.execute(q, args)]


def _t(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _by_day(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[r["trade_date"]].append(r)
    return out


def replay_miss_stop(rows: list[dict], minutes: float) -> dict:
    """Keep each day's entries in order; drop one when an EARLIER KEPT spread was still
    uncompleted at that moment and had been open for `minutes` or longer. A spread that later
    completed still counts while it was open -- at the time, it was a miss in progress."""
    kept: list[dict] = []
    for rs in _by_day(rows).values():
        taken: list[dict] = []
        for r in rs:
            t = _t(r["entry_time"])
            blocked = False
            for k in taken:
                done = _t(k["completed_at"]) if k["kind"] == "fly" else None
                still_open = done is None or done > t
                if still_open and (t - _t(k["entry_time"])).total_seconds() >= minutes * 60:
                    blocked = True
                    break
            if not blocked:
                taken.append(r)
        kept.extend(taken)
    return summarize(rows, kept)


def replay_trend_bucket(rows: list[dict], bucket: str) -> dict:
    return summarize(rows, [r for r in rows if r.get("entry_trend_bucket") != bucket])


def summarize(rows: list[dict], kept: list[dict]) -> dict:
    days = _by_day(rows)
    kept_days = _by_day(kept)
    per_day = {d: round(sum(r["pnl"] for r in kept_days.get(d, [])), 2) for d in days}
    pnls = list(per_day.values())
    return {
        "entries": len(rows),
        "kept": len(kept),
        "dropped": len(rows) - len(kept),
        "misses_kept": sum(1 for r in kept if r["kind"] == "short_vertical"),
        "completion_rate": round(sum(1 for r in kept if r["kind"] == "fly") / len(kept), 4) if kept else None,
        "net_pnl": round(sum(r["pnl"] for r in kept), 2),
        "days": len(days),
        "losing_days": sum(1 for p in pnls if p < 0),
        "worst_day": round(min(pnls), 2) if pnls else None,
        "median_day": round(median(pnls), 2) if pnls else None,
        "mean_day": round(mean(pnls), 2) if pnls else None,
        "per_day": per_day,
    }


def sweep(rows: list[dict]) -> dict:
    base = summarize(rows, rows)
    return {
        "base": base,
        "miss_stop": {str(m): replay_miss_stop(rows, m) for m in MISS_STOP_SWEEP},
        "trend_bucket": {b: replay_trend_bucket(rows, b) for b in TREND_BUCKETS},
    }
