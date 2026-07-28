"""Profiles calibration + promotion advisor surface (read-only).

Turns accumulated **paper** P&L into a per-profile calibration *reading* (sample, win rate, distinct
sessions, net-of-cost P&L) and an **advisory** promotion recommendation per risk-ladder rung — the
reporting hub's calibration read-side, alongside `report`. Reads paper DBs only (files, no broker, no
network); it never mutates config or switches live risk — graduating live is a human decision.

The closed-trade extraction is reused from `report` (its per-schema readers also emit a `session`
date), so the net-of-cost SQL lives in one place. Grouping and the promotion rule come from the shared
`cherrypick.core.profiles` engine (`compare_profiles`, `recommend_promotion`, `PROMOTION_RULE`) via the
src/_core submodule — bootstrapped onto sys.path in this package's __init__.
"""

from __future__ import annotations

from datetime import datetime, timezone

from cherrypick.core.profiles import PROMOTION_RULE, compare_profiles, recommend_promotion

from . import config as cfgmod
from . import report


# --------------------------------------------------------------------------- readings
def _reading(records: list[dict]) -> dict:
    """Calibration metrics over one profile's closed paper trades (net_pnl already cost-adjusted)."""
    n = len(records)
    wins = sum(1 for r in records if r["net_pnl"] > 0)
    sessions = {r.get("session") for r in records if r.get("session")}
    # Cost-sensitivity view: net restated at a doubled slippage fraction (linear, so it
    # costs the recorded slippage again). Coverage counts the rows carrying the datum —
    # a reading whose stressed net only covers part of the sample says so.
    known_slips = [r["slippage"] for r in records if r.get("slippage") is not None]
    return {
        "sample": n,
        "win_rate": round(wins / n, 4) if n else None,
        "days": len(sessions),
        "net_pnl": round(sum(r["net_pnl"] for r in records), 2),
        "net_pnl_2x_slippage": round(sum(r["net_pnl"] for r in records) - sum(known_slips), 2),
        "slippage_coverage": len(known_slips),
    }


def _group_readings(records: list[dict]) -> dict:
    """Group closed trades by attribution tag and build a reading per group (shared compare_profiles)."""
    return compare_profiles(records, tag_key="profile", summarize=_reading)


# --------------------------------------------------------------------------- entrypoint
def run(cfg: dict | None = None) -> dict:
    """Per-module, per-profile calibration readings + advisory promotion recommendations. Read-only."""
    cfg = cfg or cfgmod.load_config()
    epoch = cfgmod.data_epoch(cfg)
    modules_out: dict[str, dict] = {}

    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        paper = mcfg.get("paper", {})
        schema = paper.get("trade_schema", "meic_ic")
        reader = report._READERS.get(schema)
        db_path = cfgmod.paper_db_path(mcfg, name)
        cal = mcfg.get("calibration", {}) or {}
        ladder = list(cal.get("ladder", []))
        deliberate_only = tuple(cal.get("deliberate_only", []))
        rule = cal.get("rule")

        if reader is None:
            modules_out[name] = {"ok": False, "reason": f"unknown schema {schema!r}"}
            continue
        if not db_path.exists():
            modules_out[name] = {"ok": False, "reason": "paper DB not found", "db": str(db_path)}
            continue

        conn = report._connect_ro(db_path)
        try:
            records = reader(conn)
        except Exception as exc:  # empty/uninitialized DB, missing table — never crash calibration
            modules_out[name] = {"ok": False, "reason": f"read failed: {exc}"}
            continue
        finally:
            conn.close()

        # The epoch is ENFORCED here: a promotion reading must never blend sessions produced
        # by retired code (pre-fix leg ratios, fee undercounts, status-based wins) into its
        # sample/days/win-rate. Records without a session date are treated as pre-epoch —
        # if we can't date it, it can't support a promotion.
        if epoch is not None:
            records = [r for r in records if r.get("session") and r["session"] >= epoch["date"]]

        readings = _group_readings(records)
        profiles_out: dict[str, dict] = {}
        for tag, reading in readings.items():
            rec = (
                recommend_promotion(reading, tag, ladder, rule=rule, deliberate_only=deliberate_only)
                if tag in ladder
                else None
            )
            profiles_out[tag] = {"reading": reading, "recommendation": rec}

        modules_out[name] = {
            "ok": True,
            "schema": schema,
            "ladder": ladder,
            "rule": {**PROMOTION_RULE, **(rule or {})},
            "profiles": profiles_out,
        }

    return {"ok": True, "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_epoch": epoch, "modules": modules_out}
