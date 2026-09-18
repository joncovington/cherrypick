"""cherrypick.core.execution — the shared live adapter and fill primitives.

The three adapter tests are the flies incident tests (2026-08-04, 2026-07-30) moved here with
the code they pin: a per-instance loop, a half-built adapter, and a swallowed orphan sweep each
silently broke live placement once, and the point of hoisting the adapter is that the next
module to go live inherits the fix AND the test.
"""

from __future__ import annotations

import pytest

from cherrypick.core import broker as _broker
from cherrypick.core import execution


def _adapter(monkeypatch, *, account=None, gates=None, **kw):
    sentinel = account if account is not None else object()

    async def resolve_ok(session, number):
        return sentinel

    monkeypatch.setattr(_broker, "resolve_account", resolve_ok)
    return execution.Broker(
        get_session=lambda: object(),
        designated_account=lambda: "****0000",
        live_gates=gates,
        **kw,
    )


@pytest.fixture(autouse=True)
def _fresh_loop():
    execution.Broker._shared_loop = None
    yield
    if execution.Broker._shared_loop is not None:
        execution.Broker._shared_loop.close()
    execution.Broker._shared_loop = None


# --------------------------------------------------------------------------- result readers
def test_order_id_is_read_one_way():
    assert execution.order_id_of({"response": {"order": {"id": 123}}}) == "123"
    assert execution.order_id_of({"order_id": "abc", "response": {"order": {"id": 1}}}) == "abc"
    assert execution.order_id_of({"response": {"order": {}}}) is None
    assert execution.order_id_of({"response": "raw sdk object"}) is None
    assert execution.order_id_of(None) is None


def test_fill_state_reads_filled_working_and_terminal():
    assert execution.fill_state({"status": "Filled", "price": "-2.45"}) == ("filled", 2.45)
    assert execution.fill_state({"status": "filled", "price": None}, fallback_price=2.0) == ("filled", 2.0)
    assert execution.fill_state({"status": "Live", "price": None}) == ("working", None)
    assert execution.fill_state({"status": "Cancelled"}) == ("cancelled", None)
    assert execution.fill_state({"status": "REJECTED"}) == ("rejected", None)
    assert execution.fill_state({"status": None, "error": "boom"}) == ("working", None)
    assert execution.fill_state(None) == ("working", None)


# --------------------------------------------------------------------------- the adapter
def test_every_adapter_in_a_process_shares_one_event_loop(monkeypatch):
    """One process-global cached session must mean exactly one loop, or the SDK's asyncio
    primitives raise the moment a second adapter drives the session the first one built."""
    a, b = _adapter(monkeypatch), _adapter(monkeypatch)

    async def noop():
        return "ran"

    assert a.run(noop()) == "ran"
    assert b.run(noop()) == "ran"
    assert a.run(noop()) == "ran"
    assert execution.Broker._shared_loop is not None


def test_ensure_leaves_nothing_behind_when_account_resolution_raises(monkeypatch):
    """The bug: `_ensure` set `self._session`, then raised resolving the account, leaving
    session-set/account-None -- and its guard then considered the adapter ready forever."""

    async def resolve_raises(session, number):
        raise RuntimeError("<asyncio.locks.Event ...> is bound to a different event loop")

    adapter = execution.Broker(get_session=lambda: object(), designated_account=lambda: "x")
    monkeypatch.setattr(_broker, "resolve_account", resolve_raises)
    with pytest.raises(RuntimeError):
        adapter._ensure()
    assert adapter._session is None and adapter._account is None

    sentinel = object()

    async def resolve_ok(session, number):
        return sentinel

    monkeypatch.setattr(_broker, "resolve_account", resolve_ok)
    adapter._ensure()
    assert adapter._account is sentinel


def test_ensure_rebuilds_a_half_built_adapter(monkeypatch):
    sentinel = object()
    adapter = _adapter(monkeypatch, account=sentinel)
    adapter._session, adapter._account = object(), None  # the poisoned shape
    adapter._ensure()
    assert adapter._account is sentinel


def test_working_orders_resets_the_session_and_stays_loud(monkeypatch):
    async def working_raises(account, session):
        raise RuntimeError("bound to a different event loop")

    monkeypatch.setattr(_broker, "working_orders", working_raises)
    adapter = _adapter(monkeypatch)
    adapter._session, adapter._account = object(), object()
    with pytest.raises(RuntimeError):
        adapter.working_orders()
    assert adapter._session is None and adapter._account is None


def test_place_rechecks_the_gates_on_every_live_submit_and_passes_the_serializer(monkeypatch):
    seen = {}

    async def fake_place(account, session, order, *, live, serialize, deploy_limit_pct, get_balances=None):
        seen.update(live=live, serialize=serialize, deploy=deploy_limit_pct)
        return {"ok": True, "dry_run": not live, "response": {"order": {"id": 77}}}

    monkeypatch.setattr(_broker, "place_order", fake_place)
    monkeypatch.setattr(_broker, "build_order", lambda spec: spec)
    gates: list[str] = []
    ser = lambda x: x  # noqa: E731
    adapter = _adapter(monkeypatch, gates=lambda: gates, serialize=ser, deploy_limit_pct=50)

    out = adapter.place({"legs": []}, live=True)
    assert out["ok"] and out["order_id"] == "77"
    assert seen == {"live": True, "serialize": ser, "deploy": 50}

    gates.append("halt flag present")  # a gate flipped mid-session bites on the NEXT order
    out = adapter.place({"legs": []}, live=True)
    assert out == {"ok": False, "error": "live submission gated", "unmet_gates": ["halt flag present"]}
    # ...but never a dry run
    assert adapter.place({"legs": []}, live=False)["ok"] is True


def test_place_surfaces_a_broker_exception_and_rebuilds_next_call(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("session expired")

    monkeypatch.setattr(_broker, "place_order", boom)
    monkeypatch.setattr(_broker, "build_order", lambda spec: spec)
    adapter = _adapter(monkeypatch)
    out = adapter.place({}, live=False)
    assert out["ok"] is False and "session expired" in out["error"]
    assert adapter._session is None, "dropped so the next call rebuilds"


def test_replace_goes_through_the_same_gated_path(monkeypatch):
    calls = []

    async def fake_replace(
        account, session, order_id, order, *, live, serialize, deploy_limit_pct, get_balances=None
    ):
        calls.append((order_id, live))
        return {"ok": True, "dry_run": not live, "order_id": order_id}

    monkeypatch.setattr(_broker, "replace_order", fake_replace)
    monkeypatch.setattr(_broker, "build_order", lambda spec: spec)
    adapter = _adapter(monkeypatch, gates=lambda: ["live.enabled is false"])
    assert adapter.replace("OID", {}, live=True)["error"] == "live submission gated"
    assert adapter.replace("OID", {}, live=False)["ok"] is True
    assert calls == [("OID", False)]


# --------------------------------------------------------------------------- sweeps and watches
class _FakeBroker:
    def __init__(self, working=(), statuses=None):
        self._working = list(working)
        self._statuses = dict(statuses or {})
        self.polled: list[str] = []

    def working_orders(self):
        return list(self._working)

    def status(self, oid):
        self.polled.append(oid)
        return self._statuses.get(oid, {"order_id": oid, "status": "Live"})


def test_orphans_are_working_orders_the_ledger_never_heard_of():
    b = _FakeBroker(working=[{"order_id": "1"}, {"order_id": 2}, {"order_id": "3"}])
    assert execution.orphans(b, ["1", 3, None]) == [{"order_id": 2}]
    assert execution.orphans(b, []) == b._working


def test_orphans_never_invents_a_clean_sweep():
    class Loud(_FakeBroker):
        def working_orders(self):
            raise RuntimeError("bound to a different event loop")

    with pytest.raises(RuntimeError):
        execution.orphans(Loud(), [])


def test_watch_polls_on_touch_or_alert_or_heartbeat_and_stops_when_resolved():
    """Shown to fail without each rule: an untouched, unalerted order is NOT polled before its
    heartbeat; a touch polls it now; an alert polls it now; a resolved order leaves `pending`."""
    t = {"now": 0.0}
    b = _FakeBroker(statuses={"A": {"status": "Live"}, "B": {"status": "Filled", "price": "1.2"}})
    pending = {"A": "posA", "B": "posB"}
    touched_now = {"A": False, "B": False}
    alerts_now: set[str] = set()
    resolved_ids: list[str] = []

    def on_status(oid, handle, status):
        state, _ = execution.fill_state(status)
        if state != "working":
            resolved_ids.append(oid)
            return True
        return False

    def tick(seconds):
        t["now"] += seconds

    # First pass: nothing touched, nothing alerted, heartbeat not due -> no polls at all.
    n = execution.watch(
        b,
        dict(pending),
        deadline=1.0,
        poll_seconds=1.0,
        heartbeat_seconds=100.0,
        clock=lambda: t["now"],
        sleep=tick,
        touched=lambda oid, h: touched_now[oid],
        alerts=lambda ids, wait: [{"order_id": a} for a in alerts_now],
        on_status=on_status,
    )
    assert n == 0 and b.polled == []

    # A touch on A polls A only; A is still working, so it stays pending.
    t["now"] = 0.0
    touched_now["A"] = True
    execution.watch(
        b,
        dict(pending),
        deadline=0.5,
        poll_seconds=1.0,
        heartbeat_seconds=100.0,
        clock=lambda: t["now"],
        sleep=tick,
        touched=lambda oid, h: touched_now[oid],
        alerts=lambda ids, wait: [],
        on_status=on_status,
    )
    assert b.polled == ["A"] and resolved_ids == []

    # An alert naming B polls B, which is filled -> resolved, and the loop ends with nothing pending.
    b.polled.clear()
    t["now"] = 0.0
    touched_now["A"] = False
    alerts_now.add("B")
    live = {"B": "posB"}
    n = execution.watch(
        b,
        live,
        deadline=5.0,
        poll_seconds=1.0,
        heartbeat_seconds=100.0,
        clock=lambda: t["now"],
        sleep=tick,
        touched=lambda oid, h: False,
        alerts=lambda ids, wait: [{"order_id": a} for a in alerts_now],
        on_status=on_status,
    )
    assert n == 1 and b.polled == ["B"] and live == {} and resolved_ids == ["B"]

    # The heartbeat alone polls an untouched order once it is due.
    b.polled.clear()
    t["now"] = 0.0
    execution.watch(
        b,
        {"A": "posA"},
        deadline=3.0,
        poll_seconds=1.0,
        heartbeat_seconds=2.0,
        clock=lambda: t["now"],
        sleep=tick,
        touched=lambda oid, h: False,
        alerts=None,
        on_status=on_status,
    )
    assert b.polled == ["A"]


def test_watch_treats_a_failing_alert_source_as_an_accelerator_not_a_dependency():
    b = _FakeBroker(statuses={"A": {"status": "Filled", "price": "1"}})
    t = {"now": 0.0}

    def bad_alerts(ids, wait):
        raise RuntimeError("websocket down")

    n = execution.watch(
        b,
        {"A": "p"},
        deadline=1.0,
        poll_seconds=1.0,
        heartbeat_seconds=0.0,
        clock=lambda: t["now"],
        sleep=lambda s: t.__setitem__("now", t["now"] + s),
        touched=None,
        alerts=bad_alerts,
        on_status=lambda oid, h, s: execution.fill_state(s)[0] != "working",
    )
    assert n == 1


# --------------------------------------------------------------------------- idempotent submits (2026-09-17)
def test_every_live_submit_carries_an_external_identifier_and_returns_it(monkeypatch):
    seen = {}

    async def fake_place(account, session, order, *, live, serialize, deploy_limit_pct, get_balances=None):
        seen["order"] = order
        return {"ok": True, "dry_run": not live, "response": {"order": {"id": 1}}}

    monkeypatch.setattr(_broker, "place_order", fake_place)
    monkeypatch.setattr(_broker, "build_order", lambda spec: dict(spec))
    adapter = _adapter(monkeypatch)
    out = adapter.place({"legs": []}, live=True)
    assert (
        out["external_identifier"].startswith("cp-")
        and seen["order"]["external_identifier"] == out["external_identifier"]
    )
    # a module's own identity is kept verbatim
    out = adapter.place({"legs": [], "external_identifier": "flies-live-7500-1"}, live=True)
    assert out["external_identifier"] == "flies-live-7500-1" == seen["order"]["external_identifier"]
    # two placements never share one
    a = adapter.place({"legs": []}, live=True)["external_identifier"]
    b = adapter.place({"legs": []}, live=True)["external_identifier"]
    assert a != b


def test_an_uncertain_submit_is_recovered_by_its_identifier_instead_of_reported_failed(monkeypatch):
    """Shown to fail without recovery: the submit raises AFTER the broker accepted (a timeout),
    the order is at the broker -- already filled, so `working_orders` would not list it -- and
    the seam must hand back that order rather than a failure a caller would retry into a
    duplicate."""
    at_broker: list[dict] = []

    async def timeout_after_accept(
        account, session, order, *, live, serialize, deploy_limit_pct, get_balances=None
    ):
        at_broker.append(
            {
                "order_id": 4242,
                "status": "Filled",
                "external_identifier": order["external_identifier"],
                "terminal": True,
            }
        )
        raise TimeoutError("read timed out")

    async def today(account, session):
        return list(at_broker)

    monkeypatch.setattr(_broker, "place_order", timeout_after_accept)
    monkeypatch.setattr(_broker, "orders_today", today)
    monkeypatch.setattr(_broker, "build_order", lambda spec: dict(spec))
    adapter = _adapter(monkeypatch)
    out = adapter.place({"legs": []}, live=True)
    assert out["ok"] is True and out["recovered"] is True and out["order_id"] == "4242"
    assert out["external_identifier"] == at_broker[0]["external_identifier"]
    assert "timed out" in out["error"]


def test_a_failed_submit_with_nothing_at_the_broker_is_still_a_failure(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("connection refused")

    async def today(account, session):
        return [{"order_id": 1, "status": "Live", "external_identifier": "someone-elses", "terminal": False}]

    monkeypatch.setattr(_broker, "place_order", boom)
    monkeypatch.setattr(_broker, "orders_today", today)
    monkeypatch.setattr(_broker, "build_order", lambda spec: dict(spec))
    adapter = _adapter(monkeypatch)
    out = adapter.place({"legs": []}, live=True)
    assert (
        out["ok"] is False
        and "connection refused" in out["error"]
        and out["external_identifier"].startswith("cp-")
    )


def test_a_dry_run_never_recovers(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("preflight failed")

    calls = []

    async def today(account, session):
        calls.append(1)
        return []

    monkeypatch.setattr(_broker, "place_order", boom)
    monkeypatch.setattr(_broker, "orders_today", today)
    monkeypatch.setattr(_broker, "build_order", lambda spec: dict(spec))
    out = _adapter(monkeypatch).place({"legs": []}, live=False)
    assert out["ok"] is False and calls == []
