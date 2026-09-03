"""The one query layer every read surface goes through. Read-only.

MEIC grew three call sites that disagreed about what "net" means; flies fixed that with one layer
and a test asserting the headline equals what that layer returns. Same rule here: the CLI, the
review's health/expected readers, and any future console page all read THROUGH these functions.

`None` never means zero — a position not yet closed reports `net_pnl: None`, and an average over
an empty bucket is `None`, because "not recorded" and "was zero" are different facts.
"""

from __future__ import annotations

import statistics

from cherrypick.core.metrics import excursions as _mae_mfe

# The era the module counts as evidence, MEIC's `CURRENT_ERA` convention adopted verbatim. `era` on
# `pmcc_positions` is an ADDED column (2026-08-23) — every row from before it existed reads back
# NULL, which never equals a literal era string, so old rows are excluded from `headline()` by
# construction rather than by a backfilled guess.
#
# One era so far: `"redesign"` (2026-08-23 ->), stamped by `book.enter_position` on every new row.
# It closes the pre-redesign window — TNA/UPRO alongside TQQQ, the `keltner`/`roll` books, the
# ~99-delta-floor long and yield-targeted ITM short, the early-tv-exhaustion default exit — which
# ran symbol/book/rule combinations the redesigned engine no longer produces and never will again.
# Four closed cycles exist from that window (all TQQQ, one apiece across control/keltner/roll/
# advised:control); they stay in the ledger as history and are still visible in the console's
# History tab and any `era="ALL"` read, but pooling them into the new design's headline would
# average two incomparable strategies into one number. See the module CLAUDE.md's 2026-08-23
# measurement-break note and the `measurement_breaks` row this reset journals.
CURRENT_ERA = "redesign"


def headline(conn, era: str | None = CURRENT_ERA) -> dict:
    """Per-book, per-symbol results over CLOSED positions, plus what is still open. Net is
    `gross_pnl - fees`, the same subtraction the suite's ledger reader performs — one convention,
    stated once.

    `era=CURRENT_ERA` (the default) scopes to the module's current evidence window; `era="ALL"`
    disables the filter for an explicit cross-era read; any other value scopes to that era alone.
    """
    where = "status = 'closed'"
    params: list[str] = []
    if era and era != "ALL":
        where += " AND era = ?"
        params.append(era)
    books: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT book, symbol, COUNT(*) AS n, SUM(gross_pnl) AS gross, SUM(fees) AS fees, "
        "SUM(gross_pnl) - SUM(fees) AS net, SUM((gross_pnl - fees) > 0) AS wins, "
        f"SUM(roll_count) AS rolls FROM pmcc_positions WHERE {where} "
        "GROUP BY book, symbol ORDER BY book, symbol",
        params,
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


def excursions(conn, era: str | None = CURRENT_ERA) -> dict:
    """MAE/MFE per CLOSED position, plus their distributions (docs/metrics-plan.md Phase 2).

    `era=CURRENT_ERA` (the default) scopes to the module's current evidence window, same rule as
    `headline`; `era="ALL"` disables the filter.

    `core.metrics.excursions` owns the generic MAE/MFE computation; this is the module-specific
    half -- pairing `pmcc_marks`' per-tick `long_call`/`short_call` leg mids (by `marked_at`, one
    shared timestamp per tick per the schema's own comment: "legs reassemble by equality") into
    `long_call.mid - short_call.mid`, the position's value if closed right then. This is exactly
    `engine.worksheet_metrics`'s own `net_debit = long_mid - short_mid` -- the formula that
    produced `net_debit` at entry -- so `value - net_debit` is what closing then would have
    realized against the debit paid, and the series needs no separate basis
    (`core.metrics.excursions` is called with `basis=0.0`).

    Positions with no `net_debit` (pre-instrumentation row) or no tick where both legs were usable
    are skipped from the per-position list rather than reported with a fabricated 0.0."""
    where = "status = 'closed'"
    params: list[str] = []
    if era and era != "ALL":
        where += " AND era = ?"
        params.append(era)

    positions = []
    for p in conn.execute(
        f"SELECT position_id, symbol, book, net_debit, quantity FROM pmcc_positions "
        f"WHERE {where} ORDER BY symbol, book",
        params,
    ):
        if p["net_debit"] is None:
            continue
        legs: dict[float, dict] = {}
        for row in conn.execute(
            "SELECT leg_role, marked_at, mid FROM pmcc_marks WHERE position_id = ? "
            "AND leg_role IN ('long_call', 'short_call') AND usable = 1 AND mid IS NOT NULL "
            "ORDER BY marked_at",
            (p["position_id"],),
        ):
            legs.setdefault(row["marked_at"], {})[row["leg_role"]] = row["mid"]
        mult = 100 * (p["quantity"] or 1)
        pnl_series = [
            round((tick["long_call"] - tick["short_call"] - p["net_debit"]) * mult, 2)
            for tick in (legs[ts] for ts in sorted(legs))
            if "long_call" in tick and "short_call" in tick
        ]
        mae_mfe = _mae_mfe(pnl_series, basis=0.0)
        if mae_mfe["n"] == 0:
            continue
        positions.append(
            {
                "position_id": p["position_id"],
                "symbol": p["symbol"],
                "book": p["book"],
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
