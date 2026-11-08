"""The read side's one query layer: each cycle's result, measured as item 57 measured it.

Item 57's unit is the **cycle** (one expiry): the equal-weight mean, over the names that traded it,
of each put's net P&L in basis points of its entry forward notional. A name missed in a cycle is
absent from that cycle's mean, never a zero, and the missed names are listed beside it. Dollars
are shown beside, whole-position and adding up (the money layout); the judged measure is bp.

This is a forward read. Item 57's verdict stays item 57's; nothing here re-judges it. The figures
the study measured stay local (licensed), so the comparison with them is read beside, not here.
"""

from __future__ import annotations

import statistics

from cherrypick.weeklyput import db


def cycles(conn, arm: str | None = None) -> list[dict]:
    """One row per (arm, expiry), newest first: each name's net, the cycle's mean, what is open."""
    by_key: dict[tuple[str, str], dict] = {}
    for p in db.positions(conn, arm):
        row = by_key.setdefault(
            (p["arm"], p["expiration"]),
            {
                "arm": p["arm"],
                "expiration": p["expiration"],
                "entry_session": p["entry_session"],
                "names": {},
            },
        )
        row["names"][p["symbol"]] = {
            "status": p["status"],
            "strike": p["strike"],
            "entry_delta": p["entry_delta"],
            "entry_value": p["entry_value"],
            "exit_value": p["exit_value"],
            "gross_pnl": p["gross_pnl"],
            "fees": p["fees"] if p["fees"] is not None else p["entry_fees"],
            "settlement": p["settlement"],
            "slippage": p["slippage"] if p["slippage"] is not None else p["entry_slippage"],
            "net_pnl": p["net_pnl"],
            "net_bp": p["net_bp"],
            "exit_reason": p["exit_reason"],
            "assignment_exposed": p["assignment_exposed_at"] is not None,
        }
    for e in db.entries(conn, arm=arm):
        row = by_key.get((e["arm"], e["expiration"]))
        if row is None:
            row = by_key.setdefault(
                (e["arm"], e["expiration"]),
                {
                    "arm": e["arm"],
                    "expiration": e["expiration"],
                    "entry_session": e["entry_session"],
                    "names": {},
                },
            )
        if e["outcome"] in ("missed", "pending"):
            row.setdefault("unentered", {})[e["symbol"]] = {"outcome": e["outcome"], "refusal": e["refusal"]}
    out = []
    for row in by_key.values():
        closed = [n for n in row["names"].values() if n["status"] == "closed" and n["net_bp"] is not None]
        is_open = any(n["status"] == "open" for n in row["names"].values())
        row["unentered"] = row.get("unentered", {})
        row["finished"] = bool(closed) and not is_open
        row["cycle_net_bp"] = (
            round(statistics.fmean(n["net_bp"] for n in closed), 4) if row["finished"] else None
        )
        row["net_pnl"] = round(sum(n["net_pnl"] for n in closed), 2) if row["finished"] else None
        out.append(row)
    return sorted(out, key=lambda r: (r["expiration"], r["arm"]), reverse=True)


def summary(conn, arm: str | None = None) -> dict:
    """Each arm's forward read: finished cycles, their mean and spread in bp, the worst, per name."""
    rows = [c for c in cycles(conn, arm) if c["finished"]]
    arms: dict[str, dict] = {}
    for a in sorted({c["arm"] for c in cycles(conn, arm)}):
        mine = [c for c in rows if c["arm"] == a]
        vals = [c["cycle_net_bp"] for c in mine]
        per_name: dict[str, list[float]] = {}
        dollars = {"entry_value": 0.0, "exit_value": 0.0, "fees": 0.0, "settlement": 0.0, "slippage": 0.0}
        for c in mine:
            for sym, n in c["names"].items():
                if n["net_bp"] is None:
                    continue
                per_name.setdefault(sym, []).append(n["net_bp"])
                for k in dollars:
                    dollars[k] += float(n.get(k) or 0.0)
        gross = dollars["entry_value"] + dollars["exit_value"]
        net = gross - dollars["fees"] - dollars["settlement"] - dollars["slippage"]
        worst = min(mine, key=lambda c: c["cycle_net_bp"]) if mine else None
        arms[a] = {
            "cycles": len(mine),
            "mean_bp": round(statistics.fmean(vals), 4) if vals else None,
            "stdev_bp": round(statistics.stdev(vals), 4) if len(vals) > 1 else None,
            "positive_share": round(sum(v > 0 for v in vals) / len(vals), 4) if vals else None,
            "worst": {"expiration": worst["expiration"], "cycle_net_bp": worst["cycle_net_bp"]}
            if worst
            else None,
            "by_name": {
                s: {"trades": len(v), "mean_bp": round(statistics.fmean(v), 4)}
                for s, v in sorted(per_name.items())
            },
            "money": {
                **{k: round(v, 2) for k, v in dollars.items()},
                "gross_pnl": round(gross, 2),
                "net_pnl": round(net, 2),
                "fee_drag": round(dollars["fees"] / dollars["entry_value"], 4)
                if dollars["entry_value"]
                else None,
            },
            "missed": sum(len(c["unentered"]) for c in cycles(conn, a)),
        }
    return {"arms": arms}
