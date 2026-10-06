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
from cherrypick.core.metrics import nav as _nav

from cherrypick.pmcc import db
from cherrypick.pmcc import tracker as _tracker

# The held-long read (2026-10-04): one position week by week, the picker, the arms' weekly A/B, and
# the open mark-to-market. Exposed here so every read surface still goes through this one layer.
tracker = _tracker.tracker
tracker_index = _tracker.tracker_index
weekly_by_arm = _tracker.weekly_by_arm
open_mtm = _tracker.open_mtm
value_at = _tracker.value_at

# The era the module counts as evidence, MEIC's `CURRENT_ERA` convention adopted verbatim. `era` on
# `pmcc_positions` is an ADDED column (2026-08-23) — every row from before it existed reads back
# NULL, which never equals a literal era string, so old rows are excluded from `headline()` by
# construction rather than by a backfilled guess.
#
# `"shield"` (2026-10-05 ->, `paper_loop.SHIELD_FROM`): the held-long arms `shield` and
# `shield_hold` join control, and the symbols become XSP, QQQ, GLD, IWM and SLV (TQQQ runs off). It
# closes `"redesign"`, whose XSP control rules carry on unchanged but whose roster, symbols and
# position cap do not -- docs/shield-study.md and the CLAUDE.md boundary note.
#
# `"redesign"` (2026-08-23 -> 2026-10-04), stamped by `book.enter_position` on every new row,
# closed the pre-redesign window — TNA/UPRO alongside TQQQ, the `keltner`/`roll` arms, the
# ~99-delta-floor long and yield-targeted ITM short, the early-tv-exhaustion default exit — which
# ran symbol/arm/rule combinations the redesigned engine no longer produces and never will again.
# Four closed cycles exist from that window (all TQQQ, one apiece across control/keltner/roll/
# advised:control); they stay in the ledger as history and are still visible in the console's
# History tab and any `era="ALL"` read, but pooling them into the new design's headline would
# average two incomparable strategies into one number. See the module CLAUDE.md's 2026-08-23
# measurement-break note and the `measurement_breaks` row this reset journals.
CURRENT_ERA = "shield"


def headline(conn, era: str | None = CURRENT_ERA) -> dict:
    """Per-arm, per-symbol results over CLOSED positions, plus what is still open. Net is
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
    arms: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT arm, symbol, COUNT(*) AS n, SUM(gross_pnl) AS gross, SUM(fees) AS fees, "
        "SUM(gross_pnl) - SUM(fees) AS net, SUM((gross_pnl - fees) > 0) AS wins, "
        f"SUM(roll_count) AS rolls FROM pmcc_positions WHERE {where} "
        "GROUP BY arm, symbol ORDER BY arm, symbol",
        params,
    ):
        arms.setdefault(row["arm"], {})[row["symbol"]] = {
            "positions": row["n"],
            "gross_pnl": round(row["gross"], 2) if row["gross"] is not None else None,
            "fees": round(row["fees"], 2) if row["fees"] is not None else None,
            "net_pnl": round(row["net"], 2) if row["net"] is not None else None,
            "win_rate": round(row["wins"] / row["n"], 4) if row["n"] else None,
            "rolls": row["rolls"],
        }
    open_rows = conn.execute("SELECT COUNT(*) AS n FROM pmcc_positions WHERE status != 'closed'").fetchone()
    # Open positions marked to market (`tracker.value_at(now)`), beside the closed results rather
    # than in them: a held-long position closes ~10 months after it opens, and until then this is
    # the only number its arm has. Not era-scoped -- an open position is in the current era.
    return {"arms": arms, "open_positions": open_rows["n"], "open_mtm": open_mtm(conn)}


def worksheet(conn) -> list[dict]:
    """The live per-position worksheet — the module's human read, one row per open position with
    its entry metrics and the latest usable short-leg mark's time value."""
    out = []
    for p in conn.execute("SELECT * FROM pmcc_positions WHERE status != 'closed' ORDER BY symbol, arm"):
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
                "arm": p["arm"],
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
        f"SELECT position_id, symbol, arm, net_debit, quantity, entry_long_dte FROM pmcc_positions "
        f"WHERE {where} ORDER BY symbol, arm",
        params,
    ):
        if p["net_debit"] is None:
            continue
        if (p["entry_long_dte"] or 0) > _HELD_LONG_DTE:
            sampled = _held_long_excursion(conn, p["position_id"])
            if sampled is not None:
                positions.append(
                    {"position_id": p["position_id"], "symbol": p["symbol"], "arm": p["arm"], **sampled}
                )
            continue
        legs: dict[float, dict] = {}
        # The short's role is `short_call_<n>` (engine.plan_entry, db.next_short_role). This
        # filtered on a bare `short_call` -- a role nothing writes -- until 2026-10-04, so it
        # returned no position at all on the real ledger while its test seeded the same typo.
        for row in conn.execute(
            "SELECT leg_role, marked_at, mid FROM pmcc_marks WHERE position_id = ? "
            "AND leg_role IS NOT NULL AND usable = 1 AND mid IS NOT NULL "
            "ORDER BY marked_at",
            (p["position_id"],),
        ):
            role = "long_call" if row["leg_role"] == "long_call" else "short_call"
            legs.setdefault(row["marked_at"], {})[role] = row["mid"]
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


# A long bought further out than this is a held-long position's (~1 year), never control's ~21 DTE:
# the ledger's own shape decides which excursion path a position takes, so no config is needed here.
_HELD_LONG_DTE = 120


def _held_long_excursion(conn, position_id: str) -> dict | None:
    """MAE/MFE of a held-long position's GROSS P&L, sampled at each session's close by
    `tracker.value_at` -- the long, every short sold and rolled, and any delivered shares. The
    long-minus-short pairing above cannot see a rolled short's realised P&L, and ten months of
    minute ticks is a series nobody reads at that resolution."""
    from datetime import date, timedelta

    from cherrypick.core import calendar as _cal

    position = dict(
        conn.execute("SELECT * FROM pmcc_positions WHERE position_id = ?", (position_id,)).fetchone()
    )
    legs = db.legs_for(conn, position_id)
    assignments = db.assignments_for(conn, position_id)
    end = date.fromisoformat(position.get("closed_session") or position["entry_session"])
    day = date.fromisoformat(position["entry_session"])
    series = []
    while day <= end:
        if _cal.is_trading_day(day):
            v = value_at(conn, position, legs, assignments, _tracker._close_epoch(day))
            if v is not None:
                series.append(v["gross"])
        day += timedelta(days=1)
    result = _mae_mfe(series, basis=0.0)
    if result["n"] == 0:
        return None
    return {"mae": result["mae"], "mfe": result["mfe"], "n": result["n"], "sampled": "session_close"}


# How good the day's mark substrate is (marks, refusal share, per-refusal counts) -- the ledger
# store's reader, which calendars, pmcc and curve had each copied over their own prefix.
mark_coverage = db._store.mark_coverage


def _sessions(conn) -> list[str]:
    """Every session the loop ran -- its own iterations, the days it marked anything."""
    days = {r[0] for r in conn.execute("SELECT DISTINCT session_date FROM pmcc_loop_iterations")}
    days |= {r[0] for r in conn.execute("SELECT DISTINCT session_date FROM pmcc_marks")}
    return sorted(d for d in days if d)


def daily_equity(conn, era: str | None = CURRENT_ERA) -> dict[str, dict]:
    """Each arm's marked equity, one point per session, in dollars from 0, net of costs to date.

    Every position is valued at each session's close by `tracker.value_at` -- the long at its mark,
    every short sold (realised once rolled, marked while open), any delivered shares -- the same
    arithmetic the tracker and the held-long excursions use, so this adds none of its own. A closed
    position contributes its realised net from its close session on.

    This is the view a held long needs: a year-long call can sit far below its cost for months while
    every weekly short closes for a small win, and the closed-trade figures see only the wins until
    the long itself is sold. A session where a position cannot be priced carries its previous value
    and is counted in `carried`, so a stretch of stale marks reads as one, never as a flat market.

    `era` scopes positions the way `headline` does; "ALL" pools every era.
    """
    from datetime import date

    where, params = "1 = 1", []
    if era and era != "ALL":
        where, params = "era = ?", [era]
    positions = [dict(r) for r in conn.execute(f"SELECT * FROM pmcc_positions WHERE {where}", params)]
    sessions = _sessions(conn)
    legs = {p["position_id"]: db.legs_for(conn, p["position_id"]) for p in positions}
    assignments = {p["position_id"]: db.assignments_for(conn, p["position_id"]) for p in positions}
    out: dict[str, dict] = {}
    for arm in sorted({p["arm"] for p in positions}):
        mine = [p for p in positions if p["arm"] == arm]
        start = min(p["entry_session"] for p in mine)
        series, carried, last = [], 0, {}
        for day in (d for d in sessions if d >= start):
            close_t = _tracker._close_epoch(date.fromisoformat(day))
            equity = 0.0
            for p in mine:
                pid = p["position_id"]
                if p["entry_session"] > day:
                    continue
                closed = p["status"] == "closed" and p.get("closed_session") and p["closed_session"] <= day
                if closed:
                    equity += (p["gross_pnl"] or 0.0) - (p["fees"] or 0.0)
                    continue
                v = value_at(conn, p, legs[pid], assignments[pid], close_t)
                if v is None:
                    if pid in last:
                        carried += 1
                        equity += last[pid]
                    continue
                last[pid] = v["net"]
                equity += v["net"]
            series.append((day, round(equity, 2)))
        out[arm] = {"series": series, "carried": carried, "reading": _nav.equity_reading(series)}
    return out
