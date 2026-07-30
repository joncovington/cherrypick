"""The live scaffold (docs/live-trading-plan.md): gates, order builders, and the gated loop.

Everything here is offline — the broker is a fake, and the point under test is that the
scaffold is INERT by default: no gate, no order.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import db as dbmod
import live_loop
import live_orders
import provider as _provider
from broker_cli import live_gates
from engine import PUT

# Today, not a literal: run_watch and run_settle_live resolve "today" internally via
# provider.now_et(), so fixture rows pinned to a past date would be invisible to them.
DAY = _provider.now_et().date().isoformat()


def _snapshot(**over):
    def q(occ, mid):
        return {
            "bid": mid - 0.1,
            "ask": mid + 0.1,
            "mid": mid,
            "occ_symbol": occ,
            "instrument_type": "Equity Option",
        }

    base = {
        "ok": True,
        "symbol": "SPX",
        "date": DAY,
        "expiration": DAY,
        "dte": 0,
        "underlying_price": 7500.0,
        "now_min": 11 * 60,
        # Enough skew that the ATM credit spread clears the 10%-of-width floor without
        # tripping the mostly-intrinsic ceiling.
        "puts": {
            7500.0: q("SPXW  260729P07500000", 2.6),
            7495.0: q("SPXW  260729P07495000", 1.4),
            7490.0: q("SPXW  260729P07490000", 0.7),
        },
        "calls": {},
        "gex": {"ok": False},
    }
    base.update(over)
    return base


ENTRY_PLAN = {
    "side": PUT,
    "center": 7495.0,
    "wing_width": 5,
    "credit": 1.07,
    "quantity": 1,
    "open_fee": 3.44,
    "completing_strike": 7500.0,
    "completing_direction": "up",
    "entry_window": "10:30-11:00",
}


# --------------------------------------------------------------------------- order builders
def test_entry_spec_sells_center_buys_wing_at_tick_floored_credit():
    spec = live_orders.entry_spec(_snapshot(), ENTRY_PLAN)
    assert spec["price"] == 1.05 and spec["price_effect"] == "credit"  # 1.07 floors to a nickel
    actions = {leg["symbol"]: leg["action"] for leg in spec["legs"]}
    assert actions["SPXW  260729P07495000"] == "sell to open"
    assert actions["SPXW  260729P07490000"] == "buy to open"


def test_completion_spec_never_prices_past_the_engine_gate():
    pos = {"side": PUT, "center": 7495.0, "wing_width": 5, "quantity": 1}
    plan = {"debit": 0.93, "gate_debit": 0.87, "long_strike": 7500.0}
    spec = live_orders.completion_spec(_snapshot(), pos, plan)
    # 0.93 floors to 0.90, but the gate is 0.87 -> 0.85: the working order must not be able
    # to fill at a price the completion gate would have refused.
    assert spec["price"] == 0.85 and spec["price_effect"] == "debit"
    actions = {leg["symbol"]: leg["action"] for leg in spec["legs"]}
    assert actions["SPXW  260729P07500000"] == "buy to open"  # the far strike
    assert actions["SPXW  260729P07495000"] == "sell to open"  # the centre, doubled to -2


def test_order_builders_refuse_quotes_without_occ_symbols():
    snap = _snapshot()
    for q in snap["puts"].values():
        q.pop("occ_symbol")
    with pytest.raises(ValueError, match="OCC"):
        live_orders.entry_spec(snap, ENTRY_PLAN)


# --------------------------------------------------------------------------- gates
BASE_CFG = {
    "arms": {"gex": {}, "control": {}},
    "live": {"enabled": True, "gate0_confirmed": "jon 2026-08-15", "arm": "gex"},
}


def test_readiness_passes_only_with_every_gate():
    assert live_loop.readiness(BASE_CFG, halt_present=False, designated="5W1") == []


def test_readiness_names_each_unmet_gate():
    unmet = live_loop.readiness({"arms": {"gex": {}}, "live": {}}, halt_present=True, designated=None)
    text = " ".join(unmet)
    assert "live.enabled" in text and "gate0_confirmed" in text
    assert "halt flag" in text and "designated" in text


def test_readiness_requires_a_real_arm():
    cfg = {"arms": {"control": {}}, "live": {**BASE_CFG["live"], "arm": "bogus"}}
    assert any(
        "not a configured arm" in u for u in live_loop.readiness(cfg, halt_present=False, designated="x")
    )


def test_broker_cli_live_gates_are_the_same_posture():
    assert live_gates({}) == ["live.enabled is false (docs/live-trading-plan.md, Gate 0 first)"]
    assert live_gates({"live": {"enabled": True}})  # no attestation -> still gated
    assert live_gates({"live": {"enabled": True, "gate0_confirmed": "jon"}}) == []


# --------------------------------------------------------------------------- the loop, faked
class FakeBroker:
    def __init__(self, order_statuses=None, cancel_ok=True, working=None):
        self.placed = []
        self.cancelled = []
        self.status_calls = []
        # order_id -> {"status": ..., "price": ...} the next status() call for it returns
        self._statuses = dict(order_statuses or {})
        self._cancel_ok = cancel_ok
        self._working = list(working or [])

    def place(self, spec, live):
        self.placed.append({"spec": spec, "live": live})
        return {"ok": True, "dry_run": not live, "order_id": f"ORD{len(self.placed)}"}

    def cancel(self, order_id):
        self.cancelled.append(order_id)
        return {"ok": True} if self._cancel_ok else {"ok": False, "error": "cancel refused"}

    def status(self, order_id):
        self.status_calls.append(order_id)
        return self._statuses.get(order_id, {"status": "Live", "price": None, "filled": False})

    def working_orders(self):
        return list(self._working)


@pytest.fixture
def live_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return dbmod.connect(dbmod.live_db_path())


def _loop_cfg():
    return {
        "defaults": {
            "wing_width": 5,
            "quantity": 1,
            "min_credit_pct_of_width": 0.10,
            "max_credit_pct_of_width": 0.60,
            "entry_windows": [["10:30", "11:30"]],
            "max_positions": 4,
            "fee_buffer": 0.10,
            "min_floor_dollars": 10,
            "completion_cutoff": "15:30",
        },
        "arms": {"gex": {}},
        "live": {"enabled": True, "gate0_confirmed": "jon", "arm": "gex"},
    }


def test_dry_run_places_nothing_live_but_records_nothing_either(live_conn):
    broker = FakeBroker()
    summary = live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=False, log=lambda *_: None)
    assert summary["entered"] == 1
    assert broker.placed and broker.placed[0]["live"] is False
    # A dry-run preflight must leave the live ledger empty — nothing was actually opened.
    n = live_conn.execute("SELECT COUNT(*) FROM fly_positions").fetchone()[0]
    assert n == 0


def test_live_mode_records_the_entry_with_its_order_id(live_conn):
    broker = FakeBroker()
    summary = live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    assert summary["entered"] == 1
    row = live_conn.execute("SELECT * FROM fly_positions").fetchone()
    assert row["entry_order_id"] == "ORD1"
    assert row["kind"] == "short_vertical" and row["arm"] == "gex"


def test_working_completion_is_cancelled_at_the_cutoff(live_conn):
    import clock

    dbmod.save_position(
        live_conn,
        {
            "position_id": "P1",
            "book_id": f"{DAY}:gex:SPX",
            "trade_date": DAY,
            "arm": "gex",
            "entry_mode": "legged",
            "symbol": "SPX",
            "kind": "short_vertical",
            "side": PUT,
            "center": 7495.0,
            "wing_width": 5,
            "quantity": 1,
            "net": 1.05,
            "credit": 1.05,
            "fees": 3.44,
            "status": "open",
            "entry_time": clock.now_iso(),
            "entry_order_id": "ORD-P1",
            "entry_fill_status": "filled",
            "completion_order_id": "ORD9",
            "completion_fill_status": "pending",
        },
    )
    broker = FakeBroker()
    snap = _snapshot(now_min=15 * 60 + 45)  # past the 15:30 cutoff
    summary = live_loop.run_once(_loop_cfg(), snap, live_conn, broker, live=True, log=lambda *_: None)
    assert broker.cancelled == ["ORD9"] and summary["cancelled"] == 1
    row = live_conn.execute("SELECT completion_order_id FROM fly_positions").fetchone()
    assert row[0] is None


def test_daily_loss_breaker(live_conn):
    import clock

    dbmod.save_position(
        live_conn,
        {
            "position_id": "L1",
            "book_id": f"{DAY}:gex:SPX",
            "trade_date": DAY,
            "arm": "gex",
            "entry_mode": "legged",
            "symbol": "SPX",
            "kind": "short_vertical",
            "side": PUT,
            "center": 7480.0,
            "wing_width": 5,
            "quantity": 1,
            "net": 1.0,
            "credit": 1.0,
            "fees": 3.44,
            "status": "settled",
            "pnl": -250.0,
            "entry_time": clock.now_iso(),
        },
    )
    assert live_loop.daily_loss_tripped(live_conn, DAY, 200.0) is True
    assert live_loop.daily_loss_tripped(live_conn, DAY, 300.0) is False
    assert live_loop.daily_loss_tripped(live_conn, DAY, None) is False


def test_live_ledger_is_a_separate_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    assert dbmod.live_db_path().endswith("live_trades.db")
    assert dbmod.default_db_path().endswith("paper_trades.db")
    assert dbmod.live_db_path() != dbmod.default_db_path()


# --------------------------------------------------------------------------- concurrency gate
def _pos(**over):
    base = {
        "status": "open",
        "kind": "short_vertical",
        "position_id": "X",
        "net": 1.0,
        "fees": 0.0,
        "wing_width": 5,
        "quantity": 1,
    }
    base.update(over)
    return base


def test_open_short_vertical_always_blocks():
    assert live_loop._is_blocking(_pos(kind="short_vertical"), None) is True


def test_completed_risk_free_fly_does_not_block():
    # floor = 1.0*100 - 0 fees = $100 >= 0 -> risk-free
    pos = _pos(kind="fly", net=1.0, fees=0.0)
    assert live_loop._is_blocking(pos, None) is False


def test_completed_negative_floor_fly_blocks_without_override():
    # floor = 0.1*100 - 20 fees = -$10 < 0 -> not risk-free
    pos = _pos(kind="fly", net=0.1, fees=20.0, position_id="NEG1")
    assert live_loop._is_blocking(pos, None) is True
    assert live_loop._is_blocking(pos, "some-other-id") is True


def test_completed_negative_floor_fly_unblocked_by_matching_override():
    pos = _pos(kind="fly", net=0.1, fees=20.0, position_id="NEG1")
    assert live_loop._is_blocking(pos, "NEG1") is False


def test_blocking_positions_ignores_closed_rows():
    open_pos = _pos(position_id="A", status="open")
    closed_pos = _pos(position_id="B", status="settled")
    assert live_loop._blocking_positions([open_pos, closed_pos], None) == [open_pos]


def test_entry_refused_while_a_spread_is_still_open(live_conn):
    import clock

    dbmod.save_position(
        live_conn,
        {
            "position_id": "OPEN1",
            "book_id": f"{DAY}:gex:SPX",
            "trade_date": DAY,
            "arm": "gex",
            "entry_mode": "legged",
            "symbol": "SPX",
            "kind": "short_vertical",
            "side": PUT,
            "center": 7495.0,
            "wing_width": 5,
            "quantity": 1,
            "net": 1.05,
            "credit": 1.05,
            "fees": 3.44,
            "status": "open",
            "entry_time": clock.now_iso(),
            "entry_order_id": "ORD-OPEN1",
            "entry_fill_status": "filled",
        },
    )
    broker = FakeBroker()
    summary = live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    assert summary["entered"] == 0
    assert any("still an incomplete spread" in s.get("entry", "") for s in summary["skips"])
    # No second ENTRY was placed — the only order is the resting COMPLETION for the confirmed
    # spread (a debit), which the new tick places immediately on a confirmed entry fill.
    assert all(p["spec"]["price_effect"] == "debit" for p in broker.placed)
    assert len(broker.placed) == 1


def test_entry_allowed_once_completed_fly_is_risk_free(live_conn):
    import clock

    dbmod.save_position(
        live_conn,
        {
            "position_id": "FLY1",
            "book_id": f"{DAY}:gex:SPX",
            "trade_date": DAY,
            "arm": "gex",
            "entry_mode": "legged",
            "symbol": "SPX",
            "kind": "fly",
            "side": PUT,
            "center": 7495.0,
            "wing_width": 5,
            "quantity": 1,
            "net": 0.20,  # floor = 0.20*100 - 3.44 = $16.56 >= 0 -> risk-free
            "fees": 3.44,
            "status": "open",
            "entry_time": clock.now_iso(),
            "completed_at": clock.now_iso(),
        },
    )
    broker = FakeBroker()
    summary = live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    assert summary["entered"] == 1  # the risk-free completed fly did not block a new entry


def test_entry_refused_when_completed_fly_has_negative_floor(live_conn):
    import clock

    dbmod.save_position(
        live_conn,
        {
            "position_id": "FLYNEG",
            "book_id": f"{DAY}:gex:SPX",
            "trade_date": DAY,
            "arm": "gex",
            "entry_mode": "legged",
            "symbol": "SPX",
            "kind": "fly",
            "side": PUT,
            "center": 7495.0,
            "wing_width": 5,
            "quantity": 1,
            "net": -50.0,  # deeply negative floor
            "fees": 3.44,
            "status": "open",
            "entry_time": clock.now_iso(),
            "completed_at": clock.now_iso(),
        },
    )
    broker = FakeBroker()
    cfg = _loop_cfg()
    summary = live_loop.run_once(cfg, _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    assert summary["entered"] == 0
    assert any("negative floor" in s.get("entry", "") for s in summary["skips"])
    assert any("FLYNEG" in s.get("entry", "") for s in summary["skips"])

    # With the explicit override naming this exact position, entry is permitted.
    cfg["live"]["negative_floor_override"] = "FLYNEG"
    summary2 = live_loop.run_once(cfg, _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    assert summary2["entered"] == 1


# --------------------------------------------------------------------------- fill confirmation
def _open_entry_row(order_id="ORD-E1", entry_fill_status="pending"):
    import clock

    return {
        "position_id": "E1",
        "book_id": f"{DAY}:gex:SPX",
        "trade_date": DAY,
        "arm": "gex",
        "entry_mode": "legged",
        "symbol": "SPX",
        "kind": "short_vertical",
        "side": PUT,
        "center": 7495.0,
        "wing_width": 5,
        "quantity": 1,
        "net": 1.05,  # modeled credit
        "credit": 1.05,
        "fees": 3.44,
        "status": "open",
        "entry_time": clock.now_iso(),
        "entry_order_id": order_id,
        "entry_fill_status": entry_fill_status,
    }


def test_entry_fill_confirmation_records_actual_price(live_conn):
    dbmod.save_position(live_conn, _open_entry_row())
    broker = FakeBroker(order_statuses={"ORD-E1": {"status": "Filled", "price": "1.15", "filled": True}})
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    row = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert row["entry_fill_status"] == "filled"
    assert row["net"] == pytest.approx(1.15) and row["credit"] == pytest.approx(1.15)


def test_entry_rejection_closes_the_position_without_a_trade(live_conn):
    dbmod.save_position(live_conn, _open_entry_row())
    broker = FakeBroker(order_statuses={"ORD-E1": {"status": "Rejected", "price": None, "filled": False}})
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    row = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert row["entry_fill_status"] == "rejected"
    assert row["status"] == "cancelled"


def test_completion_not_evaluated_until_entry_confirmed_filled(live_conn):
    # entry still pending -> the completion-management loop must not try to complete it, even
    # though evaluate_completion would otherwise fire on this snapshot/position combo.
    dbmod.save_position(live_conn, _open_entry_row(entry_fill_status="pending"))
    broker = FakeBroker(order_statuses={"ORD-E1": {"status": "Live", "price": "1.05", "filled": False}})
    summary = live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    assert summary["completed_orders"] == 0
    row = live_conn.execute("SELECT kind FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert row["kind"] == "short_vertical"


def test_completion_fill_confirmation_flips_kind_and_records_actual_debit(live_conn):
    import clock

    dbmod.save_position(
        live_conn,
        {
            **_open_entry_row(entry_fill_status="filled"),
            "completion_order_id": "ORD-C1",
            "completion_fill_status": "pending",
        },
    )
    broker = FakeBroker(order_statuses={"ORD-C1": {"status": "Filled", "price": "0.80", "filled": True}})
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    row = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert row["kind"] == "fly"
    assert row["completion_fill_status"] == "filled"
    assert row["debit"] == pytest.approx(0.80)
    # net = entry credit (1.05) - actual debit (0.80) = 0.25
    assert row["net"] == pytest.approx(0.25)
    assert row["completed_at"] is not None
    _ = clock  # imported for parity with sibling tests; no direct use beyond fixture setup


# --------------------------------------------------------------------------- resting completion pricing
def test_max_safe_completion_debit_is_min_of_both_gates():
    # SPX entry: credit 1.05, fees 3.44 recorded; completion adds another 3.44.
    # buffer gate: 1.05 - 0.10 = 0.95
    # floor bound at min_floor=10: 1.05 - (10 + 3.44 + 3.4433)/100 = 1.05 - 0.168833 = 0.881167
    pos = {
        "side": PUT,
        "center": 7495.0,
        "wing_width": 5,
        "quantity": 1,
        "net": 1.05,
        "fees": 3.44,
        "symbol": "SPX",
    }
    bound = live_orders.max_safe_completion_debit(pos, 10.0, 0.10)
    assert bound == pytest.approx(0.881167, abs=1e-4)
    # with a tiny floor requirement the buffer gate binds instead
    bound2 = live_orders.max_safe_completion_debit(pos, 0.0, 0.10)
    assert bound2 == pytest.approx(0.95)


def test_resting_completion_spec_prices_at_the_bound_and_derives_legs():
    pos = {
        "side": PUT,
        "center": 7495.0,
        "wing_width": 5,
        "quantity": 1,
        "net": 1.05,
        "fees": 3.44,
        "symbol": "SPX",
    }
    spec = live_orders.resting_completion_spec(
        _snapshot(), pos, {"min_floor_dollars": 10, "fee_buffer": 0.10}
    )
    assert spec["price"] == 0.85  # 0.881167 tick-floored
    actions = {leg["symbol"]: leg["action"] for leg in spec["legs"]}
    assert actions["SPXW  260729P07500000"] == "buy to open"  # completing long = center + W for puts
    assert actions["SPXW  260729P07495000"] == "sell to open"


def test_completing_long_strike_by_side():
    assert live_orders.completing_long_strike({"side": "put", "center": 100.0, "wing_width": 2}) == 102.0
    assert live_orders.completing_long_strike({"side": "call", "center": 100.0, "wing_width": 2}) == 98.0


def test_resting_completion_refuses_unsubmittable_credit():
    pos = {
        "side": PUT,
        "center": 7495.0,
        "wing_width": 5,
        "quantity": 1,
        "net": 0.05,
        "fees": 3.44,
        "symbol": "SPX",
    }
    with pytest.raises(ValueError, match="nothing submittable"):
        live_orders.resting_completion_spec(_snapshot(), pos, {"min_floor_dollars": 10, "fee_buffer": 0.10})


# --------------------------------------------------------------------------- entry management
def test_unmoved_entry_evaluation_leaves_resting_order_alone(live_conn):
    # The engine on this snapshot picks center 7500 at credit 1.15; a stored row at that center
    # with a credit within one tick (1.12) is an UNMOVED evaluation -> the resting order stands.
    row = _open_entry_row()
    row["center"] = 7500.0
    row["net"] = 1.12
    row["credit"] = 1.12
    dbmod.save_position(live_conn, row)
    broker = FakeBroker()  # status defaults to Live/unfilled
    summary = live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    assert broker.cancelled == []  # resting order untouched
    row = live_conn.execute("SELECT status, entry_fill_status FROM fly_positions").fetchone()
    assert row["status"] == "open" and row["entry_fill_status"] == "pending"
    assert summary["entered"] == 0  # the pending spread still occupies the one slot


def test_moved_entry_evaluation_cancels_and_replaces(live_conn):
    stale = _open_entry_row()
    stale["net"] = 1.50  # stored credit far from the fresh model's 1.07 -> replace
    stale["credit"] = 1.50
    dbmod.save_position(live_conn, stale)
    broker = FakeBroker()
    summary = live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    assert broker.cancelled == ["ORD-E1"]
    old = live_conn.execute("SELECT status FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert old["status"] == "cancelled"
    assert summary["entered"] == 1  # a fresh entry went in at current prices


def test_entry_cancel_failure_repolls_and_records_the_fill(live_conn):
    stale = _open_entry_row()
    stale["net"] = 1.50
    stale["credit"] = 1.50
    dbmod.save_position(live_conn, stale)
    broker = FakeBroker(
        order_statuses={"ORD-E1": {"status": "Filled", "price": "1.48", "filled": True}}, cancel_ok=False
    )
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    row = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert row["entry_fill_status"] == "filled"
    assert row["net"] == pytest.approx(1.48)  # the actual fill price, recorded on the repoll


# --------------------------------------------------------------------------- atomic claim
def test_completion_claim_is_atomic(live_conn):
    dbmod.save_position(live_conn, {**_open_entry_row(entry_fill_status="filled")})
    row = dict(live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone())
    broker = FakeBroker()
    params = {"min_floor_dollars": 0.0, "fee_buffer": 0.10}
    first = live_loop.place_resting_completion(
        live_conn, row, _snapshot(), params, broker, live=True, log=lambda *_: None
    )
    assert first["completion_order_id"] == "ORD1"
    # A second claimant (the row now carries an order id) must lose without placing anything.
    stale_row = dict(row)  # what a racing watcher would hold: the pre-claim snapshot of the row
    second = live_loop.place_resting_completion(
        live_conn, stale_row, _snapshot(), params, broker, live=True, log=lambda *_: None
    )
    assert second is None
    assert len(broker.placed) == 1


def test_failed_placement_releases_the_claim(live_conn):
    class RefusingBroker(FakeBroker):
        def place(self, spec, live):
            super().place(spec, live)
            return {"ok": False, "error": "rejected"}

    dbmod.save_position(live_conn, {**_open_entry_row(entry_fill_status="filled")})
    row = dict(live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone())
    live_loop.place_resting_completion(
        live_conn,
        row,
        _snapshot(),
        {"min_floor_dollars": 0.0, "fee_buffer": 0.10},
        RefusingBroker(),
        live=True,
        log=lambda *_: None,
    )
    after = live_conn.execute(
        "SELECT completion_order_id FROM fly_positions WHERE position_id = 'E1'"
    ).fetchone()
    assert after["completion_order_id"] is None  # claim released; a later tick can retry


# --------------------------------------------------------------------------- watcher
def _watch_cfg():
    cfg = _loop_cfg()
    cfg["live"]["symbol"] = "SPX"
    cfg["live"]["fill_watch_seconds"] = 30
    cfg["live"]["fill_watch_poll_seconds"] = 1
    cfg["live"]["fill_heartbeat_seconds"] = 5
    return cfg


def _fake_cache(monkeypatch, snapshot):
    import provider as providermod

    monkeypatch.setattr(providermod, "build_snapshot", lambda *a, **k: snapshot)


def test_watcher_confirms_entry_and_places_completion(live_conn, monkeypatch):
    dbmod.save_position(live_conn, _open_entry_row())
    _fake_cache(monkeypatch, _snapshot())
    broker = FakeBroker(order_statuses={"ORD-E1": {"status": "Filled", "price": "1.10", "filled": True}})
    ticks = iter(range(0, 1000))
    out = live_loop.run_watch(
        _watch_cfg(),
        live_conn,
        broker,
        cache_path="unused",
        live=True,
        log=lambda *_: None,
        sleep=lambda s: None,
        clock_fn=lambda: next(ticks),
    )
    assert out["confirmed"] >= 1
    row = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert row["entry_fill_status"] == "filled"
    assert row["completion_order_id"] == "ORD1"  # placed by the watcher, immediately
    assert broker.cancelled == []  # the watcher never cancels


def test_watcher_exits_when_nothing_pending(live_conn, monkeypatch):
    _fake_cache(monkeypatch, _snapshot())
    broker = FakeBroker()
    out = live_loop.run_watch(
        _watch_cfg(),
        live_conn,
        broker,
        cache_path="unused",
        live=True,
        log=lambda *_: None,
        sleep=lambda s: None,
        clock_fn=lambda: 0,
    )
    assert out["cycles"] == 0
    assert broker.status_calls == []


def test_watcher_cache_gates_status_polls(live_conn, monkeypatch):
    # Market far from the limit: stored credit 5.00 vs natural ~1.2 -> not touched, and with a
    # huge heartbeat the watcher should never call the broker at all.
    far = _open_entry_row()
    far["net"] = 5.00
    far["credit"] = 5.00
    dbmod.save_position(live_conn, far)
    _fake_cache(monkeypatch, _snapshot())
    broker = FakeBroker()
    cfg = _watch_cfg()
    cfg["live"]["fill_heartbeat_seconds"] = 10_000
    t = {"now": 0.0}

    def clock_fn():
        t["now"] += 1.0
        return t["now"]

    out = live_loop.run_watch(
        cfg,
        live_conn,
        broker,
        cache_path="unused",
        live=True,
        log=lambda *_: None,
        sleep=lambda s: None,
        clock_fn=clock_fn,
    )
    assert out["cycles"] >= 1
    assert broker.status_calls == []  # cache said "not touchable", heartbeat never elapsed


def test_watcher_heartbeat_forces_a_poll(live_conn, monkeypatch):
    far = _open_entry_row()
    far["net"] = 5.00
    far["credit"] = 5.00
    dbmod.save_position(live_conn, far)
    _fake_cache(monkeypatch, _snapshot())
    broker = FakeBroker()
    cfg = _watch_cfg()
    cfg["live"]["fill_heartbeat_seconds"] = 3
    t = {"now": 0.0}

    def clock_fn():
        t["now"] += 1.0
        return t["now"]

    live_loop.run_watch(
        cfg,
        live_conn,
        broker,
        cache_path="unused",
        live=True,
        log=lambda *_: None,
        sleep=lambda s: None,
        clock_fn=clock_fn,
    )
    assert broker.status_calls  # the heartbeat kicked in even though the cache said untouchable


# --------------------------------------------------------------------------- settlement
def _settle_cfg():
    cfg = _loop_cfg()
    cfg["live"]["symbol"] = "SPX"
    cfg["symbols"] = ["SPX"]
    return cfg


def test_provisional_settle_marks_source_and_official_overwrites(live_conn, monkeypatch):
    import provider as providermod

    dbmod.save_position(
        live_conn,
        {**_open_entry_row(entry_fill_status="filled"), "kind": "fly", "net": 0.25, "debit": 0.80},
    )
    monkeypatch.setattr(providermod, "read_spot", lambda *a, **k: 7495.5)
    out = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused")
    assert out["ok"] and out["source"] == "last_trade_provisional"
    book = live_conn.execute("SELECT * FROM fly_books").fetchone()
    assert book["status"] == "settled" and book["settlement_source"] == "last_trade_provisional"
    pos = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert pos["status"] == "settled" and pos["settlement_source"] == "last_trade_provisional"
    provisional_pnl = pos["pnl"]

    # Official print re-settles at a different price and overwrites the provisional numbers.
    out2 = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", price=7494.0)
    assert out2["ok"] and out2["source"] == "official"
    pos2 = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert pos2["settlement_source"] == "official"
    assert pos2["pnl"] != provisional_pnl  # different print, different P&L

    # A second official settle without --force is refused.
    out3 = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", price=7490.0)
    assert not out3["ok"] and "official" in out3["reason"]
    # ...and allowed with force.
    out4 = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", price=7490.0, force=True)
    assert out4["ok"]


def test_settle_targets_an_explicit_past_date(live_conn, monkeypatch):
    """The next-morning official-print confirm settles YESTERDAY's book — `when` must reach
    run_settle_live rather than being pinned to wall-clock today."""
    from datetime import datetime as _dt

    import provider as providermod

    monkeypatch.setattr(providermod, "read_spot", lambda *a, **k: None)  # explicit price only
    dbmod.save_position(
        live_conn,
        {**_open_entry_row(entry_fill_status="filled"), "kind": "fly", "net": 0.25, "debit": 0.80},
    )
    when = _dt.fromisoformat(f"{DAY}T12:00:00")
    out = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", when=when, price=7495.0)
    assert out["ok"] and out["source"] == "official"
    pos = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert pos["status"] == "settled" and pos["settlement_source"] == "official"


def test_settle_refuses_without_fresh_spot_and_retries(live_conn, monkeypatch):
    import provider as providermod

    dbmod.save_position(live_conn, {**_open_entry_row(entry_fill_status="filled")})
    monkeypatch.setattr(providermod, "read_spot", lambda *a, **k: None)
    out = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused")
    assert not out["ok"] and out["reason"] == "no_settlement_price"
    row = live_conn.execute("SELECT status FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert row["status"] == "open"  # untouched — the next tick retries


def test_live_book_rollup_written_by_tick(live_conn):
    broker = FakeBroker()
    cfg = _loop_cfg()
    cfg["live"]["symbol"] = "SPX"
    live_loop.run_once(cfg, _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    book = live_conn.execute("SELECT * FROM fly_books").fetchone()
    assert book is not None and book["arm"] == "gex" and book["status"] == "open"


# --------------------------------------------------------------------------- orphan sweep
def test_orphan_sweep_flags_unknown_working_orders(live_conn):
    dbmod.save_position(live_conn, _open_entry_row(order_id="ORD-KNOWN"))
    broker = FakeBroker(
        working=[
            {"order_id": "ORD-KNOWN", "status": "Live", "underlying_symbol": "XSP"},
            {"order_id": "ORD-MYSTERY", "status": "Live", "underlying_symbol": "XSP"},
        ]
    )
    logs = []
    summary = live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=logs.append)
    assert summary["orphaned_orders"] == 1
    assert any("ORPHANED" in m and "ORD-MYSTERY" in m for m in logs)
    assert live_loop.read_orphans()[0]["order_id"] == "ORD-MYSTERY"  # persisted for --status


def test_orphan_sweep_clean_when_all_accounted_for(live_conn):
    dbmod.save_position(live_conn, _open_entry_row(order_id="ORD-KNOWN"))
    broker = FakeBroker(working=[{"order_id": "ORD-KNOWN", "status": "Live", "underlying_symbol": "XSP"}])
    summary = live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    assert summary["orphaned_orders"] == 0
    assert live_loop.read_orphans() == []


def test_orphan_sweep_failure_does_not_break_the_tick(live_conn):
    class SweepFails(FakeBroker):
        def working_orders(self):
            raise RuntimeError("broker unreachable")

    logs = []
    summary = live_loop.run_once(
        _loop_cfg(), _snapshot(), live_conn, SweepFails(), live=True, log=logs.append
    )
    assert summary["orphaned_orders"] == 0
    assert any("orphan sweep failed" in m for m in logs)
    assert summary["entered"] == 1  # the rest of the tick ran normally


# --------------------------------------------------------------------------- locks and disarm
def test_once_lock_blocks_second_acquirer(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = live_loop._once_lock_path()
    assert live_loop._acquire_lock(path) is True
    assert live_loop._acquire_lock(path) is False  # held
    live_loop._release_lock(path)
    assert live_loop._acquire_lock(path) is True
    live_loop._release_lock(path)


def test_stale_lock_is_stolen(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = live_loop._once_lock_path()
    assert live_loop._acquire_lock(path) is True
    old = __import__("time").time() - 600
    __import__("os").utime(path, (old, old))
    assert live_loop._acquire_lock(path, stale_seconds=180) is True  # stolen
    live_loop._release_lock(path)


def test_should_disarm(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    cfg = {"live": {"disarm_time": "17:00"}}
    import provider as providermod

    today = providermod.now_et().date().isoformat()
    # No stamp at all -> disarm (arming didn't go through the command path).
    assert live_loop.should_disarm(cfg, 10 * 60, today) is not None
    live_loop._write_arm_stamp()
    # Fresh stamp, mid-session -> keep running.
    assert live_loop.should_disarm(cfg, 10 * 60, today) is None
    # Fresh stamp but past disarm time -> disarm.
    assert "disarm time" in live_loop.should_disarm(cfg, 17 * 60 + 1, today)
    # Stale stamp (yesterday's arm surviving into today) -> disarm regardless of clock.
    assert live_loop.should_disarm(cfg, 10 * 60, "2099-01-01") is not None


def test_completion_cancel_failure_is_logged_not_silently_dropped(live_conn):
    import clock

    dbmod.save_position(
        live_conn,
        {
            "position_id": "STUCK1",
            "book_id": f"{DAY}:gex:SPX",
            "trade_date": DAY,
            "arm": "gex",
            "entry_mode": "legged",
            "symbol": "SPX",
            "kind": "short_vertical",
            "side": PUT,
            "center": 7495.0,
            "wing_width": 5,
            "quantity": 1,
            "net": 1.05,
            "credit": 1.05,
            "fees": 3.44,
            "status": "open",
            "entry_time": clock.now_iso(),
            "entry_order_id": "ORD-S1",
            "entry_fill_status": "filled",
            "completion_order_id": "ORD-STUCK",
            "completion_fill_status": "pending",
        },
    )
    logs = []
    broker = FakeBroker(cancel_ok=False)
    snap = _snapshot(now_min=15 * 60 + 45)  # past cutoff
    summary = live_loop.run_once(_loop_cfg(), snap, live_conn, broker, live=True, log=logs.append)
    assert summary["cancelled"] == 0
    assert any("cutoff cancel FAILED" in m for m in logs)
    row = live_conn.execute(
        "SELECT completion_order_id FROM fly_positions WHERE position_id = 'STUCK1'"
    ).fetchone()
    assert row["completion_order_id"] == "ORD-STUCK"  # left in place, not silently cleared
