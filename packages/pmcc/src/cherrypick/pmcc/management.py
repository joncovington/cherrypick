"""Position management: what should happen to an open position, and whether we may act on it.

Two layers, kept apart on purpose (the earnings pattern, via calendars):

- `effective_params` is the ONE choke point that restates a position's frozen advised params over
  the config. An advised book's rules are stamped on the row at entry and read back here every
  tick, so advice lapsing mid-position never hands it to rules nobody chose — and a control row
  comes back untouched.
- `evaluate` is pure over (position, params, a priced mark, the clock) and returns a verdict.
- `execution_gate` separately answers "may we act on this mark at all" — a verdict blocked by a
  gate is still recorded (`executed=0` with the gate), which is the only record that an exit was
  SEEN before it was allowed.

Book semantics, since the 2026-08-23 redesign to a single `control` book (now run across two
symbols, TQQQ and XSP):
- `control` — mechanical entry whenever the slot is free; the default exit HOLDS the position to
  the short's own expiration and then closes both legs (`short_expiration`), rather than the old
  tv-exhaustion trigger. There is no more roll book and no more breach special case — the short can
  now be OTM or ITM by construction (the ATM leg-selection redesign), so "hold like a covered call
  on a breach" no longer means anything distinct from "hold".
- `tv_managed_exit` (default False) is a live, advisor-tunable escape hatch back to the PRE-redesign
  behavior: when True, `evaluate` closes early once the short's time value decays to
  `tv_close_threshold` — settable only through an `advised:control` row's frozen `advice_params`
  overlay, so the suite can run hold-to-expiry vs. early-tv-exit as a paper A/B without a new book.
- `advised:<base>` — the base book's rules with the admitted param overrides frozen at entry.

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
}


@dataclass(frozen=True)
class Decision:
    """A verdict about one position. `executed` is decided by the caller after `execution_gate`."""

    action: str  # "hold" | "close_all"
    reason: str
    detail: dict = field(default_factory=dict)

    @property
    def acts(self) -> bool:
        return self.action != "hold"


def effective_params(position: dict, config: dict) -> dict:
    """The params governing this position: the base book's merged config, with the row's frozen
    `advice_params` overlaid for an advised book. An unreadable stamp is the base's config, never a
    guess."""
    book = position.get("book") or "control"
    base = book.split(":", 1)[1] if book.startswith("advised:") else book
    params = {**PARAM_DEFAULTS, **engine.merged_params(config, base)}
    params["book"] = book
    raw = position.get("advice_params")
    if book.startswith("advised:") and raw:
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
    breach special case (the short can legitimately sit OTM or ITM) and no more roll book.
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


def _spread_blocks(mark_snapshot: dict, params: dict) -> bool:
    """Whether any leg is too wide to act on -- wide in PERCENT and in MONEY, both, per leg.

    The zero-bid arithmetic, pre-empted rather than measured here: this module HOLDS its short to
    the short's own expiration by design (the 2026-08-23 redesign), which is exactly when its quote
    goes penny-wide -- 0.00/0.01 is a one-cent buyback and, as a ratio, a 200% spread -- so a
    percentage-only gate would refuse the combined disposal on precisely the day the design says to
    take it. earnings measured 32 profit-target exits refused this way before its 2026-08-31 fix,
    and calendars lost a Friday close to it; this gate had not fired yet only because no position
    under the new design has aged into the state. A leg is refused only when both readings say
    wide; an older snapshot with no per-leg detail falls back to the percentage alone.
    """
    max_pct = params.get("max_leg_spread_pct", 0.25)
    legs = mark_snapshot.get("leg_spreads")
    if not legs:
        widest = mark_snapshot.get("max_spread_pct")
        return widest is not None and widest > max_pct
    max_abs = params.get("max_leg_spread_abs", 0.05)
    return any(leg["pct"] > max_pct and leg["abs"] > max_abs for leg in legs)
