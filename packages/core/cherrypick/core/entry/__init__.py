"""cherrypick.core.entry — the shared entry-permission primitives: cadence and the leg-sign rule.

Two rules that MEIC and flies must apply *identically*, which is the whole reason they live here
rather than once in each module. Both modules run their arms as independent portfolios with
unbounded capital, so cadence and the sign rule are the only things that bind an entry — and an arm
comparison is only valid if every arm was bound the same way. Two copies of these rules would be two
chances for the arms to stop being comparable without anyone noticing.

Pure functions over values the caller supplies. No clock, no database, no config lookup: the caller
passes `now`, the last fill time, and the open legs. That keeps them testable at the boundary the
rules actually live at, and keeps the core's "never reach into a consumer" invariant intact.

**Cadence** (`entry_allowed`) is keyed on the last *fill*, not the last attempt. An order that was
placed and never filled, or was cancelled, did not use the arm's slot — charging it for one would
make a quiet market look like a throttled arm, which is precisely the distinction the entry_attempts
ledger exists to preserve.

**The sign rule** (`sign_conflict`) refuses a proposed leg that opposes an open leg at the same
strike. Two legs that net to zero mean the ledger's recorded risk is not the risk actually on, and
every number downstream of it — floor, MAE, payoff curve, settlement — then describes a position
that does not exist. Same-sign stacking is explicitly fine: it is how a butterfly shares a wing with
its neighbour (`+1 -2 +2 -2 +1` is two flies, not a broken one) and how MEIC nests a condor inside
an existing one.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

# A leg is (expiry, right, strike, sign): `right` is "P" or "C", sign is +1 long, -1 short.
#
# The option TYPE is part of the key, not decoration. A short put and a long call at the same strike
# are different instruments and do not net against each other -- only same-(expiry, right, strike)
# legs do. Getting this wrong would refuse perfectly good structures: an iron fly is short a put AND
# short a call at its centre, and a MEIC condor's put wing routinely sits at a strike some other
# structure holds a call at.
#
# `expiry` is whatever the caller uses to identify an expiration consistently (an ISO date string
# throughout this suite). Both modules are 0DTE, so in practice there is one expiry per session --
# keying on it anyway costs nothing and is the correct statement of the rule if a non-0DTE arm ever
# appears.
Leg = tuple[str, str, float, int]

__all__ = ["Leg", "entry_allowed", "next_eligible", "sign_conflict", "structure_key"]


def _right(value: object) -> str:
    """Normalize an option type to "P"/"C". Accepts the several spellings the suite already uses --
    MEIC says "put"/"call", flies says PUT/CALL, broker payloads say "P"/"C" -- because a mismatch
    here would silently make every leg look like a different contract and the rule would refuse
    nothing at all. Anything unrecognized is passed through uppercased rather than coerced, so a
    typo fails loudly as its own bucket instead of quietly joining the puts.
    """
    text = str(value).strip().upper()
    if text.startswith("P"):
        return "P"
    if text.startswith("C"):
        return "C"
    return text


def entry_allowed(
    last_fill: datetime | None,
    now: datetime,
    spacing_seconds: int,
) -> tuple[bool, int]:
    """May this arm take an entry now? Returns ``(allowed, seconds_remaining)``.

    Args:
        last_fill: When this arm last *filled* an entry, or None if it has not filled today.
        now: Current time, in the same tz-awareness as `last_fill` (both ET throughout the suite).
        spacing_seconds: Minimum seconds between fills. 0 or negative disables the gate.

    `seconds_remaining` is 0 when allowed, and otherwise the whole seconds still to wait -- rounded
    UP, so a caller that sleeps exactly that long wakes eligible rather than one tick short. It is
    recorded on every refused attempt, which is what makes the cadence itself measurable: the
    distribution of how long arms spent waiting is the cost of the current spacing, and the only
    honest input to changing it.

    A `last_fill` in the future (clock skew, or a caller passing a stale `now`) is treated as
    blocking rather than allowing. Fail-closed: an entry refused for a bad clock is recoverable on
    the next tick, an entry admitted on one is in the book forever.
    """
    if spacing_seconds <= 0 or last_fill is None:
        return True, 0
    elapsed = (now - last_fill).total_seconds()
    if elapsed >= spacing_seconds:
        return True, 0
    # -(-x // 1) is ceil without importing math, and stays exact on the float seconds above.
    remaining = int(-(-(spacing_seconds - elapsed) // 1))
    return False, max(remaining, 0)


def next_eligible(last_fill: datetime | None, spacing_seconds: int) -> datetime | None:
    """When this arm becomes eligible again, or None if it already is (or cadence is off).

    Exists for the read side: the console's arm rail counts down to a wall-clock instant, and
    deriving that instant from `entry_allowed`'s remaining-seconds would re-anchor the countdown to
    whenever the page last polled.
    """
    if spacing_seconds <= 0 or last_fill is None:
        return None
    return last_fill + timedelta(seconds=spacing_seconds)


def sign_conflict(
    open_legs: Iterable[Leg],
    proposed_legs: Iterable[Leg],
) -> tuple[str, str, float] | None:
    """Would any proposed leg oppose an open leg on the same contract? Returns the blocking
    ``(expiry, right, strike)`` or None.

    `open_legs` is the arm's own currently-constraining legs -- for MEIC the legs of its open ICs
    (a stopped spread has released its strikes and must not be passed in), for flies the whole day's
    book (flies complete rather than close, so nothing is ever released before EOD).

    Legs with sign 0 are ignored, so a caller may pass a closed/void leg through as a placeholder
    without it constraining anything.

    Returns the FIRST conflict found rather than all of them. The caller records one blocking strike
    per refused attempt, and a proposal that collides at two strikes is refused for the same reason
    either way; a full list would suggest a partial-fill remedy that does not exist here.
    """
    occupied: dict[tuple[str, str, float], int] = {}
    for expiry, right, strike, sign in open_legs:
        if not sign:
            continue
        key = (str(expiry), _right(right), float(strike))
        # Same-sign stacking is legal, so accumulate rather than overwrite; the sign of the running
        # total is what a new leg is compared against. A key can only ever hold one sign, because
        # this very rule refused the leg that would have mixed them.
        occupied[key] = occupied.get(key, 0) + (1 if sign > 0 else -1)
    for expiry, right, strike, sign in proposed_legs:
        if not sign:
            continue
        key = (str(expiry), _right(right), float(strike))
        held = occupied.get(key, 0)
        if held and (held > 0) != (sign > 0):
            return key
    return None


def structure_key(
    symbol: str,
    expiry: str,
    direction: str,
    center: float,
    wing_width: float,
    far_width: float | None = None,
) -> tuple:
    """The identity of a butterfly structure, for "never enter the same trade twice".

    Deliberately keyed on the full geometry rather than the centre alone. Today it collapses exactly
    to flies' existing one-structure-per-centre rule, because `wing_width` is a scalar per arm
    (width variation is expressed as SEPARATE arms -- width-2..width-5, wide_wing -- precisely so the
    sweep stays one-variable), and `far_width` is likewise arm-fixed via `bwb_far_width_ratio`. So
    within one arm, same centre implies same wings implies same trade.

    Written this way anyway because it is the correct statement of the rule rather than a
    coincidence of the current config, and it stays right the day an arm sweeps width internally.
    Note this cannot be shared with MEIC: MEIC ranks a whole wing shortlist within one profile, so a
    MEIC structure is genuinely not pinned by its short strikes -- which is moot, since nesting is
    the point there and MEIC has no duplicate rule at all.
    """
    return (
        str(symbol).upper(),
        str(expiry),
        str(direction),
        float(center),
        float(wing_width),
        None if far_width is None else float(far_width),
    )
