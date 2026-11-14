"""The forward read, per arm: closed lots and their money, open lots and their hold, from the ledger.

Pure over rows. No verdict lives here: item 26 failed and item 27's `decay` passed, and paper says
what the rule did since its first lot, nothing more.

Two counts put paper in the group's own terms: lots sold within 18 days of purchase, and open lots
older than 18 days (their "trade hospital", where they write calls until the lot recovers). A
closed-lot win rate is always 100% under a no-stop rule; what matters is how much is in hospital.
"""

from __future__ import annotations

from datetime import date

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
