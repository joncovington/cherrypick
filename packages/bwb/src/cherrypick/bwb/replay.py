"""The read-side threshold replay over `bwb_trigger_ticks`.

Per the plan: with the full cohort-level trigger-tick path recorded (near-wing delta, peak delta,
spot, gamma_flip, the below-flip latch, and the add-on bracket's own quotes at every tick),
alternative thresholds become a read-side replay over data this module itself recorded — a
different pullback, a different flip buffer, a raw delta trigger at a different level — exactly
paired against the real books, the calendars `exit_policies.py` pattern (see that module's
docstring: forward-recorded, then replayed, never vendor-imagined).

Split mirrors calendars: `replay_cohort_ticks` is the pure compute layer (a list of tick dicts in,
a fire outcome out) using the SAME pure functions `triggers.py` exports — this module never
reimplements trigger logic, only replays it under different `params`. `replay_thresholds` and
`validate_against_real` are the thin DB-touching layer that reads `bwb_trigger_ticks` through
`db.trigger_ticks_for_cohort` and formats the result for `cli.py`.

Two honesty rails, both load-bearing (the calendars precedent):

- **An unmeasured tick is excluded, never guessed.** `triggers.update_peak` /
  `triggers.update_below_flip` already no-op on a `None` measure, so a hole in a cohort's recorded
  path simply cannot advance a latch or fire a trigger at that tick — the same discipline as the
  live loop.
- **A hypothetical fire is priceable, not just timeable.** The add-on bracket's own bid/ask are
  carried on every trigger-tick row for exactly this reason; a fire tick whose bracket quotes are
  incomplete reports `priceable: False` rather than inventing a credit.

`validate_against_real` checks the replay's base-threshold (`TRIGGER_DEFAULTS`) reconstruction
against what the real books actually recorded for this cohort. It compares ARM outcome (whether the
book armed at all), not a timestamp-to-timestamp match: `bwb_positions.armed_at` is a stamped
wall-clock string while `bwb_trigger_ticks.ticked_at` is the loop's own epoch float for the same
instant, so the two are not directly diffable — replaying at the base config should reproduce
whether each arm-eligible book armed, which is the fact this validation exists to protect.
"""

from __future__ import annotations

from cherrypick.bwb import db as _db
from cherrypick.bwb import triggers

ARM_BOOKS = ("delta", "bounce", "flip")


def _addon_credit(tick: dict) -> float | None:
    """The add-on bracket's credit at this tick's recorded quotes, or None if any leg is missing —
    never guessed."""
    short_bid, short_ask = tick.get("addon_short_bid"), tick.get("addon_short_ask")
    long_bid, long_ask = tick.get("addon_long_bid"), tick.get("addon_long_ask")
    if None in (short_bid, short_ask, long_bid, long_ask):
        return None
    short_mid = (short_bid + short_ask) / 2
    long_mid = (long_bid + long_ask) / 2
    return round(short_mid - long_mid, 4)


def _fire_record(tick: dict, book: str) -> dict:
    credit = _addon_credit(tick)
    return {
        "book": book,
        "ticked_at": tick.get("ticked_at"),
        "session_date": tick.get("session_date"),
        "addon_credit": credit,
        "priceable": credit is not None,
    }


def replay_cohort_ticks(ticks: list[dict], params: dict | None = None) -> dict:
    """Pure replay over one cohort's recorded trigger-tick rows, oldest-first. `params` overrides
    any of `delta_trigger`/`bounce_pullback`/`flip_buffer`; unset keys keep `triggers.TRIGGER_DEFAULTS`.

    Reuses `triggers.update_peak` / `update_below_flip` / `delta_fires` / `bounce_fires` /
    `flip_fires` directly — the SAME pure functions the live loop evaluates, so a replay at the base
    thresholds is not a second implementation free to drift.

    Returns the first hypothetical fire tick per arm book (or None if the recorded history never
    would have fired under `params`), plus the running latch state at the end of the cohort's
    history (useful for a still-open cohort whose fire, if any, hasn't happened yet)."""
    p = triggers._params(params)
    peak: float | None = None
    below_flip = False
    fires: dict[str, dict | None] = {book: None for book in ARM_BOOKS}

    for tick in ticks:
        abs_delta = tick.get("near_abs_delta")
        spot = tick.get("spot")
        gamma_flip = tick.get("gamma_flip")

        peak = triggers.update_peak(peak, abs_delta)
        below_flip = triggers.update_below_flip(below_flip, spot, gamma_flip)

        if fires["delta"] is None and triggers.delta_fires(abs_delta, p):
            fires["delta"] = _fire_record(tick, "delta")
        if fires["bounce"] is None and triggers.bounce_fires(peak, abs_delta, p):
            fires["bounce"] = _fire_record(tick, "bounce")
        if fires["flip"] is None and triggers.flip_fires(below_flip, spot, gamma_flip, p):
            fires["flip"] = _fire_record(tick, "flip")

    return {
        "params": p,
        "ticks_considered": len(ticks),
        "final_peak_abs_delta": peak,
        "final_below_flip_seen": below_flip,
        "fires": fires,
    }


def replay_thresholds(conn, *, entry_session: str, structure_signature: str, thresholds: dict | None = None) -> dict:
    """Read `bwb_trigger_ticks` for one cohort and replay it under `thresholds` — the DB-touching
    entry point `cli.py replay` calls."""
    ticks = _db.trigger_ticks_for_cohort(conn, entry_session, structure_signature)
    result = replay_cohort_ticks(ticks, thresholds)
    return {"entry_session": entry_session, "structure_signature": structure_signature, **result}


def validate_against_real(conn, *, entry_session: str, structure_signature: str) -> dict:
    """Validate the BASE-threshold replay's arm outcome against what the real books actually
    recorded for this cohort (`bwb_positions.armed_at`) — exact-pairing validation, the calendars
    precedent: replaying at the base config should reproduce reality, so a mismatch here is a bug
    made visible, not noise to explain away."""
    ticks = _db.trigger_ticks_for_cohort(conn, entry_session, structure_signature)
    replayed = replay_cohort_ticks(ticks, None)

    checks = []
    for book in ARM_BOOKS:
        real = conn.execute(
            "SELECT position_id, armed_at, addon_fired_at FROM bwb_positions "
            "WHERE book = ? AND entry_session = ? AND structure_signature = ?",
            (book, entry_session, structure_signature),
        ).fetchone()
        if real is None:
            checks.append({"book": book, "ok": True, "reason": "no_real_position"})
            continue
        replayed_fire = replayed["fires"].get(book)
        real_armed = bool(real["armed_at"])
        replayed_armed = replayed_fire is not None
        ok = real_armed == replayed_armed
        checks.append(
            {
                "book": book,
                "ok": ok,
                "real_armed": real_armed,
                "real_armed_at": real["armed_at"],
                "replayed_fire_tick": replayed_fire.get("ticked_at") if replayed_fire else None,
                "replayed_session": replayed_fire.get("session_date") if replayed_fire else None,
            }
        )
    return {
        "entry_session": entry_session,
        "structure_signature": structure_signature,
        "compared": len(checks),
        "ok": all(c["ok"] for c in checks),
        "mismatches": [c for c in checks if not c["ok"]],
    }
