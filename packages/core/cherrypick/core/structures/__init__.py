"""cherrypick.core.structures — shared option-structure arithmetic.

One home for the small pure formulas two trading modules would otherwise each carry a copy of (the
core bar: two packages must never be able to disagree about what a number means). First resident is
the straddle-based expected move, previously computed independently by the earnings scanner and its
double-calendar order builder, and now also the strike-targeting input for the weekly calendars
module — three call sites, one 0.85, no drift.

Everything here is a pure function over floats the caller already fetched: no I/O, no broker, no
cache reads, so it is safe on any loop-decision path.
"""

from __future__ import annotations

# The standard straddle-to-expected-move correction. An ATM straddle's price overstates the market's
# expected absolute move because the straddle also prices the tails; ~0.85x is the practitioner
# convention (e.g. a $14.00 straddle -> $11.90 expected move). Callers may override per config, but
# the default lives HERE so a recalibration reaches every module at once.
STRADDLE_TO_EM_FACTOR = 0.85


def expected_move(atm_call_mid: float, atm_put_mid: float, *, factor: float = STRADDLE_TO_EM_FACTOR) -> float:
    """The expected absolute move ($) implied by an ATM straddle's mid price, over the straddle
    expiration's horizon. The horizon is the caller's choice of chain: an earnings module feeds the
    post-event expiration, a weekly calendar module feeds its short-leg (front) expiration and reads
    the answer as the expected move to that Friday."""
    return factor * (atm_call_mid + atm_put_mid)
