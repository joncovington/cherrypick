"""The shadow report: each armed day's live dry-run buys against what paper's arm did that day.

Read-only over both ledgers, no broker, no network. For a day the live loop journaled anything, per fund
the live pilot trades (`live.symbols`; paper's other funds are not compared):

- **A live buy** (a dry-run intent, `would place buy <fund> <client id>`) is laid beside paper's lot with
  the same number that session. Same shares and same level is `agree`. No paper lot with that number is
  `resting`, consistent, when paper's lowest ask that day never reached the level, and `live_only` when
  it did (paper would have bought and did not).
- **A paper lot** no live buy accounts for is `capped` when a live block covers its minute (a cap, the
  halt flag, an early close: the live pilot is narrower than paper), `live_not_ticking` when the live
  loop ran no tick within two minutes of it, and `paper_only` otherwise.

`agree`, `resting` and `capped` are consistent; `live_only`, `paper_only` and `differs` are not. A dry-run
loop fills nothing, so its lot count never moves: past its first lot a fund's later live levels are
blocked by the per-fund cap, which is what `capped` reads. Sells are not compared: a dry-run book holds
no lots to sell.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

CONSISTENT = frozenset({"agree", "resting", "capped"})
TICK_WINDOW = timedelta(minutes=2)
_NOT_A_CAP = frozenset({"after_disarm_at", "before_session_start", "unarmed"})


def _when(text) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def _live_days(live) -> list[str]:
    return [
        r[0] for r in live.execute("SELECT DISTINCT trade_date FROM ladder_decisions ORDER BY trade_date")
    ]


def _live_buys(live, day: str) -> dict[str, list[dict]]:
    """Each fund's dry-run buy intents that day, from the journal's own `detail` (shares, limit), one per
    client id, numbered in level order: a session's lots are bought from the highest level down, so the
    nth level is the nth lot. The order layer is not read here (the module fence keeps it to the tick)."""
    seen: dict[str, dict[str, dict]] = {}
    for symbol, reason, detail in live.execute(
        "SELECT symbol, reason, detail FROM ladder_decisions WHERE trade_date = ? AND mode = 'dry_run' "
        "AND reason LIKE 'would place buy %' ORDER BY id",
        (day,),
    ):
        try:
            d = json.loads(detail or "{}")
            shares, price = int(d["shares"]), round(float(d["limit"]), 2)
        except (TypeError, ValueError, KeyError):
            continue
        # the latest row for a client id carries its current terms
        seen.setdefault(str(symbol).upper(), {})[str(reason)] = {"shares": shares, "price": price}
    out = {}
    for fund, by_id in seen.items():
        ordered = sorted(by_id.values(), key=lambda b: -b["price"])
        out[fund] = [{**b, "n": i} for i, b in enumerate(ordered, start=1)]
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
        "SELECT position_id, quantity, buy_level, buy_price, buy_time FROM ladder_positions "
        "WHERE arm = ? AND symbol = ? AND buy_session = ? ORDER BY buy_time, id",
        (arm, fund, day),
    )
    keys = ("position_id", "shares", "level", "price", "buy_time")
    return [dict(zip(keys, r, strict=True)) for r in rows]


def _paper_low_ask(paper, day: str, fund: str) -> float | None:
    row = paper.execute(
        "SELECT MIN(ask) FROM ladder_snapshots WHERE trade_date = ? AND symbol = ? AND status = 'ok'",
        (day, fund),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _cap_covering(blocks: list[dict], at: datetime | None) -> list[str]:
    """The live blocks (not the day's end or start) whose journaled span covers `at`, give or take a tick."""
    hits = []
    for b in blocks:
        if b["reason"] in _NOT_A_CAP:
            continue
        first, last = _when(b["first_ts"]), _when(b["last_ts"])
        if at is None or first is None or last is None or first - TICK_WINDOW <= at <= last + TICK_WINDOW:
            hits.append(b["reason"])
    return sorted(set(hits))


def _fund_rows(live, paper, *, arm: str, day: str, fund: str, buys: list[dict]) -> dict:
    lots = _paper_lots(paper, arm, day, fund)
    low = _paper_low_ask(paper, day, fund)
    blocks = _live_blocks(live, day, fund)
    ticks = _live_ticks(live, day)
    rows = []
    for b in buys:
        lot = lots[b["n"] - 1] if b["n"] <= len(lots) else None
        row = {"n": b["n"], "live": {"shares": b["shares"], "level": b["price"]}}
        if lot is not None:
            row["paper"] = {"shares": lot["shares"], "level": lot["level"], "time": lot["buy_time"]}
            same = lot["shares"] == b["shares"] and round(float(lot["level"]), 2) == round(
                float(b["price"]), 2
            )
            row["verdict"] = "agree" if same else "differs"
        else:
            reached = low is not None and low <= float(b["price"])
            row["verdict"] = "live_only" if reached else "resting"
            row["paper_low_ask"] = low
        rows.append(row)
    taken = {b["n"] for b in buys}
    for i, lot in enumerate(lots, start=1):
        if i in taken:
            continue
        at = _when(lot["buy_time"])
        row = {"n": i, "paper": {"shares": lot["shares"], "level": lot["level"], "time": lot["buy_time"]}}
        caps = _cap_covering(blocks, at)
        if caps:
            row.update(verdict="capped", live_blocked=caps)
        elif at is not None and not any(abs(t - at) <= TICK_WINDOW for t in ticks):
            row["verdict"] = "live_not_ticking"
        else:
            row["verdict"] = "paper_only"
        rows.append(row)
    rows.sort(key=lambda r: r["n"])
    return {
        "rows": rows,
        "live_blocked": {b["reason"]: b["occurrences"] for b in blocks},
        "paper_low_ask": low,
        "consistent": all(r["verdict"] in CONSISTENT for r in rows),
    }


def report(live, paper, *, arm: str, funds: list[str], since: str | None = None) -> dict:
    """Every day the live loop journaled, from `since`: per fund the live pilot trades (`live.symbols`),
    each live dry-run buy and each paper lot of `arm`, with a verdict, and whether the day is consistent.
    Paper's other funds are not compared: the live loop never looks at them."""
    pilot = sorted({str(f).strip().upper() for f in funds if str(f).strip()})
    days = []
    for day in _live_days(live):
        if since and day < since:
            continue
        buys = _live_buys(live, day)
        held = {r[0] for r in paper.execute(
            "SELECT DISTINCT symbol FROM ladder_positions WHERE arm = ? AND buy_session = ?", (arm, day)
        )}  # fmt: skip
        funds = [f for f in pilot if f in buys or f in held]
        per = {f: _fund_rows(live, paper, arm=arm, day=day, fund=f, buys=buys.get(f, [])) for f in funds}
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
