"""The three add-on trigger conditions, pure over (tick telemetry, position latch state).

All delta conditions are on **|delta|** — puts quote negative deltas; every function here takes the
absolute value once at its boundary (`abs_delta`) and every threshold reads unsigned.

Triggers are defined on the 60s SAMPLED series, not the continuous path: a 50-delta touch between
ticks does not exist by definition. Latch state (`peak_abs_delta`, `below_flip_seen`) is meant to be
persisted on the position row every tick — never held only in loop memory, so a supervisor restart
mid-session cannot amnesia a morning touch. `bwb_trigger_ticks` (the module's second product) can
re-derive the latches independently from the recorded tick history; `derive_latches_from_ticks`
below is that derivation, and it must agree with whatever the position row carries.
"""

from __future__ import annotations

TRIGGER_DEFAULTS = {
    "delta_trigger": 0.50,
    "bounce_pullback": 0.05,
    "flip_buffer": 1.001,
}


def _params(params: dict | None) -> dict:
    return {**TRIGGER_DEFAULTS, **{k: v for k, v in (params or {}).items() if k in TRIGGER_DEFAULTS}}


def update_peak(peak_abs_delta: float | None, abs_delta: float | None) -> float | None:
    """The running peak |delta| since entry, advanced only on a MEASURED tick (never carried
    forward on a guess, never regresses)."""
    if abs_delta is None:
        return peak_abs_delta
    if peak_abs_delta is None:
        return abs_delta
    return max(peak_abs_delta, abs_delta)


def delta_fires(abs_delta: float | None, params: dict | None = None) -> bool:
    """`delta` book: the near wing's |delta| has reached `delta_trigger` on THIS tick — raw
    proximity, the naive baseline."""
    if abs_delta is None:
        return False
    p = _params(params)
    return abs_delta >= p["delta_trigger"]


def bounce_fires(peak_abs_delta: float | None, abs_delta: float | None, params: dict | None = None) -> bool:
    """`bounce` book: peak |delta| since entry cleared `delta_trigger` AND current |delta| has
    pulled back to `delta_trigger - bounce_pullback` or below — a confirmed reversal, not a touch.

    There is deliberately no separate `bounce_peak` key: the qualifying bar for the peak IS
    `delta_trigger` itself, so at `bounce_pullback == 0` this degenerates to exactly `delta_fires`
    (the config-lint guard this property exists to make impossible to break by drift)."""
    if peak_abs_delta is None or abs_delta is None:
        return False
    p = _params(params)
    if peak_abs_delta < p["delta_trigger"]:
        return False
    return abs_delta <= p["delta_trigger"] - p["bounce_pullback"]


def update_below_flip(below_flip_seen: bool, spot: float | None, gamma_flip: float | None) -> bool:
    """Latches True the first tick spot trades below `gamma_flip`; never un-latches. None inputs
    leave the latch unchanged (an unmeasured tick proves nothing)."""
    if below_flip_seen:
        return True
    if spot is None or gamma_flip is None:
        return False
    return spot < gamma_flip


def flip_fires(
    below_flip_seen: bool, spot: float | None, gamma_flip: float | None, params: dict | None = None
) -> bool:
    """`flip` book: spot has traded below `gamma_flip` at some point since entry (the latch) AND
    has reclaimed to `gamma_flip * flip_buffer` or above on THIS tick — the buffer (default 0.1%,
    the curve `contango_max` precedent) so a knife-edge tick-through is not mistaken for a
    reclaim."""
    if not below_flip_seen or spot is None or gamma_flip is None:
        return False
    p = _params(params)
    return spot >= gamma_flip * p["flip_buffer"]


def evaluate(book: str, state: dict, tick: dict, params: dict | None = None) -> dict:
    """One book's trigger read for one tick. `state` carries `peak_abs_delta`/`below_flip_seen`
    (the position's persisted latches); `tick` carries `abs_delta`/`spot`/`gamma_flip` (this tick's
    measures, any of which may be None on an unmeasured tick).

    Returns the UPDATED latch values plus whether the book's own condition fires on this tick — the
    caller persists the latches regardless of book (every book's rows carry the same telemetry, the
    module's counterfactual-on-control property) and only ACTS on `fired` for its own book."""
    peak = update_peak(state.get("peak_abs_delta"), tick.get("abs_delta"))
    below_flip = update_below_flip(bool(state.get("below_flip_seen")), tick.get("spot"), tick.get("gamma_flip"))
    fired = {
        "control": False,
        "delta": delta_fires(tick.get("abs_delta"), params),
        "bounce": bounce_fires(peak, tick.get("abs_delta"), params),
        "flip": flip_fires(below_flip, tick.get("spot"), tick.get("gamma_flip"), params),
    }.get(book, False)
    return {
        "peak_abs_delta": peak,
        "below_flip_seen": below_flip,
        "fired": fired,
    }


def derive_latches_from_ticks(ticks: list[dict]) -> dict:
    """Re-derive `peak_abs_delta`/`below_flip_seen` from the module's own recorded tick history
    (`bwb_trigger_ticks`, ordered oldest-first) — the integrity cross-check: a position row whose
    latch disagrees with this derivation is a bug made visible."""
    peak = None
    below_flip = False
    for tick in ticks:
        peak = update_peak(peak, tick.get("abs_delta"))
        below_flip = update_below_flip(below_flip, tick.get("spot"), tick.get("gamma_flip"))
    return {"peak_abs_delta": peak, "below_flip_seen": below_flip}
