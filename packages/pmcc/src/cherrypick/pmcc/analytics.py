"""The one query layer every read surface goes through. Read-only.

MEIC grew three call sites that disagreed about what "net" means; flies fixed that with one layer
and a test asserting the headline equals what that layer returns. Same rule here: the CLI, the
review's health/expected readers, and any future console page all read THROUGH these functions.

`None` never means zero — a position not yet closed reports `net_pnl: None`, and an average over
an empty bucket is `None`, because "not recorded" and "was zero" are different facts.
"""

from __future__ import annotations


def headline(conn) -> dict:
    """Per-book, per-symbol results over CLOSED positions, plus what is still open. Net is
    `gross_pnl - fees`, the same subtraction the suite's ledger reader performs — one convention,
    stated once."""
    books: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT book, symbol, COUNT(*) AS n, SUM(gross_pnl) AS gross, SUM(fees) AS fees, "
        "SUM(gross_pnl) - SUM(fees) AS net, SUM((gross_pnl - fees) > 0) AS wins, "
        "SUM(roll_count) AS rolls FROM pmcc_positions WHERE status = 'closed' "
        "GROUP BY book, symbol ORDER BY book, symbol"
    ):
        books.setdefault(row["book"], {})[row["symbol"]] = {
            "positions": row["n"],
            "gross_pnl": round(row["gross"], 2) if row["gross"] is not None else None,
            "fees": round(row["fees"], 2) if row["fees"] is not None else None,
            "net_pnl": round(row["net"], 2) if row["net"] is not None else None,
            "win_rate": round(row["wins"] / row["n"], 4) if row["n"] else None,
            "rolls": row["rolls"],
        }
    open_rows = conn.execute("SELECT COUNT(*) AS n FROM pmcc_positions WHERE status != 'closed'").fetchone()
    return {"books": books, "open_positions": open_rows["n"]}


def worksheet(conn) -> list[dict]:
    """The live per-position worksheet — the module's human read, one row per open position with
    its entry metrics and the latest usable short-leg mark's time value."""
    out = []
    for p in conn.execute("SELECT * FROM pmcc_positions WHERE status != 'closed' ORDER BY symbol, book"):
        p = dict(p)
        latest = conn.execute(
            "SELECT short_tv, spot, marked_at FROM pmcc_marks WHERE position_id = ? "
            "AND short_tv IS NOT NULL AND usable = 1 ORDER BY marked_at DESC LIMIT 1",
            (p["position_id"],),
        ).fetchone()
        out.append(
            {
                "position_id": p["position_id"],
                "symbol": p["symbol"],
                "book": p["book"],
                "status": p["status"],
                "long_strike": p["long_strike"],
                "long_expiration": p["long_expiration"],
                "short_strike": p["short_strike"],
                "short_expiration": p["short_expiration"],
                "entry_spot": p["entry_spot"],
                "net_debit": p["net_debit"],
                "total_premium": p["entry_total_premium"],
                "intrinsic": p["entry_short_intrinsic"],
                "time_value": p["entry_short_tv"],
                "net_time_value": p["entry_net_tv"],
                "profit_pct": p["entry_profit_pct"],
                "weekly_yield_pct": p["entry_weekly_yield_pct"],
                "downside_protection_pct": p["entry_downside_protection_pct"],
                "breakeven": p["entry_breakeven"],
                "roll_count": p["roll_count"],
                "exposure_ticks": p["exposure_ticks"],
                "current_short_tv": latest["short_tv"] if latest else None,
                "current_spot": latest["spot"] if latest else None,
            }
        )
    return out


def exposure(conn) -> dict:
    """The early-assignment-exposure telemetry, aggregated: per position, how many marked ticks the
    short's extrinsic sat under the exposure threshold, and its share of usable short-leg marks.
    This is the module's honest bound on what unmodelled early assignment could have touched."""
    positions = []
    for row in conn.execute(
        "SELECT position_id, "
        "SUM(CASE WHEN assignment_exposed = 1 THEN 1 ELSE 0 END) AS exposed, "
        "COUNT(*) AS marked FROM pmcc_marks WHERE short_tv IS NOT NULL AND usable = 1 "
        "GROUP BY position_id ORDER BY position_id"
    ):
        positions.append(
            {
                "position_id": row["position_id"],
                "exposed_ticks": row["exposed"],
                "marked_ticks": row["marked"],
                "exposed_share": round(row["exposed"] / row["marked"], 4) if row["marked"] else None,
            }
        )
    exposed_positions = sum(1 for p in positions if (p["exposed_ticks"] or 0) > 0)
    return {"positions": positions, "positions_with_exposure": exposed_positions}


def mark_coverage(conn, session_date: str) -> dict:
    """How good the day's substrate is: marks written, refusal share, and per-refusal counts —
    a barren session should be explicable as "the data was thin", never mistaken for a market."""
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(usable = 0) AS refused FROM pmcc_marks WHERE session_date = ?",
        (session_date,),
    ).fetchone()
    refusals = {
        r["refusal"]: r["n"]
        for r in conn.execute(
            "SELECT refusal, COUNT(*) AS n FROM pmcc_marks WHERE session_date = ? AND usable = 0 "
            "GROUP BY refusal",
            (session_date,),
        )
        if r["refusal"]
    }
    total = row["total"] or 0
    return {
        "session": session_date,
        "marks": total,
        "refused": row["refused"] or 0,
        "refusal_share": round((row["refused"] or 0) / total, 4) if total else None,
        "refusals": refusals,
    }
