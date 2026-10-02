"""The policy layer — every gate an order must clear, as pure functions.

This is the load-bearing half of the desk's security. The PIN and the confirmation code raise the
bar on *who* is asking; these gates bind regardless of who is asking, and are the part that a
mistaken (or too-obliging) automation cannot talk its way past. Nothing here does I/O: it takes the
already-read world (config, halt-flag presence, resolved account, today's journal tally, the broker
preflight) and returns the refusals. That makes every gate unit-testable without a broker, a keyring,
or a clock.

Fail-closed throughout: each check appends a refusal on the *bad* path, so a gate that is somehow
skipped leaves the order refused rather than allowed, and a cap that is missing or not a finite
number refuses rather than reading as "no cap". `evaluate` returns the FULL list of refusals rather
than the first — a human fixing a proposal should see everything wrong with it at once.

Only a covering order — every leg "buy to close" — is exempt from the risk gates (never from the
halt flag, the account allowlist or the underlying allowlist). Buying back shorts can only remove
exposure, and blocking it is the cap misfiring (see `order.py`'s note on the BKNG close). A
sell-to-close leg is not exempt: selling the long wing of a condor leaves a naked short, and the
desk sees the order, not the position.
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any

from cherrypick.core.redact import mask_account  # noqa: F401 -- re-exported: cli/journal import it here

from .order import RiskProfile


def _undefined_text(risk: RiskProfile) -> str:
    """Why the worst case is not a number — the two cases read very differently to a human, and
    'undefined risk' alone would send someone hunting for a naked leg in a calendar spread."""
    if risk.undefined_reason == "multi_expiry":
        return (
            "worst case is not computable — the order spans multiple expirations, where a "
            "single-expiry payoff is the wrong model (the far leg still carries time value)"
        )
    return "undefined risk (loss is unbounded to the upside)"


def _cap(cfg: dict[str, Any], key: str, refusals: list[str]) -> float | None:
    """A cap as a finite non-negative float, or None after appending a refusal. A raw dict with a
    missing or junk cap refuses here, so only a `config.resolve`d config can ever pass."""
    value = cfg.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        refusals.append(f"desk.{key} is not a finite non-negative number ({value!r}) — refusing")
        return None
    return float(value)


def _config_refusals(cfg: dict[str, Any]) -> list[str]:
    out = [f"desk.json refused: {e}" for e in cfg.get("config_errors") or []]
    if cfg.get("enabled") is not True:
        out.append("desk.enabled is false — the manual desk is switched off")
    return out


def _account_refusals(cfg: dict[str, Any], account_number: str | None) -> list[str]:
    allowed = cfg.get("allowed_accounts") or []
    if not allowed:
        return ["desk.allowed_accounts is empty — no account is authorized for manual orders"]
    if not account_number:
        return ["no account resolved to check against the allowlist"]
    if str(account_number)[-4:] not in allowed:
        return [f"account {mask_account(account_number)} is not in desk.allowed_accounts"]
    return []


def _underlying_refusals(cfg: dict[str, Any], risk: RiskProfile) -> list[str]:
    allowed = {str(u).upper() for u in cfg.get("allowed_underlyings") or []}
    if not allowed:
        return ["desk.allowed_underlyings is empty — no underlying is authorized for manual orders"]
    outside = sorted(u for u in risk.underlyings if u.upper() not in allowed)
    if outside or not risk.underlyings:
        return [f"underlying {', '.join(outside) or '?'} is not in desk.allowed_underlyings"]
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

    `orders_today`/`risk_today` are the journal's tally of today's submit attempts (placed, failed
    and in-flight alike); see `journal.today_totals`.
    """
    refusals = _config_refusals(cfg)

    # The suite-wide kill switch. One file halts everything that can touch real money.
    if halt_present:
        refusals.append("suite halt flag present (state/halt-live.flag) — all live action halted")

    refusals += _account_refusals(cfg, account_number)
    refusals += _underlying_refusals(cfg, risk)

    if risk.covering_only:
        return refusals

    # --- risk gates: everything except a pure buy-to-close ---------------------------------
    if cfg.get("require_defined_risk", True) is not False and not risk.defined:
        refusals.append(f"{_undefined_text(risk)} and desk.require_defined_risk is true")

    cap = _cap(cfg, "max_order_risk_dollars", refusals)
    if cap is not None:
        if risk.max_loss is None:
            # An uncomputable worst case cannot satisfy any finite cap. Stated explicitly so this is
            # not silently skipped when require_defined_risk has been turned off.
            refusals.append(f"{_undefined_text(risk)}, which cannot satisfy the ${cap:,.2f} cap")
        elif risk.max_loss > cap:
            refusals.append(
                f"worst case ${risk.max_loss:,.2f} exceeds desk.max_order_risk_dollars ${cap:,.2f}"
            )

    max_spreads = _cap(cfg, "max_spreads_per_order", refusals)
    if max_spreads is not None and risk.spreads > max_spreads:
        refusals.append(f"{risk.spreads} spreads exceeds desk.max_spreads_per_order {int(max_spreads)}")

    max_orders = _cap(cfg, "max_orders_per_day", refusals)
    if max_orders is not None and orders_today >= max_orders:
        refusals.append(f"daily order cap reached ({orders_today}/{int(max_orders)} attempted today)")

    max_daily = _cap(cfg, "max_daily_risk_dollars", refusals)
    if max_daily is not None:
        if risk.max_loss is None:
            refusals.append(
                "worst case is not computable, so it cannot be counted against desk.max_daily_risk_dollars"
            )
        elif risk_today + risk.max_loss > max_daily:
            refusals.append(
                f"would put today's desk risk at ${risk_today + risk.max_loss:,.2f}, "
                f"over desk.max_daily_risk_dollars ${max_daily:,.2f}"
            )

    return refusals


def buying_power_change(preflight: dict[str, Any] | None) -> Decimal | None:
    """The broker preflight's `change_in_buying_power`, signed the SDK's way (negative = consumed),
    or None when it is absent or not a finite number."""
    bp = (preflight or {}).get("buying_power")
    if not isinstance(bp, dict):
        return None
    raw = bp.get("change_in_buying_power")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def evaluate_buying_power(
    risk: RiskProfile, *, cfg: dict[str, Any], preflight: dict[str, Any] | None
) -> list[str]:
    """The broker's own view of what the order consumes, held to `max_order_buying_power_dollars`.

    The payoff diagram can be right and the margin still surprising (a broker can charge more than
    the worst case). A preflight that does not report a buying-power change is refused, never read
    as zero. Covering orders are exempt, as from every risk gate.
    """
    if risk.covering_only:
        return []
    refusals: list[str] = []
    cap = _cap(cfg, "max_order_buying_power_dollars", refusals)
    change = buying_power_change(preflight)
    if change is None:
        refusals.append("broker preflight reported no buying-power change — refusing rather than guessing")
        return refusals
    consumed = -change  # tastytrade signs a debit to buying power negative
    if cap is not None and consumed > Decimal(str(cap)):
        refusals.append(
            f"order consumes ${consumed:,.2f} of buying power, over "
            f"desk.max_order_buying_power_dollars ${cap:,.2f}"
        )
    return refusals


def evaluate_management(*, cfg: dict[str, Any], account_number: str | None) -> list[str]:
    """Gates for cancelling a resting order (and, by composition, the cancel-then-repropose path
    that stands in for 'replace' — see `cli.py`'s module docstring for why there is no separate
    replace primitive). Deliberately exempt from the halt flag: pulling a resting order only
    *reduces* exposure, and a halt that trapped an account inside a stale working order would be
    the safety flag misfiring in the wrong direction. Still requires an accepted config,
    `desk.enabled` and the account allowlist: this is not a blanket bypass, only the one gate whose
    direction is wrong for a risk-reducing action.
    """
    return _config_refusals(cfg) + _account_refusals(cfg, account_number)
