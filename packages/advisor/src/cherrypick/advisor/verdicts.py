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
from cherrypick.advisor import clock as _clock
from cherrypick.advisor import paths as _paths

# Module -> ledger schema. The same map review keeps, for the same reason: the schema decides which
# reader knows this module's net, cost, capital and session rules.
SCHEMAS = {
    "meic": "meic_ic",
    "flies": "fly_book",
    "earnings": "earnings",
    "calendars": "dc_week",
    "pmcc": "pmcc_99",
    # bwb and curve joined `bounds.MODULES` on 2026-08-26 but not this map, so bwb reached an
    # active experiment (exp-2026-08-27-bwb-1, three sessions in) with arm_readings.bwb an empty
    # object: `closed_records` found no reader and there was nothing to score it against. The
    # review package had the identical miss on the same date. `test_every_advisable_module_has_a_
    # scoreable_ledger_schema` now fails the moment a module can be advised and not scored.
    "bwb": "bwb_132",
    "curve": "curve_vx",
}


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
    module: str,
    base_profile: str,
    *,
    strategy: str | None = None,
    rule: dict | None = None,
    start: str | None = None,
    end: str | None = None,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    """The advised book beside its control, with the qualification checks for both.

    This is the whole comparison, and it is deliberately paired: the two books ran the same sessions
    against the same underlying, so the difference between them is worth far more than either
    book's absolute numbers. Reporting the advised book alone would invite exactly the conclusion
    the pairing exists to prevent.

    `start` bounds BOTH sides to sessions on/after it. Until 2026-08-20 this read the whole ledger,
    which made the docstring's own claim false on the base side: the advised tag only exists for the
    experiment's sessions, while the base pooled its entire history — for flies that was 23 sessions
    reaching back into the XSP era. `for_experiment` passes the experiment's own first session, so
    the pair really does compare the same window.

    `end` bounds both sides to sessions on/before it (2026-09-16). The window used to be open-ended,
    and a verdict is recomputed whenever a recommendation is attached -- so a closing verdict on an
    expired experiment, attached after its successor had started, would have pooled the successor's
    rows into the old experiment's advised book. `for_experiment` passes the concluding session.

    `experiment_id` keeps, on the ADVISED side only, rows stamped with this experiment or with no
    stamp at all (rows written before the stamp existed, 2026-09-16). One `advised:<base>` tag serves
    every experiment on that base in turn; the stamp is what tells them apart inside a shared window.
    """
    tag_advised = _bounds.advised_tag(module, base_profile, strategy)
    tag_base = f"{base_profile}:{strategy}" if (module == "earnings" and strategy) else base_profile

    records = closed_records(module, start=start, end=end)
    if experiment_id:
        records = [
            r
            for r in records
            if r.get("profile") != tag_advised or r.get("experiment_id") in (None, experiment_id)
        ]
    every = compare_profiles(records, tag_key="profile", summarize=calibration_reading)
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
    return (reading.get("sample") or 0) < thresholds["min_sample"] or (reading.get("days") or 0) < thresholds[
        "min_days"
    ]


def concluded_session(experiment: dict) -> str | None:
    """The last session an expired or killed experiment's advice could have governed, from its
    journal; None while it is still running (the window stays open at the near end). Read from
    the `experiment_events` row that concluded it rather than a column, so a store written before
    this existed answers the same way. The concluding session itself is INCLUDED: the successor's
    first artifact targets the next session, so rows entered on the kill day are still this one's."""
    if experiment.get("status") not in ("expired", "killed"):
        return None
    try:
        from cherrypick.advisor import store as _store

        conn = _store.connect()
        try:
            events = _store.events(conn, experiment["id"])
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — an unreadable journal degrades to the open-ended window
        return None
    for event in reversed(events):
        if event.get("event") in ("expired", "killed") and event.get("session"):
            return str(event["session"])
    return None


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

    # Window both sides from the first session this experiment could have issued advice for —
    # enact targets next_session(created_session), so that is the experiment's true start.
    created = experiment.get("created_session")
    try:
        start = _clock.next_session(created) if created else None
    except Exception:  # noqa: BLE001 — an unparseable date must degrade to unwindowed, not crash a verdict
        start = created
    end = concluded_session(experiment)
    strategies = _bounds.strategies_in(module, params) or [None]
    pairs = [
        reading_pair(
            module, base, strategy=s, rule=rule, start=start, end=end, experiment_id=experiment.get("id")
        )
        for s in strategies
    ]

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
