"""The one query layer every read surface goes through. Read-only.

`None` never means zero — a position not yet closed reports `net_pnl: None`, and an average over an
empty bucket is `None`.
"""

from __future__ import annotations


def headline(conn) -> dict:
    """Per-book, per-symbol results over CLOSED positions, plus what is still open and each arm's
    fire count — the real effective sample for an arm-vs-control comparison, not the trade count
    (until an arm's add-on fires, its rows are byte-identical to control's, an expected
    `find_identical_readings` collision)."""
    books: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT book, symbol, COUNT(*) AS n, SUM(gross_pnl) AS gross, SUM(fees) AS fees, "
        "SUM(gross_pnl) - SUM(fees) AS net, SUM((gross_pnl - fees) > 0) AS wins "
        "FROM bwb_positions WHERE status = 'closed' GROUP BY book, symbol ORDER BY book, symbol"
    ):
        books.setdefault(row["book"], {})[row["symbol"]] = {
            "positions": row["n"],
            "gross_pnl": round(row["gross"], 2) if row["gross"] is not None else None,
            "fees": round(row["fees"], 2) if row["fees"] is not None else None,
            "net_pnl": round(row["net"], 2) if row["net"] is not None else None,
            "win_rate": round(row["wins"] / row["n"], 4) if row["n"] else None,
        }
    open_rows = conn.execute("SELECT COUNT(*) AS n FROM bwb_positions WHERE status != 'closed'").fetchone()
    return {"books": books, "open_positions": open_rows["n"], "fire_counts": fire_counts(conn)}


def fire_counts(conn) -> dict:
    """Per-book add-on fire counts — trade count vs fire count, the plan's own honesty rule.
    `delta` fires most (raw proximity); `bounce` needs the move plus a turn; `flip` needs spot to
    have entered negative-gamma territory at all. A quiet `flip` book is the honest state."""
    out: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT book, COUNT(*) AS n, SUM(addon_fired_at IS NOT NULL) AS fired "
        "FROM bwb_positions GROUP BY book ORDER BY book"
    ):
        out[row["book"]] = {
            "positions": row["n"],
            "fired": row["fired"] or 0,
            "fire_rate": round((row["fired"] or 0) / row["n"], 4) if row["n"] else None,
        }
    return out


def worksheet(conn) -> list[dict]:
    """The live per-position worksheet: one row per open position with its entry metrics and the
    latest usable close-cost mark."""
    out = []
    for p in conn.execute("SELECT * FROM bwb_positions WHERE status != 'closed' ORDER BY symbol, book"):
        p = dict(p)
        latest = conn.execute(
            "SELECT close_cost, spot, marked_at FROM bwb_marks WHERE position_id = ? "
            "AND close_cost IS NOT NULL AND usable = 1 ORDER BY marked_at DESC LIMIT 1",
            (p["position_id"],),
        ).fetchone()
        out.append(
            {
                "position_id": p["position_id"],
                "symbol": p["symbol"],
                "book": p["book"],
                "status": p["status"],
                "body_strike": p["body_strike"],
                "near_strike": p["near_strike"],
                "far_strike": p["far_strike"],
                "expiration": p["expiration"],
                "entry_spot": p["entry_spot"],
                "entry_credit": p["entry_credit"],
                "entry_max_loss": p["entry_max_loss"],
                "peak_abs_delta": p["peak_abs_delta"],
                "below_flip_seen": bool(p["below_flip_seen"]),
                "armed_at": p["armed_at"],
                "addon_fired_at": p["addon_fired_at"],
                "addon_credit": p["addon_credit"],
                "current_close_cost": latest["close_cost"] if latest else None,
                "current_spot": latest["spot"] if latest else None,
            }
        )
    return out


def trigger_coverage(conn, session_date: str) -> dict:
    """How good the day's trigger-tick substrate is — a barren session should read as thin data,
    never as a market."""
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(measured = 0) AS refused FROM bwb_trigger_ticks WHERE session_date = ?",
        (session_date,),
    ).fetchone()
    total = row["total"] or 0
    return {
        "session": session_date,
        "ticks": total,
        "refused": row["refused"] or 0,
        "refusal_share": round((row["refused"] or 0) / total, 4) if total else None,
    }


def mark_coverage(conn, session_date: str) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(usable = 0) AS refused FROM bwb_marks WHERE session_date = ?",
        (session_date,),
    ).fetchone()
    total = row["total"] or 0
    return {
        "session": session_date,
        "marks": total,
        "refused": row["refused"] or 0,
        "refusal_share": round((row["refused"] or 0) / total, 4) if total else None,
    }
