"""Position management: what should happen to an open position, and whether we may act on it.

Two layers, kept apart on purpose (the earnings pattern, via calendars):

- `effective_params` is the ONE choke point that restates a position's frozen advised params over
  the config. An advised arm's rules are stamped on the row at entry and read back here every
  tick, so advice lapsing mid-position never hands it to rules nobody chose — and a control row
  comes back untouched.
- `evaluate` is pure over (position, params, a priced mark, the clock) and returns a verdict.
- `execution_gate` separately answers "may we act on this mark at all" — a verdict blocked by a
  gate is still recorded (`executed=0` with the gate), which is the only record that an exit was
  SEEN before it was allowed.

Arm semantics, since the 2026-08-23 redesign to a single `control` arm (now run across two
symbols, TQQQ and XSP):
- `control` — mechanical entry whenever the slot is free; the default exit HOLDS the position to
  the short's own expiration and then closes both legs (`short_expiration`), rather than the old
  tv-exhaustion trigger. There is no more roll arm and no more breach special case — the short can
  now be OTM or ITM by construction (the ATM leg-selection redesign), so "hold like a covered call
  on a breach" no longer means anything distinct from "hold".
- `tv_managed_exit` (default False) is a live, advisor-tunable escape hatch back to the PRE-redesign
  behavior: when True, `evaluate` closes early once the short's time value decays to
  `tv_close_threshold` — settable only through an `advised:control` row's frozen `advice_params`
  overlay, so the suite can run hold-to-expiry vs. early-tv-exit as a paper A/B without a new arm.
- `advised:<experiment name>` (one arm per advisor experiment since 2026-09-17; `advised:control`
  before) — the base arm's rules with the admitted param overrides frozen at entry.

Assignment-exposure telemetry lives BESIDE the verdict, not in it: `assignment_exposed` flags a
mark whose short extrinsic sits under `assignment_exposure_tv` — the region where a real short is
liable to be assigned early, which this module measures and deliberately does not model. It gates
nothing. This is a PHYSICAL-settlement risk only: a European, cash-settled short (XSP) cannot be
exercised before its own expiration, so `assignment_exposed` is exempt (always False) for a
cash-settled position — pass `settlement_style` through so the flag never fires a phantom warning
on a symbol that structurally cannot be assigned early.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from cherrypick.core import spreadbook as _spreadbook

from cherrypick.pmcc import clock, engine

PARAM_DEFAULTS = {
    "tv_close_threshold": 0.10,
    "tv_managed_exit": False,
    "assignment_exposure_tv": 0.05,
    "entry_window_start": "10:00",
    "entry_window_end": "15:30",
    "exec_window_start": "09:40",
    "max_leg_spread_pct": 0.25,
    # The floor under the percentage: refuse a leg only when wide in percent AND in money.
    "max_leg_spread_abs": 0.05,
    "allow_extrinsic_fallback": True,
    # The held-long lifecycle (2026-10-04). `weekly` is control's: one long and one short, closed
    # together at the short's expiry. `held_long` holds a ~1-year long and rolls a weekly short
    # against it; every key below is read only under it.
    "lifecycle": "weekly",
    "short_rule": "atm",
    # Minutes before the session's own close (`clock.session_close_min`, 13:00 on an early close).
    "roll_time_offset": 60,
    "roll_deadline_offset": 20,
    # Fraction of the short's extrinsic at sale that has decayed when the early roll fires (Tom
    # King's "80-90% decayed"); None never rolls early (`shield_hold`).
    "early_roll_decay": None,
    "breach_roll": False,
    # P&L to date at or below -this x the long's cost closes the position (Tom King's 30% stop).
    "stop_loss_frac": None,
    "long_close_dte": 45,
    # How an expiring short is treated (Part 2's account policy, read by paper too so a paper arm
    # trades under its live twin's rules). `ira`: always bought back before the bell, any
    # moneyness -- an IRA cannot carry short stock. `margin`: a short further than `pin_buffer_pct`
    # out of the money may expire instead.
    "account_policy": "ira",
    "pin_buffer_pct": 0.02,
}

HELD_LONG = "held_long"


@dataclass(frozen=True)
class Decision:
    """A verdict about one position. `executed` is decided by the caller after `execution_gate`."""

    # "hold" | "close_all" | "roll_short" | "sell_short" | "close_short" (the last three: held_long)
    action: str
    reason: str
    detail: dict = field(default_factory=dict)

    @property
    def acts(self) -> bool:
        return self.action != "hold"


def effective_params(position: dict, config: dict) -> dict:
    """The params governing this position: the base arm's merged config, with the row's frozen
    `advice_params` overlaid for an advised arm. An unreadable stamp is the base's config, never a
    guess."""
    arm = position.get("arm") or "control"
    # A legacy `advised:control` row names its base in the tag; a 2026-09-17 `advised:<experiment>`
    # row shadows the configured `advice.base_book`. One rule, in `engine.base_book`.
    # The row's own stamp first (2026-09-17): an advised twin of a non-default base is managed under
    # THAT base after the session's decision file is gone, not under the configured default.
    stamped = position.get("advice_base")
    base = str(stamped) if stamped else engine.base_book(arm, config=config)
    params = {**PARAM_DEFAULTS, **engine.merged_params(config, base)}
    params["arm"] = arm
    params["base_book"] = base
    raw = position.get("advice_params")
    if arm.startswith("advised:") and raw:
        try:
            overlay = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError):
            overlay = {}
        params.update(overlay)
    return params


def assignment_exposed(short_tv: float | None, params: dict, *, settlement_style: str | None = None) -> bool:
    """Whether this mark sits in the early-assignment-exposed region. Telemetry only.

    Early assignment is a PHYSICAL-settlement risk — a European, cash-settled leg (XSP) can only
    ever be exercised at expiration, so it never carries this exposure regardless of how thin its
    extrinsic gets. `settlement_style` is optional (callers that cannot resolve it, e.g. legacy
    call sites, keep the pre-XSP behavior) but every current call site passes it."""
    if settlement_style == "cash":
        return False
    if short_tv is None:
        return False
    return short_tv < params.get("assignment_exposure_tv", 0.05)


def evaluate(
    position: dict,
    params: dict,
    *,
    now: datetime,
    short_tv: float | None,
    spot: float | None,
) -> Decision:
    """The verdict for one OPEN position this tick.

    `short_tv` is the short leg's per-share extrinsic at the current mark (None when the mark was
    refused — nothing acts on a hole). Since the 2026-08-23 redesign the default rule is simply
    HOLD until the short's own expiration day, then close both legs together — there is no more
    breach special case (the short can legitimately sit OTM or ITM) and no more roll arm.
    `tv_managed_exit` is the advisor-tunable override back to the old early-tv-exhaustion exit,
    readable only through an advised row's frozen params via `effective_params`.
    """
    if spot is None or short_tv is None:
        return Decision("hold", "unpriced_mark")

    if params.get("tv_managed_exit", False):
        if short_tv <= params.get("tv_close_threshold", 0.10):
            return Decision("close_all", "tv_exhausted", {"short_tv": short_tv, "spot": spot})
        return Decision("hold", "working")

    if now.date().isoformat() >= position["short_expiration"]:
        return Decision("close_all", "short_expiration", {"short_tv": short_tv, "spot": spot})
    return Decision("hold", "holding_to_expiry")


def is_held_long(params: dict) -> bool:
    return params.get("lifecycle") == HELD_LONG


def evaluate_held_long(
    position: dict,
    params: dict,
    *,
    now: datetime,
    short: dict | None,
    spot: float | None,
    pnl: dict | None,
    long_dte: int | None,
    rolled_today: bool,
    session_close_min: int,
) -> Decision:
    """The verdict for one open HELD-LONG position this tick, in this order:

    1. `stop_loss`: P&L to date at or below -`stop_loss_frac` x the long's cost -> close all.
    2. `long_roll_due`: the long at or under `long_close_dte` -> close all (a new long enters on a
       later session, as a new position).
    3. `no_short`: no short open -> sell one.
    4. `roll_deadline`: the short expires today and the bell is `roll_deadline_offset` away -> buy
       it back alone (a margin account may instead let a far-OTM short expire).
    5. `expiry`: the short expires today, `roll_time_offset` before the bell -> roll it.
    6. `decayed`: (`early_roll_decay` set) the short's extrinsic is down to (1 - decay) of its
       extrinsic at sale -> roll it, at most once a session.
    7. `breach`: (`breach_roll`) spot at or below the short's strike -> roll it down, at most once a
       session.
    8. hold.

    `short` is `{"strike", "expiration", "tv", "entry_tv"}` for the open short, or None. A short
    without a price holds (`unpriced_mark`) rather than acting on a hole; the stop and the long roll
    still fire when their own inputs are known."""
    long_cost = float(position.get("long_entry_mid") or 0.0) * 100 * int(position.get("quantity") or 1)
    stop = params.get("stop_loss_frac")
    if stop and pnl is not None and long_cost > 0 and pnl["net"] <= -stop * long_cost:
        return Decision("close_all", "stop_loss", {"net": pnl["net"], "long_cost": round(long_cost, 2)})
    if long_dte is not None and long_dte <= params.get("long_close_dte", 45):
        return Decision("close_all", "long_roll_due", {"long_dte": long_dte})
    if short is None:
        return Decision("sell_short", "no_short")
    if spot is None or short.get("tv") is None:
        return Decision("hold", "unpriced_mark")

    minute = clock.minute_of_day(now)
    today = now.date().isoformat()
    if today >= short["expiration"]:
        if minute >= session_close_min - params.get("roll_deadline_offset", 20):
            far_otm = spot < short["strike"] * (1.0 - params.get("pin_buffer_pct", 0.02))
            if params.get("account_policy", "ira") == "margin" and far_otm:
                return Decision("hold", "let_expire", {"spot": spot, "strike": short["strike"]})
            return Decision("close_short", "roll_deadline", {"spot": spot, "short_tv": short["tv"]})
        if minute >= session_close_min - params.get("roll_time_offset", 60):
            return Decision("roll_short", "expiry", {"spot": spot, "short_tv": short["tv"]})

    decay = params.get("early_roll_decay")
    entry_tv = short.get("entry_tv")
    if decay is not None and not rolled_today and entry_tv and entry_tv > 0:
        if short["tv"] <= (1.0 - decay) * entry_tv:
            return Decision("roll_short", "decayed", {"short_tv": short["tv"], "entry_tv": entry_tv})
    if params.get("breach_roll") and not rolled_today and spot <= short["strike"]:
        return Decision("roll_short", "breach", {"spot": spot, "strike": short["strike"]})
    return Decision("hold", "holding")


def short_execution_gate(quotes: list[dict | None], params: dict, *, now: datetime) -> str | None:
    """Why a short-only ticket (a roll, a sale, a buyback) may not be acted on, or None. The roll
    trades the short legs only, so it is gated on them -- not on the ~1-year long, whose own spread
    has nothing to do with this ticket and would otherwise hold every roll of every week behind it."""
    exec_start = clock.hhmm_to_min(params.get("exec_window_start"), 9 * 60 + 40)
    if clock.minute_of_day(now) < exec_start:
        return "before_exec_window"
    for quote in quotes:
        if quote is None:
            return "missing_leg_quotes"
        mid = quote.get("mid") or 0.0
        if mid > 0:
            pct = (quote["ask"] - quote["bid"]) / mid
            if pct > params.get("max_leg_spread_pct", 0.25) and (quote["ask"] - quote["bid"]) > params.get(
                "max_leg_spread_abs", 0.05
            ):
                return "spread_too_wide"
    return None


def execution_gate(mark_snapshot: dict, params: dict, *, now: datetime) -> str | None:
    """Why this mark may not be acted on, or None if it may. Separate from `evaluate` so a blocked
    verdict is still recorded — an exit seen at 09:33 and taken at 09:41 must be legible as that."""
    if not mark_snapshot.get("ok"):
        return "unusable_mark"
    exec_start = clock.hhmm_to_min(params.get("exec_window_start"), 9 * 60 + 40)
    if clock.minute_of_day(now) < exec_start:
        return "before_exec_window"
    if _spread_blocks(mark_snapshot, params):
        return "spread_too_wide"
    return None


# The exit spread gate (percent AND money, per leg) is `cherrypick.core.spreadbook.exit_spread_blocks`:
# calendars, pmcc and curve carried identical copies. Its docstring holds why a percentage alone
# refused penny-wide exits.
_spread_blocks = _spreadbook.exit_spread_blocks
