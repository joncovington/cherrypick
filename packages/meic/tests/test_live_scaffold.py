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
# Real symbols: the row and the candidate carry the DXLink streamer symbol (the stream cache key),
# the broker takes and reports OCC. Placeholder names here once hid that every live order named its
# legs by the streamer symbol (2026-10-12); `FakeBroker.place` now refuses anything but OCC.
SP, LP, SC, LC = ".XSP260708P583", ".XSP260708P578", ".XSP260708C598", ".XSP260708C603"
OCC = {
    SP: "XSP   260708P00583000",
    LP: "XSP   260708P00578000",
    SC: "XSP   260708C00598000",
    LC: "XSP   260708C00603000",
}


def _is_occ(symbol) -> bool:
    """An OCC 2010 option symbol: 21 characters, and the cache's converter maps it to a streamer
    symbol that maps straight back."""
    from cherrypick.core.streamcache import occ_to_streamer_symbol, streamer_to_occ_symbol

    return (
        isinstance(symbol, str)
        and len(symbol) == 21
        and streamer_to_occ_symbol(occ_to_streamer_symbol(symbol)) == symbol
    )


def _leg(strike, sym, bid, ask):
    return {"strike": strike, "streamer_symbol": sym, "bid": bid, "ask": ask}


CHOSEN = {
    "short_put": _leg(583, SP, 0.55, 0.65),
    "long_put": _leg(578, LP, 0.15, 0.25),
    "short_call": _leg(598, SC, 0.50, 0.60),
    "long_call": _leg(603, LC, 0.12, 0.22),
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
    assert actions[OCC[SP]] == "sell to open" and actions[OCC[LP]] == "buy to open"
    assert actions[OCC[SC]] == "sell to open" and actions[OCC[LC]] == "buy to open"


def test_entry_spec_refuses_non_positive_credit():
    with pytest.raises(ValueError, match="floors to nothing"):
        live_orders.entry_spec({**CHOSEN, "net_credit": 0.02})


def test_entry_spec_refuses_legs_without_streamer_symbol():
    chosen = {**CHOSEN, "short_put": {"strike": 583, "bid": 0.55, "ask": 0.65}}
    with pytest.raises(ValueError, match="streamer_symbol"):
        live_orders.entry_spec(chosen)


TRADE = {
    "put_symbol": SP,
    "long_put_symbol": LP,
    "call_symbol": SC,
    "long_call_symbol": LC,
    "quantity": 1,
}
LEG_QUOTES = {
    SP: {"bid": 0.55, "ask": 0.65},
    LP: {"bid": 0.15, "ask": 0.25},
    SC: {"bid": 0.50, "ask": 0.60},
    LC: {"bid": 0.12, "ask": 0.22},
}


def test_stop_close_spec_prices_the_cushioned_crossing_debit():
    spec = live_orders.stop_close_spec(TRADE, "put", LEG_QUOTES, stop_limit_ratio=1.02)
    raw = 0.65 - 0.15  # short ask - long bid
    assert spec["price"] == live_orders.tick_ceil(raw * 1.02)
    assert spec["price_effect"] == "debit"
    actions = {leg["symbol"]: leg["action"] for leg in spec["legs"]}
    assert actions[OCC[SP]] == "buy to close" and actions[OCC[LP]] == "sell to close"


def test_force_close_spec_covers_only_the_open_sides():
    spec = live_orders.force_close_spec(TRADE, LEG_QUOTES, put_open=True, call_open=False)
    symbols = {leg["symbol"] for leg in spec["legs"]}
    assert symbols == {OCC[SP], OCC[LP]}


def test_every_order_builder_names_its_legs_by_occ_symbol():
    """Shown to fail before 2026-10-12: all three builders sent the streamer symbol, which no
    tastytrade order accepts. Pairs as the streamer recorded them (`stream_chain`) included."""
    specs = [
        live_orders.entry_spec(CHOSEN, 2),
        live_orders.stop_close_spec(TRADE, "put", LEG_QUOTES),
        live_orders.stop_close_spec(TRADE, "call", LEG_QUOTES),
        live_orders.force_close_spec(TRADE, LEG_QUOTES),
    ]
    sent = [leg["symbol"] for spec in specs for leg in spec["legs"]]
    assert all(_is_occ(s) for s in sent), sent
    assert set(sent) == set(OCC.values())
    assert [leg["quantity"] for leg in specs[0]["legs"]] == [2, 2, 2, 2]
    for streamer, occ in (
        (".XSP261009P460", "XSP   261009P00460000"),
        (".SPXW261009C3000", "SPXW  261009C03000000"),
        (".QQQ261009P450", "QQQ   261009P00450000"),
        (".KR260918P52.5", "KR    260918P00052500"),
    ):
        assert live_orders.order_symbol(streamer) == occ
        assert live_orders.order_symbol(occ) == occ  # an OCC symbol passes through


def test_a_leg_with_no_occ_form_is_never_sent():
    for bad in ("SP", "", None, ".XSP260708Q583", "XSP"):
        with pytest.raises(ValueError, match="no OCC form"):
            live_orders.order_symbol(bad)
    with pytest.raises(ValueError, match="no OCC form"):
        live_orders.stop_close_spec(
            {**TRADE, "put_symbol": "SP"}, "put", {**LEG_QUOTES, "SP": LEG_QUOTES[SP]}
        )


def test_force_close_spec_refuses_nothing_open():
    with pytest.raises(ValueError, match="nothing open"):
        live_orders.force_close_spec(TRADE, LEG_QUOTES, put_open=False, call_open=False)


# --------------------------------------------------------------------------- readiness gates

BASE_CFG = {
    "enable_live_trading": True,
    "live": {"symbol": "XSP", "gate0_confirmed": "jon 2026-08-01"},
}


def test_readiness_passes_only_with_every_gate():
    assert live_loop.readiness(BASE_CFG, designated="5W1") == []


def test_readiness_names_each_unmet_gate():
    unmet = live_loop.readiness({"live": {}}, designated=None)
    text = " ".join(unmet)
    assert "enable_live_trading" in text
    assert "live.symbol" in text
    assert "gate0_confirmed" in text
    assert "designated" in text
    assert "halt" not in text  # an ENTRY blocker since 2026-10-08 (entry_blockers), never a close blocker


def test_readiness_requires_a_pinned_symbol():
    cfg = {**BASE_CFG, "live": {**BASE_CFG["live"], "symbol": ""}}
    assert any("live.symbol" in u for u in live_loop.readiness(cfg, designated="x"))


# --------------------------------------------------------------------------- daily-loss breaker


def test_daily_loss_breaker(tmp_path):
    db_path = _init_db(tmp_path)
    paper._save_trade({"ic_order_id": "L1", "trade_date": DAY, "symbol": "XSP", "pnl": -250.0}, db_path)
    assert live_loop.daily_loss_tripped(db_path, DAY, 200.0) is True
    assert live_loop.daily_loss_tripped(db_path, DAY, 300.0) is False
    assert live_loop.daily_loss_tripped(db_path, DAY, None) is False


def test_the_daily_loss_breaker_fails_closed_on_an_unreadable_ledger(tmp_path):
    """It only blocks new entries now, so an unreadable ledger blocks them rather than waving them
    through (it returned False on any sqlite error until 2026-10-08)."""
    assert live_loop.daily_loss_tripped(str(tmp_path / "missing" / "x.db"), DAY, 200.0) is True


def test_the_halt_and_the_breaker_are_entry_blockers(tmp_path):
    db_path = _init_db(tmp_path)
    assert live_loop.entry_blockers(BASE_CFG, db_path, DAY, halt_present=True) == ["halt_flag_present"]
    assert live_loop.entry_blockers(BASE_CFG, db_path, DAY, halt_present=False) == []


def test_live_ledger_is_a_separate_file():
    from cherrypick.meic import paths as _paths

    assert str(_paths.live_db_path()).endswith("meic_trades.db")
    assert str(_paths.paper_db_path()).endswith("paper_trades.db")
    assert _paths.live_db_path() != _paths.paper_db_path()


# --------------------------------------------------------------------------- the loop, faked


class FakeBroker:
    def __init__(self, statuses=None, working=None):
        self.placed = []
        self.polled = []
        self.cancelled = []
        self._statuses = dict(statuses or {})
        self.working = list(working or [])

    def working_orders(self):
        # The orphan sweep's read (2026-10-14). None of the loop's own orders need listing: the
        # sweep only reports ids the ledger does not know.
        return list(self.working)

    def place(self, spec, live):
        # tastytrade identifies an option leg by its OCC symbol and rejects anything else. Raised,
        # not returned as a rejection: a refused order would read as an ordinary skip in the loop.
        bad = [leg.get("symbol") for leg in spec.get("legs", []) if not _is_occ(leg.get("symbol"))]
        assert not bad, f"order leg symbols must be OCC, got {bad}"
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
        "short_put": q(583, SP, 0.55, 0.65, -0.15),
        "long_put": q(578, LP, 0.15, 0.25, -0.06),
        "short_call": q(598, SC, 0.50, 0.60, 0.15),
        "long_call": q(603, LC, 0.12, 0.22, 0.06),
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
            for sym, b, a in ((SP, 0.55, 0.65), (LP, 0.15, 0.25), (SC, 0.50, 0.60), (LC, 0.12, 0.22))
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


def test_live_takes_no_entry_on_a_quarter_end_session(tmp_path):
    """All day on the last trading day of a quarter: nothing placed, the reason named. The shared
    `paper.evaluate_entry` is untouched -- its own quarterly rules are partial-day."""
    db_path = _init_db(tmp_path)
    broker = FakeBroker()
    snap = _entry_snapshot(date="2026-12-31", expiration="2026-12-31")
    summary = live_loop.run_once(_config(), snap, db_path, broker, live=True, log=lambda *_: None)
    assert summary["entry"]["reason"] == "quarter_end_no_new_entries"
    assert broker.placed == []


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
    params as paper.live_params(config); with no `live.arm` that is an EMPTY profile overlay, so no
    registry key can reach it regardless of what any paper profile declares (only the arm a person
    names in `live.arm` can, test_live_block.py); only
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

    # Live side: with no live.arm the registry is never consulted (paper.live_params(config)), so
    # only config.json's own top-level overlap_scope ("shorts", the independent-sampling
    # default) applies -- still refuses an exact short-pair repeat, unlike paper's "none" stream.
    broker = FakeBroker()
    cfg = _config(max_structures_per_day=99)  # the overlap gate under test, not the daily cap
    summary = live_loop.run_once(cfg, snap, db_path, broker, live=True, log=lambda *_: None)
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
        "put_symbol": SP,
        "call_symbol": SC,
        "long_put_symbol": LP,
        "long_call_symbol": LC,
        "put_credit": 0.55,
        "call_credit": 0.52,
        "net_credit": 1.07,
        "quantity": 1,
        "status": "open",
        "arm": "live",
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
    unmet = live_loop.readiness({"live": {"symbol": "XSP"}}, designated=None)
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
    # slippage_dollars is MEASURED on a live row (2026-09-18): mid at submission minus the fill.
    assert row["entry_mid_at_submit"] is not None
    assert row["slippage_dollars"] == pytest.approx((row["entry_mid_at_submit"] - 1.37) * 100.0)
    # No per-leg fills on this status: the sides split the fill in the modelled ratio and add up.
    assert row["side_credit_source"] == "proportional"
    assert row["put_credit"] + row["call_credit"] == pytest.approx(1.37)


def _drop_column(db_path, column):
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(f"ALTER TABLE ic_trades DROP COLUMN {column}")
    conn.commit()
    conn.close()


def test_a_fill_the_ledger_refuses_is_not_counted_and_stays_pending(tmp_path):
    """2026-10-15: a live ledger missing `side_credit_source` refused every confirm, and the tick
    counted it anyway. Shown to fail on the pre-change loop (entries_confirmed == 1, no CRITICAL)."""
    db_path, broker = _place_one(tmp_path, {"ORD1": {"status": "Filled", "price": "-1.37", "filled": True}})
    _drop_column(db_path, "side_credit_source")
    lines = []
    summary = live_loop.run_once(_config(), _entry_snapshot(), db_path, broker, live=True, log=lines.append)
    assert summary["entries_confirmed"] == 0 and summary["entries_unrecorded"] == 1
    assert live_loop._trade_row("LIVE-XSP-ORD1", db_path)["status"] == "pending"
    assert any(line.startswith("CRITICAL") and "side_credit_source" in line for line in lines)


def test_the_tick_brings_an_old_live_ledger_up_to_date_and_the_fill_lands(tmp_path, monkeypatch):
    """The live loop never ran db.py's migrations (only the paper loop did). Shown to fail without
    `_ensure_live_schema`: the column stays missing and the confirm is refused again."""
    db_path, broker = _place_one(tmp_path, {"ORD1": {"status": "Filled", "price": "-1.37", "filled": True}})
    for column in ("side_credit_source", "put_exit_fill_source", "call_exit_fill_source"):
        _drop_column(db_path, column)
    notes = []
    monkeypatch.setattr(live_loop, "_quiet", notes.append)
    live_loop._ensure_live_schema(db_path)
    assert notes == []
    summary = live_loop.run_once(
        _config(), _entry_snapshot(), db_path, broker, live=True, log=lambda *_: None
    )
    assert summary["entries_confirmed"] == 1
    row = live_loop._trade_row("LIVE-XSP-ORD1", db_path)
    assert row["status"] == "open" and row["side_credit_source"] == "proportional"


# --------------------------------------------------------------------------- per-side credits (2026-10-12)
def _fills(sp=0.62, lp=0.18, sc=0.55, lc=0.15, syms=(OCC[SP], OCC[LP], OCC[SC], OCC[LC])):
    """Per-leg executions as the broker reports them: OCC symbols."""
    out = []
    for sym, action, price in zip(
        syms, ("Sell to Open", "Buy to Open", "Sell to Open", "Buy to Open"), (sp, lp, sc, lc), strict=True
    ):
        if price is not None:
            out.append({"symbol": sym, "action": action, "quantity": "1", "fill_price": str(price)})
    return out


_PENDING = {
    "put_symbol": SP,
    "long_put_symbol": LP,
    "call_symbol": SC,
    "long_call_symbol": LC,
    "put_credit": 0.45,  # modelled at quote time
    "call_credit": 0.35,
    "net_credit": 0.75,  # the limit asked
}


def test_entry_fill_credits_take_each_side_from_its_own_leg_fills():
    out = live_loop.entry_fill_credits(_PENDING, {"price": "0.75", "fills": _fills()}, 0.75)
    assert out == {
        "net_credit": 0.84,  # 0.44 + 0.40: what was received, not the 0.75 limit
        "put_credit": 0.44,
        "call_credit": 0.40,
        "side_credit_source": "broker_legs",
    }


def test_entry_fill_credits_match_the_brokers_occ_symbols_to_the_rows_streamer_symbols():
    trade = {
        **_PENDING,
        "put_symbol": ".XSP260708P583",
        "long_put_symbol": ".XSP260708P578",
        "call_symbol": ".XSP260708C598",
        "long_call_symbol": ".XSP260708C603",
    }
    occ = ("XSP   260708P00583000", "XSP   260708P00578000", "XSP   260708C00598000", "XSP   260708C00603000")
    out = live_loop.entry_fill_credits(trade, {"fills": _fills(syms=occ)}, 0.75)
    assert out["side_credit_source"] == "broker_legs" and out["put_credit"] == 0.44


def test_entry_fill_credits_fall_back_to_the_modelled_ratio_and_still_add_up():
    """No fills, a missing leg, a leg on the wrong action, or a leg sum below the limit (a credit
    limit cannot fill worse than itself): the booked fill is split 0.45 : 0.35, never re-derived."""
    cases = (
        {"price": "0.80"},
        {"fills": _fills(lc=None)},
        {"fills": [{**f, "action": "Buy to Open"} if f["symbol"] == OCC[SP] else f for f in _fills()]},
        {"fills": _fills(sp=0.50)},  # 0.32 + 0.40 = 0.72 < the 0.75 limit
    )
    for status in cases:
        out = live_loop.entry_fill_credits(_PENDING, status, 0.80)
        assert out["side_credit_source"] == "proportional", status
        assert out["net_credit"] == 0.80
        assert out["put_credit"] == pytest.approx(0.80 * 0.45 / 0.80)
        assert out["put_credit"] + out["call_credit"] == pytest.approx(0.80)
    lopsided = {**_PENDING, "call_credit": -0.02}
    assert live_loop.entry_fill_credits(lopsided, {}, 0.80)["put_credit"] == 0.40  # halves, never > net


def _place_and_fill(tmp_path, fills):
    db_path, broker = _place_one(tmp_path)
    broker._statuses = {"ORD1": {"status": "Filled", "price": "-0.75", "filled": True, "fills": fills}}
    live_loop.run_once(_config(), _entry_snapshot(), db_path, broker, live=True, log=lambda *_: None)
    return db_path, broker, live_loop._trade_row("LIVE-XSP-ORD1", db_path)


def test_a_confirmed_entry_books_the_sides_the_broker_filled(tmp_path):
    """Shown to fail on the pre-change loop, which left both sides at the quote-time model."""
    db_path, _broker, row = _place_and_fill(tmp_path, _fills())
    assert row["status"] == "open" and row["side_credit_source"] == "broker_legs"
    assert (row["put_credit"], row["call_credit"], row["net_credit"]) == (0.44, 0.40, 0.84)
    assert row["put_credit_fraction"] == pytest.approx(0.44 / 0.84, abs=1e-4)
    assert row["slippage_dollars"] == pytest.approx((row["entry_mid_at_submit"] - 0.84) * 100.0)


def test_live_stop_and_expiry_pnl_are_charged_against_the_filled_side_credits(tmp_path):
    """The point of the split: a stop and an expiry on a live row add up to the net received."""
    db_path, _broker, row = _place_and_fill(tmp_path, _fills())
    paper._apply_exit_decision(
        row, {"action": "stop_put", "put_exit_price": 1.10, "put_exit_slippage": 0}, "XSP", db_path
    )
    row = live_loop._trade_row("LIVE-XSP-ORD1", db_path)
    assert row["pnl"] == pytest.approx((0.44 - 1.10) * 100)
    paper._apply_exit_decision(
        row,
        {
            "action": "expire",
            "put_open": False,
            "call_open": True,
            "call_exit_price": 0.0,
            "settle_underlying": 590.0,
        },
        "XSP",
        db_path,
    )
    row = live_loop._trade_row("LIVE-XSP-ORD1", db_path)
    assert row["pnl"] == pytest.approx((0.84 - 1.10 - 0.0) * 100)


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
    cfg = _config(max_structures_per_day=99)  # the concurrency gate under test, not the daily cap
    cfg["max_concurrent_ics"] = 1
    db_path, broker = _place_one(tmp_path)
    summary = live_loop.run_once(cfg, _entry_snapshot(), db_path, broker, live=True, log=lambda *_: None)
    assert summary["entry"] == {"entry": "skipped", "reason": "max_concurrent_ics_reached"}
    assert summary["held"] == 1 and len(broker.placed) == 1, "no close order for a position not yet held"


def _stopping_snapshot():
    """A snapshot where the QQQ put side's cost-to-close has blown through the stop trigger."""
    snap = _entry_snapshot(symbol="QQQ", now_et="11:00", candidates=[])
    snap["leg_quotes"] = {
        SP: {"bid": 2.40, "ask": 2.50},  # short put: was 0.55 credit, now 2.5 to close
        LP: {"bid": 0.10, "ask": 0.15},
        SC: {"bid": 0.30, "ask": 0.35},
        LC: {"bid": 0.05, "ask": 0.08},
    }
    return snap


def test_a_halt_blocks_new_ics_and_still_force_closes_an_open_one(tmp_path):
    """2026-10-08: the halt flag returned the tick before any management, and gated every submit at
    the seam, so a halt left open 0DTE ICs with no stops. Now a halted tick still sends the close."""
    db_path = _init_db(tmp_path)
    paper._save_trade(_open_trade_row(), db_path)
    broker = FakeBroker()
    snap = _entry_snapshot(symbol="QQQ", now_et="15:31", candidates=[])
    summary = live_loop.run_once(
        _config(symbol="QQQ"), snap, db_path, broker, live=True, log=lambda *_: None,
        entry_block=["halt_flag_present"],
    )  # fmt: skip
    assert summary["closing"] == 1 and any(p["live"] is True for p in broker.placed)
    assert summary["entry"] == {"blocked": ["halt_flag_present"]}


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
    snap["leg_quotes"][SP] = {"bid": 2.90, "ask": 3.00}
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
    summary = live_loop.run_once(
        _config(symbol="XSP"),
        snap,
        db_path,
        broker,
        live=True,
        log=lambda *_: None,
        settle_price=lambda _s: (590.0, "tastytrade_close"),  # a posted close (2026-10-15 rule)
    )
    assert summary["expired"] == 1
    assert broker.cancelled == ["W1"], "the working close is cancelled, not chased into the bell"
    done = live_loop._trade_row("OPEN1", db_path)
    assert done["status"] == "expired" and done["put_stop_fill_status"] == "cancelled"
    assert done["put_stop_cost"] is None, "never recorded as stopped"


def test_the_live_seam_is_the_shared_adapter(monkeypatch):
    from cherrypick.core import execution

    broker = live_loop.make_broker(_config(), "1234")
    assert isinstance(broker, execution.Broker)
    # Its gates are this module's ACCOUNT gates, re-checked per live submit. The halt flag is not
    # one: a close must always get through, so the halt is enforced on entries in run_once.
    monkeypatch.setattr(live_loop.os.path, "exists", lambda p: True)  # halt flag present
    assert live_loop.make_broker(_config(), "1234")._live_gates() == []
    ungated = live_loop.make_broker({**_config(), "enable_live_trading": False}, "1234")
    out = ungated.place({"legs": []}, live=True)
    assert out["error"] == "live submission gated" and any(
        "enable_live_trading" in g for g in out["unmet_gates"]
    )


# --------------------------------------------------------------------------- exit fills (2026-10-12)
def _close_fills(short=2.38, long=0.12, short_sym=None, long_sym=None, short_action="Buy to Close"):
    out = []
    if short is not None:
        out.append(
            {
                "symbol": short_sym or OCC[SP],
                "action": short_action,
                "quantity": "1",
                "fill_price": str(short),
            }
        )
    if long is not None:
        out.append(
            {
                "symbol": long_sym or OCC[LP],
                "action": "Sell to Close",
                "quantity": "1",
                "fill_price": str(long),
            }
        )
    return out


def test_exit_fill_price_takes_the_debit_from_the_sides_own_leg_fills():
    out = live_loop.exit_fill_price(TRADE, "put", {"price": "2.45", "fills": _close_fills()}, 2.45, 2.45)
    assert out == (2.26, "broker_legs")  # 2.38 - 0.12, inside the 2.45 limit
    call = live_loop.exit_fill_price(
        TRADE, "call", {"fills": _close_fills(0.85, 0.20, OCC[SC], OCC[LC])}, 0.60, 0.60
    )
    assert call == (0.60, "booked"), "a 0.65 debit above its own 0.60 limit is a bad read, not a fill"
    call = live_loop.exit_fill_price(
        TRADE, "call", {"fills": _close_fills(0.50, 0.20, OCC[SC], OCC[LC])}, 0.60, 0.60
    )
    assert call == (0.30, "broker_legs")


def test_exit_fill_price_falls_back_to_the_booked_price():
    for status in (
        {"price": "2.45"},  # no fills at all
        {"fills": _close_fills(long=None)},  # a leg missing
        {"fills": _close_fills(short_action="Sell to Close")},  # the short on the wrong action
        {"fills": _close_fills(short_sym=OCC[SC])},  # the other side's leg, not this one's
        {"fills": _close_fills(short=0.10, long=0.12)},  # a negative debit
    ):
        assert live_loop.exit_fill_price(TRADE, "put", status, 2.45, 2.45) == (2.45, "booked"), status


def test_a_confirmed_stop_is_booked_at_the_brokers_leg_fills_not_the_limit(tmp_path):
    """Shown to fail before 2026-10-12: the side was booked at the limit asked (2.45 here), never
    at what the broker filled it for."""
    db_path = _init_db(tmp_path)
    paper._save_trade(_open_trade_row("QQQ"), db_path)
    broker = FakeBroker()
    snap = _stopping_snapshot()
    live_loop.run_once(_config(symbol="QQQ"), snap, db_path, broker, live=True, log=lambda *_: None)
    row = live_loop._trade_row("OPEN1", db_path)
    asked = float(broker.placed[-1]["spec"]["price"])
    assert asked == pytest.approx(2.45)
    broker._statuses = {
        row["put_stop_order_id"]: {"status": "Filled", "price": "-2.45", "fills": _close_fills(2.38, 0.12)}
    }
    live_loop.run_once(_config(symbol="QQQ"), snap, db_path, broker, live=True, log=lambda *_: None)
    row = live_loop._trade_row("OPEN1", db_path)
    assert row["put_stop_cost"] == pytest.approx(2.26) and row["put_exit_fill_source"] == "broker_legs"
    assert row["pnl"] == pytest.approx((0.55 - 2.26) * 100)
    assert row["call_exit_fill_source"] is None


def test_a_stop_without_usable_leg_fills_is_booked_at_its_price_and_says_so(tmp_path):
    db_path = _init_db(tmp_path)
    paper._save_trade(_open_trade_row("QQQ"), db_path)
    broker = FakeBroker()
    snap = _stopping_snapshot()
    live_loop.run_once(_config(symbol="QQQ"), snap, db_path, broker, live=True, log=lambda *_: None)
    row = live_loop._trade_row("OPEN1", db_path)
    broker._statuses = {
        row["put_stop_order_id"]: {"status": "Filled", "price": "-2.45", "fills": _close_fills(long=None)}
    }
    live_loop.run_once(_config(symbol="QQQ"), snap, db_path, broker, live=True, log=lambda *_: None)
    row = live_loop._trade_row("OPEN1", db_path)
    assert row["put_stop_cost"] == pytest.approx(2.45) and row["put_exit_fill_source"] == "booked"


# --------------------------------------------------------------------------- quantity (2026-10-12)
def test_a_live_entry_records_the_quantity_its_order_carried(tmp_path):
    """Shown to fail before 2026-10-12: the order went out at `quantity: 2` and the row said 1, so
    the ledger held half the position -- its capital, fees, slippage, P&L and the position export
    the account risk layer reads."""
    db_path = _init_db(tmp_path)
    cfg = {**_config(), "quantity": 2}
    broker = FakeBroker()
    live_loop.run_once(cfg, _entry_snapshot(), db_path, broker, live=True, log=lambda *_: None)
    assert [leg["quantity"] for leg in broker.placed[0]["spec"]["legs"]] == [2, 2, 2, 2]
    row = live_loop._trade_row("LIVE-XSP-ORD1", db_path)
    assert row["quantity"] == 2
    assert row["fees"] == pytest.approx(paper.open_fees("XSP", 2))
    one = paper.synthetic_entry_fill(
        _entry_snapshot(), "live", paper.evaluate_entry(
            _entry_snapshot(), paper._merged_params(cfg, {}), [], account_open_count=0
        )[2], paper._merged_params(cfg, {}), "live",
    )  # fmt: skip
    assert one["quantity"] == 1, "paper's default is unchanged"
    assert row["slippage_dollars"] == pytest.approx(2 * one["slippage_dollars"])
    # the mid is per share whatever the quantity
    assert row["entry_mid_at_submit"] == pytest.approx(one["net_credit"] + one["slippage_dollars"] / 100.0)

    broker._statuses = {"ORD1": {"status": "Filled", "price": "-1.05", "fills": []}}
    live_loop.run_once(cfg, _entry_snapshot(), db_path, broker, live=True, log=lambda *_: None)
    row = live_loop._trade_row("LIVE-XSP-ORD1", db_path)
    assert row["slippage_dollars"] == pytest.approx((row["entry_mid_at_submit"] - 1.05) * 100 * 2)
    fees_open = row["fees"]
    paper._apply_exit_decision(
        row, {"action": "stop_put", "put_exit_price": 1.50, "put_exit_slippage": 0.5}, "XSP", db_path
    )
    done = live_loop._trade_row("LIVE-XSP-ORD1", db_path)
    assert done["pnl"] == pytest.approx((row["put_credit"] - 1.50) * 100 * 2)
    assert done["fees"] == pytest.approx(fees_open + paper.close_fees_one_side("XSP", 2))
    assert done["slippage_dollars"] == pytest.approx(row["slippage_dollars"] + 0.5 * 2)


def test_a_quantity_one_row_is_charged_exactly_as_before(tmp_path):
    """Paper rows are all quantity 1: the exit accounting's x quantity must not move them."""
    db_path = _init_db(tmp_path)
    paper._save_trade(_open_trade_row("XSP"), db_path)
    row = live_loop._trade_row("OPEN1", db_path)
    paper._apply_exit_decision(
        row, {"action": "stop_call", "call_exit_price": 1.10, "call_exit_slippage": 0.25}, "XSP", db_path
    )
    done = live_loop._trade_row("OPEN1", db_path)
    assert done["pnl"] == round((0.52 - 1.10) * 100, 2)
    assert done["fees"] == paper.close_fees_one_side("XSP")
    assert done["slippage_dollars"] == 0.25
