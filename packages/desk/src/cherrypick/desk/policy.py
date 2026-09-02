"""The policy layer — every gate an order must clear, as one pure function.

This is the load-bearing half of the desk's security. The PIN and the confirmation code raise the
bar on *who* is asking; these gates bind regardless of who is asking, and are the part that a
mistaken (or too-obliging) automation cannot talk its way past. Nothing here does I/O: it takes the
already-read world (config, halt-flag presence, resolved account, today's journal tally) and returns
the refusals. That makes every gate unit-testable without a broker, a keyring, or a clock.

Fail-closed throughout: each check appends a refusal on the *bad* path, so a gate that is somehow
skipped leaves the order refused rather than allowed. `evaluate` returns the FULL list of refusals
rather than the first — a human fixing a proposal should see everything wrong with it at once, not
peel them off one round-trip at a time.

Closing orders are deliberately exempt from the defined-risk and per-order risk gates (never from
the halt flag or the account allowlist). Blocking someone from flattening a position is a
risk-*increasing* refusal, and a cap that does it is the cap misfiring — see `order.py`'s note on
the BKNG close that motivated this package.
"""

from __future__ import annotations

from typing import Any

from .order import RiskProfile


def mask_account(number: str | None) -> str:
    """`****1234`, the suite-wide masked form. Never emit a full account number."""
    s = str(number or "")
    return f"****{s[-4:]}" if len(s) >= 4 else "****"


def _undefined_text(risk: RiskProfile) -> str:
    """Why the worst case is not a number — the two cases read very differently to a human, and
    'undefined risk' alone would send someone hunting for a naked leg in a calendar spread."""
    if risk.undefined_reason == "multi_expiry":
        return (
            "worst case is not computable — the order spans multiple expirations, where a "
            "single-expiry payoff is the wrong model (the far leg still carries time value)"
        )
    return "undefined risk (loss is unbounded to the upside)"


def _account_refusals(cfg: dict[str, Any], account_number: str | None) -> list[str]:
    allowed = cfg.get("allowed_accounts") or []
    if not allowed:
        return ["desk.allowed_accounts is empty — no account is authorized for manual orders"]
    if not account_number:
        return ["no account resolved to check against the allowlist"]
    if str(account_number)[-4:] not in allowed:
        return [f"account {mask_account(account_number)} is not in desk.allowed_accounts"]
    return []


def evaluate(
    risk: RiskProfile,
    *,
    cfg: dict[str, Any],
    halt_present: bool,
    account_number: str | None,
    orders_today: int = 0,
    risk_today: float = 0.0,
) -> list[str]:
    """Unmet gates for this order — empty means it may be submitted.

    `cfg` must already be `config.resolve`d (defaults applied), so a caller cannot accidentally pass
    a raw dict whose absent keys would read as permissive.
    """
    refusals: list[str] = []

    if not cfg.get("enabled"):
        refusals.append("desk.enabled is false — the manual desk is switched off")

    # The suite-wide kill switch, honored by the flies live loop and now here. One file halts
    # everything that can touch real money.
    if halt_present:
        refusals.append("suite halt flag present (state/halt-live.flag) — all live action halted")

    refusals += _account_refusals(cfg, account_number)

    # --- risk gates: opening exposure only -------------------------------------------------
    # "mixed" (a roll) counts as opening: it establishes new legs, so it must clear the same bar.
    if risk.classification in ("opening", "mixed"):
        if cfg.get("require_defined_risk", True) and not risk.defined:
            refusals.append(f"{_undefined_text(risk)} and desk.require_defined_risk is true")
        cap = cfg.get("max_order_risk_dollars")
        if cap is not None:
            if risk.max_loss is None:
                # An uncomputable worst case cannot satisfy any finite cap. Stated explicitly so
                # this is not silently skipped when require_defined_risk has been turned off.
                refusals.append(f"{_undefined_text(risk)}, which cannot satisfy the ${float(cap):,.2f} cap")
            elif risk.max_loss > float(cap):
                refusals.append(
                    f"worst case ${risk.max_loss:,.2f} exceeds desk.max_order_risk_dollars ${float(cap):,.2f}"
                )

        # Optional daily brakes (off unless configured).
        max_orders = cfg.get("max_orders_per_day")
        if max_orders is not None and orders_today >= int(max_orders):
            refusals.append(f"daily order cap reached ({orders_today}/{int(max_orders)} placed today)")
        max_daily = cfg.get("max_daily_risk_dollars")
        if max_daily is not None and risk.max_loss is not None:
            if risk_today + risk.max_loss > float(max_daily):
                refusals.append(
                    f"would put today's desk risk at ${risk_today + risk.max_loss:,.2f}, "
                    f"over desk.max_daily_risk_dollars ${float(max_daily):,.2f}"
                )

    return refusals


def evaluate_management(*, cfg: dict[str, Any], account_number: str | None) -> list[str]:
    """Gates for cancelling a resting order (and, by composition, the cancel-then-repropose path
    that stands in for 'replace' — see `cli.py`'s module docstring for why there is no separate
    replace primitive). Deliberately exempt from the halt flag, for the same reason closing orders
    are exempt from the risk gates in `evaluate`: pulling a resting order only *reduces* exposure,
    and a halt that trapped an account inside a stale working order would be the safety flag
    misfiring in the wrong direction — the same shape of mistake the BKNG close (see `order.py`)
    exists to prevent, one step earlier in the order's life. Still requires `desk.enabled` and the
    account allowlist: this is not a blanket bypass, only the one gate whose direction is wrong for
    a risk-reducing action.
    """
    refusals: list[str] = []
    if not cfg.get("enabled"):
        refusals.append("desk.enabled is false — the manual desk is switched off")
    refusals += _account_refusals(cfg, account_number)
    return refusals
