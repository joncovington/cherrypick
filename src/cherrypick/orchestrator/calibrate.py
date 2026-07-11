"""Profiles calibration + promotion advisor surface (read-only).

Turns accumulated **paper** P&L into a per-profile calibration *reading* (sample, win rate, distinct
sessions, net-of-cost P&L) and an **advisory** promotion recommendation per risk-ladder rung — the
reporting hub's calibration read-side, alongside `report`. Reads paper DBs only (files, no broker, no
network); it never mutates config or switches live risk — graduating live is a human decision.

The closed-trade extraction is reused from `report` (its per-schema readers now also emit a `session`
date), so the net-of-cost SQL lives in one place. The promotion rule below is an inline mirror of
`cherrypick.core.profiles.recommend_promotion` / `PROMOTION_RULE`: the umbrella is not yet a
cherrypick-core consumer (no `_core` submodule), and the shared engine must not depend on a module's
vendored copy (same reasoning as `report`'s inline `compare_profiles`). Swap to
`cherrypick.core.profiles` if/when Cherrypick vendors cherrypick-core.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import config as cfgmod
from . import report

# Mirror of cherrypick.core.profiles.PROMOTION_RULE (MEICAgent docs/risk-profiles.md progression):
# graduate a rung only after a minimum window, a sustained win rate, and a sufficient sample.
PROMOTION_RULE = {"min_days": 14, "min_win_rate": 0.60, "min_sample": 20}


# --------------------------------------------------------------------------- readings
def _reading(records: list[dict]) -> dict:
    """Calibration metrics over one profile's closed paper trades (net_pnl already cost-adjusted)."""
    n = len(records)
    wins = sum(1 for r in records if r["net_pnl"] > 0)
    sessions = {r.get("session") for r in records if r.get("session")}
    return {
        "sample": n,
        "win_rate": round(wins / n, 4) if n else None,
        "days": len(sessions),
        "net_pnl": round(sum(r["net_pnl"] for r in records), 2),
    }


def _group_readings(records: list[dict]) -> dict:
    """Group closed trades by attribution tag and build a reading per group (mirrors compare_profiles)."""
    groups: dict[str, list[dict]] = {}
    for r in records:
        groups.setdefault(r["profile"], []).append(r)
    return {tag: _reading(g) for tag, g in groups.items()}


# --------------------------------------------------------------------------- promotion advisor
def _recommend(reading: dict, current: str, ladder: list[str], *, rule=None, deliberate_only=()) -> dict:
    """Advisory-only: should `current` graduate one rung up `ladder`? Inline mirror of core's
    recommend_promotion — pure, never mutates config or switches live risk (a human applies it)."""
    if current not in ladder:
        raise ValueError(f"current profile {current!r} not in ladder {ladder}")
    thresholds = {**PROMOTION_RULE, **(rule or {})}
    idx = ladder.index(current)
    nxt = ladder[idx + 1] if idx + 1 < len(ladder) else None

    def _check(value, threshold):
        return {"value": value, "threshold": threshold, "pass": value is not None and value >= threshold}

    checks = {
        "sample": _check(reading.get("sample"), thresholds["min_sample"]),
        "win_rate": _check(reading.get("win_rate"), thresholds["min_win_rate"]),
        "days": _check(reading.get("days"), thresholds["min_days"]),
    }

    def _verdict(eligible, recommendation, reason):
        return {
            "current": current,
            "next": nxt,
            "eligible": eligible,
            "checks": checks,
            "recommendation": recommendation,
            "reason": reason,
        }

    if nxt is None:
        return _verdict(False, "hold", f"{current} is the top of the ladder; nothing to graduate to.")
    if nxt in deliberate_only:
        return _verdict(
            False,
            "hold",
            f"graduating to {nxt} is a deliberate, human-chosen experiment -- never auto-recommended.",
        )
    if all(c["pass"] for c in checks.values()):
        return _verdict(
            True,
            f"graduate:{nxt}",
            f"{current} met every threshold over {reading.get('days')} sessions "
            f"(win rate {reading.get('win_rate')}, {reading.get('sample')} trades); "
            f"eligible to graduate to {nxt}.",
        )
    failed = [name for name, c in checks.items() if not c["pass"]]
    return _verdict(False, "hold", f"hold {current}: {', '.join(failed)} below threshold.")


# --------------------------------------------------------------------------- entrypoint
def run(cfg: dict | None = None) -> dict:
    """Per-module, per-profile calibration readings + advisory promotion recommendations. Read-only."""
    cfg = cfg or cfgmod.load_config()
    modules_out: dict[str, dict] = {}

    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        paper = mcfg.get("paper", {})
        schema = paper.get("trade_schema", "meic_ic")
        reader = report._READERS.get(schema)
        db_path = cfgmod.module_root(mcfg) / paper.get("paper_db", "data/paper_trades.db")
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

        readings = _group_readings(records)
        profiles_out: dict[str, dict] = {}
        for tag, reading in readings.items():
            rec = (
                _recommend(reading, tag, ladder, rule=rule, deliberate_only=deliberate_only)
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

    return {"ok": True, "generated_at": datetime.now(timezone.utc).isoformat(), "modules": modules_out}
