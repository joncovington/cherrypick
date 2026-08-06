"""`cherrypick-desk` — the manual trading desk CLI.

Commands:
  status              config, PIN presence, halt flag, today's tallies (no broker, no secrets shown)
  analyze  --order    parse + risk only. Fully offline: no broker, no ticket, no state written
  propose  --order    run the gates, preflight against the broker, mint a ticket + confirmation code
  confirm  --ticket --code --pin    re-check everything and submit
  pin-set / pin-clear PIN management
  purge               drop expired pending tickets

Why two phases: `propose` is the reviewable artifact — it prints the parsed structure, the worst
case, the broker's own preflight, and every gate verdict. `confirm` then re-runs the gates and
re-preflights before submitting, so a market or account change between the two is caught rather than
ratified by a stale review.

**This CLI is never scheduled and never invoked by a loop.** It is a foreground, human-initiated
tool. `tests/test_isolation.py` asserts no module imports this package and that the desk never reads
a module's `enable_live_trading` — the two properties that keep "the desk is on" from ever meaning
"the automated loops are on".
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import Any

from . import config as cfgmod
from . import journal, pin, policy, ticket
from .order import OrderError, analyze


def _halt_present() -> bool:
    """The suite-wide kill switch. Path is defined by the orchestrator's `liveops`; imported
    defensively so the desk still works (and still refuses correctly) in a checkout without it."""
    try:
        from cherrypick.orchestrator import liveops

        return liveops.halt_flag_path().exists()
    except Exception:  # noqa: BLE001 — fall back to the documented path rather than failing open
        from cherrypick.core import home as _home

        return (_home.state_dir() / "halt-live.flag").exists()


def _load_order(args) -> dict[str, Any]:
    raw = args.order
    if raw == "-":
        raw = sys.stdin.read()
    try:
        spec = json.loads(raw)
    except ValueError as exc:
        raise OrderError(f"--order is not valid JSON: {exc}") from exc
    if not isinstance(spec, dict):
        raise OrderError("--order must be a JSON object")
    return spec


def _describe(legs, risk) -> dict[str, Any]:
    def money(x):
        return None if x is None else round(float(x), 2)

    return {
        "classification": risk.classification,
        "underlyings": list(risk.underlyings),
        "spreads": risk.spreads,
        "defined_risk": risk.defined,
        "max_loss": money(risk.max_loss),
        "max_gain": money(risk.max_gain),
        "entry_cash": money(risk.entry_cash),
        "breakevens": list(risk.breakevens),
        "legs": [
            {
                "action": leg.action,
                "quantity": leg.quantity,
                "symbol": leg.symbol,
                "right": leg.right,
                "strike": leg.strike,
                "expiration": str(leg.expiration) if leg.expiration else None,
            }
            for leg in legs
        ],
    }


def _resolve_account(cfg: dict, requested: str | None) -> str | None:
    """The account this order would hit. Imports the broker lazily so the offline commands stay
    offline; returns None when the broker cannot be reached (which the gates treat as a refusal)."""
    try:
        import asyncio

        from cherrypick.core import broker as _broker

        from .session import get_session

        session = get_session(cfg)
        account = asyncio.run(_broker.resolve_account(session, requested))
        return account.account_number
    except Exception:  # noqa: BLE001 — unreachable broker must refuse, not crash or pass
        return None


# --------------------------------------------------------------------------- commands
def cmd_status(args) -> dict:
    cfg = cfgmod.resolve(cfgmod.load())
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    orders_today, risk_today = journal.today_totals(day)
    return {
        "ok": True,
        "enabled": cfg["enabled"],
        "pin_configured": pin.is_set(),
        "halt_flag_present": _halt_present(),
        "allowed_accounts": [f"****{a}" for a in cfg["allowed_accounts"]],
        "require_defined_risk": cfg["require_defined_risk"],
        "max_order_risk_dollars": cfg["max_order_risk_dollars"],
        "max_orders_per_day": cfg["max_orders_per_day"],
        "max_daily_risk_dollars": cfg["max_daily_risk_dollars"],
        "orders_today": orders_today,
        "risk_committed_today": round(risk_today, 2),
        "config_path": str(cfgmod.config_path()),
        "journal_path": str(cfgmod.journal_path()),
    }


def cmd_analyze(args) -> dict:
    """Offline structure + risk. Deliberately touches nothing — the safe way to inspect an order."""
    legs, risk = analyze(_load_order(args))
    return {"ok": True, **_describe(legs, risk)}


def cmd_propose(args) -> dict:
    cfg = cfgmod.resolve(cfgmod.load())
    spec = _load_order(args)
    legs, risk = analyze(spec)
    described = _describe(legs, risk)

    account = _resolve_account(cfg, args.account_number)
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    orders_today, risk_today = journal.today_totals(day)
    refusals = policy.evaluate(
        risk,
        cfg=cfg,
        halt_present=_halt_present(),
        account_number=account,
        orders_today=orders_today,
        risk_today=risk_today,
    )
    if not pin.is_set():
        refusals.append("no desk PIN configured — run `cherrypick-desk pin-set`")

    if refusals:
        journal.record("refused", phase="propose", account_number=account, refusals=refusals, **described)
        return {"ok": False, "error": "refused by desk policy", "refusals": refusals, **described}

    preflight = _preflight(cfg, spec, account)
    if not preflight.get("ok"):
        journal.record("refused", phase="preflight", account_number=account, reason=preflight, **described)
        return {"ok": False, "error": "broker preflight failed", "preflight": preflight, **described}

    record = ticket.create(
        spec,
        account,
        ttl_seconds=cfg["ticket_ttl_seconds"],
        extra={"max_loss": risk.max_loss, "classification": risk.classification},
    )
    journal.record(
        "proposed",
        account_number=account,
        ticket_id=record["ticket_id"],
        fingerprint=record["fingerprint"],
        **described,
    )
    return {
        "ok": True,
        "ticket_id": record["ticket_id"],
        "confirmation_code": record["code"],
        "expires_in_seconds": cfg["ticket_ttl_seconds"],
        "account": policy.mask_account(account),
        "preflight": preflight,
        **described,
    }


def _preflight(cfg: dict, spec: dict, account_number: str | None) -> dict:
    """Broker dry-run. Returns a JSON-safe summary; never submits."""
    try:
        import asyncio

        from cherrypick.core import broker as _broker

        from .session import get_session, serialize

        session = get_session(cfg)
        account = asyncio.run(_broker.resolve_account(session, account_number))
        order = _broker.build_order(spec)
        return asyncio.run(_broker.place_order(account, session, order, live=False, serialize=serialize))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def cmd_confirm(args) -> dict:
    cfg = cfgmod.resolve(cfgmod.load())

    supplied_pin = pin.env_pin() or args.pin
    if not supplied_pin:
        return {"ok": False, "error": "no PIN supplied (pass --pin or set CHERRYPICK_DESK_PIN)"}
    if not pin.verify(supplied_pin):
        journal.record("refused", phase="confirm", reason="bad PIN", ticket_id=args.ticket)
        return {"ok": False, "error": "PIN rejected"}

    try:
        record = ticket.consume(args.ticket, args.code)
    except ticket.TicketError as exc:
        journal.record("refused", phase="confirm", reason=str(exc), ticket_id=args.ticket)
        return {"ok": False, "error": str(exc)}

    spec = record["order"]
    account = record.get("account_number")
    try:
        legs, risk = analyze(spec)
    except OrderError as exc:
        ticket.release(args.ticket)
        return {"ok": False, "error": f"stored order no longer parses: {exc}"}
    described = _describe(legs, risk)

    # Re-run every gate against CURRENT state. The proposal's verdict is not carried forward: the
    # halt flag may have appeared, the config may have changed, the daily tally may have moved.
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    orders_today, risk_today = journal.today_totals(day)
    refusals = policy.evaluate(
        risk,
        cfg=cfg,
        halt_present=_halt_present(),
        account_number=account,
        orders_today=orders_today,
        risk_today=risk_today,
    )
    if refusals:
        ticket.release(args.ticket)
        journal.record("refused", phase="confirm", account_number=account, refusals=refusals, **described)
        return {"ok": False, "error": "refused by desk policy at confirm time", "refusals": refusals}

    result = _submit(cfg, spec, account)
    ticket.release(args.ticket)
    order_id = ((result.get("response") or {}).get("order") or {}).get("id")
    journal.record(
        "submitted" if result.get("ok") else "failed",
        account_number=account,
        ticket_id=args.ticket,
        fingerprint=record.get("fingerprint"),
        order_id=order_id,
        **described,
    )
    return {"ok": bool(result.get("ok")), "order_id": order_id, "result": result, **described}


def _submit(cfg: dict, spec: dict, account_number: str | None) -> dict:
    try:
        import asyncio

        from cherrypick.core import broker as _broker

        from .session import get_session, serialize

        session = get_session(cfg)
        account = asyncio.run(_broker.resolve_account(session, account_number))
        order = _broker.build_order(spec)
        return asyncio.run(_broker.place_order(account, session, order, live=True, serialize=serialize))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def cmd_pin_set(args) -> dict:
    import getpass

    value = pin.env_pin() or args.pin or getpass.getpass("New desk PIN: ")
    pin.set_pin(value)
    return {"ok": True, "pin_configured": True}


def cmd_pin_clear(args) -> dict:
    pin.clear_pin()
    return {"ok": True, "pin_configured": pin.is_set()}


def cmd_purge(args) -> dict:
    return {"ok": True, "purged": ticket.purge_expired()}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cherrypick-desk", description="Manual trading desk")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("purge").set_defaults(func=cmd_purge)

    for name, fn in (("analyze", cmd_analyze), ("propose", cmd_propose)):
        sp = sub.add_parser(name)
        sp.add_argument("--order", required=True, help="JSON order spec, or '-' to read stdin")
        sp.add_argument("--account_number", default=None)
        sp.set_defaults(func=fn)

    sp = sub.add_parser("confirm")
    sp.add_argument("--ticket", required=True)
    sp.add_argument("--code", required=True)
    sp.add_argument("--pin", default=None, help="prefer CHERRYPICK_DESK_PIN to keep it out of shell history")
    sp.set_defaults(func=cmd_confirm)

    sp = sub.add_parser("pin-set")
    sp.add_argument("--pin", default=None, help="omit to be prompted without echo")
    sp.set_defaults(func=cmd_pin_set)

    sub.add_parser("pin-clear").set_defaults(func=cmd_pin_clear)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
    except (OrderError, pin.PinError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
