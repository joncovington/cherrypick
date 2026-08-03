"""Order dry-run validation and staged-ticket persistence -- the builder's "Validate with broker"
and "Stage" actions.

**This is the single broker-order call site in the whole package.** `dry_run_order` calls
`cherrypick.core.broker.place_order(..., live=False, ...)` with `live=False` a hardcoded literal --
never threaded through as a variable, a config value, or a request field. Every mutating caller
(`api/orders.py`'s dry-run route, and `stage_ticket` below) goes through this one function, so there
is exactly one place in this package that can ever reach the SDK's order-placement call, and it can
never submit live. `test_dry_run_only.py` both exercises this with a faked broker and source-scans
the package for that invariant.

Staging must not depend on validation succeeding (see the plan's risk #9): `stage_ticket` always
saves the ticket, even when `dry_run_order` itself fails (no credentials, a network hiccup, a
preflight rejection, an SDK response-shape drift) -- the failure is recorded as the ticket's
`dry_run` field rather than blocking the save. A research session shouldn't lose a ticket to a
broker hiccup on something the user just wants to park for later.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from cherrypick.core import broker as _broker

from .session import BrokerSession


def _mask_account(value: Any) -> str:
    s = str(value or "")
    return f"****{s[-4:]}" if len(s) >= 4 else "****"


def _serialize(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return str(obj)


def _leg_action(quantity: float) -> str:
    return "buy to open" if quantity > 0 else "sell to open"


def build_order_spec(legs: list[dict]) -> dict:
    """A leg basket (each ``{"symbol", "quantity", "price"}``; ``symbol`` is the OCC option symbol
    from `chain_service`, ``quantity`` signed -- positive long/buy, negative short/sell; ``price`` per
    share) -> `cherrypick.core.broker.build_order`'s dict spec. Every leg is opening (the builder
    stages new positions, never closes existing ones) and every leg is an option -- the builder has
    no stock-leg picker yet. Net price is per share, matching the SDK's own order-price convention --
    not the *100 dollar totals the screener/builder display panels show."""
    order_legs = [
        {
            "instrument_type": "Equity Option",
            "symbol": leg["symbol"],
            "action": _leg_action(leg["quantity"]),
            "quantity": abs(leg["quantity"]),
        }
        for leg in legs
    ]
    spec: dict = {"legs": order_legs}
    net = -sum(leg["quantity"] * leg["price"] for leg in legs)
    if net != 0:
        spec["price"] = round(abs(net), 2)
        spec["price_effect"] = "credit" if net >= 0 else "debit"
    return spec


async def dry_run_order(broker_session: BrokerSession, legs: list[dict]) -> dict:
    """Validate a leg basket against the broker's dry-run preflight -- buying-power effect, fees,
    warnings, **no order created**. Never raises: any failure (missing credentials, no accounts on
    the login, a network hiccup, an SDK response-shape change) comes back as
    ``{"ok": False, "error": ...}`` instead of propagating, so neither caller needs its own broad
    except around this. The account resolved is simply the first on the login (scout has no stored
    account-designation config of its own, unlike the trading modules) -- account numbers are masked
    before this ever returns."""
    if not legs:
        return {"ok": False, "error": "no legs to validate"}
    spec = build_order_spec(legs)

    async def _run(session: Any) -> dict:
        account = await _broker.resolve_account(session)
        order = _broker.build_order(spec)
        return await _broker.place_order(account, session, order, live=False, serialize=_serialize)

    try:
        result = await broker_session.call(_run)
    except Exception as exc:  # noqa: BLE001 -- soft-fail by design, see module/function docstrings
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if result.get("account_number") is not None:
        result["account_number"] = _mask_account(result["account_number"])
    return result


def _row_to_dict(row: tuple) -> dict:
    (id_, created_at, symbol, strategy, legs_json, credit, max_risk, dry_run_json, note, status) = row
    return {
        "id": id_,
        "created_at": created_at,
        "symbol": symbol,
        "strategy": strategy,
        "legs": json.loads(legs_json),
        "credit": credit,
        "max_risk": max_risk,
        "dry_run": json.loads(dry_run_json) if dry_run_json else None,
        "note": note,
        "status": status,
    }


def list_staged(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, created_at, symbol, strategy, legs_json, credit, max_risk, dry_run_json, note, "
        "status FROM staged_orders ORDER BY created_at DESC"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


async def stage_ticket(
    conn: sqlite3.Connection,
    broker_session: BrokerSession,
    *,
    symbol: str,
    strategy: str,
    legs: list[dict],
    credit: float | None,
    max_risk: float | None,
    note: str | None,
    now: float | None = None,
) -> dict:
    """Validate (best-effort -- see `dry_run_order`) and persist a staged ticket. Always saves, even
    when the dry-run itself failed."""
    dry_run = await dry_run_order(broker_session, legs)
    ticket_id = uuid.uuid4().hex
    created_at = time.time() if now is None else now
    conn.execute(
        "INSERT INTO staged_orders (id, created_at, symbol, strategy, legs_json, credit, max_risk, "
        "dry_run_json, note, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'staged')",
        (
            ticket_id,
            created_at,
            symbol,
            strategy,
            json.dumps(legs),
            credit,
            max_risk,
            json.dumps(dry_run),
            note,
        ),
    )
    conn.commit()
    return _row_to_dict(
        (
            ticket_id,
            created_at,
            symbol,
            strategy,
            json.dumps(legs),
            credit,
            max_risk,
            json.dumps(dry_run),
            note,
            "staged",
        )
    )


def delete_staged(conn: sqlite3.Connection, ticket_id: str) -> bool:
    cur = conn.execute("DELETE FROM staged_orders WHERE id = ?", (ticket_id,))
    conn.commit()
    return cur.rowcount > 0
