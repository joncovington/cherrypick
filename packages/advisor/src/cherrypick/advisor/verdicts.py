"""What an experiment actually did — computed here, never asked of the model.

The chain is the suite's existing one, not a new one: ``cherrypick.core.ledgers`` READERS normalise
each module's closed rows, ``compare_profiles`` groups them by attribution tag,
``cherrypick.core.metrics.calibration_reading`` builds the reading, and ``qualify_readings`` applies
the same promotion gate everything else in this suite is measured against.

The model gets these numbers and may recommend keep / kill / promote **over** them. It never
produces them, and its recommendation is stored beside the computation, never instead of it.

One label matters more than the rest: **`underpowered`**. The qualification rule wants 14 days and
20 trades. An experiment that expires with fewer has not failed — it has not been measured — and
saying so is different from both a pass and a fail. Default experiment length is 15 sessions
precisely so an experiment that runs its course can clear that bar; anything shorter is labeled
rather than quietly judged.
"""

from __future__ import annotations

from typing import Any

from cherrypick.core import ledgers as _ledgers
from cherrypick.core.metrics import calibration_reading
from cherrypick.core.profiles import QUALIFICATION_RULE, compare_profiles, qualify_readings

from cherrypick.advisor import bounds as _bounds
from cherrypick.advisor import paths as _paths

# Module -> ledger schema. The same map review keeps, for the same reason: the schema decides which
# reader knows this module's net, cost, capital and session rules.
SCHEMAS = {"meic": "meic_ic", "flies": "fly_book", "earnings": "earnings", "calendars": "dc_week"}


def paper_db(module: str):
    return _paths.module_data_dir(module) / "paper_trades.db"


def closed_records(module: str, *, start: str | None = None, end: str | None = None) -> list[dict]:
    """Every closed row this module has, normalised. `[]` when the module has never run — a module
    with no ledger has no evidence, which is not the same as evidence of nothing."""
    reader = _ledgers.READERS.get(SCHEMAS.get(module, ""))
    path = paper_db(module)
    if reader is None or not path.exists():
        return []
    conn = _ledgers.connect_ro(path)
    try:
        return reader(conn, start=start, end=end)
    except Exception:
        # An empty or half-migrated ledger yields no evidence rather than taking the run down.
        return []
    finally:
        conn.close()


def readings(module: str, *, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """`{tag: reading}` for every attribution tag in this module's closed book."""
    records = closed_records(module, start=start, end=end)
    return compare_profiles(records, tag_key="profile", summarize=calibration_reading)


def _delta(advised: dict | None, base: dict | None) -> dict[str, Any]:
    """Advised minus base on the handful of fields a comparison actually turns on. `None` stays
    `None`: a field neither side recorded has no difference to report."""
    out: dict[str, Any] = {}
    for field in ("net_pnl", "win_rate", "return_on_capital", "sharpe"):
        a = (advised or {}).get(field)
        b = (base or {}).get(field)
        out[field] = round(a - b, 4) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
    return out


def reading_pair(
    module: str, base_profile: str, *, strategy: str | None = None, rule: dict | None = None
) -> dict[str, Any]:
    """The advised book beside its control, with the qualification checks for both.

    This is the whole comparison, and it is deliberately paired: the two books ran the same sessions
    against the same underlying, so the difference between them is worth far more than either
    book's absolute numbers. Reporting the advised book alone would invite exactly the conclusion
    the pairing exists to prevent.
    """
    tag_advised = _bounds.advised_tag(module, base_profile, strategy)
    tag_base = f"{base_profile}:{strategy}" if (module == "earnings" and strategy) else base_profile

    every = readings(module)
    advised, base = every.get(tag_advised), every.get(tag_base)
    qualified = qualify_readings(
        {t: r for t, r in ((tag_advised, advised), (tag_base, base)) if r}, rule=rule
    )
    thresholds = {**QUALIFICATION_RULE, **(rule or {})}
    return {
        "module": module,
        "advised_tag": tag_advised,
        "base_tag": tag_base,
        "advised": advised,
        "base": base,
        "delta": _delta(advised, base),
        "qualification": qualified,
        "rule": thresholds,
        "underpowered": _underpowered(advised, thresholds),
    }


def _underpowered(reading: dict | None, thresholds: dict) -> bool:
    """Not enough evidence to have measured anything, whatever the numbers say. Sample and days
    only — a book can miss the win-rate bar honestly, but it cannot miss the sample bar honestly."""
    if not reading:
        return True
    return (reading.get("sample") or 0) < thresholds["min_sample"] or (
        reading.get("days") or 0
    ) < thresholds["min_days"]


def for_experiment(experiment: dict[str, Any], *, rule: dict | None = None) -> dict[str, Any]:
    """The deterministic verdict body for one experiment row, ready to be stored on it.

    Earnings experiments produce one pair per strategy they touched: a twin only exists for a
    strategy something was proposed about, and pooling two strategies' twins would compare books
    that never faced the same trades.
    """
    import json

    module = experiment["module"]
    params = json.loads(experiment.get("params_json") or "{}")
    base = experiment["base_profile"]

    strategies = _bounds.strategies_in(module, params) or [None]
    pairs = [reading_pair(module, base, strategy=s, rule=rule) for s in strategies]

    return {
        "experiment_id": experiment["id"],
        "module": module,
        "params": params,
        "sessions_run": experiment.get("sessions_run"),
        "pairs": pairs,
        # An experiment is underpowered unless at least one of its pairs was actually measured.
        "underpowered": all(p["underpowered"] for p in pairs),
        "computed_by": "cherrypick.advisor.verdicts",
    }
