"""cherrypick.core.execution — the one live broker adapter and the fill primitives every live
loop shares.

`cherrypick.core.broker` is the submission seam: the single function that can place or replace a
live order, structurally preflight-then-submit, plus status / cancel / working orders / history /
alerts. What each module used to write AROUND that seam is what lives here (2026-09-17):

* **`Broker`** — the adapter. Holds ONE session and account for its lifetime, drives every SDK
  call on ONE process-wide event loop, drops and rebuilds the session on any broker error, and
  fails closed in the shape each caller can act on. It is the flies adapter hoisted verbatim,
  because that adapter's three docstrings each record a live incident (a per-instance loop that
  silently disabled placement for most of 2026-08-04; a half-built adapter that considered itself
  ready forever; an orphan sweep that reported success on a check it never ran). Those lessons
  were about to be re-learned by the next module to go live. meic's adapter shelled out to its own
  CLI and scraped stdout; earnings had none.
* **`order_id_of`** — the one extraction of an order id from a `place_order` result. There were
  three copies, and the flies one records that getting it wrong caused a real incident.
* **`fill_state`** — the one reading of a status row into `filled` / `working` / a terminal
  unfilled state, with the fill price parsed once. What a module DOES with a fill (which ledger
  columns, which journal row) stays module-side; how it reads the broker's answer does not.
* **`orphans`** — working orders the ledger has never heard of: broker truth against ledger
  belief, the sweep every live loop must run first.
* **`watch`** — the fill-watch loop skeleton: poll the pending order ids, each on a heartbeat,
  early when the caller's `touched` predicate says the market touched the working limit or a
  pushed alert named the order, until nothing is pending or the deadline passes. The predicate
  and the alert source are injected; the loop is not copied.

The module keeps: its order-spec builders (a butterfly and a condor are different specs), its
gates (`readiness` / `live_gates` -- paper/live isolation is a per-module contract), its ledger
schema, and its loop. Nothing here reads a module's config, and nothing here imports a module:
the session, the account, the gate and the serializer are handed in.

The desk is deliberately NOT built on this. Its authorization is per order and human -- a PIN and
a single-use ticket -- and `packages/desk/tests/test_isolation.py` forbids any automated package
from importing it. This layer is for loops the desk exists to keep separate from.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

from cherrypick.core import broker as _broker

#: Status strings (lower-cased) after which a working order will never fill.
TERMINAL_UNFILLED = frozenset({"cancelled", "rejected", "expired"})


# --------------------------------------------------------------------------- result readers
def order_id_of(result: dict | None) -> str | None:
    """The broker order id in a `place_order` / `replace_order` result, or None.

    The SDK's placed-order response serializes as `{"order": {"id": ...}, ...}` under `response`.
    Reading it in three places produced three readings; on 2026-07-30 one of them silently kept
    a rejected attempt's row under a stale id."""
    if not isinstance(result, dict):
        return None
    explicit = result.get("order_id")
    if explicit is not None:
        return str(explicit)
    response = result.get("response")
    order = response.get("order") if isinstance(response, dict) else None
    if isinstance(order, dict) and order.get("id") is not None:
        return str(order["id"])
    return None


def fill_state(status: dict | None, *, fallback_price: float | None = None) -> tuple[str, float | None]:
    """`("filled", price)`, `("working", None)`, or `(<terminal>, None)` for one status row.

    `price` is the absolute fill price when it parses; when the broker reports a fill without a
    parseable price the caller's `fallback_price` (its modeled price) is returned rather than a
    corrupting zero -- keeping the model beats keeping a wrong number. Terminal states are the
    lower-cased status string itself so a ledger can record WHICH way an order died."""
    state = str((status or {}).get("status") or "").strip().lower()
    if state == "filled":
        try:
            return "filled", abs(float((status or {}).get("price")))
        except (TypeError, ValueError):
            return "filled", fallback_price
    if state in TERMINAL_UNFILLED:
        return state, None
    return "working", None


# --------------------------------------------------------------------------- the adapter
class Broker:
    """The real submission seam over core.broker, holding ONE session/account for its lifetime
    (a per-call session build was ~1 OAuth handshake per broker op). All calls run on one event
    loop so the SDK's async client stays bound to a single loop. On any broker exception the
    session is dropped and rebuilt once on the next call.

    **The loop is process-wide (`_shared_loop`), not per-instance.** A module's `get_session` is
    a process-global cached SessionManager, so every adapter in a tick gets the SAME session
    object -- and a tastytrade session's asyncio primitives bind to whichever loop first drives
    them. A per-instance loop therefore broke the moment a tick built a second adapter: adapter #2
    drove adapter #1's cached session on a different loop and the SDK raised `RuntimeError:
    <asyncio.locks.Event ...> is bound to a different event loop`. One cached session must mean
    one loop. Measured on 2026-08-04 in flies: this fired on EVERY live tick and, via the
    half-built state `_ensure` used to leave behind, silently disabled live order placement for
    most of the session -- 8 entries the engine wanted were never submitted.

    Injected, never imported: `get_session()` -> the module's cached session; `designated_account()`
    -> the account number it is designated to trade (None refuses); `live_gates()` -> the unmet
    live gates as a list of strings (empty means a live submit may proceed) -- re-checked on EVERY
    live placement, so a gate flipped mid-session bites on the next order; `serialize` -> the
    module's JSON shaper for SDK objects (the 2026-07-30 regression: forgetting it leaves raw SDK
    objects in the result); `deploy_limit_pct` -> the account deploy governor cap, or None.
    """

    _shared_loop: asyncio.AbstractEventLoop | None = None

    def __init__(
        self,
        *,
        get_session: Callable[[], Any],
        designated_account: Callable[[], str | None],
        live_gates: Callable[[], list[str]] | None = None,
        serialize: Callable[[Any], Any] | None = None,
        deploy_limit_pct: float | None = None,
    ):
        self._get_session = get_session
        self._designated_account = designated_account
        self._live_gates = live_gates or (lambda: [])
        self._serialize = serialize
        self._deploy_limit_pct = deploy_limit_pct
        self._session = None
        self._account = None
        # Identities whose outcome is unknown (the submit raised AND the read-back failed), and
        # identities the read-back later FOUND at the broker that no caller recorded. Either one
        # refuses every further live submission from this adapter -- see `place`.
        self._unresolved: dict[str, str] = {}
        self._unrecorded: dict[str, dict] = {}

    # --- lifecycle -------------------------------------------------------------------------
    def run(self, coro):
        """Drive a coroutine on the process-wide loop. Public so a module can run its OWN
        broker-side coroutine (a REST quote refresh, a settlement-print fetch) on the same loop
        the cached session is bound to, without a second adapter."""
        cls = type(self)
        if cls._shared_loop is None:
            cls._shared_loop = asyncio.new_event_loop()
        return cls._shared_loop.run_until_complete(coro)

    def _ensure(self):
        """Build session+account, or raise having changed nothing.

        Atomic on purpose: the old form assigned `self._session` and then `self._account` on the
        next line, so a raise in between left the adapter half-built -- session set, account None.
        Its guard was `if self._session is None`, so it then considered itself ready forever and
        every later `place()` handed `None` to `core.broker.place_order`, which surfaced as
        `AttributeError: 'NoneType' object has no attribute 'place_order'` rather than as the
        broker error it really was. Locals first, `self` only once both succeed, and the guard
        checks BOTH fields so a half-built adapter repairs itself on the next call."""
        if self._session is not None and self._account is not None:
            return
        try:
            session = self._get_session()
            account = self.run(_broker.resolve_account(session, self._designated_account()))
        except Exception:
            self._reset()
            raise
        self._session, self._account = session, account

    def _reset(self):
        self._session = None
        self._account = None

    @property
    def session(self):
        self._ensure()
        return self._session

    @property
    def account(self):
        self._ensure()
        return self._account

    # --- submission ------------------------------------------------------------------------
    def place(self, spec: dict, live: bool) -> dict:
        """Build and submit `spec` (dry-run unless `live`). A live submit re-checks the module's
        gates first. The result is core.broker's, with `order_id` extracted when one came back.
        Any broker exception is surfaced as `{ok: False, error}` and the session rebuilt next call.

        **Every live submission carries an `external_identifier`, and an uncertain outcome is
        recovered by it (2026-09-17).** tastytrade does not deduplicate retries and has no
        idempotency header: a timeout or a 5xx after the broker has accepted the order looks, from
        here, exactly like a failure before it, and a caller that resubmits on that has placed the
        same order twice. So the seam stamps its own identity on the order before submitting (the
        spec's `external_identifier` if the module supplied one, else one minted here), and when
        the live submit raises it reads back today's orders -- terminal ones included, since the
        order may have filled in the meantime -- and, finding that identity, returns the placement
        as OK with the broker's order id and `recovered: True`. Only when nothing at the broker
        carries the identity is the raise reported as a failure. The identity rides on the result
        as `external_identifier` so a ledger can store it beside the order id.

        **An outcome that could not be read back holds the adapter (2026-09-17).** tastytrade's
        retry protocol is: never resubmit until a read of today's orders has shown the identity
        absent. When the submit raised and the read-back raised too, this adapter knows neither
        whether the order exists nor that it does not, so it reports `{ok: False, uncertain:
        True}` and REFUSES every later live submission until a read succeeds. If that read finds
        the identity absent, the hold lifts and the new submission proceeds. If it finds the
        order -- placed, and recorded nowhere, because the caller was told it failed -- the
        adapter keeps refusing, naming the order, until `acknowledge()` is called for it: a live
        order this process cannot account for is a human's call, and a loop that kept placing
        beside it would be the duplicate the protocol exists to prevent. Dry runs are never
        held; they place nothing."""
        if live:
            unmet = self._live_gates()
            if unmet:
                return {"ok": False, "error": "live submission gated", "unmet_gates": list(unmet)}
            held = self._resolve_held()
            if held is not None:
                return held
        ext = str(spec.get("external_identifier") or f"cp-{uuid.uuid4().hex}")
        spec = {**spec, "external_identifier": ext}
        try:
            self._ensure()
            order = _broker.build_order(spec)
            result = self.run(
                _broker.place_order(
                    self._account,
                    self._session,
                    order,
                    live=live,
                    serialize=self._serialize,
                    deploy_limit_pct=self._deploy_limit_pct,
                )
            )
        except Exception as exc:  # noqa: BLE001 -- surfaced to the caller, session rebuilt next call
            self._reset()
            if live:
                recovered, read_ok = self._recover(ext)
                if recovered is not None:
                    return {
                        "ok": True,
                        "dry_run": False,
                        "recovered": True,
                        "order_id": str(recovered["order_id"]),
                        "external_identifier": ext,
                        "response": recovered,
                        "error": (
                            f"submit raised {type(exc).__name__}: {exc}; "
                            "order found at the broker by its identifier"
                        ),
                    }
                if not read_ok:
                    self._unresolved[ext] = f"{type(exc).__name__}: {exc}"
                    return {
                        "ok": False,
                        "uncertain": True,
                        "error": (
                            f"{type(exc).__name__}: {exc}; and today's orders could not be read back, so "
                            f"whether {ext} was placed is unknown -- held; no live submission until a read "
                            "resolves it"
                        ),
                        "external_identifier": ext,
                    }
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "external_identifier": ext}
        result["external_identifier"] = ext
        oid = order_id_of(result)
        if oid is not None:
            result["order_id"] = oid
        return result

    def _recover(self, external_identifier: str) -> tuple[dict | None, bool]:
        """`(today's order carrying the identity or None, whether the read succeeded)`. Absent
        and unreadable are different answers -- only the first permits a resubmission -- so a
        read that itself fails is `(None, False)`, never a phantom absence."""
        try:
            self._ensure()
            orders = self.run(_broker.orders_today(self._account, self._session))
        except Exception:  # noqa: BLE001
            self._reset()
            return None, False
        for o in orders:
            if o.get("external_identifier") == external_identifier and o.get("order_id") is not None:
                return o, True
        return None, True

    def _resolve_held(self) -> dict | None:
        """Resolve every held identity against one read of today's orders. Returns the refusal
        to hand back in place of a submission, or None when nothing holds the adapter."""
        if self._unresolved:
            try:
                self._ensure()
                orders = self.run(_broker.orders_today(self._account, self._session))
            except Exception as exc:  # noqa: BLE001
                self._reset()
                return {
                    "ok": False,
                    "uncertain": True,
                    "error": (
                        f"prior submission(s) {sorted(self._unresolved)} unresolved and today's orders "
                        f"still unreadable ({type(exc).__name__}: {exc}) -- refusing to submit"
                    ),
                    "unresolved": sorted(self._unresolved),
                }
            by_ext = {o.get("external_identifier"): o for o in orders if o.get("order_id") is not None}
            for ext in list(self._unresolved):
                found = by_ext.get(ext)
                if found is not None:
                    self._unrecorded[ext] = found
                del self._unresolved[ext]
        if self._unrecorded:
            listing = ", ".join(f"{ext} -> order {o.get('order_id')}" for ext, o in self._unrecorded.items())
            return {
                "ok": False,
                "unrecorded": {ext: str(o.get("order_id")) for ext, o in self._unrecorded.items()},
                "error": (
                    "prior submission(s) reported failed were FOUND at the broker, recorded nowhere: "
                    f"{listing} -- refusing every live submission until acknowledged"
                ),
            }
        return None

    def acknowledge(self, external_identifier: str) -> bool:
        """Lift the hold on an unrecorded order a caller has now accounted for (recorded, or
        cancelled at the broker by a human). True if it was held."""
        return self._unrecorded.pop(external_identifier, None) is not None

    @property
    def held(self) -> dict:
        """What currently refuses live submissions: `{unresolved: [...], unrecorded: {ext: order_id}}`."""
        return {
            "unresolved": sorted(self._unresolved),
            "unrecorded": {ext: str(o.get("order_id")) for ext, o in self._unrecorded.items()},
        }

    def orders_today(self) -> list[dict]:
        """Every order placed today, terminal ones included, each with its external identifier."""
        try:
            self._ensure()
            return self.run(_broker.orders_today(self._account, self._session))
        except Exception:
            self._reset()
            raise

    def replace(self, order_id: str, spec: dict, live: bool) -> dict:
        """Replace a working order with `spec`, on the same gated, governed path as `place`."""
        if live:
            unmet = self._live_gates()
            if unmet:
                return {"ok": False, "error": "live submission gated", "unmet_gates": list(unmet)}
        try:
            self._ensure()
            order = _broker.build_order(spec)
            return self.run(
                _broker.replace_order(
                    self._account,
                    self._session,
                    order_id,
                    order,
                    live=live,
                    serialize=self._serialize,
                    deploy_limit_pct=self._deploy_limit_pct,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self._reset()
            return {"ok": False, "order_id": order_id, "error": f"{type(exc).__name__}: {exc}"}

    # --- order lifecycle -------------------------------------------------------------------
    def status(self, order_id: str) -> dict:
        try:
            self._ensure()
            return self.run(_broker.order_status(self._account, self._session, order_id))
        except Exception as exc:  # noqa: BLE001
            self._reset()
            return {"order_id": order_id, "status": None, "error": f"{type(exc).__name__}: {exc}"}

    def cancel(self, order_id: str) -> dict:
        try:
            self._ensure()
            return self.run(_broker.cancel_order(self._account, self._session, order_id))
        except Exception as exc:  # noqa: BLE001
            self._reset()
            return {"ok": False, "order_id": order_id, "error": f"{type(exc).__name__}: {exc}"}

    def working_orders(self) -> list[dict]:
        """Drops the session on any error, then **re-raises** -- deliberately not the `return []`
        every sibling here uses. In flies this was the one method with no try/except at all,
        which made it the leak that poisoned the adapter: the orphan sweep calls it first on
        every tick and catches its own exceptions, so a raise here escaped without ever running
        `_reset()`, and the half-built adapter it left behind is what broke `place()` later in
        the SAME tick.

        It re-raises rather than failing closed to `[]` because this caller's failure must stay
        LOUD: swallowing the error into an empty list would render as a clean 'no orphans', which
        is precisely the sweep reporting success on a check it never performed. `[]` is the one
        return value this method must never invent."""
        try:
            self._ensure()
            return self.run(_broker.working_orders(self._account, self._session))
        except Exception:
            self._reset()
            raise

    def wait_for_order_alerts(self, order_ids: Iterable[str], timeout_seconds: float) -> list[dict]:
        """Block (up to `timeout_seconds`) for PUSHED fill/cancel/reject updates on `order_ids`
        via the account-alert websocket. Same row shape as `.status()`. Fails closed to `[]` on
        any error -- the caller's own heartbeat poll is the safety net that makes this an
        optimization, not a dependency."""
        try:
            self._ensure()
            return self.run(
                _broker.wait_for_order_alerts(self._session, self._account, set(order_ids), timeout_seconds)
            )
        except Exception:  # noqa: BLE001
            self._reset()
            return []

    def history(self, trade_date: str, symbol: str | None = None) -> tuple[list[dict] | None, str | None]:
        """Real broker transactions for one session (the fee-reconciliation source of truth).
        Fails closed: any error returns `(None, reason)`, which a caller treats as "try again
        next tick" -- never a reason to reconcile from an empty or partial list."""
        try:
            self._ensure()
            d = date.fromisoformat(trade_date)
            transactions = self.run(
                _broker.transaction_history(
                    self._account, self._session, start_date=d, underlying_symbol=symbol
                )
            )
            return transactions, None
        except Exception as exc:  # noqa: BLE001 -- fail-closed, see docstring
            self._reset()
            return None, f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- sweeps and watches
def orphans(broker: Any, known_order_ids: Iterable[Any]) -> list[dict]:
    """Working orders at the broker that `known_order_ids` (the ledger's) does not contain.

    Broker truth against ledger belief, first thing on every live tick. Raises what
    `working_orders` raises: a sweep that cannot run must not report 'no orphans'."""
    known = {str(k) for k in known_order_ids if k is not None}
    return [o for o in broker.working_orders() if str(o.get("order_id")) not in known]


def watch(
    broker: Any,
    pending: dict[str, Any],
    *,
    deadline: float,
    poll_seconds: float,
    heartbeat_seconds: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    touched: Callable[[str, Any], bool] | None = None,
    alerts: Callable[[set[str], float], Iterable[dict]] | None = None,
    on_status: Callable[[str, Any, dict], bool],
) -> int:
    """The fill-watch loop. `pending` maps order id -> the caller's own handle (a position row);
    `on_status(order_id, handle, status)` receives each fresh status row and returns True when
    that order is resolved (filled or terminal) and should stop being watched. Returns how many
    were resolved.

    Cache-gated on purpose (streamer before API): an order is polled EARLY only when `touched`
    says the cached market touched its working limit, or a pushed alert (from `alerts`, the
    module's daemon inbox or the websocket) named it; otherwise it is polled on the heartbeat. A
    push is treated exactly like a cache touch -- still confirmed through `.status()`, never a
    second, divergent write path. This is the flies watcher's loop with its two predicates
    injected, so the next module gets the same behaviour without the same 150 lines."""
    start = clock()
    last_poll: dict[str, float] = {}
    resolved = 0
    while pending and clock() < deadline:
        ids = set(pending)
        alerted: set[str] = set()
        if alerts is not None:
            try:
                alerted = {str(a.get("order_id")) for a in (alerts(ids, poll_seconds) or [])}
            except Exception:  # noqa: BLE001 -- an alert source is an accelerator, never a dependency
                alerted = set()
        for oid in list(pending):
            handle = pending[oid]
            hit = True if touched is None else bool(touched(oid, handle))
            hit = hit or oid in alerted
            due = clock() - last_poll.get(oid, start) >= heartbeat_seconds
            if not hit and not due:
                continue
            last_poll[oid] = clock()
            status = broker.status(oid)
            if on_status(oid, handle, status):
                resolved += 1
                del pending[oid]
        if pending:
            remaining = deadline - clock()
            if remaining > 0:
                sleep(min(poll_seconds, remaining))
    return resolved
