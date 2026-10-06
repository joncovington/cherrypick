"""The one query layer every read surface goes through. Read-only.

`None` never means zero — a position not yet closed reports `net_pnl: None`, and an average over an
empty bucket is `None`.
"""

from __future__ import annotations

import statistics

from cherrypick.core.metrics import excursions as _mae_mfe
from cherrypick.core.metrics import nav as _nav

from cherrypick.curve import db


def headline(conn) -> dict:
    """Per-arm, per-symbol results over CLOSED positions, plus what is still open."""
    arms: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT arm, symbol, COUNT(*) AS n, SUM(gross_pnl) AS gross, SUM(fees) AS fees, "
        "SUM(gross_pnl) - SUM(fees) AS net, SUM((gross_pnl - fees) > 0) AS wins "
        "FROM curve_positions WHERE status = 'closed' GROUP BY arm, symbol ORDER BY arm, symbol"
    ):
        arms.setdefault(row["arm"], {})[row["symbol"]] = {
            "positions": row["n"],
            "gross_pnl": round(row["gross"], 2) if row["gross"] is not None else None,
            "fees": round(row["fees"], 2) if row["fees"] is not None else None,
            "net_pnl": round(row["net"], 2) if row["net"] is not None else None,
            "win_rate": round(row["wins"] / row["n"], 4) if row["n"] else None,
        }
    open_rows = conn.execute("SELECT COUNT(*) AS n FROM curve_positions WHERE status != 'closed'").fetchone()
    return {"arms": arms, "open_positions": open_rows["n"], "flip_divergence": flip_divergence(conn)}


def flip_divergence(conn) -> dict:
    """How many (symbol, entry_session) pairs saw `control` close on `regime_flip` while `noflip`
    held past that point — the noflip comparison's EFFECTIVE sample, per the module's own honesty
    rule. Until a flip actually fires, control and noflip are byte-identical by construction (the
    known, expected `find_identical_readings` collision), so the trade count is not the right
    denominator for "what did the flip rule do" — this count is."""
    rows = conn.execute(
        "SELECT symbol, entry_session FROM curve_positions "
        "WHERE arm = 'control' AND exit_reason = 'regime_flip'"
    ).fetchall()
    diverged = 0
    for r in rows:
        noflip = conn.execute(
            "SELECT 1 FROM curve_positions WHERE arm = 'noflip' AND symbol = ? AND entry_session = ? "
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
    for p in conn.execute("SELECT * FROM curve_positions WHERE status != 'closed' ORDER BY symbol, arm"):
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
                "arm": p["arm"],
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


def excursions(conn) -> dict:
    """MAE/MFE per CLOSED position (docs/metrics-plan.md Phase 2), plus their distributions.

    `core.metrics.excursions` owns the generic MAE/MFE computation; this function is the module-
    specific half the plan calls for -- pairing `curve_marks`' per-tick `close_cost` (the spread's
    modeled cost to close right then, stored once per tick on the short leg's row per
    `paper_loop.py`) to one position and turning it into an ordered P&L-relative-to-entry series.
    At each usable mark, `entry_credit - close_cost` is exactly what closing then would have
    realized against the credit received at entry, so the series already starts near zero and
    needs no separate basis (unlike a raw price series would) -- `core.metrics.excursions` is
    called with `basis=0.0`.

    Positions with no `entry_credit` (pre-instrumentation row) or no usable marks are skipped from
    the per-position list rather than reported with a fabricated 0.0."""
    positions = []
    for p in conn.execute(
        "SELECT position_id, symbol, arm, entry_credit, quantity FROM curve_positions "
        "WHERE status = 'closed' ORDER BY symbol, arm"
    ):
        if p["entry_credit"] is None:
            continue
        marks = conn.execute(
            "SELECT close_cost FROM curve_marks WHERE position_id = ? AND close_cost IS NOT NULL "
            "AND usable = 1 ORDER BY marked_at",
            (p["position_id"],),
        ).fetchall()
        mult = 100 * (p["quantity"] or 1)
        pnl_series = [round((p["entry_credit"] - m["close_cost"]) * mult, 2) for m in marks]
        mae_mfe = _mae_mfe(pnl_series, basis=0.0)
        if mae_mfe["n"] == 0:
            continue
        positions.append(
            {
                "position_id": p["position_id"],
                "symbol": p["symbol"],
                "arm": p["arm"],
                "mae": mae_mfe["mae"],
                "mfe": mae_mfe["mfe"],
                "n": mae_mfe["n"],
            }
        )

    def _distribution(key: str) -> dict:
        values = [p[key] for p in positions]
        return {"median": round(statistics.median(values), 2) if values else None, "n": len(values)}

    return {
        "positions": positions,
        "mae_distribution": _distribution("mae"),
        "mfe_distribution": _distribution("mfe"),
    }


# How good the day's mark substrate is (marks, refusal share, per-refusal counts) -- the ledger
# store's reader, which calendars, pmcc and curve had each copied over their own prefix.
mark_coverage = db._store.mark_coverage


def _sessions(conn) -> list[str]:
    """Every session the module ran: the loop's own iterations, and the regime series written every
    session, traded or not."""
    days = {r[0] for r in conn.execute("SELECT DISTINCT session_date FROM curve_loop_iterations")}
    days |= {r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM curve_regime")}
    return sorted(d for d in days if d)


def daily_equity(conn) -> dict[str, dict]:
    """Each arm's marked equity, one point per session, in dollars from 0: every closed position's
    net on and after the session it closed, plus each open position's mark that session.

    The mark is the last usable close cost of the session -- (credit - close cost) x 100 x quantity,
    less the entry's fee and slippage, which are already spent. A session with no usable mark carries
    the previous one forward and is counted in `carried`, so a stretch of stale marks reads as one,
    never as a flat market. A position whose legs have settled (shares awaiting disposal) keeps its
    last pre-settlement mark until it closes: an approximation the count does not cover, and rare.

    This is the series the closed-trade drawdown cannot see: a spread that is down 80% of its max
    loss for three weeks and recovers to a small win shows as a win there, and as a drawdown here.
    """
    sessions = _sessions(conn)
    positions = [dict(r) for r in conn.execute("SELECT * FROM curve_positions ORDER BY entry_session")]
    marks: dict[str, dict[str, float]] = {}
    for r in conn.execute(
        "SELECT position_id, session_date, close_cost FROM curve_marks "
        "WHERE leg_role = 'short_call' AND usable = 1 AND close_cost IS NOT NULL ORDER BY marked_at"
    ):
        marks.setdefault(r["position_id"], {})[r["session_date"]] = float(r["close_cost"])
    out: dict[str, dict] = {}
    for arm in sorted({p["arm"] for p in positions}):
        mine = [p for p in positions if p["arm"] == arm]
        start = min(p["entry_session"] for p in mine)
        series, carried, last_mark = [], 0, {}
        for day in (d for d in sessions if d >= start):
            equity = 0.0
            for p in mine:
                if p["entry_session"] > day:
                    continue
                closed_by = (
                    p["status"] == "closed" and p["closed_session"] is not None and p["closed_session"] <= day
                )
                if closed_by:
                    equity += (p["gross_pnl"] or 0.0) - (p["fees"] or 0.0)
                    continue
                cost = marks.get(p["position_id"], {}).get(day)
                if cost is None:
                    cost = last_mark.get(p["position_id"])
                    carried += cost is not None
                if cost is None:
                    continue  # entered today with no usable mark yet: nothing to mark
                last_mark[p["position_id"]] = cost
                qty = p["quantity"] or 1
                spent = (p["entry_cost"] or 0.0) + (p["entry_slippage"] or 0.0)
                equity += ((p["entry_credit"] or 0.0) - cost) * 100 * qty - spent
            series.append((day, round(equity, 2)))
        out[arm] = {"series": series, "carried": carried, "reading": _nav.equity_reading(series)}
    return out
