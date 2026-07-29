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
from broker_cli import live_gates
from engine import PUT

DAY = "2026-07-29"


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
    def __init__(self):
        self.placed = []
        self.cancelled = []

    def place(self, spec, live):
        self.placed.append({"spec": spec, "live": live})
        return {"ok": True, "dry_run": not live, "order_id": f"ORD{len(self.placed)}"}

    def cancel(self, order_id):
        self.cancelled.append(order_id)
        return {"ok": True}


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
            "completion_order_id": "ORD9",
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
