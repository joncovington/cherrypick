"""What an experiment actually did — computed here, never asked of the model.

The chain is the suite's existing one, not a new one: ``cherrypick.core.ledgers`` READERS normalise
each module's closed rows, ``group_by_tag`` groups them by attribution tag,
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

from cherrypick.core import advice as _advice
from cherrypick.core import compare as _compare
from cherrypick.core import ledgers as _ledgers
from cherrypick.core.config import SESSIONS_KEYS, first_present
from cherrypick.core.metrics import calibration_reading
from cherrypick.core.profiles import group_by_tag, qualification_rule, qualify_readings

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
    "pmcc": "pmcc",
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
    return group_by_tag(records, tag_key="arm", summarize=calibration_reading)


def _delta(advised: dict | None, base: dict | None) -> dict[str, Any]:
    """Advised minus base on the handful of fields a comparison actually turns on. `None` stays
    `None`: a field neither side recorded has no difference to report.

    The Sharpe compared is the per-SESSION one. The per-trade `sharpe` rewards an arm for taking
    more entries on the same days — they are not independent draws — so a delta in it says more
    about entry count than about the risk the arm ran."""
    out: dict[str, Any] = {}
    for field in ("net_pnl", "win_rate", "return_on_capital", "session_sharpe"):
        a = (advised or {}).get(field)
        b = (base or {}).get(field)
        out[field] = round(a - b, 4) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
    return out


# What makes two closed rows the same trade for the exposure check: every money field the ledger
# reader normalises, to the cent, plus what was traded. An advised twin that never diverged from its
# base writes rows identical on all of these, session by session.
_TWIN_FIELDS = ("symbol", "strategy", "gross_pnl", "cost", "net_pnl", "capital")


def _twin_key(record: dict) -> tuple:
    return tuple(round(v, 2) if isinstance(v, float) else v for v in (record.get(f) for f in _TWIN_FIELDS))


def exposure(records: list[dict], tag_advised: str, tag_base: str) -> dict[str, Any]:
    """Did the advised book ever trade differently from its base?

    An advised twin is planned from its base's own entry, so until the treated parameter acts the
    two books write the same rows. `diverged` is False when the advised book has closed rows and
    every one of them has an identical twin (`_TWIN_FIELDS`, to the cent) in the base book on the
    same session; True as soon as one does not; None when the advised book has nothing closed, which
    is no evidence either way. A verdict built on a book that never diverged has measured nothing:
    its effective sample is zero whatever its trade count says (2026-10-12: bwb's
    `delta-trigger-early` put a trigger param on `control`, which has no trigger, and its eleven
    rows matched control's exactly).

    Matching is per session and one-to-one, and the base may hold extra rows (positions it entered
    before the experiment started that close inside the window), so the advised side must be a
    subset of the base side, not equal to it.
    """
    from collections import Counter

    advised: dict[str, Counter] = {}
    base: dict[str, Counter] = {}
    for r in records:
        side = advised if r.get("arm") == tag_advised else base if r.get("arm") == tag_base else None
        if side is not None:
            side.setdefault(str(r.get("session") or ""), Counter())[_twin_key(r)] += 1
    rows = sum(sum(c.values()) for c in advised.values())
    unmatched = 0
    diverged_sessions = []
    for session, counts in sorted(advised.items()):
        missing = counts - base.get(session, Counter())
        if missing:
            unmatched += sum(missing.values())
            diverged_sessions.append(session)
    return {
        "diverged": None if rows == 0 else unmatched > 0,
        "advised_rows": rows,
        "unmatched_rows": unmatched,
        "sessions": len(advised),
        "diverged_sessions": diverged_sessions,
    }


def inert_params(module: str, params: dict[str, Any], base_profile: str) -> list[str]:
    """The params the module declares cannot act on `base_profile` (a bounds rule's `arms`, read
    through `core.advice.param_acts_on`). Earnings' dotted names are checked whole -- its base is a
    prefix, and no earnings rule declares `arms`."""
    posture = _bounds.resolve(module)
    rules = posture.get("bounds") or {}
    return [p for p in params if not _advice.param_acts_on(rules.get(p), base_profile)]


def reading_pair(
    module: str,
    base_profile: str,
    *,
    strategy: str | None = None,
    rule: dict | None = None,
    start: str | None = None,
    end: str | None = None,
    experiment_id: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """The advised book beside its control, with the qualification checks for both.

    `tag` names the advised book directly (2026-09-17: an experiment's own `advised:<name>`);
    without it the legacy `advised:<base>` book is read.

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
    tag_advised = _bounds.advised_tag(module, base_profile, strategy, tag=tag)
    tag_base = f"{base_profile}:{strategy}" if (module == "earnings" and strategy) else base_profile

    records = closed_records(module, start=start, end=end)
    if experiment_id:
        records = [
            r
            for r in records
            if r.get("arm") != tag_advised or r.get("experiment_id") in (None, experiment_id)
        ]
    every = group_by_tag(records, tag_key="arm", summarize=calibration_reading)
    advised, base = every.get(tag_advised), every.get(tag_base)
    qualified = qualify_readings(
        {t: r for t, r in ((tag_advised, advised), (tag_base, base)) if r}, rule=rule
    )
    thresholds = qualification_rule(rule)
    return {
        "module": module,
        "advised_tag": tag_advised,
        "base_tag": tag_base,
        "advised": advised,
        "base": base,
        "delta": _delta(advised, base),
        "paired": _paired(module, records, tag_advised, tag_base),
        "qualification": qualified,
        "rule": thresholds,
        "underpowered": _underpowered(advised, thresholds),
        "exposure": exposure(records, tag_advised, tag_base),
    }


def _paired(module: str, records: list[dict], tag_advised: str, tag_base: str) -> dict[str, Any] | None:
    """The advised book against its base, session by session (`core.compare.pair_with_control`,
    the rule the console's compare page uses): mean difference per session with its seeded 95%
    session-bootstrap interval, the exact sign test, and how many more sessions the interval would
    need to exclude zero at the observed mean and spread (`sessions_to_decision`).

    Beside the qualification, never in it: `qualify_readings` and its thresholds
    (`core.profiles.QUALIFICATION_RULE`) are untouched, and `underpowered` still means what it did.
    Kept out of `delta` on purpose, because the fact pack carries `delta` into the model's input
    and a new field there is a measurement break for the advisor; this rides on the stored verdict
    and the console. None when either book has no dated rows in the window."""
    schema = SCHEMAS.get(module)
    if schema is None:
        return None
    vs = _compare.pair_with_control(
        [r for r in records if r.get("arm") == tag_advised],
        [r for r in records if r.get("arm") == tag_base],
        schema=schema,
    )
    if vs is None:
        return None
    return {
        "sessions": vs["sessions"],
        "paired_on": vs["paired_on"],
        "mean_diff": vs["mean_diff"],
        "total_diff": vs["total_diff"],
        "ci": vs["ci"],
        "ci_level": vs["ci_level"],
        "ci_excludes_zero": vs["ci_excludes_zero"],
        "t_lower": vs["t_lower"],
        "advised_better": vs["arm_better"],
        "base_better": vs["control_better"],
        "ties": vs["ties"],
        "sign_test_p": vs["sign_test_p"],
        "sessions_to_decision": vs["sessions_to_exclude_zero"],
        "advised_only_sessions": vs["arm_only_sessions"],
        "base_only_sessions": vs["control_only_sessions"],
    }


def _underpowered(reading: dict | None, thresholds: dict) -> bool:
    """Not enough evidence to have measured anything, whatever the numbers say. Sample and sessions
    only — a book can miss the win-rate bar honestly, but it cannot miss the sample bar honestly."""
    if not reading:
        return True
    sessions = first_present(reading, *SESSIONS_KEYS) or 0
    return (reading.get("sample") or 0) < thresholds["min_sample"] or sessions < thresholds["min_sessions"]


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
    # The experiment's own book (2026-09-17). An experiment admitted before books were per
    # experiment carries no tag/name and reads the legacy `advised:<base>` book, where the stamp
    # still tells its rows from its neighbours'.
    own_tag = experiment.get("tag") or (
        _advice.advised_tag(experiment["name"]) if experiment.get("name") else None
    )
    pairs = [
        reading_pair(
            module,
            base,
            strategy=s,
            rule=rule,
            start=start,
            end=end,
            experiment_id=experiment.get("id"),
            tag=own_tag,
        )
        for s in strategies
    ]

    underpowered = all(p["underpowered"] for p in pairs)
    no_exposure = _no_exposure(module, params, base, pairs)
    return {
        "experiment_id": experiment["id"],
        "module": module,
        "params": params,
        "sessions_run": experiment.get("sessions_run"),
        "pairs": pairs,
        # An experiment is underpowered unless at least one of its pairs was actually measured.
        "underpowered": underpowered,
        # Ahead of pass/fail: an advised book that never traded differently from its base has
        # measured nothing, and no qualification read over it means anything.
        "no_exposure": no_exposure,
        "conclusion": ("no_exposure" if no_exposure else "underpowered" if underpowered else "measured"),
        "computed_by": "cherrypick.advisor.verdicts",
    }


def _no_exposure(module: str, params: dict[str, Any], base: str, pairs: list[dict]) -> dict | None:
    """Why this experiment measured nothing, or None when it may have measured something.

    Two routes. `declared`: every treated param is one the module declares cannot act on the base
    (bounds `arms`) -- known without a single row. `never_diverged`: every pair's advised book has
    closed rows and each one is identical to a base row on its session. A pair with no closed rows
    is not evidence of no exposure; it leaves the verdict to `underpowered`."""
    inert = inert_params(module, params, base) if params else []
    if params and len(inert) == len(params):
        return {
            "reason": "declared",
            "detail": f"{', '.join(sorted(inert))} cannot act on base arm {base!r} (advice.bounds arms)",
            "params": sorted(inert),
        }
    exposures = [p.get("exposure") or {} for p in pairs]
    if exposures and all(e.get("diverged") is False for e in exposures):
        rows = sum(e.get("advised_rows") or 0 for e in exposures)
        return {
            "reason": "never_diverged",
            "detail": f"all {rows} advised row(s) identical to {base!r} on their sessions",
            "params": sorted(params),
        }
    return None
