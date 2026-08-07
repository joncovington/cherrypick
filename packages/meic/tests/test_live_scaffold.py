"""The MEIC live scaffold: pure order builders, readiness gates, and the gated loop.

Everything here is offline -- the broker is a fake, and the point under test is that the
scaffold is INERT by default: no gate, no order. Mirrors flies/tests/test_live_scaffold.py's
structure. Reuses paper.py's own pure decision functions directly (not reimplemented) so a
regression in the shared engine shows up here too.
"""

from __future__ import annotations

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
    def __init__(self):
        self.placed = []

    def place(self, spec, live):
        self.placed.append({"spec": spec, "live": live})
        return {"ok": True, "response": {"order": {"id": f"ORD{len(self.placed)}"}}}


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
    assert summary["entry"]["entry"] == "filled"
    assert summary["entry"]["ic_order_id"] == "LIVE-XSP-ORD1"


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
    assert summary["force_closed"] == 1
    assert any(p["live"] is True for p in broker.placed)
    assert _open_trades(db_path, "QQQ") == []  # no longer open


def test_readiness_blocks_live_before_run_once_would_even_be_reached():
    # Belt-and-suspenders: main() checks this before calling run_once at all when --live is
    # passed. Exercised directly here since main() itself needs real credentials/config on disk.
    unmet = live_loop.readiness({"live": {"symbol": "XSP"}}, halt_present=False, designated=None)
    assert unmet  # gate0_confirmed and designated account are both still unmet
