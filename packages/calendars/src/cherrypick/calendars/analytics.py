"""The one query layer every read surface goes through. Read-only.

MEIC grew three call sites that disagreed about what "net" means; flies fixed that with one layer
and a test asserting the headline equals what this layer returns. Same rule here: the CLI, the
review's health/expected readers, and any future console page all read THROUGH these functions.

`None` never means zero — a position not yet closed reports `net_pnl: None`, and an average over
an empty bucket is `None`, because "not recorded" and "was zero" are different facts.
"""

from __future__ import annotations

import statistics

from cherrypick.core.metrics import excursions as _mae_mfe

from cherrypick.calendars import db, exit_policies


def headline(conn) -> dict:
    """Per-arm, per-structure results over CLOSED positions, plus what is still open. Net is
    `gross_pnl - fees`, the same subtraction the suite's ledger reader performs — one convention,
    stated once."""
    arms: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT arm, structure, COUNT(*) AS n, SUM(gross_pnl) AS gross, SUM(fees) AS fees, "
        "SUM(gross_pnl) - SUM(fees) AS net, SUM((gross_pnl - fees) > 0) AS wins, "
        "COUNT(DISTINCT week_of) AS weeks FROM dc_positions WHERE status = 'closed' "
        "GROUP BY arm, structure ORDER BY arm, structure"
    ):
        arms.setdefault(row["arm"], {})[row["structure"]] = {
            "positions": row["n"],
            "weeks": row["weeks"],
            "gross_pnl": round(row["gross"], 2) if row["gross"] is not None else None,
            "fees": round(row["fees"], 2) if row["fees"] is not None else None,
            "net_pnl": round(row["net"], 2) if row["net"] is not None else None,
            "win_rate": round(row["wins"] / row["n"], 4) if row["n"] else None,
        }
    open_rows = conn.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT week_of) AS weeks FROM dc_positions WHERE status != 'closed'"
    ).fetchone()
    return {"arms": arms, "open_positions": open_rows["n"], "open_weeks": open_rows["weeks"]}


def week_detail(conn, week_of: str) -> dict:
    """Everything on file for one week: positions with their legs, and the management trail."""
    positions = []
    for p in conn.execute("SELECT * FROM dc_positions WHERE week_of = ? ORDER BY arm, side", (week_of,)):
        p = dict(p)
        p["legs"] = [
            dict(leg)
            for leg in conn.execute(
                "SELECT * FROM dc_legs WHERE position_id = ? ORDER BY leg_role", (p["position_id"],)
            )
        ]
        p["events"] = [
            dict(e)
            for e in conn.execute(
                "SELECT * FROM dc_management_events WHERE position_id = ? ORDER BY occurred_at",
                (p["position_id"],),
            )
        ]
        positions.append(p)
    return {"week_of": week_of, "positions": positions}


def exit_policy_table(conn, config: dict) -> dict:
    """The exit study's answer, with its own validation attached — one call so no surface can show
    the ranking without the reason to believe it."""
    return exit_policies.comparison_table(conn, config)


def em_vs_realized(conn) -> list[dict]:
    """Per settled week: the expected move measured at entry against the move actually realized to
    the front expiration (|settlement spot − entry spot|). The strategy's premise, measured — one
    row per week, floats not verdicts."""
    out = []
    for row in conn.execute(
        "SELECT week_of, structure, MIN(entry_spot) AS entry_spot, MIN(entry_em) AS em, "
        "MIN(settlement_spot) AS settle_spot FROM dc_positions WHERE arm = 'path' "
        "AND settlement_spot IS NOT NULL GROUP BY week_of ORDER BY week_of"
    ):
        realized = (
            round(abs(row["settle_spot"] - row["entry_spot"]), 4)
            if row["settle_spot"] is not None and row["entry_spot"] is not None
            else None
        )
        out.append(
            {
                "week_of": row["week_of"],
                "structure": row["structure"],
                "expected_move": row["em"],
                "realized_move": realized,
                "ratio": (round(realized / row["em"], 4) if realized is not None and row["em"] else None),
            }
        )
    return out


def excursions(conn) -> dict:
    """MAE/MFE per CLOSED position (one `dc_positions` row -- one side, one arm, one week; the
    same per-trade granularity `cherrypick.core.ledgers`'s `dc_week` reader uses), plus their
    distributions (docs/metrics-plan.md Phase 2).

    `core.metrics.excursions` owns the generic MAE/MFE computation; this is the module-specific
    half -- pairing `dc_marks`' per-tick `front`/`back` leg mids (by `marked_at`, one shared
    timestamp per tick, the same pairing `exit_policies.week_data` uses) into `back.mid -
    front.mid`, the position's value if closed right then. This is exactly `engine.plan_entry`'s
    own `debit = back_quote["mid"] - front_quote["mid"]` -- the formula that produced
    `entry_debit` in the first place -- so `value - entry_debit` is what closing then would have
    realized against the debit paid at entry, and the series needs no separate basis
    (`core.metrics.excursions` is called with `basis=0.0`).

    Positions with no `entry_debit` (pre-instrumentation row) or no tick where both legs were
    usable are skipped from the per-position list rather than reported with a fabricated 0.0."""
    positions = []
    for p in conn.execute(
        "SELECT position_id, symbol, arm, side, entry_debit, quantity FROM dc_positions "
        "WHERE status = 'closed' ORDER BY week_of, arm, side"
    ):
        if p["entry_debit"] is None:
            continue
        legs: dict[float, dict] = {}
        for row in conn.execute(
            "SELECT leg_role, marked_at, mid FROM dc_marks WHERE position_id = ? "
            "AND leg_role IN ('front', 'back') AND usable = 1 AND mid IS NOT NULL "
            "ORDER BY marked_at",
            (p["position_id"],),
        ):
            legs.setdefault(row["marked_at"], {})[row["leg_role"]] = row["mid"]
        mult = 100 * (p["quantity"] or 1)
        pnl_series = [
            round((tick["back"] - tick["front"] - p["entry_debit"]) * mult, 2)
            for tick in (legs[ts] for ts in sorted(legs))
            if "front" in tick and "back" in tick
        ]
        mae_mfe = _mae_mfe(pnl_series, basis=0.0)
        if mae_mfe["n"] == 0:
            continue
        positions.append(
            {
                "position_id": p["position_id"],
                "symbol": p["symbol"],
                "arm": p["arm"],
                "side": p["side"],
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
