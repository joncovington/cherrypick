"""The shadow report: each armed day's live dry-run buys against what paper's arm did that day.

Read-only over both ledgers, no broker, no network. For a day the live loop journaled anything, per fund
the live pilot trades (`live.symbols`; paper's other funds are not compared), a live buy and a paper lot
are paired by their level, which both compute from the same rule (`rules.buy_level`):

- **A live buy** (a dry-run intent) with a paper lot at its level is `agree` when the shares match and
  `differs` when they do not. With none, it is `resting` when paper's lowest ask that day never reached
  the level, `live_only` when it did (paper would have bought and did not), and `paper_no_data` when
  paper recorded no quote that day.
- **A paper lot** no live buy accounts for is `capped` when one of the live pilot's own limits covers its
  minute (`LIVE_LIMITS`: its caps, the halt flag, an early close, the breaker, settled cash),
  `live_not_ticking` when the live loop ran no tick within two minutes of it, and `paper_only` otherwise.

`agree`, `resting` and `capped` are consistent; the rest are not. A dry-run loop fills nothing, so its lot
count never moves: past its first lot a fund's later live levels are blocked by the per-fund cap, which
is what `capped` reads. Sells are not compared: a dry-run book holds no lots to sell.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

CONSISTENT = frozenset({"agree", "resting", "capped"})
TICK_WINDOW = timedelta(minutes=2)

#: The block reasons (`orders.plan`'s `buys_blocked`) that are the live pilot's own limits, narrower than
#: paper's by design. Anything else (a missing reference close, a positions mismatch, an unreadable
#: broker, the day's start or end) does not explain a paper lot.
LIVE_LIMITS = frozenset(
    {
        "name_cap_full",
        "total_cap_full",
        "group_cap_full",
        "dollar_ceiling",
        "lot_too_small",
        "settled_cash_short",
        "halted",
        "half_day_no_entries",
        "drawdown_breaker",
    }
)


def _when(text) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def _level(price) -> float:
    return round(float(price), 2)


def _live_days(live) -> list[str]:
    return [
        r[0] for r in live.execute("SELECT DISTINCT trade_date FROM ladder_decisions ORDER BY trade_date")
    ]


def _live_buys(live, day: str) -> dict[str, dict[float, dict]]:
    """Each fund's dry-run buy intents that day by level, from the journal's own `detail` (shares,
    limit); the latest row at a level carries its current terms, so a re-sized intent replaces the old
    one. The order layer is not read here (the module fence keeps it to the tick)."""
    out: dict[str, dict[float, dict]] = {}
    for symbol, detail in live.execute(
        "SELECT symbol, detail FROM ladder_decisions WHERE trade_date = ? AND mode = 'dry_run' "
        "AND reason LIKE 'would place buy %' ORDER BY id",
        (day,),
    ):
        try:
            d = json.loads(detail or "{}")
            shares, level = int(d["shares"]), _level(d["limit"])
        except (TypeError, ValueError, KeyError):
            continue
        out.setdefault(str(symbol).upper(), {})[level] = {"shares": shares, "level": level}
    return out


def _live_blocks(live, day: str, fund: str) -> list[dict]:
    rows = live.execute(
        "SELECT symbol, reason, occurrences, first_ts, last_ts FROM ladder_decisions WHERE trade_date = ? "
        "AND mode = 'blocked' AND symbol IN (?, '-') ORDER BY id",
        (day, fund),
    )
    return [
        dict(zip(("symbol", "reason", "occurrences", "first_ts", "last_ts"), r, strict=True)) for r in rows
    ]


def _live_ticks(live, day: str) -> list[datetime]:
    rows = live.execute("SELECT ran_at FROM ladder_loop_iterations WHERE session_date = ?", (day,))
    return [datetime.fromtimestamp(float(r[0])).astimezone() for r in rows if r[0] is not None]


def _paper_lots(paper, arm: str, day: str, fund: str) -> list[dict]:
    rows = paper.execute(
        "SELECT position_id, quantity, buy_level, buy_time FROM ladder_positions "
        "WHERE arm = ? AND symbol = ? AND buy_session = ? ORDER BY buy_time, id",
        (arm, fund, day),
    )
    return [{"shares": r[1], "level": _level(r[2]), "time": r[3]} for r in rows]


def _paper_low_ask(paper, day: str, fund: str) -> float | None:
    row = paper.execute(
        "SELECT MIN(ask) FROM ladder_snapshots WHERE trade_date = ? AND symbol = ? AND status = 'ok'",
        (day, fund),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _limits_covering(blocks: list[dict], at: datetime | None) -> list[str]:
    """The live pilot's own limits whose journaled span covers `at`, give or take a tick."""
    hits = []
    for b in blocks:
        if b["reason"] not in LIVE_LIMITS:
            continue
        first, last = _when(b["first_ts"]), _when(b["last_ts"])
        if at is None or first is None or last is None or first - TICK_WINDOW <= at <= last + TICK_WINDOW:
            hits.append(b["reason"])
    return sorted(set(hits))


def _fund_rows(live, paper, *, arm: str, day: str, fund: str, buys: dict[float, dict]) -> dict:
    lots = _paper_lots(paper, arm, day, fund)
    low = _paper_low_ask(paper, day, fund)
    blocks = _live_blocks(live, day, fund)
    ticks = _live_ticks(live, day)
    by_level = {lot["level"]: lot for lot in lots}
    rows = []
    for level, b in buys.items():
        row = {"level": level, "live": {"shares": b["shares"]}}
        lot = by_level.get(level)
        if lot is not None:
            row["paper"] = {"shares": lot["shares"], "time": lot["time"]}
            row["verdict"] = "agree" if lot["shares"] == b["shares"] else "differs"
        elif low is None:
            row["verdict"] = "paper_no_data"
        else:
            row["verdict"] = "live_only" if low <= level else "resting"
            row["paper_low_ask"] = low
        rows.append(row)
    for lot in lots:
        if lot["level"] in buys:
            continue
        at = _when(lot["time"])
        row = {"level": lot["level"], "paper": {"shares": lot["shares"], "time": lot["time"]}}
        limits = _limits_covering(blocks, at)
        if limits:
            row.update(verdict="capped", live_blocked=limits)
        elif at is not None and not any(abs(t - at) <= TICK_WINDOW for t in ticks):
            row["verdict"] = "live_not_ticking"
        else:
            row["verdict"] = "paper_only"
        rows.append(row)
    rows.sort(key=lambda r: -r["level"])
    return {
        "rows": rows,
        "live_blocked": {b["reason"]: b["occurrences"] for b in blocks},
        "paper_low_ask": low,
        "consistent": all(r["verdict"] in CONSISTENT for r in rows),
    }


def report(live, paper, *, arm: str, funds: list[str], since: str | None = None) -> dict:
    """Every day the live loop journaled, from `since`: per fund the live pilot trades (`live.symbols`),
    each live dry-run buy and each paper lot of `arm`, with a verdict, and whether the day is consistent.
    With no pilot funds there is nothing to compare, and that is a refusal, not an empty agreement."""
    pilot = sorted({str(f).strip().upper() for f in funds if str(f).strip()})
    if not pilot:
        return {"ok": False, "error": "live.symbols is empty: the live pilot trades no fund to compare"}
    days = []
    for day in _live_days(live):
        if since and day < since:
            continue
        buys = _live_buys(live, day)
        held = {r[0] for r in paper.execute(
            "SELECT DISTINCT symbol FROM ladder_positions WHERE arm = ? AND buy_session = ?", (arm, day)
        )}  # fmt: skip
        compared = [f for f in pilot if f in buys or f in held]
        per = {f: _fund_rows(live, paper, arm=arm, day=day, fund=f, buys=buys.get(f, {})) for f in compared}
        days.append({"day": day, "funds": per, "consistent": all(v["consistent"] for v in per.values())})
    mismatches = sum(
        1 for d in days for f in d["funds"].values() for r in f["rows"] if r["verdict"] not in CONSISTENT
    )
    return {
        "ok": True,
        "arm": arm,
        "funds": pilot,
        "days": days,
        "summary": {
            "days": len(days),
            "consistent_days": sum(1 for d in days if d["consistent"]),
            "mismatches": mismatches,
        },
    }
