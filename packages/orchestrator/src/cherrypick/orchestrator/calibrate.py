"""Per-profile paper calibration readings (read-only).

Turns accumulated **paper** P&L into a per-profile calibration *reading* — sample, win rate,
distinct sessions, net-of-cost P&L — plus the qualification checks against a declared rule. Reads
paper DBs only (files, no broker, no network); it mutates nothing.

**The champion/challenger surface was retired 2026-08-20.** This module used to name one tag the
live champion and judge every other tag against it as a challenger, emitting an advisory
"promote / hold" verdict. Judging arms is `packages/advisor`'s job now, through its experiments —
one mechanism rather than two answering the same question from different evidence and different
thresholds. `core.profiles.recommend_champion` went with it; `compare_profiles`,
`qualify_readings` and `QUALIFICATION_RULE` stayed, because the advisor reads through exactly that
chain.

What remains is the reading itself, which nothing else in the suite emits as a CLI verb. The
closed-trade extraction is reused from `report` (its per-schema readers also emit a `session`
date), so the net-of-cost SQL lives in one place, and the grouping comes from the shared
`cherrypick.core.profiles` engine.
"""

from __future__ import annotations

from datetime import datetime, timezone

from cherrypick.core.metrics import calibration_reading
from cherrypick.core.profiles import (
    QUALIFICATION_RULE,
    compare_profiles,
    qualify_readings,
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
    """Per-module, per-profile calibration readings and their qualification checks. Read-only."""
    cfg = cfgmod.load_config() if cfg is None else cfg  # an explicit {} must stay {}, not fall back
    epoch = cfgmod.data_epoch(cfg)
    modules_out: dict[str, dict] = {}

    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        paper = mcfg.get("paper", {})
        schema = paper.get("trade_schema", "meic_ic")
        reader = report._READERS.get(schema)
        db_path = cfgmod.paper_db_path(mcfg, name)
        cal = mcfg.get("calibration", {}) or {}
        rule = dict(cal.get("rule") or {})
        # `margin` and `deliberate_only` belonged to the retired champion comparison — a margin a
        # challenger had to beat, and tags never auto-recommended. Popped rather than passed so a
        # config still carrying them is read without error and without effect.
        rule.pop("margin", None)

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

        # The epoch is ENFORCED here: a calibration reading must never blend sessions
        # produced by retired code (pre-fix leg ratios, fee undercounts, status-based wins) into
        # its sample/days/win-rate. Records without a session date are treated as pre-epoch —
        # if we can't date it, it can't support a recommendation.
        if epoch is not None:
            records = [r for r in records if r.get("session") and r["session"] >= epoch["date"]]

        readings = _group_readings(records)

        # Readings only. The champion/challenger comparison was retired 2026-08-20 — arms are
        # judged by `packages/advisor`'s experiments now, which is the one place that decides
        # whether a variant earned anything. What survives here is the per-tag READING (sample,
        # sessions, win rate, net of cost) and the qualification checks, both of which the advisor
        # reads through the same `cherrypick.core.profiles` chain.
        qualifications = qualify_readings(readings, rule=rule)
        profiles_out = {
            tag: {"reading": reading, "role": None, **qualifications.get(tag, {})}
            for tag, reading in readings.items()
        }
        modules_out[name] = {
            "ok": True,
            "schema": schema,
            "rule": {**QUALIFICATION_RULE, **rule},
            "profiles": profiles_out,
        }

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_epoch": epoch,
        "modules": modules_out,
    }
