"""Profiles calibration + champion/challenger advisor surface (read-only).

Turns accumulated **paper** P&L into a per-profile calibration *reading* (sample, win rate, distinct
sessions, net-of-cost P&L) and an **advisory** verdict on whether a challenger should replace the
live champion — the reporting hub's calibration read-side, alongside `report`. Reads paper DBs only
(files, no broker, no network); it never mutates config or switches live risk — that is a human
decision.

The closed-trade extraction is reused from `report` (its per-schema readers also emit a `session`
date), so the net-of-cost SQL lives in one place. Grouping and the qualification rule come from the
shared `cherrypick.core.profiles` engine (`compare_profiles`, `recommend_champion`,
`qualify_readings`, `QUALIFICATION_RULE`) via the src/_core submodule — bootstrapped onto sys.path in
this package's __init__.

A module either declares `calibration.champion` (the tag currently live — every other tag observed
is a challenger judged against it, `recommend_champion`) or declares no champion at all (its tags are
parallel, unordered experiments with nothing to compare against — `qualify_readings`, readings only,
no recommendation). This replaced a fixed-ladder "graduate to the next rung" model 2026-08-01: that
model assumed every module's tags form one ordered sequence, which produced a real, reproducible,
meaningless recommendation the moment a module's tags were parallel experiments instead (flies'
control/gex/time_window/width-N arms) — a fully-qualifying reading recommended "graduate" into an
unrelated sibling arm with no basis for that direction.
"""

from __future__ import annotations

from datetime import datetime, timezone

from cherrypick.core.metrics import calibration_reading
from cherrypick.core.profiles import (
    QUALIFICATION_RULE,
    compare_profiles,
    qualify_readings,
    recommend_champion,
)

from . import config as cfgmod
from . import report

# --------------------------------------------------------------------------- readings
# The reading IS the shared bundle (cherrypick.core.metrics.calibration_reading): sample /
# win_rate / days / net_pnl plus return_on_capital, per-trade sharpe, session-ordered max
# drawdown, sample_progress, and the 2x-slippage restatement with coverage counts — one
# metric vocabulary for every tag on every module, and the shape the hardened qualification
# checks (min_return_on_capital, require_slippage_survival) consume.
_reading = calibration_reading


def _group_readings(records: list[dict]) -> dict:
    """Group closed trades by attribution tag and build a reading per group (shared compare_profiles)."""
    return compare_profiles(records, tag_key="profile", summarize=_reading)


# --------------------------------------------------------------------------- entrypoint
def run(cfg: dict | None = None) -> dict:
    """Per-module, per-profile calibration readings + advisory champion/challenger verdicts. Read-only."""
    cfg = cfg or cfgmod.load_config()
    epoch = cfgmod.data_epoch(cfg)
    modules_out: dict[str, dict] = {}

    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        paper = mcfg.get("paper", {})
        schema = paper.get("trade_schema", "meic_ic")
        reader = report._READERS.get(schema)
        db_path = cfgmod.paper_db_path(mcfg, name)
        cal = mcfg.get("calibration", {}) or {}
        champion = cal.get("champion")  # None -> readings-only mode (qualify_readings)
        deliberate_only = tuple(cal.get("deliberate_only", []))
        rule = dict(cal.get("rule") or {})
        margin = rule.pop("margin", 0.0)

        if reader is None:
            modules_out[name] = {"ok": False, "reason": f"unknown schema {schema!r}"}
            continue
        if not db_path.exists():
            modules_out[name] = {"ok": False, "reason": "paper DB not found", "db": str(db_path)}
            continue

        conn = report._connect_ro(db_path)
        try:
            # Epoch pushdown: readers that can bound the session in SQL skip pre-epoch rows
            # at the query; the Python filter below stays as the belt (earnings, undated rows).
            records = reader(conn, start=(epoch["date"] if epoch else None))
        except Exception as exc:  # empty/uninitialized DB, missing table — never crash calibration
            modules_out[name] = {"ok": False, "reason": f"read failed: {exc}"}
            continue
        finally:
            conn.close()

        # The epoch is ENFORCED here: a champion/challenger reading must never blend sessions
        # produced by retired code (pre-fix leg ratios, fee undercounts, status-based wins) into
        # its sample/days/win-rate. Records without a session date are treated as pre-epoch —
        # if we can't date it, it can't support a recommendation.
        if epoch is not None:
            records = [r for r in records if r.get("session") and r["session"] >= epoch["date"]]

        readings = _group_readings(records)

        if champion is not None:
            verdict = recommend_champion(
                readings, champion, rule=rule, deliberate_only=deliberate_only, margin=margin
            )
            profiles_out: dict[str, dict] = {}
            for tag, reading in readings.items():
                if tag == champion:
                    profiles_out[tag] = {
                        "reading": reading,
                        "role": "champion",
                        "metric": verdict["champion_metric"],
                    }
                else:
                    c = verdict["challengers"].get(tag, {})
                    profiles_out[tag] = {"reading": reading, "role": "challenger", **c}
            modules_out[name] = {
                "ok": True,
                "schema": schema,
                "champion": champion,
                "rule": {**QUALIFICATION_RULE, **rule},
                "recommendation": {
                    "eligible": verdict["eligible"],
                    "recommendation": verdict["recommendation"],
                    "reason": verdict["reason"],
                },
                "profiles": profiles_out,
            }
        else:
            qualifications = qualify_readings(readings, rule=rule)
            profiles_out = {
                tag: {"reading": reading, "role": None, **qualifications.get(tag, {})}
                for tag, reading in readings.items()
            }
            modules_out[name] = {
                "ok": True,
                "schema": schema,
                "champion": None,
                "rule": {**QUALIFICATION_RULE, **rule},
                "recommendation": None,
                "profiles": profiles_out,
            }

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_epoch": epoch,
        "modules": modules_out,
    }
