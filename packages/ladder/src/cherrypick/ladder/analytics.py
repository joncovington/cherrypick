"""The forward read, per arm: closed lots and their money, open lots and their hold, from the ledger.

Pure over rows. No verdict lives here: item 26 failed and item 27's `decay` passed, and paper says
what the rule did since its first lot, nothing more.

Two counts put paper in the group's own terms: lots sold within 18 days of purchase, and open lots
older than 18 days (their "trade hospital", where they write calls until the lot recovers). A
closed-lot win rate is always 100% under a no-stop rule; what matters is how much is in hospital.

`equity` is the same book by session: realized net so far, and realized plus the lots still open
marked at that session's last bid. Under a no-stop rule the realized line can only climb while held
lots fall, so the marked line is the one that says what the arm is worth.
"""

from __future__ import annotations

from datetime import date, timedelta

from cherrypick.core import calendar as _cal

from cherrypick.ladder import rules

HOSPITAL_DAYS = 18  # the group's rule: a lot not sold within 18 days goes to their "trade hospital"


def summary(conn, arm: str | None = None, *, today: date | None = None) -> dict:
    today = today or date.today()
    sql, args = "SELECT * FROM ladder_positions", []
    if arm is not None:
        sql += " WHERE arm = ?"
        args.append(arm)
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY buy_time", args)]
    out: dict[str, dict] = {}
    for r in rows:
        a = out.setdefault(
            r["arm"],
            {
                "lots": 0,
                "closed": 0,
                "open": 0,
                "net_pnl": 0.0,
                "gross_pnl": 0.0,
                "fees": 0.0,
                "hold_days": [],
                "open_cost": 0.0,
                "open_marked": 0.0,
                "oldest_open_days": None,
                "sold_within_18d": 0,
                "in_hospital": 0,
                "symbols": set(),
            },
        )
        a["lots"] += 1
        a["symbols"].add(r["symbol"])
        if r["status"] == "closed":
            a["closed"] += 1
            if (r["hold_days"] or 0) <= HOSPITAL_DAYS:
                a["sold_within_18d"] += 1
            a["net_pnl"] += r["net_pnl"] or 0.0
            a["gross_pnl"] += r["gross_pnl"] or 0.0
            a["fees"] += r["fees"] or 0.0
            a["hold_days"].append(r["hold_days"] or 0)
        else:
            a["open"] += 1
            a["open_cost"] += -(r["entry_value"] or 0.0)
            if r["last_bid"] is not None:
                a["open_marked"] += r["quantity"] * r["last_bid"]
            age = (today - date.fromisoformat(r["buy_session"])).days
            a["oldest_open_days"] = max(a["oldest_open_days"] or 0, age)
            if age > HOSPITAL_DAYS:
                a["in_hospital"] += 1
    for a in out.values():
        holds = sorted(a.pop("hold_days"))
        a["median_hold_days"] = holds[len(holds) // 2] if holds else None
        a["longest_closed_hold_days"] = holds[-1] if holds else None
        a["unrealised_at_bid"] = round(a["open_marked"] - a["open_cost"], 2) if a["open"] else 0.0
        a["symbols"] = sorted(a["symbols"])
        for k in ("net_pnl", "gross_pnl", "fees", "open_cost", "open_marked"):
            a[k] = round(a[k], 2)
    return {"arms": out}


def _session_bids(conn) -> dict[tuple[str, str], float]:
    """(session, fund) -> the bid of that session's last tick that read one. The feed ledger is the
    loop's own record of the quote it acted on, so the mark is the price paper would have sold at."""
    has = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ladder_snapshots'")
    if has.fetchone() is None:
        return {}
    rows = conn.execute(
        "SELECT s.trade_date, s.symbol, s.bid FROM ladder_snapshots s JOIN ("
        "  SELECT trade_date, symbol, MAX(id) AS id FROM ladder_snapshots"
        "  WHERE status = 'ok' AND bid IS NOT NULL GROUP BY trade_date, symbol"
        ") last ON last.id = s.id"
    )
    return {(r[0], r[1]): float(r[2]) for r in rows}


def _sessions(first: str, last: str) -> list[str]:
    d, end, out = date.fromisoformat(first), date.fromisoformat(last), []
    while d <= end:
        if _cal.is_trading_day(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def equity(conn, arm: str | None = None) -> dict:
    """Each arm's book by session, from its first buy to the last session the ledger saw.

    Per session: `realized` is the cumulative net of lots sold by its close; `unrealized` is what
    the lots still open at its close would net if sold at that session's last bid, by the same
    `rules.money` a sale books (both fills' fees included); `marked` is the two together. A lot
    bought and sold in one session is realized only. A session where an open lot's fund has no bid
    (the loop did not run, or read no quote) has `unrealized` and `marked` None and counts the lot
    in `unmarked`: a gap, never a carried-forward price. The latest session is marked at its latest
    tick, so while it is in progress the point moves.
    """
    sql, args = "SELECT * FROM ladder_positions", []
    if arm is not None:
        sql += " WHERE arm = ?"
        args.append(arm)
    lots = [dict(r) for r in conn.execute(sql + " ORDER BY buy_session, id", args)]
    bids = _session_bids(conn)
    if not lots:
        return {"arms": {}}
    sold = [r["sell_session"] for r in lots if r["sell_session"]]
    last = max([s for s, _ in bids] + [r["buy_session"] for r in lots] + sold)
    by_arm: dict[str, list[dict]] = {}
    for r in lots:
        by_arm.setdefault(r["arm"], []).append(r)
    out: dict[str, list[dict]] = {}
    for name, rows in by_arm.items():
        points = []
        for s in _sessions(min(r["buy_session"] for r in rows), last):
            realized, unrealized, cost, held, unmarked = 0.0, 0.0, 0.0, 0, 0
            for r in rows:
                sold = r["status"] == "closed" and r["sell_session"] is not None
                if sold and r["sell_session"] <= s:
                    realized += r["net_pnl"] or 0.0
                elif r["buy_session"] <= s:
                    held += 1
                    cost += -(r["entry_value"] or 0.0)
                    bid = bids.get((s, r["symbol"]))
                    if bid is None:
                        unmarked += 1
                    else:
                        unrealized += rules.money(r["quantity"], r["buy_price"], bid)["net_pnl"]
            u = None if unmarked else round(unrealized, 2)
            points.append(
                {
                    "session": s,
                    "realized": round(realized, 2),
                    "unrealized": u,
                    "marked": None if u is None else round(realized + u, 2),
                    "open_lots": held,
                    "open_cost": round(cost, 2),
                    "unmarked": unmarked,
                }
            )
        out[name] = points
    return {"arms": out}
