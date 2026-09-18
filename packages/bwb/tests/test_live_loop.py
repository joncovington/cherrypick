"""The bwb live tick end to end against a scripted broker: what it places, when, at what price,
what it records and -- above all -- what it refuses. Snapshots and plans are patched at the seam
(`provider.build_entry_snapshot` / `engine.plan_entry` / `engine.plan_addon`) so the decision
layer's own tests keep owning the arithmetic; this file owns the order flow.

Every guard here was broken on purpose once (drop the live check, write the legs on placement,
empty the readiness list, flip the cap comparison, count cancelled rows, accept a provisional
source) and watched to fail before it was trusted.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import pytest
from cherrypick.core import execution as _execution
from cherrypick.core import live as _live
from cherrypick.core import streamcache
from conftest import occ, streamer_sym

from cherrypick.bwb import db, engine, live_loop, provider

WHEN = datetime(2026, 9, 16, 10, 5)  # a Wednesday, inside the entry window (10:00-11:00)
DAY = "2026-09-16"
EXP = "2026-09-18"
BODY = 7600.0


def _leg(role, strike, action):
    return {
        "leg_role": role,
        "occ_symbol": occ("SPXW", EXP, strike),
        "streamer_symbol": streamer_sym("SPXW", EXP, strike),
        "expiration": EXP,
        "strike": strike,
        "option_type": "put",
        "action": action,
        "bid": 1.0,
        "ask": 1.2,
        "mid": 1.1,
        "iv": 0.2,
        "delta": -0.3,
    }


def _plan(credit=0.9, body=BODY):
    near, far = body + 5, body - 10
    return {
        "symbol": "SPX",
        "spot": 7700.0,
        "expiration": EXP,
        "dte": 2,
        "atm_strike": 7700.0,
        "expected_move": 100.0,
        "body_strike": body,
        "near_strike": near,
        "far_strike": far,
        "body_mid": 1.5,
        "near_mid": 1.9,
        "far_mid": 0.2,
        "credit": credit,
        "narrow_width": 5.0,
        "wide_width": 10.0,
        "max_loss_up": 0.0,
        "max_loss_down": 5.0 - credit,
        "max_loss": 5.0 - credit,
        "legs": [
            _leg("near_long", near, "Buy to Open"),
            _leg("body_short_1", body, "Sell to Open"),
            _leg("body_short_2", body, "Sell to Open"),
            _leg("far_long", far, "Buy to Open"),
        ],
    }


def _addon_plan(credit=0.5, far=BODY - 10):
    return {
        "credit": credit,
        "short_strike": far + 5,
        "long_strike": far - 5,
        "legs": [_leg("addon_short", far + 5, "Sell to Open"), _leg("addon_long", far - 5, "Buy to Open")],
    }


class FakeBroker:
    """Scripted: `statuses[order_id]` is what `.status()` answers; `place` hands out ids in order."""

    def __init__(self, *, statuses=None, dry_run=False, fee=6.89, fail=None):
        self.statuses = dict(statuses or {})
        self.dry_run = dry_run
        self.fee = fee
        self.fail = fail
        self.placed: list[dict] = []
        self.replaced: list[tuple[str, dict]] = []
        self.cancelled: list[str] = []
        self.polled: list[str] = []
        self.working: list[dict] = []
        self.settlement = (None, "no_source_available")
        self._n = 0
        self.held = {}

    def place(self, spec, live):
        self.placed.append({**spec, "_live": live})
        if self.fail:
            return dict(self.fail)
        self._n += 1
        oid = f"ORD{self._n}"
        response = {"order": {"id": oid, "status": "Received"}}
        if self.fee is not None:
            response["fee_calculation"] = {"total_fees": str(-self.fee)}
        result = {
            "ok": True,
            "dry_run": not live or self.dry_run,
            "response": response,
            "external_identifier": spec.get("external_identifier"),
        }
        if live and not self.dry_run:
            result["order_id"] = oid
            self.statuses.setdefault(oid, {"status": "Received", "price": None})
        return result

    def replace(self, order_id, spec, live):
        self.replaced.append((order_id, dict(spec)))
        self._n += 1
        oid = f"ORD{self._n}"
        self.statuses.setdefault(oid, {"status": "Received", "price": None})
        return {"ok": True, "order_id": oid, "response": {"order": {"id": oid}}}

    def status(self, order_id):
        self.polled.append(order_id)
        return {"order_id": order_id, **self.statuses.get(order_id, {"status": "Received"})}

    def cancel(self, order_id):
        self.cancelled.append(order_id)
        self.statuses[order_id] = {"status": "Cancelled"}
        return {"ok": True}

    def working_orders(self):
        return list(self.working)

    def official_settlement_price(self, symbol):
        return self.settlement


@pytest.fixture
def live_config(config):
    return {
        **config,
        "live": {
            "enabled": True,
            "gate0_confirmed": "jon 2026-09-18",
            "arm": "control",
            "max_open_margin_dollars": 2000,
            "max_structures_per_day": 1,
            "entry_concession": 0.05,
            "entry_cancel_after_minutes": 20,
            "entry_cutoff": "11:00",
            "reprice_after_minutes": 2,
            "min_net_credit_dollars": 15,
            "fill_watch_seconds": 0,
            "settle_time": "16:20",
            "disarm_time": "17:00",
        },
    }


@pytest.fixture
def cache(tmp_path):
    path = tmp_path / "cache.db"
    streamcache.connect(str(path)).close()  # the schema, no rows: marks are unusable, nothing to price
    return str(path)


@pytest.fixture
def conn(managed_home):
    c = db.connect(db.live_db_path())
    yield c
    c.close()


@pytest.fixture
def planned(monkeypatch):
    """Patch the entry seam to a canned plan; the returned dict lets a test change the plan."""
    state = {"plan": _plan(), "ok": True, "addon": _addon_plan()}
    monkeypatch.setattr(
        provider,
        "build_entry_snapshot",
        lambda *a, **k: {
            "ok": True,
            "spot": 7700.0,
            "expiration": EXP,
            "quote_stats": {"fresh": 4, "rejected": 0},
        },
    )
    monkeypatch.setattr(
        engine,
        "plan_entry",
        lambda snapshot, params: (
            {"ok": True, "plan": state["plan"]} if state["ok"] else {"ok": False, "reason": "no_credit"}
        ),
    )
    monkeypatch.setattr(engine, "plan_addon", lambda snap, far, params: {"ok": True, "plan": state["addon"]})
    return state


def _tick(config, conn, broker, cache, *, when=WHEN, live=True, armed=True):
    if armed:
        _live.write_arm_record("bwb", date=when.date().isoformat(), at="t", armed_by="live-bwb-start")
    return live_loop.run_once(
        config,
        conn,
        broker,
        cache_path=cache,
        when=when,
        live=live,
        force=True,
        log=lambda *_: None,
        clock_fn=lambda: 0.0,
        sleep_fn=lambda s: None,
    )


def _row(conn, pid):
    r = conn.execute("SELECT * FROM bwb_positions WHERE position_id = ?", (pid,)).fetchone()
    return dict(r) if r else None


def _decisions(conn, mode=None):
    q = "SELECT mode, reason, accepted FROM bwb_decisions"
    rows = conn.execute(q + (" WHERE mode = ?" if mode else ""), (mode,) if mode else ()).fetchall()
    return [(r["mode"], r["reason"], r["accepted"]) for r in rows]


# --------------------------------------------------------------------------- readiness
def test_readiness_names_every_unmet_gate_and_passes_only_when_all_are_met(live_config):
    unmet = live_loop.readiness({"live": {}}, halt_present=True, designated=None)
    assert len(unmet) == 4 and any("halt flag" in g for g in unmet)  # arm defaults to control
    assert live_loop.readiness(live_config, halt_present=False, designated="5WX1234") == []
    bad = {**live_config, "live": {**live_config["live"], "arm": "wall"}}
    assert any("not a base book" in g for g in live_loop.readiness(bad, halt_present=False, designated="x"))


# --------------------------------------------------------------------------- entry
def test_dry_run_preflights_and_records_nothing_but_the_journal(live_config, conn, cache, planned):
    broker = FakeBroker()
    out = _tick(live_config, conn, broker, cache, live=False)
    assert out["entry"]["entry"] == "dry_run" and out["entry"]["price"] == pytest.approx(0.85)
    assert broker.placed and broker.placed[0]["_live"] is False
    assert conn.execute("SELECT COUNT(*) FROM bwb_positions").fetchone()[0] == 0
    assert any("dry_run_preflight" in r for _, r, _ in _decisions(conn, "entry"))


def test_live_entry_is_a_three_leg_limit_with_the_ledger_key_as_its_identifier(
    live_config, conn, cache, planned
):
    broker = FakeBroker()
    out = _tick(live_config, conn, broker, cache)
    assert out["entry"]["entry"] == "placed" and out["entry"]["order_id"] == "ORD1"
    spec = broker.placed[0]
    assert [(leg["action"], leg["quantity"]) for leg in spec["legs"]] == [
        ("Buy to Open", 1),
        ("Sell to Open", 2),
        ("Buy to Open", 1),
    ]
    assert spec["price"] == pytest.approx(0.85) and spec["price_effect"] == "credit"
    pid = "SPX:control:2026-09-16:1"
    assert spec["external_identifier"] == pid
    row = _row(conn, pid)
    assert row["status"] == "pending" and row["entry_fill_status"] == "pending"
    assert row["entry_order_id"] == "ORD1" and row["entry_external_id"] == pid
    assert row["entry_limit"] == pytest.approx(0.85) and row["entry_mid_at_submit"] == pytest.approx(0.9)
    assert row["entry_credit"] == pytest.approx(0.9)  # the modeled credit until the broker says otherwise
    assert row["fees_source"] == "broker_estimate" and row["entry_fee_estimate"] == pytest.approx(6.89)
    assert row["entry_live_floor"] == pytest.approx(0.25)
    # a pending row is NOT an open position: nothing marks or manages it until it fills
    assert db.open_positions(conn) == []
    assert db.established_today(conn, "control", DAY) == 1


def test_a_fill_overwrites_the_credit_with_the_broker_price_and_measures_slippage(
    live_config, conn, cache, planned
):
    broker = FakeBroker()
    _tick(live_config, conn, broker, cache)
    broker.statuses["ORD1"] = {"status": "Filled", "price": "0.87"}
    out = _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 6))
    assert out["confirm"]["entries_filled"] == 1 and out["entry"]["entry"] == "done"
    row = _row(conn, "SPX:control:2026-09-16:1")
    assert row["status"] == "open" and row["entry_fill_status"] == "filled"
    assert row["entry_credit"] == pytest.approx(0.87)
    assert row["entry_slippage"] == pytest.approx((0.9 - 0.87) * 100)  # mid minus fill, measured
    assert len(db.open_positions(conn)) == 1


def test_a_rejected_entry_is_cancelled_and_spends_no_budget_and_a_retry_gets_a_new_id(
    live_config, conn, cache, planned
):
    broker = FakeBroker()
    _tick(live_config, conn, broker, cache)
    broker.statuses["ORD1"] = {"status": "Rejected"}
    out = _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 6))
    assert out["confirm"]["entries_dead"] == 1
    first = _row(conn, "SPX:control:2026-09-16:1")
    assert first["status"] == "cancelled" and first["entry_fill_status"] == "rejected"
    assert db.established_today(conn, "control", DAY) == 1  # the retry, placed on the same tick
    second = _row(conn, "SPX:control:2026-09-16:2")
    assert second is not None and second["entry_order_id"] == "ORD2"
    assert out["entry"]["entry"] == "placed"


def test_one_structure_per_day_and_a_pending_entry_block_a_second_attempt(live_config, conn, cache, planned):
    broker = FakeBroker()
    _tick(live_config, conn, broker, cache)
    out = _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 6))
    assert out["entry"]["entry"] == "done"
    assert len(broker.placed) == 1


def test_entry_below_the_live_floor_is_refused_and_journaled(live_config, conn, cache, planned):
    planned["plan"] = _plan(credit=0.28)  # 0.23 after the concession, under the 0.25 floor
    broker = FakeBroker()
    out = _tick(live_config, conn, broker, cache)
    assert out["entry"]["reason"] == "credit_below_live_floor"
    assert not broker.placed
    assert ("entry", "credit_below_live_floor", 0) in _decisions(conn)


def test_a_broker_fee_estimate_above_the_schedule_can_lift_the_floor_over_the_limit(
    live_config, conn, cache, planned
):
    planned["plan"] = _plan(credit=0.30)  # limit 0.25: on the schedule floor exactly
    broker = FakeBroker(fee=15.0)  # estimate floor: (15 + 15) / 100 -> 0.30
    out = _tick(live_config, conn, broker, cache)
    assert out["entry"]["reason"] == "credit_below_live_floor"
    assert broker.cancelled == ["ORD1"]  # the order was working; it is pulled, not left resting
    assert conn.execute("SELECT COUNT(*) FROM bwb_positions").fetchone()[0] == 0


def test_margin_caps_refuse_with_the_numbers_and_reserve_the_addon_for_a_firing_arm(
    live_config, conn, cache, planned
):
    # control, one open 410 position + proposed 410 = 820 < 2000: fine
    broker = FakeBroker()
    _tick(live_config, conn, broker, cache)
    broker.statuses["ORD1"] = {"status": "Filled", "price": "0.90"}
    _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 6))
    # next day: an arm that can fire reserves $1000 per unfired position -> 410 + 1000 + 410 + 1000 > 2000
    cfg = {**live_config, "live": {**live_config["live"], "arm": "delta"}}
    out = _tick(cfg, conn, broker, cache, when=datetime(2026, 9, 17, 10, 5))
    assert out["entry"]["reason"] == "max_open_margin_total_reached"
    assert len(broker.placed) == 1
    # control the same day: 410 + 410 fits
    out = _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 17, 10, 5))
    assert out["entry"]["entry"] == "placed"
    # null caps are off
    cfg = {**live_config, "live": {**live_config["live"], "arm": "delta", "max_open_margin_dollars": None}}
    broker.statuses["ORD2"] = {"status": "Filled", "price": "0.90"}
    out = _tick(cfg, conn, broker, cache, when=datetime(2026, 9, 18, 10, 5))
    assert out["entry"]["entry"] == "placed"


def test_the_mark_drawdown_breaker_blocks_the_next_entry_and_touches_no_position(
    live_config, conn, cache, planned
):
    broker = FakeBroker()
    _tick(live_config, conn, broker, cache)
    broker.statuses["ORD1"] = {"status": "Filled", "price": "0.90"}
    _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 6))
    pid = "SPX:control:2026-09-16:1"
    for role in ("near_long", "body_short_1", "body_short_2", "far_long"):
        db.record_mark(
            conn, position_id=pid, leg_role=role, marked_at=9e9, session_date=DAY, close_cost=4.4, usable=1
        )
    cfg = {**live_config, "live": {**live_config["live"], "mark_drawdown_halt_dollars": 300}}
    out = _tick(cfg, conn, broker, cache, when=datetime(2026, 9, 17, 10, 5))
    assert out["entry"]["reason"] == "mark_drawdown_halt"
    assert _row(conn, pid)["status"] == "open" and not broker.cancelled
    assert len(broker.placed) == 1


def test_the_settled_net_breaker_reads_the_live_ledger(conn, live_config):
    from cherrypick.bwb import book as bookmod

    assert not live_loop.daily_loss_tripped(conn, DAY, 500)
    bookmod.enter_position(
        conn, _plan(), live_config, "control", entry_session="2026-09-10", advice_params=None
    )
    db.save_position(
        conn,
        {
            "position_id": "SPX:control:2026-09-10",
            "status": "closed",
            "closed_session": DAY,
            "gross_pnl": -600.0,
            "fees": 10.0,
        },
    )
    assert live_loop.daily_loss_tripped(conn, DAY, 500) and not live_loop.daily_loss_tripped(conn, DAY, None)


# --------------------------------------------------------------------------- resting entries
def test_the_walk_down_steps_a_tick_per_interval_lands_on_the_floor_and_rests(
    live_config, conn, cache, planned
):
    planned["plan"] = _plan(credit=0.40)  # limit 0.35, floor 0.25: two steps to the floor
    broker = FakeBroker()
    _tick(live_config, conn, broker, cache)
    pid = "SPX:control:2026-09-16:1"
    assert _row(conn, pid)["entry_limit"] == pytest.approx(0.35)
    # inside the reprice interval: no broker call
    _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 6))
    assert not broker.replaced
    out = _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 8))
    assert out["resting"] == ["repriced"]
    row = _row(conn, pid)
    assert (
        row["entry_limit"] == pytest.approx(0.30)
        and row["entry_reprice_count"] == 1
        and row["entry_order_id"] == "ORD2"
    )
    assert broker.replaced[-1][1]["price"] == pytest.approx(0.30)
    out = _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 11))
    assert out["resting"] == ["repriced"] and _row(conn, pid)["entry_limit"] == pytest.approx(0.25)
    # on the floor: rests, no broker call, however long it sits (until cutoff / age)
    out = _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 14))
    assert out["resting"] == ["resting"] and len(broker.replaced) == 2
    assert ("entry", "reprice:1", 1) in _decisions(conn) and ("entry", "reprice:2", 1) in _decisions(conn)


def test_a_rising_mid_lifts_the_walk_back_up(live_config, conn, cache, planned):
    planned["plan"] = _plan(credit=0.90)
    broker = FakeBroker()
    _tick(live_config, conn, broker, cache)
    _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 8))  # step 1 -> 0.80
    planned["plan"] = _plan(credit=1.10)
    _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 11))  # target 1.05 - 0.10
    assert _row(conn, "SPX:control:2026-09-16:1")["entry_limit"] == pytest.approx(0.95)


def test_the_cutoff_and_the_age_limit_cancel_a_resting_entry_and_journal_no_fill(
    live_config, conn, cache, planned
):
    broker = FakeBroker()
    _tick(live_config, conn, broker, cache)
    out = _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 26))  # 21 minutes old
    assert out["resting"] == ["cancelled"] and broker.cancelled == ["ORD1"]
    row = _row(conn, "SPX:control:2026-09-16:1")
    assert row["status"] == "cancelled"
    assert any(r.startswith("no_fill:older_than") for _, r, _ in _decisions(conn, "entry"))
    # the retry the same tick (still inside the window) gets attempt 2
    assert out["entry"]["entry"] == "placed" and _row(conn, "SPX:control:2026-09-16:2")
    out = _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 11, 0))
    assert out["resting"] == ["cancelled"] and out["entry"]["entry"] == "outside_window"
    assert ("entry", "no_fill:entry_cutoff", 0) in _decisions(conn)


def test_moved_strikes_cancel_the_resting_entry(live_config, conn, cache, planned):
    broker = FakeBroker()
    _tick(live_config, conn, broker, cache)
    planned["plan"] = _plan(body=7580.0)
    out = _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 6))
    assert out["resting"] == ["cancelled"] and ("entry", "no_fill:strikes_moved", 0) in _decisions(conn)


def test_a_refused_cancel_leaves_the_marker_for_the_next_poll(live_config, conn, cache, planned):
    broker = FakeBroker()
    _tick(live_config, conn, broker, cache)
    broker.cancel = lambda oid: {"ok": False, "error": "already filled"}
    out = _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 11, 0))
    assert out["resting"] == ["resting"] and _row(conn, "SPX:control:2026-09-16:1")["status"] == "pending"


# --------------------------------------------------------------------------- the add-on
def _open_armed(live_config, conn, cache, planned, broker, *, arm="delta"):
    cfg = {**live_config, "live": {**live_config["live"], "arm": arm, "max_open_margin_dollars": None}}
    _tick(cfg, conn, broker, cache)
    broker.statuses["ORD1"] = {"status": "Filled", "price": "0.90"}
    _tick(cfg, conn, broker, cache, when=datetime(2026, 9, 16, 10, 6))
    pid = f"SPX:{arm}:2026-09-16:1"
    assert _row(conn, pid)["status"] == "open"
    db.save_position(
        conn, {"position_id": pid, "armed_at": "2026-09-16T10:07:00", "arm_reason": "delta_trigger_met"}
    )
    return cfg, pid


def _fire_ready(monkeypatch):
    """Make the management pass see a fireable add-on regardless of the (empty) cache."""
    from cherrypick.bwb import management
    from cherrypick.bwb import paper_loop as pl

    monkeypatch.setattr(
        management,
        "evaluate",
        lambda position, params, *, trigger_state, tick, addon_credit: (
            management.Decision("fire_addon", "addon_credit_met"),
            {"peak_abs_delta": 0.55, "below_flip_seen": False},
        ),
    )
    monkeypatch.setattr(management, "execution_gate", lambda snap, params, *, now: None)
    monkeypatch.setattr(pl, "_addon_snapshot", lambda *a, **k: {"ok": True})


def test_the_addon_is_placed_and_recorded_only_on_confirmation(
    live_config, conn, cache, planned, monkeypatch
):
    broker = FakeBroker()
    cfg, pid = _open_armed(live_config, conn, cache, planned, broker)
    _fire_ready(monkeypatch)
    out = _tick(cfg, conn, broker, cache, when=datetime(2026, 9, 16, 10, 8))
    spec = broker.placed[-1]
    assert [(leg["action"], leg["quantity"]) for leg in spec["legs"]] == [
        ("Sell to Open", 1),
        ("Buy to Open", 1),
    ]
    assert spec["price"] == pytest.approx(0.45) and spec["external_identifier"] == f"{pid}-addon1"
    row = _row(conn, pid)
    assert row["addon_order_id"] == "ORD2" and row["addon_fill_status"] == "pending"
    assert row["addon_fired_at"] is None and row["addon_credit"] is None  # NOT recorded fired
    assert db.legs_for(conn, pid) and not any(
        leg["leg_role"].startswith("addon") for leg in db.legs_for(conn, pid)
    )
    assert json.loads(row["pending_addon_json"])["asked"] == pytest.approx(0.45)
    ev = conn.execute(
        "SELECT action, executed, gate FROM bwb_management_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert ev["action"] == "fire_addon" and ev["executed"] == 0 and ev["gate"] == "pending_fill"
    # a later tick: the fill writes the legs with the ACTUAL credit
    broker.statuses["ORD2"] = {"status": "Filled", "price": "0.47"}
    out = _tick(cfg, conn, broker, cache, when=datetime(2026, 9, 16, 10, 9))
    assert out["confirm"]["addons_filled"] == 1
    row = _row(conn, pid)
    assert (
        row["addon_fired_at"]
        and row["addon_credit"] == pytest.approx(0.47)
        and row["addon_fill_status"] == "filled"
    )
    roles = sorted(leg["leg_role"] for leg in db.legs_for(conn, pid))
    assert roles == ["addon_long", "addon_short", "body_short_1", "body_short_2", "far_long", "near_long"]
    assert row["addon_slippage"] == pytest.approx((0.5 - 0.47) * 100)
    # and it is not re-fired
    assert len(broker.placed) == 2


def test_a_dead_addon_clears_the_marker_keeps_the_arm_and_may_re_fire_with_a_new_suffix(
    live_config, conn, cache, planned, monkeypatch
):
    broker = FakeBroker()
    cfg, pid = _open_armed(live_config, conn, cache, planned, broker)
    _fire_ready(monkeypatch)
    _tick(cfg, conn, broker, cache, when=datetime(2026, 9, 16, 10, 8))
    broker.statuses["ORD2"] = {"status": "Cancelled"}
    out = _tick(cfg, conn, broker, cache, when=datetime(2026, 9, 16, 10, 9))
    assert out["confirm"]["addons_dead"] == 1
    row = _row(conn, pid)
    assert row["armed_at"] and row["addon_fired_at"] is None and row["addon_order_id"] == "ORD3"
    assert broker.placed[-1]["external_identifier"] == f"{pid}-addon2"


def test_a_pending_addon_is_not_re_fired_and_only_one_addon_is_placed_per_tick(
    live_config, conn, cache, planned, monkeypatch
):
    broker = FakeBroker()
    cfg, pid = _open_armed(live_config, conn, cache, planned, broker)
    # a second armed position, filled the day before
    from cherrypick.bwb import book as bookmod

    bookmod.enter_position(
        conn,
        _plan(),
        cfg,
        "delta",
        entry_session="2026-09-15",
        advice_params=None,
        position_id_override="SPX:delta:2026-09-15:1",
        extra={"armed_at": "2026-09-15T14:00:00", "entry_fill_status": "filled"},
    )
    _fire_ready(monkeypatch)
    _tick(cfg, conn, broker, cache, when=datetime(2026, 9, 16, 10, 8))
    addon_orders = [s for s in broker.placed if len(s["legs"]) == 2]
    assert len(addon_orders) == 1
    assert ("addon", "addon_throttled:one_per_tick", 0) in _decisions(conn)
    _tick(cfg, conn, broker, cache, when=datetime(2026, 9, 16, 10, 9))
    assert len([s for s in broker.placed if len(s["legs"]) == 2]) == 2  # the other one, next tick
    _tick(cfg, conn, broker, cache, when=datetime(2026, 9, 16, 10, 10))
    assert len([s for s in broker.placed if len(s["legs"]) == 2]) == 2  # both pending: nothing more


def test_an_addon_below_its_live_floor_is_refused_and_the_arm_stays(
    live_config, conn, cache, planned, monkeypatch
):
    broker = FakeBroker()
    cfg, pid = _open_armed(live_config, conn, cache, planned, broker)
    planned["addon"] = _addon_plan(credit=0.20)  # 0.15 after concession, under the 0.20 floor
    _fire_ready(monkeypatch)
    _tick(cfg, conn, broker, cache, when=datetime(2026, 9, 16, 10, 8))
    assert len(broker.placed) == 1 and ("addon", "addon_below_live_floor", 0) in _decisions(conn)
    assert _row(conn, pid)["armed_at"]


def test_a_resting_addon_is_cancelled_after_its_age_limit(live_config, conn, cache, planned, monkeypatch):
    broker = FakeBroker()
    cfg, pid = _open_armed(live_config, conn, cache, planned, broker)
    _fire_ready(monkeypatch)
    _tick(cfg, conn, broker, cache, when=datetime(2026, 9, 16, 10, 8))
    out = _tick(cfg, conn, broker, cache, when=datetime(2026, 9, 16, 10, 29))
    assert "cancelled" in out["resting"] and "ORD2" in broker.cancelled
    row = _row(conn, pid)
    # the arm stayed live, so the SAME tick's management pass placed a fresh attempt
    assert row["armed_at"] and row["addon_fired_at"] is None
    assert row["addon_order_id"] == "ORD3" and row["addon_attempts"] == 2
    assert ("addon", "addon_no_fill:older_than_20m", 0) in _decisions(conn)


# --------------------------------------------------------------------------- orphans, settlement, arming
def test_the_orphan_sweep_reports_unknown_working_orders_and_never_invents_a_clean_sweep(
    live_config, conn, cache, planned, monkeypatch
):
    broker = FakeBroker()
    _tick(live_config, conn, broker, cache)
    broker.working = [
        {"order_id": "ORD1", "underlying_symbol": "SPX"},
        {"order_id": "GHOST", "underlying_symbol": "SPX"},
    ]
    out = _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 6))
    assert out["orphans"] == 1 and [o["order_id"] for o in live_loop.read_orphans()] == ["GHOST"]

    def boom():
        raise RuntimeError("broker down")

    broker.working_orders = boom
    out = _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 7))
    assert out["orphans"] == 0 and [o["order_id"] for o in live_loop.read_orphans()] == [
        "GHOST"
    ]  # the file stands


def test_settlement_needs_an_official_print_and_stamps_its_source(live_config, conn, cache, planned):
    broker = FakeBroker()
    _tick(live_config, conn, broker, cache)
    broker.statuses["ORD1"] = {"status": "Filled", "price": "0.90"}
    _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 10, 6))
    pid = "SPX:control:2026-09-16:1"
    friday = datetime(2026, 9, 18, 16, 21)
    out = _tick(live_config, conn, broker, cache, when=friday)
    assert out["settle"]["reason"] == "official_print_unavailable" and _row(conn, pid)["status"] == "open"
    broker.settlement = (7650.0, "tastytrade_last_provisional")
    out = _tick(live_config, conn, broker, cache, when=friday)
    assert out["settle"]["reason"] == "official_print_unavailable"
    broker.settlement = (7650.0, "yahoo")
    out = _tick(live_config, conn, broker, cache, when=friday)
    assert out["settle"]["ok"] and _row(conn, pid)["settlement_source"] == "yahoo"
    assert _row(conn, pid)["status"] == "closed" and _row(conn, pid)["settlement_spot"] == 7650.0


def test_a_hand_supplied_price_settles_as_official_and_a_pending_entry_is_never_settled(
    live_config, conn, cache, planned
):
    broker = FakeBroker()
    _tick(live_config, conn, broker, cache)  # pending, never filled
    out = live_loop.run_settle_live(
        live_config,
        conn,
        cache_path=cache,
        when=datetime(2026, 9, 18, 16, 30),
        price=7650.0,
        log=lambda *_: None,
    )
    assert out["ok"] and out["results"] == []
    assert _row(conn, "SPX:control:2026-09-16:1")["status"] == "pending"


def test_the_dead_man_disarms_before_any_broker_read(live_config, conn, cache, planned, monkeypatch):
    broker = FakeBroker()
    out = _tick(live_config, conn, broker, cache, armed=False)  # no arm record at all
    assert "disarmed" in out and "per-day" in out["disarmed"]
    assert not broker.placed and not broker.polled
    # armed yesterday: still "per-day"
    _live.write_arm_record("bwb", date="2026-09-15", at="t", armed_by="live-bwb-start")
    out = _tick(live_config, conn, broker, cache, armed=False)
    assert "per-day" in out["disarmed"] and not broker.placed
    # armed for today: the tick proceeds; past disarm time: it disarms and removes the record
    out = _tick(live_config, conn, broker, cache)
    assert "disarmed" not in out and out["entry"]["entry"] == "placed"
    out = _tick(live_config, conn, broker, cache, when=datetime(2026, 9, 16, 17, 0))
    assert "past disarm" in out["disarmed"] and not os.path.exists(live_loop.arm_record_path())
    # a dry run is never gated on the arm record: it places nothing
    out = _tick(live_config, conn, broker, cache, live=False, armed=False)
    assert "disarmed" not in out


def test_install_refuses_without_a_supervisor_and_writes_the_record_under_one(managed_home, monkeypatch):
    out = live_loop.install_task()
    assert not out["ok"] and "no supervisor" in out["error"]
    monkeypatch.setattr(_live, "supervisor_heartbeat_fresh", lambda max_age_seconds=90: True)
    monkeypatch.setattr(live_loop, "_spawn_first_tick", lambda: None)
    out = live_loop.install_task()
    assert out["ok"] and live_loop.arm_stamp_date() == out["armed_for"]
    rec = json.loads(open(live_loop.arm_record_path(), encoding="utf-8").read())
    assert rec["armed_by"] == "live-bwb-start" and rec["confirmation"] == "literal-YES"
    assert live_loop.uninstall_task()["arm_record_removed"] and live_loop.arm_stamp_date() is None


def test_status_is_files_and_db_only(live_config, conn, cache, planned):
    _live.write_arm_record("bwb", date=DAY, at="t", armed_by="live-bwb-start")
    out = live_loop.run_status(live_config, conn, cache_path=cache)
    assert out["ok"] and out["armed_for"] == DAY and out["arm"] == "control" and out["pending_orders"] == []
    assert out["live_db"].endswith("live_trades.db")


def test_a_paper_tick_still_records_the_addon_the_instant_the_credit_is_met(
    config, monkeypatch, managed_home
):
    """The no-break guard for the paper books: `_manage_positions` without a `fire` hook writes
    the legs immediately, exactly as before the live seam existed."""
    from cherrypick.bwb import book as bookmod
    from cherrypick.bwb import paper_loop as pl

    conn = db.connect(db.default_db_path())
    bookmod.enter_position(conn, _plan(), config, "delta", entry_session=DAY, advice_params=None)
    pid = "SPX:delta:2026-09-16"
    db.save_position(conn, {"position_id": pid, "armed_at": "t", "arm_reason": "delta_trigger_met"})
    _fire_ready(monkeypatch)
    monkeypatch.setattr(engine, "plan_addon", lambda snap, far, params: {"ok": True, "plan": _addon_plan()})
    position = _row(conn, pid)
    values = {
        pid: {
            "position": position,
            "snapshot": {"ok": True},
            "params": {},
            "close_cost": None,
            "trigger_state": {},
            "tick": {},
            "addon_credit": 0.5,
            "addon_block": None,
        }
    }
    actions = pl._manage_positions(config, conn, values, cache_path="unused", when=WHEN, day=DAY)
    assert actions == 1
    row = _row(conn, pid)
    assert row["addon_fired_at"] and row["addon_credit"] == pytest.approx(0.5)
    assert sorted(leg["leg_role"] for leg in db.legs_for(conn, pid))[:2] == ["addon_long", "addon_short"]


def test_fill_state_and_fee_estimate_are_read_defensively():
    assert live_loop._fee_estimate(
        {"response": {"fee_calculation": {"total_fees": "-6.89"}}}
    ) == pytest.approx(6.89)
    assert live_loop._fee_estimate({"response": {"fee-calculation": {"total-fees": "1.5"}}}) == pytest.approx(
        1.5
    )
    assert live_loop._fee_estimate({"response": {}}) is None and live_loop._fee_estimate({}) is None
    assert live_loop._fee_estimate({"response": {"fee_calculation": {"total_fees": "x"}}}) is None
    assert _execution.fill_state({"status": "Filled", "price": "0.5"}) == ("filled", 0.5)
