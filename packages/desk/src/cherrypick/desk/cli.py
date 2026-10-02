"""`cherrypick-desk` — the manual trading desk CLI. EXPERIMENTAL.

Commands:
  status              config, gates, PIN presence, halt flag, today's tallies (no broker, no secrets)
  analyze  --order    parse + risk only. Fully offline: no broker, no ticket, no state written
  propose  --order    run the gates, preflight against the broker, mint a ticket + confirmation code
  confirm  --ticket --code    PIN prompt, re-check everything, submit
  orders              working orders on the resolved account — read-only, no PIN
  cancel   --order-id         PIN prompt, pull a resting order
  pin-set / pin-clear PIN management (interactive; the current PIN is required once one is set)
  purge               drop expired pending and claimed tickets

**Two gates before anything else.** `propose`, `confirm`, `cancel` and `orders` refuse — before any
network or keyring access — unless desk.json has `"enabled": true` AND the environment has
`CHERRYPICK_DESK_EXPERIMENTAL=1`. Either alone is not enough.

Why two phases: `propose` is the reviewable artifact — it prints the parsed structure, the worst
case, the broker's own preflight, and every gate verdict. `confirm` then re-runs the gates and
re-preflights before submitting, so a market or account change between the two is caught rather than
ratified by a stale review.

**The submit path fails closed.** `confirm` takes a file lock across the cap check and the submit,
writes a `submitting` journal line (fsynced) before the order leaves, and refuses if that write
fails. Every submission carries `external_identifier = desk-<ticket>`, and a submit that raises is
resolved by reading today's orders back for that identifier — never by resubmitting.

**There is no `replace` command, on purpose.** `cancel` plus a fresh `propose`/`confirm` gets the
same result by composing two primitives this file already has to get right, rather than adding a
third authorization path. `cancel` is PIN-gated like `confirm` but evaluated through
`policy.evaluate_management`, which is exempt from the halt flag and the risk caps (pulling a resting
order only reduces exposure) while still checking the config and the account allowlist.

**Everything printed passes through the account redactor**, and every full account number this
process saw is masked by literal match as well, so a broker error string cannot leak one either.

**This CLI is never scheduled and never invoked by a loop.** `tests/test_isolation.py` asserts no
other package imports or launches it.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from typing import Any

from cherrypick.core import clock as _clock
from cherrypick.core.redact import mask_account, redact_accounts

from . import config as cfgmod
from . import journal, pin, policy, ticket
from .order import OrderError, analyze

DISCLAIMER = (
    "EXPERIMENTAL PROTOTYPE - educational use only - places REAL orders at your own risk; "
    "not financial advice. See DISCLAIMER.md."
)
DISABLED_MESSAGE = (
    "cherrypick-desk is EXPERIMENTAL and disabled. It places REAL, irreversible orders. Read "
    "packages/desk/README.md, then set enabled=true, allowed_accounts and allowed_underlyings in "
    "~/.cherrypick/config/desk.json, "
    "export CHERRYPICK_DESK_EXPERIMENTAL=1, and run `cherrypick-desk pin-set` interactively."
)
GATE_EXIT_CODE = 2
_SUBMIT_LOCK_STALE_SECONDS = 600

# Every full account number this process has seen, masked by literal match in all output.
_SEEN_ACCOUNTS: set[str] = set()


class DeskDisabled(RuntimeError):
    """One of the two gates is closed (or desk.json was refused)."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _note_account(number: str | None) -> str | None:
    if number:
        _SEEN_ACCOUNTS.add(str(number))
    return number


def _redact_text(text: str) -> str:
    text = redact_accounts(text)
    for number in sorted(_SEEN_ACCOUNTS, key=len, reverse=True):
        if len(number) >= 5:
            text = text.replace(number, mask_account(number))
    return text


def _gated_config() -> dict:
    """The resolved config, or DeskDisabled. Reads one file and the environment — nothing else —
    so a closed gate is reported before any network or keyring access."""
    cfg = cfgmod.resolve(cfgmod.load())
    reasons = cfgmod.gate_refusals(cfg)
    if reasons:
        raise DeskDisabled(reasons)
    return cfg


def _halt_present() -> bool:
    """The suite-wide kill switch, resolved by `cherrypick.core.home.halt_flag_path` — the one
    path every live loop and the orchestrator agree on, so no orchestrator import is needed."""
    from cherrypick.core import home as _home

    return _home.halt_flag_path().exists()


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
        "covering_only": risk.covering_only,
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
    _note_account(requested)
    try:
        import asyncio

        from cherrypick.core import broker as _broker

        from .session import get_session, reset

        reset()
        session = get_session(cfg)
        account = asyncio.run(_broker.resolve_account(session, requested))
        return _note_account(account.account_number)
    except Exception:  # noqa: BLE001 — unreachable broker must refuse, not crash or pass
        return None


def _resolve_masked_account(cfg: dict, masked: str | None) -> str | None:
    """The full account number behind a ticket's masked one: the single account on the session
    whose mask matches. None when there is no match, more than one, or no broker."""
    if not masked:
        return None
    try:
        import asyncio

        from cherrypick.core import broker as _broker

        from .session import get_session, reset

        reset()

        async def _run() -> list[dict]:
            return await _broker.list_accounts(get_session(cfg))

        numbers = [str(a.get("account_number") or "") for a in asyncio.run(_run())]
    except Exception:  # noqa: BLE001
        return None
    for n in numbers:
        _note_account(n)
    matches = [n for n in numbers if n and mask_account(n) == masked]
    return matches[0] if len(matches) == 1 else None


def _today() -> str:
    """The ET session date — what "today" means for the daily caps."""
    return _clock.today_iso()


@contextlib.contextmanager
def _submit_lock():
    """One desk submission at a time, held across the cap check and the submit so two confirms
    cannot both read the same tally and both spend it. Yields False when another holds it."""
    from cherrypick.core import looplock

    path = cfgmod.desk_dir() / "submit.lock"
    acquired = looplock.acquire(path, stale_seconds=_SUBMIT_LOCK_STALE_SECONDS)
    try:
        yield acquired
    finally:
        if acquired:
            looplock.release(path)


def _pin_gate(phase: str, **context: Any) -> dict | None:
    """Prompt for and check the PIN. None when it passed; otherwise the error result, journaled."""
    if not pin.is_set():
        return {"ok": False, "error": "no desk PIN configured — run `cherrypick-desk pin-set`"}
    try:
        pin.require(pin.prompt("Desk PIN: "))
    except pin.PinRejected as exc:
        if exc.outcome == pin.LOCKOUT:
            journal.record("pin_lockout", phase=phase, **context)
        journal.record("refused", phase=phase, reason=str(exc), **context)
        return {"ok": False, "error": str(exc)}
    except pin.PinError as exc:
        journal.record("refused", phase=phase, reason=str(exc), **context)
        return {"ok": False, "error": str(exc)}
    return None


# --------------------------------------------------------------------------- commands
def cmd_status(args) -> dict:
    cfg = cfgmod.resolve(cfgmod.load())
    day = _today()
    orders_today, risk_today = journal.today_totals(day)
    locked = pin.locked_until()
    return {
        "ok": True,
        "experimental": True,
        "disclaimer": DISCLAIMER,
        "gates_open": not cfgmod.gate_refusals(cfg),
        "gate_refusals": cfgmod.gate_refusals(cfg),
        "enabled": cfg["enabled"],
        "experimental_env_set": cfgmod.experimental_ack(),
        "config_errors": cfg["config_errors"],
        "pin_configured": pin.is_set(),
        "pin_locked": locked is not None,
        "halt_flag_present": _halt_present(),
        "allowed_accounts": [f"****{a}" for a in cfg["allowed_accounts"]],
        "allowed_underlyings": cfg["allowed_underlyings"],
        "require_defined_risk": cfg["require_defined_risk"],
        **{key: cfg[key] for key in cfgmod.CAPS},
        "session_date": day,
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
    cfg = _gated_config()
    spec = _load_order(args)
    legs, risk = analyze(spec)
    described = _describe(legs, risk)

    refusals: list[str] = []
    if not pin.is_set():
        refusals.append("no desk PIN configured — run `cherrypick-desk pin-set`")
    account = _resolve_account(cfg, args.account_number)
    orders_today, risk_today = journal.today_totals(_today())
    refusals += policy.evaluate(
        risk,
        cfg=cfg,
        halt_present=_halt_present(),
        account_number=account,
        orders_today=orders_today,
        risk_today=risk_today,
    )
    if refusals:
        journal.record("refused", phase="propose", account_number=account, refusals=refusals, **described)
        return {"ok": False, "error": "refused by desk policy", "refusals": refusals, **described}

    preflight = _preflight(cfg, spec, account)
    if not preflight.get("ok"):
        journal.record("refused", phase="preflight", account_number=account, reason=preflight, **described)
        return {"ok": False, "error": "broker preflight failed", "preflight": preflight, **described}
    bp_refusals = policy.evaluate_buying_power(risk, cfg=cfg, preflight=preflight)
    if bp_refusals:
        journal.record(
            "refused", phase="preflight", account_number=account, refusals=bp_refusals, **described
        )
        return {
            "ok": False,
            "error": "refused by desk policy",
            "refusals": bp_refusals,
            "preflight": preflight,
            **described,
        }

    try:
        record = ticket.create(
            spec,
            account,
            ttl_seconds=cfg["ticket_ttl_seconds"],
            extra={"max_loss": risk.max_loss, "classification": risk.classification},
        )
    except (ticket.TicketError, OSError) as exc:
        return {"ok": False, "error": f"could not create a ticket: {exc}"}
    journal.record(
        "proposed",
        account_number=account,
        ticket_id=record["ticket_id"],
        fingerprint=record["fingerprint"],
        **described,
    )
    return {
        "ok": True,
        "disclaimer": DISCLAIMER,
        "ticket_id": record["ticket_id"],
        "confirmation_code": record["code"],
        "expires_in_seconds": cfg["ticket_ttl_seconds"],
        "account": mask_account(account),
        "preflight": preflight,
        **described,
    }


def _preflight(cfg: dict, spec: dict, account_number: str | None) -> dict:
    """Broker dry-run. Returns a JSON-safe summary; never submits.

    Account resolution and the dry-run share one event loop: the borrowed session caches its async
    transport against the loop that first drives it, so a second `asyncio.run` here would find that
    transport bound to an already-closed loop.
    """
    try:
        import asyncio

        from cherrypick.core import broker as _broker

        from .session import get_session, reset, serialize

        reset()

        async def _run() -> dict:
            session = get_session(cfg)
            account = await _broker.resolve_account(session, account_number)
            order = _broker.build_order(spec)
            return await _broker.place_order(account, session, order, live=False, serialize=serialize)

        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def cmd_confirm(args) -> dict:
    cfg = _gated_config()
    ticket_id = args.ticket
    try:
        record = ticket.claim(ticket_id)
    except ticket.TicketError as exc:
        journal.record("refused", phase="confirm", reason=str(exc), ticket_id=str(ticket_id)[:40])
        return {"ok": False, "error": str(exc)}

    # The ticket is spent from here on, whatever happens below: a wrong code, a wrong PIN, a
    # refused gate and a failed submit all need a fresh proposal.
    try:
        return _confirm_claimed(cfg, record, args.code)
    finally:
        ticket.release(ticket_id)


def _confirm_claimed(cfg: dict, record: dict, code: str) -> dict:
    ticket_id = record["ticket_id"]
    try:
        ticket.check_fresh(record, max_ttl_seconds=cfg["ticket_ttl_seconds"])
    except ticket.TicketError as exc:
        journal.record("refused", phase="confirm", reason=str(exc), ticket_id=ticket_id)
        return {"ok": False, "error": str(exc)}

    denied = _pin_gate("confirm", ticket_id=ticket_id)
    if denied:
        return denied

    account = _resolve_masked_account(cfg, record.get("account"))
    if account is None:
        reason = f"could not resolve account {record.get('account')} at the broker"
        journal.record("refused", phase="confirm", reason=reason, ticket_id=ticket_id)
        return {"ok": False, "error": reason}
    try:
        ticket.check_binding(record, code, account)
    except ticket.TicketError as exc:
        journal.record("refused", phase="confirm", reason=str(exc), ticket_id=ticket_id)
        return {"ok": False, "error": str(exc)}

    spec = record["order"]
    try:
        legs, risk = analyze(spec)
    except OrderError as exc:
        return {"ok": False, "error": f"stored order no longer parses: {exc}"}
    described = _describe(legs, risk)

    with _submit_lock() as held:
        if not held:
            reason = "another desk submission is in progress — try again"
            journal.record("refused", phase="confirm", reason=reason, ticket_id=ticket_id)
            return {"ok": False, "error": reason}

        # Re-run every gate against CURRENT state, inside the lock. The proposal's verdict is not
        # carried forward: the halt flag may have appeared, the config may have changed, the daily
        # tally may have moved.
        orders_today, risk_today = journal.today_totals(_today())
        refusals = policy.evaluate(
            risk,
            cfg=cfg,
            halt_present=_halt_present(),
            account_number=account,
            orders_today=orders_today,
            risk_today=risk_today,
        )
        if refusals:
            journal.record(
                "refused",
                phase="confirm",
                account_number=account,
                refusals=refusals,
                ticket_id=ticket_id,
                **described,
            )
            return {"ok": False, "error": "refused by desk policy at confirm time", "refusals": refusals}

        preflight = _preflight(cfg, spec, account)
        refusals = (
            policy.evaluate_buying_power(risk, cfg=cfg, preflight=preflight)
            if preflight.get("ok")
            else ["broker preflight failed at confirm time"]
        )
        if refusals:
            journal.record(
                "refused",
                phase="confirm",
                account_number=account,
                refusals=refusals,
                ticket_id=ticket_id,
                reason=preflight if not preflight.get("ok") else None,
                **described,
            )
            return {
                "ok": False,
                "error": "refused at confirm time",
                "refusals": refusals,
                "preflight": preflight,
            }

        external_id = f"desk-{ticket_id}"
        common = {
            "account_number": account,
            "ticket_id": ticket_id,
            "fingerprint": record.get("fingerprint"),
            "external_identifier": external_id,
            **described,
        }
        try:
            journal.record_strict("submitting", **common)
        except journal.JournalError as exc:
            return {
                "ok": False,
                "error": f"refusing to submit: the journal could not record the attempt ({exc})",
            }

        result = _submit(cfg, {**spec, "external_identifier": external_id}, account)
        from cherrypick.core.execution import order_id_of

        order_id = order_id_of(result)
        if result.get("ok"):
            event = "submitted"
        elif result.get("uncertain"):
            event = "uncertain"
        else:
            event = "failed"
        journal.record(event, order_id=order_id, recovered=bool(result.get("recovered")), **common)

    out = {
        "ok": bool(result.get("ok")),
        "order_id": order_id,
        "external_identifier": external_id,
        "result": result,
        **described,
    }
    if event == "uncertain":
        out["error"] = (
            "the submit raised and today's orders could not be read back, so whether the order was "
            f"placed is UNKNOWN. Check `cherrypick-desk orders` or the broker for {external_id} before "
            "doing anything else."
        )
    return out


def _submit(cfg: dict, spec: dict, account_number: str | None) -> dict:
    """The one line where real money moves. Resolution, submission and any recovery read share a
    single event loop, for the reason spelled out in `_preflight`.

    tastytrade does not deduplicate retries, so a submit that raises may or may not have placed the
    order. The answer is read back from today's orders by `external_identifier`, the same protocol
    `cherrypick.core.execution` follows; the desk never resubmits."""
    external_id = str(spec.get("external_identifier") or "")
    reached_submit = False
    try:
        import asyncio

        from cherrypick.core import broker as _broker

        from .session import get_session, reset, serialize

        reset()

        async def _run() -> dict:
            nonlocal reached_submit
            session = get_session(cfg)
            account = await _broker.resolve_account(session, account_number)
            order = _broker.build_order(spec)
            reached_submit = True
            try:
                return await _broker.place_order(account, session, order, live=True, serialize=serialize)
            except Exception as exc:  # noqa: BLE001 — resolved by read-back below, never by a retry
                submit_error = f"{type(exc).__name__}: {exc}"
            try:
                today = await _broker.orders_today(account, session)
            except Exception as read_exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "uncertain": True,
                    "error": f"{submit_error}; read-back failed: {type(read_exc).__name__}: {read_exc}",
                }
            for o in today:
                if (
                    external_id
                    and o.get("external_identifier") == external_id
                    and o.get("order_id") is not None
                ):
                    return {
                        "ok": True,
                        "recovered": True,
                        "order_id": str(o["order_id"]),
                        "response": o,
                        "error": f"submit raised {submit_error}; order found at the broker by its identifier",
                    }
            return {"ok": False, "error": submit_error}

        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        # Before the submit call nothing was sent; after it (loop teardown) nothing is known.
        return {"ok": False, "uncertain": reached_submit, "error": f"{type(exc).__name__}: {exc}"}


def cmd_orders(args) -> dict:
    """Working orders on the resolved account. Read-only and PIN-free, but behind both gates like
    every command that talks to the broker."""
    cfg = _gated_config()
    account = _resolve_account(cfg, args.account_number)
    if account is None:
        return {"ok": False, "error": "could not resolve broker account"}
    try:
        import asyncio

        from cherrypick.core import broker as _broker

        from .session import get_session, reset

        reset()

        async def _run() -> list[dict]:
            session = get_session(cfg)
            acct = await _broker.resolve_account(session, account)
            return await _broker.working_orders(acct, session)

        orders = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "account": mask_account(account), "orders": orders}


def cmd_cancel(args) -> dict:
    """Pull a resting order. See the module docstring for why this exists in place of `replace`."""
    cfg = _gated_config()

    denied = _pin_gate("cancel", order_id=args.order_id)
    if denied:
        return denied

    account = _resolve_account(cfg, args.account_number)
    refusals = policy.evaluate_management(cfg=cfg, account_number=account)
    if refusals:
        journal.record(
            "refused", phase="cancel", account_number=account, order_id=args.order_id, refusals=refusals
        )
        return {"ok": False, "error": "refused by desk policy", "refusals": refusals}

    result = _cancel(cfg, args.order_id, account)
    journal.record(
        "cancelled" if result.get("ok") else "cancel_failed",
        account_number=account,
        order_id=args.order_id,
        result=result,
    )
    return {"ok": bool(result.get("ok")), "order_id": args.order_id, "result": result}


def _cancel(cfg: dict, order_id: int, account_number: str | None) -> dict:
    """Deletion, not submission — deliberately its own helper rather than routed through `_submit`,
    so the isolation suite's single-live-submit-site count stays meaningful."""
    try:
        import asyncio

        from cherrypick.core import broker as _broker

        from .session import get_session, reset

        reset()

        async def _run() -> dict:
            session = get_session(cfg)
            account = await _broker.resolve_account(session, account_number)
            return await _broker.cancel_order(account, session, order_id)

        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _pin_change_failed(phase: str, exc: pin.PinError) -> dict:
    if isinstance(exc, pin.PinRejected) and exc.outcome == pin.LOCKOUT:
        journal.record("pin_lockout", phase=phase)
    journal.record("refused", phase=phase, reason=str(exc))
    return {"ok": False, "error": str(exc)}


def cmd_pin_set(args) -> dict:
    try:
        current = pin.prompt("Current desk PIN: ") if pin.is_set() else None
        new = pin.prompt(f"New desk PIN (at least {pin.MIN_LENGTH} characters): ")
        if new != pin.prompt("Repeat the new desk PIN: "):
            return {"ok": False, "error": "the two entries did not match — PIN unchanged"}
        pin.set_pin(new, current=current)
    except pin.PinError as exc:
        return _pin_change_failed("pin-set", exc)
    journal.record("pin_set")
    return {"ok": True, "pin_configured": True}


def cmd_pin_clear(args) -> dict:
    if not pin.is_set():
        return {"ok": True, "pin_configured": False}
    try:
        pin.clear_pin(current=pin.prompt("Current desk PIN: "))
    except pin.PinError as exc:
        return _pin_change_failed("pin-clear", exc)
    journal.record("pin_cleared")
    return {"ok": True, "pin_configured": pin.is_set()}


def cmd_purge(args) -> dict:
    return {"ok": True, "purged": ticket.purge_expired()}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cherrypick-desk", description="Manual trading desk (EXPERIMENTAL)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("purge").set_defaults(func=cmd_purge)

    for name, fn in (("analyze", cmd_analyze), ("propose", cmd_propose)):
        sp = sub.add_parser(name)
        sp.add_argument("--order", required=True, help="JSON order spec, or '-' to read stdin")
        sp.add_argument("--account_number", default=None)
        sp.set_defaults(func=fn)

    sp = sub.add_parser("confirm", help="prompts for the PIN on the terminal")
    sp.add_argument("--ticket", required=True)
    sp.add_argument("--code", required=True)
    sp.set_defaults(func=cmd_confirm)

    sp = sub.add_parser("orders")
    sp.add_argument("--account_number", default=None)
    sp.set_defaults(func=cmd_orders)

    sp = sub.add_parser("cancel", help="prompts for the PIN on the terminal")
    sp.add_argument("--order-id", required=True, type=int)
    sp.add_argument("--account_number", default=None)
    sp.set_defaults(func=cmd_cancel)

    sub.add_parser(
        "pin-set", help="prompts on the terminal; needs the current PIN once one is set"
    ).set_defaults(func=cmd_pin_set)
    sub.add_parser("pin-clear", help="prompts for the current PIN").set_defaults(func=cmd_pin_clear)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
    except DeskDisabled as exc:
        lines = [DISABLED_MESSAGE, DISCLAIMER, *[f"  - {r}" for r in exc.reasons]]
        print(_redact_text("\n".join(lines)), file=sys.stderr)
        return GATE_EXIT_CODE
    except (OrderError, pin.PinError, ticket.TicketError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(_redact_text(json.dumps(result, indent=2, default=str)))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
