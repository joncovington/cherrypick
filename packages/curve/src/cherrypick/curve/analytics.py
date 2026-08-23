"""The one query layer every read surface goes through. Read-only.

`None` never means zero — a position not yet closed reports `net_pnl: None`, and an average over an
empty bucket is `None`.
"""

from __future__ import annotations


def headline(conn) -> dict:
    """Per-book, per-symbol results over CLOSED positions, plus what is still open."""
    books: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT book, symbol, COUNT(*) AS n, SUM(gross_pnl) AS gross, SUM(fees) AS fees, "
        "SUM(gross_pnl) - SUM(fees) AS net, SUM((gross_pnl - fees) > 0) AS wins "
        "FROM curve_positions WHERE status = 'closed' GROUP BY book, symbol ORDER BY book, symbol"
    ):
        books.setdefault(row["book"], {})[row["symbol"]] = {
            "positions": row["n"],
            "gross_pnl": round(row["gross"], 2) if row["gross"] is not None else None,
            "fees": round(row["fees"], 2) if row["fees"] is not None else None,
            "net_pnl": round(row["net"], 2) if row["net"] is not None else None,
            "win_rate": round(row["wins"] / row["n"], 4) if row["n"] else None,
        }
    open_rows = conn.execute("SELECT COUNT(*) AS n FROM curve_positions WHERE status != 'closed'").fetchone()
    return {"books": books, "open_positions": open_rows["n"], "flip_divergence": flip_divergence(conn)}


def flip_divergence(conn) -> dict:
    """How many (symbol, entry_session) pairs saw `control` close on `regime_flip` while `noflip`
    held past that point — the noflip comparison's EFFECTIVE sample, per the module's own honesty
    rule. Until a flip actually fires, control and noflip are byte-identical by construction (the
    known, expected `find_identical_readings` collision), so the trade count is not the right
    denominator for "what did the flip rule do" — this count is."""
    rows = conn.execute(
        "SELECT symbol, entry_session FROM curve_positions "
        "WHERE book = 'control' AND exit_reason = 'regime_flip'"
    ).fetchall()
    diverged = 0
    for r in rows:
        noflip = conn.execute(
            "SELECT 1 FROM curve_positions WHERE book = 'noflip' AND symbol = ? AND entry_session = ? "
            "AND (exit_reason != 'regime_flip' OR exit_reason IS NULL)",
            (r["symbol"], r["entry_session"]),
        ).fetchone()
        if noflip:
            diverged += 1
    return {
        "flip_divergence_count": diverged,
        "control_flip_exits": len(rows),
        "note": (
            "the noflip comparison's effective sample is this count, not the trade count — "
            "control and noflip are identical until a flip actually fires"
        ),
    }


def worksheet(conn) -> list[dict]:
    """The live per-position worksheet: one row per open position with its entry metrics and the
    latest usable close-cost mark."""
    out = []
    for p in conn.execute("SELECT * FROM curve_positions WHERE status != 'closed' ORDER BY symbol, book"):
        p = dict(p)
        latest = conn.execute(
            "SELECT close_cost, spot, marked_at FROM curve_marks WHERE position_id = ? "
            "AND close_cost IS NOT NULL AND usable = 1 ORDER BY marked_at DESC LIMIT 1",
            (p["position_id"],),
        ).fetchone()
        out.append(
            {
                "position_id": p["position_id"],
                "symbol": p["symbol"],
                "book": p["book"],
                "status": p["status"],
                "short_strike": p["short_strike"],
                "long_strike": p["long_strike"],
                "expiration": p["expiration"],
                "entry_spot": p["entry_spot"],
                "entry_credit": p["entry_credit"],
                "entry_width": p["entry_width"],
                "entry_max_loss": p["entry_max_loss"],
                "entry_credit_pct_of_width": p["entry_credit_pct_of_width"],
                "entry_ratio": p["entry_ratio"],
                "entry_regime": p["entry_regime"],
                "entry_hook": bool(p["entry_hook"]),
                "exposure_ticks": p["exposure_ticks"],
                "current_close_cost": latest["close_cost"] if latest else None,
                "current_spot": latest["spot"] if latest else None,
            }
        )
    return out


def exposure(conn) -> dict:
    """The early-assignment-exposure telemetry, aggregated per position."""
    positions = []
    for row in conn.execute(
        "SELECT position_id, "
        "SUM(CASE WHEN assignment_exposed = 1 THEN 1 ELSE 0 END) AS exposed, "
        "COUNT(*) AS marked FROM curve_marks WHERE short_tv IS NOT NULL AND usable = 1 "
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


def regime_series(conn, *, limit: int = 60) -> list[dict]:
    """The most recent `limit` daily regime rows, oldest first — the module's own read of its
    second product."""
    rows = conn.execute("SELECT * FROM curve_regime ORDER BY trade_date DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def mark_coverage(conn, session_date: str) -> dict:
    """How good the day's mark substrate is — a barren session should read as thin data, never as
    a market."""
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(usable = 0) AS refused FROM curve_marks WHERE session_date = ?",
        (session_date,),
    ).fetchone()
    refusals = {
        r["refusal"]: r["n"]
        for r in conn.execute(
            "SELECT refusal, COUNT(*) AS n FROM curve_marks WHERE session_date = ? AND usable = 0 "
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
