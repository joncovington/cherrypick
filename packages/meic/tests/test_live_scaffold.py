"""The MEIC live scaffold: pure order builders, readiness gates, and the gated loop.

Everything here is offline -- the broker is a fake, and the point under test is that the
scaffold is INERT by default: no gate, no order. Mirrors flies/tests/test_live_scaffold.py's
structure. Reuses paper.py's own pure decision functions directly (not reimplemented) so a
regression in the shared engine shows up here too.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from cherrypick.meic import live_loop, live_orders, paper

DAY = "2026-07-08"  # ordinary Wednesday -- not a quarterly/FOMC/witching day (verified)
_DBPY = ["-m", "cherrypick.meic.db"]


def _init_db(tmp_path, name="live.db"):
    db_path = str(tmp_path / name)
    subprocess.run([sys.executable, *_DBPY, "--db", db_path, "init_db"], check=True, capture_output=True)
    return db_path


# --------------------------------------------------------------------------- order builders


def _leg(strike, sym, bid, ask):
    return {"strike": strike, "streamer_symbol": sym, "bid": bid, "ask": ask}


CHOSEN = {
    "short_put": _leg(583, "SP", 0.55, 0.65),
    "long_put": _leg(578, "LP", 0.15, 0.25),
    "short_call": _leg(598, "SC", 0.50, 0.60),
    "long_call": _leg(603, "LC", 0.12, 0.22),
    "net_credit": 1.07,
}


def test_tick_floor_and_ceil_round_toward_the_house():
    assert live_orders.tick_floor(1.07) == 1.05
    assert live_orders.tick_ceil(1.07) == 1.10
    assert live_orders.tick_floor(1.05) == 1.05
    assert live_orders.tick_ceil(1.05) == 1.05


def test_entry_spec_sells_shorts_buys_longs_at_floored_credit():
    spec = live_orders.entry_spec(CHOSEN)
    assert spec["price"] == 1.05 and spec["price_effect"] == "credit"
    actions = {leg["symbol"]: leg["action"] for leg in spec["legs"]}
    assert actions["SP"] == "sell to open" and actions["LP"] == "buy to open"
    assert actions["SC"] == "sell to open" and actions["LC"] == "buy to open"


def test_entry_spec_refuses_non_positive_credit():
    with pytest.raises(ValueError, match="floors to nothing"):
        live_orders.entry_spec({**CHOSEN, "net_credit": 0.02})


def test_entry_spec_refuses_legs_without_streamer_symbol():
    chosen = {**CHOSEN, "short_put": {"strike": 583, "bid": 0.55, "ask": 0.65}}
    with pytest.raises(ValueError, match="streamer_symbol"):
        live_orders.entry_spec(chosen)


TRADE = {
    "put_symbol": "SP",
    "long_put_symbol": "LP",
    "call_symbol": "SC",
    "long_call_symbol": "LC",
    "quantity": 1,
}
LEG_QUOTES = {
    "SP": {"bid": 0.55, "ask": 0.65},
    "LP": {"bid": 0.15, "ask": 0.25},
    "SC": {"bid": 0.50, "ask": 0.60},
    "LC": {"bid": 0.12, "ask": 0.22},
}


def test_stop_close_spec_prices_the_cushioned_crossing_debit():
    spec = live_orders.stop_close_spec(TRADE, "put", LEG_QUOTES, stop_limit_ratio=1.02)
    raw = 0.65 - 0.15  # short ask - long bid
    assert spec["price"] == live_orders.tick_ceil(raw * 1.02)
    assert spec["price_effect"] == "debit"
    actions = {leg["symbol"]: leg["action"] for leg in spec["legs"]}
    assert actions["SP"] == "buy to close" and actions["LP"] == "sell to close"


def test_force_close_spec_covers_only_the_open_sides():
    spec = live_orders.force_close_spec(TRADE, LEG_QUOTES, put_open=True, call_open=False)
    symbols = {leg["symbol"] for leg in spec["legs"]}
    assert symbols == {"SP", "LP"}


def test_force_close_spec_refuses_nothing_open():
    with pytest.raises(ValueError, match="nothing open"):
        live_orders.force_close_spec(TRADE, LEG_QUOTES, put_open=False, call_open=False)


# --------------------------------------------------------------------------- readiness gates

BASE_CFG = {
    "enable_live_trading": True,
    "live": {"symbol": "XSP", "gate0_confirmed": "jon 2026-08-01"},
}


def test_readiness_passes_only_with_every_gate():
    assert live_loop.readiness(BASE_CFG, halt_present=False, designated="5W1") == []


def test_readiness_names_each_unmet_gate():
    unmet = live_loop.readiness({"live": {}}, halt_present=True, designated=None)
    text = " ".join(unmet)
    assert "enable_live_trading" in text
    assert "live.symbol" in text
    assert "gate0_confirmed" in text
    assert "halt flag" in text
    assert "designated" in text


def test_readiness_requires_a_pinned_symbol():
    cfg = {**BASE_CFG, "live": {**BASE_CFG["live"], "symbol": ""}}
    assert any("live.symbol" in u for u in live_loop.readiness(cfg, halt_present=False, designated="x"))


# --------------------------------------------------------------------------- daily-loss breaker


def test_daily_loss_breaker(tmp_path):
    db_path = _init_db(tmp_path)
    paper._save_trade({"ic_order_id": "L1", "trade_date": DAY, "symbol": "XSP", "pnl": -250.0}, db_path)
    assert live_loop.daily_loss_tripped(db_path, DAY, 200.0) is True
    assert live_loop.daily_loss_tripped(db_path, DAY, 300.0) is False
    assert live_loop.daily_loss_tripped(db_path, DAY, None) is False


def test_live_ledger_is_a_separate_file():
    from cherrypick.meic import paths as _paths

    assert str(_paths.live_db_path()).endswith("meic_trades.db")
    assert str(_paths.paper_db_path()).endswith("paper_trades.db")
    assert _paths.live_db_path() != _paths.paper_db_path()


# --------------------------------------------------------------------------- the loop, faked


class FakeBroker:
    def __init__(self, statuses=None):
        self.placed = []
        self.polled = []
        self.cancelled = []
        self._statuses = dict(statuses or {})

    def place(self, spec, live):
        self.placed.append({"spec": spec, "live": live})
        return {"ok": True, "response": {"order": {"id": f"ORD{len(self.placed)}"}}}

    def status(self, order_id):
        self.polled.append(order_id)
        return self._statuses.get(order_id, {"order_id": order_id, "status": "Live", "price": None})

    def cancel(self, order_id):
        self.cancelled.append(order_id)
        return {"ok": True, "order_id": order_id}


def _config(tmp_path=None, **live_over):
    cfg = paper.load_base_config()
    cfg = dict(cfg)
    cfg["enable_live_trading"] = True
    cfg["live"] = {
        "symbol": "XSP",
        "gate0_confirmed": "jon 2026-08-01",
        "daily_loss_halt_dollars": 200,
        **live_over,
    }
    return cfg


def _entry_snapshot(symbol="XSP", **over):
    def q(strike, sym, bid, ask, delta):
        return {"strike": strike, "streamer_symbol": sym, "bid": bid, "ask": ask, "delta": delta}

    candidate = {
        "wing_width": 5,
        "short_put": q(583, "SP", 0.55, 0.65, -0.15),
        "long_put": q(578, "LP", 0.15, 0.25, -0.06),
        "short_call": q(598, "SC", 0.50, 0.60, 0.15),
        "long_call": q(603, "LC", 0.12, 0.22, 0.06),
        "short_delta": 0.16,
        "is_default_delta": True,
    }
    snap = {
        "symbol": symbol,
        "date": DAY,
        "now_et": "12:30",  # past late_entry_bias_start_time (noon) so the borderline IV rank below clears
        "expiration": DAY,
        "dte": 0,
        "underlying_price": 590.0,
        "iv_rank": 0.35,
        "vix": 16.0,
        "vix1d_ratio": 1.0,
        "atr_5day": 5.0,
        "intraday_range_pct": 0.002,
        "session_quality": "midday",
        "gex": {"ok": False},
        "candidates": [candidate],
        "leg_quotes": {
            sym: {"bid": b, "ask": a}
            for sym, b, a in (("SP", 0.55, 0.65), ("LP", 0.15, 0.25), ("SC", 0.50, 0.60), ("LC", 0.12, 0.22))
        },
    }
    snap.update(over)
    return snap


def _open_trades(db_path, symbol):
    import json

    result = subprocess.run(
        [sys.executable, *_DBPY, "--db", db_path, "get_open_trades", "--symbol", symbol, "--date", DAY],
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1]).get("open_trades", [])


def test_dry_run_entry_places_nothing_live_and_leaves_the_ledger_empty(tmp_path):
    db_path = _init_db(tmp_path)
    broker = FakeBroker()
    summary = live_loop.run_once(
        _config(), _entry_snapshot(), db_path, broker, live=False, log=lambda *_: None
    )
    assert summary["entry"]["entry"] == "dry_run"
    assert broker.placed and broker.placed[0]["live"] is False
    # A dry-run preflight must leave the live ledger empty -- nothing was actually opened.
    assert _open_trades(db_path, "XSP") == []


def test_live_entry_records_the_real_order_id(tmp_path):
    db_path = _init_db(tmp_path)
    broker = FakeBroker()
    summary = live_loop.run_once(
        _config(), _entry_snapshot(), db_path, broker, live=True, log=lambda *_: None
    )
    assert summary["entry"]["entry"] == "placed", "an accepted order is not a fill"
    assert summary["entry"]["ic_order_id"] == "LIVE-XSP-ORD1"
    row = _open_trades(db_path, "XSP")[0]
    assert row["status"] == "pending" and row["fill_confirmed_at"] is None


def test_live_ignores_a_paper_profile_overlap_scope_and_still_refuses_overlap(tmp_path):
    """A paper stream can set overlap_scope: 'none' (config.risk.json, profile-level) to sample
    every tick independently -- live must never inherit that. live_loop.run_once builds its
    params as paper._merged_params(config, {}) — an EMPTY profile overlay — so no
    config.risk.json key can reach it regardless of what any paper profile declares; only
    config.json's own (unset) overlap_scope applies, which defaults to the strictest 'all'.
    This proves the isolation both directions: paper accepts the overlap under 'none', live
    refuses the identical candidate through the real run_once path."""
    db_path = _init_db(tmp_path)
    paper._save_trade(_open_trade_row(symbol="XSP"), db_path)  # occupies the 583/598 short pair
    snap = _entry_snapshot()  # candidate is also 583 put / 598 call — an exact overlap

    # Paper side: a stream with overlap_scope "none" accepts the exact same overlapping candidate.
    paper_params = {**paper._merged_params(paper.load_base_config(), {}), "overlap_scope": "none"}
    entered, reason, _ = paper.evaluate_entry(
        snap, paper_params, open_ics=[_open_trade_row(symbol="XSP")], account_open_count=1
    )
    assert entered is True, reason

    # Live side: config.risk.json is never consulted (params = _merged_params(config, {})), so
    # only config.json's own top-level overlap_scope ("shorts", the independent-sampling
    # default) applies -- still refuses an exact short-pair repeat, unlike paper's "none" stream.
    broker = FakeBroker()
    summary = live_loop.run_once(_config(), snap, db_path, broker, live=True, log=lambda *_: None)
    assert summary["entry"]["entry"] == "skipped"
    assert summary["entry"]["reason"] == "short_pair_occupied"
    assert broker.placed == []  # nothing submitted


def _open_trade_row(symbol="QQQ"):
    return {
        "ic_order_id": "OPEN1",
        "trade_date": DAY,
        "entry_time": f"{DAY} 10:00:00",
        "expiration": DAY,
        "symbol": symbol,
        "put_strike": 583,
        "call_strike": 598,
        "wing_width": 5,
        "put_symbol": "SP",
        "call_symbol": "SC",
        "long_put_symbol": "LP",
        "long_call_symbol": "LC",
        "put_credit": 0.55,
        "call_credit": 0.52,
        "net_credit": 1.07,
        "quantity": 1,
        "status": "open",
        "risk_profile": "live",
        "execution_mode": "live",
    }


def test_force_close_submits_a_real_close_order_and_records_its_id(tmp_path):
    db_path = _init_db(tmp_path)
    paper._save_trade(_open_trade_row(), db_path)
    broker = FakeBroker()
    # Past QQQ's physical_settlement_force_close_time (15:30) -- forces a deterministic
    # force-close regardless of credit/price math, unlike a per-side stop trigger.
    snap = _entry_snapshot(symbol="QQQ", now_et="15:31", candidates=[])
    summary = live_loop.run_once(_config(symbol="QQQ"), snap, db_path, broker, live=True, log=lambda *_: None)
    assert summary["closing"] == 1 and summary["force_closed"] == 0, (
        "recorded on confirmation, not submission"
    )
    assert any(p["live"] is True for p in broker.placed)
    row = _open_trades(db_path, "QQQ")[0]  # STILL open: the broker has not said it closed
    assert row["put_stop_fill_status"] == "pending" and row["call_stop_fill_status"] == "pending"
    assert row["put_stop_order_id"] and row["call_stop_order_id"]
    # ...until it does: both sides fill, the stashed decision is applied at the actual prices.
    broker._statuses = {
        oid: {"status": "Filled", "price": "-0.70"}
        for oid in (row["put_stop_order_id"], row["call_stop_order_id"])
    }
    summary = live_loop.run_once(_config(symbol="QQQ"), snap, db_path, broker, live=True, log=lambda *_: None)
    assert summary["closes_filled"] == 2
    assert _open_trades(db_path, "QQQ") == []
    done = live_loop._trade_row("OPEN1", db_path)
    assert done["status"] == "force_closed"
    # put (0.55 - 0.70) x100 + call (0.52 - 0.70) x100 at the ACTUAL fills, not the limits asked
    assert done["pnl"] == pytest.approx(-15.0 - 18.0)


def test_readiness_blocks_live_before_run_once_would_even_be_reached():
    # Belt-and-suspenders: main() checks this before calling run_once at all when --live is
    # passed. Exercised directly here since main() itself needs real credentials/config on disk.
    unmet = live_loop.readiness({"live": {"symbol": "XSP"}}, halt_present=False, designated=None)
    assert unmet  # gate0_confirmed and designated account are both still unmet


# --------------------------------------------------------------------------- fill confirmation (2026-09-17)
def _place_one(tmp_path, statuses=None):
    db_path = _init_db(tmp_path)
    broker = FakeBroker(statuses=statuses)
    live_loop.run_once(_config(), _entry_snapshot(), db_path, broker, live=True, log=lambda *_: None)
    return db_path, broker


def test_a_confirmed_entry_opens_at_the_actual_credit(tmp_path):
    """Shown to fail on the pre-change loop, which recorded the limit as the fill and never asked."""
    db_path, broker = _place_one(tmp_path, {"ORD1": {"status": "Filled", "price": "-1.37", "filled": True}})
    asked = _open_trades(db_path, "XSP")[0]["net_credit"]
    summary = live_loop.run_once(
        _config(), _entry_snapshot(), db_path, broker, live=True, log=lambda *_: None
    )
    assert summary["entries_confirmed"] == 1 and "ORD1" in broker.polled
    row = [t for t in _open_trades(db_path, "XSP") if t["ic_order_id"] == "LIVE-XSP-ORD1"][0]
    assert row["status"] == "open" and row["fill_confirmed_at"] is not None
    assert row["net_credit"] == pytest.approx(1.37) and row["net_credit"] != asked


def test_a_rejected_entry_is_cancelled_and_frees_its_slot(tmp_path):
    db_path, broker = _place_one(tmp_path, {"ORD1": {"status": "Rejected", "price": None, "filled": False}})
    summary = live_loop.run_once(
        _config(), _entry_snapshot(), db_path, broker, live=True, log=lambda *_: None
    )
    assert summary["entries_cancelled"] == 1
    assert all(t["ic_order_id"] != "LIVE-XSP-ORD1" for t in _open_trades(db_path, "XSP")), "no longer open"
    import sqlite3

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT status, exit_reason, pnl FROM ic_trades WHERE ic_order_id = 'LIVE-XSP-ORD1'"
    ).fetchone()
    assert row == ("cancelled", "entry_rejected", 0)


def test_a_pending_entry_holds_its_slot_but_is_not_managed(tmp_path):
    cfg = _config()
    cfg["max_concurrent_ics"] = 1
    db_path, broker = _place_one(tmp_path)
    summary = live_loop.run_once(cfg, _entry_snapshot(), db_path, broker, live=True, log=lambda *_: None)
    assert summary["entry"] == {"entry": "skipped", "reason": "max_concurrent_ics_reached"}
    assert summary["held"] == 1 and len(broker.placed) == 1, "no close order for a position not yet held"


def _stopping_snapshot():
    """A snapshot where the QQQ put side's cost-to-close has blown through the stop trigger."""
    snap = _entry_snapshot(symbol="QQQ", now_et="11:00", candidates=[])
    snap["leg_quotes"] = {
        "SP": {"bid": 2.40, "ask": 2.50},  # short put: was 0.55 credit, now 2.5 to close
        "LP": {"bid": 0.10, "ask": 0.15},
        "SC": {"bid": 0.30, "ask": 0.35},
        "LC": {"bid": 0.05, "ask": 0.08},
    }
    return snap


def test_a_stop_is_recorded_only_when_the_broker_confirms_it_at_the_actual_price(tmp_path):
    """Shown to fail on the pre-change loop, which recorded the side closed at submission at the
    limit asked for. Now: submit -> side marked closing, ledger unchanged; fill -> the shared exit
    accounting runs with the ACTUAL price; the IC reads partial with that side's stop cost."""
    db_path = _init_db(tmp_path)
    paper._save_trade(_open_trade_row("QQQ"), db_path)
    broker = FakeBroker()
    snap = _stopping_snapshot()
    s1 = live_loop.run_once(_config(symbol="QQQ"), snap, db_path, broker, live=True, log=lambda *_: None)
    assert s1["closing"] == 1 and s1["stopped"] == 0
    row = live_loop._trade_row("OPEN1", db_path)
    assert row["status"] == "open" and row["put_stop_cost"] is None, "not recorded closed on submit"
    assert row["put_stop_fill_status"] == "pending" and row["call_stop_fill_status"] is None
    asked = float(broker.placed[-1]["spec"]["price"])
    stashed = json.loads(row["pending_exit_json"])
    assert stashed["action"] == "stop_put" and stashed["decision"]["put_exit_price"] == asked

    broker._statuses = {row["put_stop_order_id"]: {"status": "Filled", "price": "-2.61"}}
    s2 = live_loop.run_once(_config(symbol="QQQ"), snap, db_path, broker, live=True, log=lambda *_: None)
    assert s2["closes_filled"] == 1
    row = live_loop._trade_row("OPEN1", db_path)
    assert (
        row["status"] == "partial"
        and row["put_stop_cost"] == 2.61
        and row["put_stop_fill_status"] == "filled"
    )
    assert row["put_stop_cost"] != asked


def test_a_working_close_is_cancelled_and_replaced_at_fresh_quotes_each_tick(tmp_path):
    db_path = _init_db(tmp_path)
    paper._save_trade(_open_trade_row("QQQ"), db_path)
    broker = FakeBroker()
    snap = _stopping_snapshot()
    live_loop.run_once(_config(symbol="QQQ"), snap, db_path, broker, live=True, log=lambda *_: None)
    first = live_loop._trade_row("OPEN1", db_path)["put_stop_order_id"]
    # next tick, still Live at the broker, quotes moved: cancel, replace at the new crossing price
    snap["leg_quotes"]["SP"] = {"bid": 2.90, "ask": 3.00}
    s2 = live_loop.run_once(_config(symbol="QQQ"), snap, db_path, broker, live=True, log=lambda *_: None)
    assert s2["closes_repriced"] == 1 and broker.cancelled == [first]
    row = live_loop._trade_row("OPEN1", db_path)
    assert row["put_stop_order_id"] != first and row["put_stop_fill_status"] == "pending"
    assert float(broker.placed[-1]["spec"]["price"]) > float(broker.placed[0]["spec"]["price"])
    assert row["status"] == "open" and row["put_stop_cost"] is None
    # and no second, duplicate stop was submitted by the entry/manage pass
    assert len([p for p in broker.placed if p["spec"]["price_effect"] == "debit"]) == 2


def test_a_dead_close_leaves_the_side_open_and_the_next_tick_resubmits(tmp_path):
    """Nothing was recorded closed, so nothing is reopened: the marker clears and the same rule
    fires again on the next evaluation -- the resubmission policy chosen 2026-09-17."""
    db_path = _init_db(tmp_path)
    paper._save_trade(_open_trade_row("QQQ"), db_path)
    broker = FakeBroker()
    snap = _stopping_snapshot()
    live_loop.run_once(_config(symbol="QQQ"), snap, db_path, broker, live=True, log=lambda *_: None)
    first = live_loop._trade_row("OPEN1", db_path)["put_stop_order_id"]
    broker._statuses = {first: {"status": "Rejected", "price": None}}
    logs = []
    s2 = live_loop.run_once(_config(symbol="QQQ"), snap, db_path, broker, live=True, log=logs.append)
    assert s2["closes_dead"] == 1
    assert not any("CRITICAL" in line for line in logs), (
        "a dead close is a normal event now, not a ledger defect"
    )
    row = live_loop._trade_row("OPEN1", db_path)
    assert row["status"] == "open" and row["put_stop_cost"] is None
    # the same tick's manage pass already re-submitted under the same rule
    assert row["put_stop_order_id"] != first and row["put_stop_fill_status"] == "pending"
    assert s2["closing"] == 1


def test_settlement_cancels_a_working_close_and_falls_through_to_expiry(tmp_path):
    """An unfilled end-of-day close on a cash-settled side is not chased: at settlement the
    working order is cancelled and the side settles at intrinsic, recorded as expired -- the
    path a held side takes (the EOD fallback chosen 2026-09-17)."""
    db_path = _init_db(tmp_path)
    row = _open_trade_row("XSP")
    row.update(
        put_stop_order_id="W1",
        put_stop_fill_status="pending",
        pending_exit_json=json.dumps(
            {"action": "stop_put", "decision": {"action": "stop_put", "put_exit_price": 2.5}}
        ),
    )
    paper._save_trade(row, db_path)
    snap = _entry_snapshot(symbol="XSP", now_et="16:01", candidates=[])
    snap["underlying_price"] = 590.0  # between the strikes: both sides expire worthless
    snap["leg_quotes"] = {}
    broker = FakeBroker()  # W1 still Live at the broker
    summary = live_loop.run_once(_config(symbol="XSP"), snap, db_path, broker, live=True, log=lambda *_: None)
    assert summary["expired"] == 1
    assert broker.cancelled == ["W1"], "the working close is cancelled, not chased into the bell"
    done = live_loop._trade_row("OPEN1", db_path)
    assert done["status"] == "expired" and done["put_stop_fill_status"] == "cancelled"
    assert done["put_stop_cost"] is None, "never recorded as stopped"


def test_the_live_seam_is_the_shared_adapter(monkeypatch):
    from cherrypick.core import execution

    broker = live_loop.make_broker(_config(), "1234")
    assert isinstance(broker, execution.Broker)
    # its gates are this module's readiness, re-checked per live submit
    monkeypatch.setattr(live_loop.os.path, "exists", lambda p: True)  # halt flag present
    out = broker.place({"legs": []}, live=True)
    assert out["error"] == "live submission gated" and any("halt flag" in g for g in out["unmet_gates"])
