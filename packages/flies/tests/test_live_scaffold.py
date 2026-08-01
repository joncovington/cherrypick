"""The live scaffold (docs/live-trading-plan.md): gates, order builders, and the gated loop.

Everything here is offline — the broker is a fake, and the point under test is that the
scaffold is INERT by default: no gate, no order.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import db as dbmod
import engine
import fly
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


def test_entry_spec_refuses_a_debit_first_plan():
    """Live v1 is legged-only. debit_first's plan has no 'credit' key (it has 'debit' instead) --
    entry_spec must refuse it with a clear error, not a confusing KeyError two lines in."""
    debit_first_plan = {**ENTRY_PLAN, "debit": 1.07}
    del debit_first_plan["credit"]
    with pytest.raises(ValueError, match="legged-only"):
        live_orders.entry_spec(_snapshot(), debit_first_plan)


def test_entry_spec_refuses_a_bwb_plan():
    """bwb_roll's plan carries 'far_width', which no legged plan ever does -- refused even though
    it (like legged) also carries 'credit'."""
    bwb_plan = {**ENTRY_PLAN, "far_width": 10.0}
    with pytest.raises(ValueError, match="legged-only"):
        live_orders.entry_spec(_snapshot(), bwb_plan)


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


def test_broker_cli_serialize_flattens_sdk_objects_and_leaves_plain_values_alone():
    from broker_cli import _serialize

    class FakeSdkOrder:
        def model_dump(self, mode="json"):
            return {"id": 489184188, "status": "Received", "reject_reason": None}

    assert _serialize(None) is None and _serialize(5) == 5 and _serialize("x") == "x"
    assert _serialize([1, FakeSdkOrder()]) == [
        1,
        {"id": 489184188, "status": "Received", "reject_reason": None},
    ]
    assert _serialize({"order": FakeSdkOrder()}) == {
        "order": {"id": 489184188, "status": "Received", "reject_reason": None}
    }
    assert _serialize(object()).startswith("<object")


def test_broker_adapter_place_extracts_order_id_from_serialized_response(monkeypatch):
    """Regression for the 2026-07-30 incident: BrokerAdapter.place() must pass serialize= into
    core.broker.place_order(), or result["response"] stays the raw (non-dict) SDK response object
    and the order_id lookup at `(result.get("response") or {}).get("order", {})` silently finds
    nothing every time. That is what let the live loop resubmit the same entry order on every
    ~1-minute tick without ever recording a position -- 5+ live submissions in one session, 0 rows
    in fly_positions, 8 orphaned broker orders left resting/rejected."""
    import cherrypick.core.broker as core_broker

    class FakeSdkResponse:
        def model_dump(self, mode="json"):
            return {
                "order": {"id": 489184188, "status": "Received"},
                "warnings": [],
                "errors": [],
            }

    async def fake_place_order(account, session, order, *, live, serialize=None, deploy_limit_pct=None):
        assert serialize is not None, "BrokerAdapter.place() must pass serialize="
        return {"ok": True, "dry_run": not live, "response": serialize(FakeSdkResponse())}

    monkeypatch.setattr(core_broker, "place_order", fake_place_order)
    monkeypatch.setattr(core_broker, "build_order", lambda spec: object())

    adapter = live_loop.BrokerAdapter(BASE_CFG)  # live.enabled + gate0_confirmed, so the gate passes
    monkeypatch.setattr(adapter, "_ensure", lambda: None)
    adapter._session, adapter._account = object(), object()

    result = adapter.place({"legs": []}, live=True)
    assert result["ok"] is True
    assert result["order_id"] == 489184188
    assert isinstance(result["response"], dict)  # never the raw SDK object


def test_broker_cli_fresh_option_quotes_shapes_and_filters_rest_rows(monkeypatch):
    """fresh_option_quotes is the fresh-quote-before-entry mechanism's one broker call: a plain
    REST market-data GET, not the streamer. Drops rows with no usable two-sided market (missing,
    crossed, or non-positive) rather than handing entry_fresh_reprice a nonsense quote."""
    from tastytrade import market_data as md

    import broker_cli

    class Row:
        def __init__(self, symbol, bid, ask, mid=None, updated_at=None):
            self.symbol, self.bid, self.ask, self.mid, self.updated_at = symbol, bid, ask, mid, updated_at

    async def fake_get_market_data_by_type(session, options=None, **kw):
        assert options == ["A", "B", "C", "D"]
        return [
            Row("A", 1.0, 1.2, 1.1),
            Row("B", 1.0, 1.2, None),  # mid absent -> computed from bid/ask
            Row("C", None, 1.2),  # missing bid -> dropped
            Row("D", 1.3, 1.2),  # crossed (bid > ask) -> dropped
        ]

    monkeypatch.setattr(md, "get_market_data_by_type", fake_get_market_data_by_type)

    out = asyncio.run(broker_cli.fresh_option_quotes(object(), ["A", "B", "C", "D"]))
    assert out["A"] == {"bid": 1.0, "ask": 1.2, "mid": 1.1, "updated_at": None}
    assert out["B"]["mid"] == 1.1  # (1.0 + 1.2) / 2
    assert "C" not in out and "D" not in out


def test_broker_cli_fresh_option_quotes_empty_symbols_skips_the_call(monkeypatch):
    from tastytrade import market_data as md

    import broker_cli

    async def boom(*a, **kw):
        raise AssertionError("must not call the SDK for an empty symbol list")

    monkeypatch.setattr(md, "get_market_data_by_type", boom)
    assert asyncio.run(broker_cli.fresh_option_quotes(object(), [])) == {}


def test_broker_adapter_fresh_quotes_delegates_and_fails_closed(monkeypatch):
    import broker_cli

    calls = []

    async def fake_fresh(session, symbols):
        calls.append(symbols)
        return {"X": {"bid": 1.0, "ask": 1.1, "mid": 1.05}}

    monkeypatch.setattr(broker_cli, "fresh_option_quotes", fake_fresh)
    adapter = live_loop.BrokerAdapter(BASE_CFG)
    monkeypatch.setattr(adapter, "_ensure", lambda: None)
    adapter._session, adapter._account = object(), object()

    assert adapter.fresh_quotes(["X"]) == {"X": {"bid": 1.0, "ask": 1.1, "mid": 1.05}}
    assert calls == [["X"]]

    async def fake_fresh_raises(session, symbols):
        raise RuntimeError("network blip")

    monkeypatch.setattr(broker_cli, "fresh_option_quotes", fake_fresh_raises)
    assert adapter.fresh_quotes(["X"]) == {}  # fails closed, never raises to the caller


# --------------------------------------------------------------------------- official settlement price
def test_official_settlement_price_prefers_tastytrade(monkeypatch):
    from tastytrade import market_data as md

    import broker_cli

    class Row:
        def __init__(self, close=None, last=None, mark=None):
            self.close, self.last, self.mark = close, last, mark

    async def fake_get_market_data_by_type(session, indices=None, **kw):
        assert indices == ["XSP"]
        return [Row(close=743.76)]

    monkeypatch.setattr(md, "get_market_data_by_type", fake_get_market_data_by_type)
    monkeypatch.setattr(broker_cli, "_yahoo_index_price", lambda s: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(broker_cli, "_barchart_index_price", lambda s: (_ for _ in ()).throw(AssertionError))

    price, source = asyncio.run(broker_cli.official_settlement_price(object(), "XSP"))
    # A posted CLOSE is the one genuinely-official reading -- tagged as such, and neither web
    # fallback is consulted (both raise if touched).
    assert price == 743.76 and source == "tastytrade_close"


def test_official_settlement_price_falls_back_to_last_or_mark_when_close_is_unset(monkeypatch):
    from tastytrade import market_data as md

    import broker_cli

    class Row:
        def __init__(self, close=None, last=None, mark=None):
            self.close, self.last, self.mark = close, last, mark

    async def fake_get_market_data_by_type(session, indices=None, **kw):
        return [Row(close=None, last=None, mark=743.76)]  # close not posted yet

    monkeypatch.setattr(md, "get_market_data_by_type", fake_get_market_data_by_type)
    # With no posted close, the web sources (whose post-close quote IS the closing print) are
    # preferred over tastytrade's intraday mark...
    monkeypatch.setattr(broker_cli, "_yahoo_index_price", lambda s: 744.10)
    price, source = asyncio.run(broker_cli.official_settlement_price(object(), "XSP"))
    assert price == 744.10 and source == "yahoo"


def test_official_settlement_price_marks_an_intraday_tick_provisional(monkeypatch):
    """...and if NOTHING authoritative answers, the intraday mark is still returned (better than
    no settlement at all) but tagged provisional, so `session_officially_settled` stays False and
    the loop keeps retrying. Before 2026-07-31 this was stamped 'official' and stopped the retry."""
    from tastytrade import market_data as md

    import broker_cli

    class Row:
        def __init__(self, close=None, last=None, mark=None):
            self.close, self.last, self.mark = close, last, mark

    async def fake_get_market_data_by_type(session, indices=None, **kw):
        return [Row(close=None, last=750.46, mark=None)]

    monkeypatch.setattr(md, "get_market_data_by_type", fake_get_market_data_by_type)
    monkeypatch.setattr(broker_cli, "_yahoo_index_price", lambda s: None)
    monkeypatch.setattr(broker_cli, "_barchart_index_price", lambda s: None)
    price, source = asyncio.run(broker_cli.official_settlement_price(object(), "XSP"))
    assert price == 750.46
    assert source == "tastytrade_last_provisional"
    assert not live_loop._is_official_source(source)


def test_official_settlement_price_falls_back_to_yahoo_then_barchart(monkeypatch):
    from tastytrade import market_data as md

    import broker_cli

    async def empty(session, indices=None, **kw):
        return []

    monkeypatch.setattr(md, "get_market_data_by_type", empty)
    monkeypatch.setattr(broker_cli, "_yahoo_index_price", lambda s: 743.76)
    monkeypatch.setattr(broker_cli, "_barchart_index_price", lambda s: (_ for _ in ()).throw(AssertionError))
    price, source = asyncio.run(broker_cli.official_settlement_price(object(), "XSP"))
    assert price == 743.76 and source == "yahoo"

    monkeypatch.setattr(broker_cli, "_yahoo_index_price", lambda s: None)
    monkeypatch.setattr(broker_cli, "_barchart_index_price", lambda s: 743.76)
    price2, source2 = asyncio.run(broker_cli.official_settlement_price(object(), "XSP"))
    assert price2 == 743.76 and source2 == "barchart"


def test_official_settlement_price_reports_no_source_when_every_source_fails(monkeypatch):
    from tastytrade import market_data as md

    import broker_cli

    async def boom(session, indices=None, **kw):
        raise RuntimeError("network blip")

    monkeypatch.setattr(md, "get_market_data_by_type", boom)
    monkeypatch.setattr(broker_cli, "_yahoo_index_price", lambda s: None)
    monkeypatch.setattr(broker_cli, "_barchart_index_price", lambda s: None)
    price, reason = asyncio.run(broker_cli.official_settlement_price(object(), "XSP"))
    assert price is None and reason == "no_source_available"


def test_broker_adapter_official_settlement_price_delegates_and_fails_closed(monkeypatch):
    import broker_cli

    calls = []

    async def fake_official(session, symbol):
        calls.append(symbol)
        return 743.76, "tastytrade"

    monkeypatch.setattr(broker_cli, "official_settlement_price", fake_official)
    adapter = live_loop.BrokerAdapter(BASE_CFG)
    monkeypatch.setattr(adapter, "_ensure", lambda: None)
    adapter._session, adapter._account = object(), object()

    assert adapter.official_settlement_price("XSP") == (743.76, "tastytrade")
    assert calls == ["XSP"]

    async def fake_official_raises(session, symbol):
        raise RuntimeError("network blip")

    monkeypatch.setattr(broker_cli, "official_settlement_price", fake_official_raises)
    assert adapter.official_settlement_price("XSP") == (None, "fetch_failed")  # fails closed


def test_entry_fresh_reprice_matches_vertical_credit_on_the_same_quotes():
    """entry_fresh_reprice must compute the credit the SAME way fly.vertical_credit already does
    for the cached path (engine.py:248) -- apples-to-apples, not a second pricing model. Note
    ENTRY_PLAN's own "credit": 1.07 is a fixture literal for the tick-floor tests above and is NOT
    what _snapshot()'s real put quotes at these strikes imply -- don't compare against it here."""
    import fly

    spec = live_orders.entry_spec(_snapshot(), ENTRY_PLAN)
    fresh = _quote_table(_snapshot())
    short_q, long_q = fresh[spec["legs"][0]["symbol"]], fresh[spec["legs"][1]["symbol"]]
    expected_credit = fly.vertical_credit(short_q, long_q)

    new_price, info = live_orders.entry_fresh_reprice(spec, fresh)
    assert info["fresh_credit"] == pytest.approx(expected_credit, abs=1e-9)
    assert new_price == live_orders.tick_floor(expected_credit)


def test_entry_fresh_reprice_missing_leg_refuses_to_price():
    spec = live_orders.entry_spec(_snapshot(), ENTRY_PLAN)
    short_sym = spec["legs"][0]["symbol"]
    fresh = {k: v for k, v in _quote_table(_snapshot()).items() if k != short_sym}
    new_price, info = live_orders.entry_fresh_reprice(spec, fresh)
    assert new_price is None
    assert info["reason"] == "fresh_quote_missing" and info["missing"] == [short_sym]


def test_entry_fresh_reprice_worse_market_lowers_the_credit():
    spec = live_orders.entry_spec(_snapshot(), ENTRY_PLAN)
    fresh = _quote_table(_snapshot())
    short_sym = spec["legs"][0]["symbol"]
    baseline_price, baseline_info = live_orders.entry_fresh_reprice(spec, fresh)
    # the short leg's real market is worth less than cached -> less credit available
    fresh[short_sym] = {**fresh[short_sym], "mid": fresh[short_sym]["mid"] - 0.5}
    new_price, info = live_orders.entry_fresh_reprice(spec, fresh)
    assert info["fresh_credit"] < baseline_info["fresh_credit"]
    assert new_price < baseline_price


# --------------------------------------------------------------------------- the loop, faked
def _quote_table(snapshot):
    """{occ_symbol: {bid, ask, mid}} for every leg quote in a snapshot — the shape
    FakeBroker.fresh_quotes reconstructs its answers from."""
    table = {}
    for book in (snapshot.get("puts") or {}, snapshot.get("calls") or {}):
        for q in book.values():
            if q.get("occ_symbol"):
                table[q["occ_symbol"]] = {"bid": q["bid"], "ask": q["ask"], "mid": q["mid"]}
    return table


class FakeBroker:
    def __init__(
        self,
        order_statuses=None,
        cancel_ok=True,
        working=None,
        snapshot=None,
        fresh=None,
        official_price=None,
        alerts=None,
    ):
        self.placed = []
        self.cancelled = []
        self.status_calls = []
        self.fresh_quote_calls = []
        self.official_settlement_calls = []
        self.alert_calls = []
        # Queue of canned return values, one per wait_for_order_alerts() call; once exhausted,
        # every further call returns [] (mirrors a real, always-fail-closed empty result).
        self._alerts = list(alerts or [])
        # order_id -> {"status": ..., "price": ...} the next status() call for it returns
        self._statuses = dict(order_statuses or {})
        self._cancel_ok = cancel_ok
        self._working = list(working or [])
        # fresh_quotes() default: reconstruct quotes from a plain _snapshot() (every entry test
        # but two uses exactly that; the two exceptions only override now_min, not strikes/quotes)
        # so the "fresh" price comes out identical to the cached one and every pre-existing test
        # keeps passing without wiring a snapshot through explicitly. `fresh` overrides entirely
        # (e.g. {} to simulate an unavailable fetch, or a hand-built table to simulate divergence).
        self._fresh_override = fresh
        self._quote_table = _quote_table(snapshot if snapshot is not None else _snapshot())
        # official_settlement_price() default: no source available (mirrors run_settle_live's
        # own fall-to-provisional behavior). Pass (price, source) to simulate a successful fetch.
        self._official_price = official_price or (None, "no_source_available")

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

    def fresh_quotes(self, symbols):
        self.fresh_quote_calls.append(list(symbols))
        if self._fresh_override is not None:
            return dict(self._fresh_override)
        return {s: self._quote_table[s] for s in symbols if s in self._quote_table}

    def official_settlement_price(self, symbol):
        self.official_settlement_calls.append(symbol)
        return self._official_price

    def wait_for_order_alerts(self, order_ids, timeout_seconds):
        self.alert_calls.append((set(order_ids), timeout_seconds))
        return self._alerts.pop(0) if self._alerts else []


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
    import fly

    broker = FakeBroker()
    summary = live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    assert summary["entered"] == 1
    row = live_conn.execute("SELECT * FROM fly_positions").fetchone()
    assert row["entry_order_id"] == "ORD1"
    assert row["kind"] == "short_vertical" and row["arm"] == "gex"
    # Regression: save_position() omitted completing_direction entirely (paper's book.py always
    # included it from the same plan dict) -- flies' first live fill (2026-07-30) recorded NULL,
    # and the Discord notifier rendered "needs spot ? to complete" on a real position.
    assert row["completing_direction"] is not None
    # Regression (2026-07-30): a live entry never recorded floor_dollars either, so the dashboard's
    # Floor column and the "max possible loss" tile sat blank until (if ever) it completed.
    assert row["floor_dollars"] is not None
    assert row["floor_dollars"] == pytest.approx(
        fly.position_floor(
            {
                "kind": "short_vertical",
                "side": row["side"],
                "center": row["center"],
                "wing_width": row["wing_width"],
                "quantity": row["quantity"],
                "net": row["net"],
                "fees": row["fees"],
            }
        )
    )


def test_dry_run_never_calls_fresh_quotes(live_conn):
    """The fresh-quote check is live-only -- paper/dry-run must issue zero extra broker calls."""
    broker = FakeBroker()
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=False, log=lambda *_: None)
    assert broker.fresh_quote_calls == []
    assert broker.placed and broker.placed[0]["spec"]["price"] != 0  # entry still proceeded, unrepriced


def test_live_entry_recorded_at_the_fresh_repriced_value(live_conn):
    """When live, the recorded position's net/credit must be the fresh (submitted) price, not the
    stale cached one -- the ledger's economics must match what actually went to the broker."""
    import fly

    broker = FakeBroker()
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    spec = broker.placed[0]["spec"]
    short_q, long_q = (
        broker._quote_table[spec["legs"][0]["symbol"]],
        broker._quote_table[spec["legs"][1]["symbol"]],
    )
    expected = live_orders.tick_floor(fly.vertical_credit(short_q, long_q))
    assert spec["price"] == expected
    row = live_conn.execute("SELECT * FROM fly_positions").fetchone()
    assert row["net"] == expected and row["credit"] == expected


def test_live_entry_skipped_when_fresh_quote_unavailable(live_conn):
    broker = FakeBroker(fresh={})  # simulates a failed/empty REST fetch
    summary = live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    assert broker.placed == []  # never reaches broker.place()
    assert any("fresh quote unavailable" in s.get("entry", "") for s in summary["skips"])
    n = live_conn.execute("SELECT COUNT(*) FROM fly_positions").fetchone()[0]
    assert n == 0


def _real_entry_short_leg_symbol(snap, cfg):
    """The OCC symbol run_once will actually use as the short/centre leg for this snapshot+config
    combo, derived by running the real entry decision -- so a divergence test perturbs a leg that
    is actually read by entry_fresh_reprice, not an arbitrary strike from the quote table."""
    params = live_loop._merged_live_params(cfg, cfg["live"]["arm"])
    enter, _, plan = engine.evaluate_credit_spread_entry(snap, params, [])
    assert enter, "fixture must produce an entry for this test to target the right leg"
    return live_orders.entry_spec(snap, plan)["legs"][0]["symbol"]


def test_live_entry_skipped_when_fresh_quote_diverged_beyond_tolerance(live_conn):
    snap = _snapshot()
    table = _quote_table(snap)
    short_symbol = _real_entry_short_leg_symbol(snap, _loop_cfg())
    # well more than the default fresh_quote_tolerance_dollars (0.05)
    worse = {k: (v if k != short_symbol else {**v, "mid": v["mid"] - 5.0}) for k, v in table.items()}
    broker = FakeBroker(snapshot=snap, fresh=worse)
    summary = live_loop.run_once(_loop_cfg(), snap, live_conn, broker, live=True, log=lambda *_: None)
    assert broker.placed == []
    assert any("fresh quote diverged" in s.get("entry", "") for s in summary["skips"])
    n = live_conn.execute("SELECT COUNT(*) FROM fly_positions").fetchone()[0]
    assert n == 0


def test_live_entry_proceeds_when_fresh_quote_within_tolerance(live_conn):
    snap = _snapshot()
    table = _quote_table(snap)
    short_symbol = _real_entry_short_leg_symbol(snap, _loop_cfg())
    within = {k: (v if k != short_symbol else {**v, "mid": v["mid"] - 0.01}) for k, v in table.items()}
    broker = FakeBroker(snapshot=snap, fresh=within)
    summary = live_loop.run_once(_loop_cfg(), snap, live_conn, broker, live=True, log=lambda *_: None)
    assert summary["entered"] == 1
    assert len(broker.placed) == 1


# --------------------------------------------------------------------------- journals (dashboard data)
def test_every_tick_journals_the_snapshot_and_the_wanted_iteration(live_conn):
    """Regression: live_loop never wrote fly_iterations/fly_snapshots at all (only paper's book.py/
    paper_loop.py did) -- the live dashboard's Session Timeline card read "No iterations recorded
    yet today" even on a session with a real fill. Every run_once tick must journal both, live or
    dry-run, entered or not -- this is feed/intent telemetry, not a trading decision."""
    broker = FakeBroker()
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=False, log=lambda *_: None)
    snap_row = live_conn.execute("SELECT * FROM fly_snapshots").fetchone()
    assert snap_row["status"] == "ok" and snap_row["symbol"] == "SPX"
    iter_row = live_conn.execute("SELECT * FROM fly_iterations").fetchone()
    assert iter_row["arm"] == "gex" and iter_row["center"] is not None


def test_accepted_entry_is_journaled_as_a_decision(live_conn):
    broker = FakeBroker()
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    row = live_conn.execute("SELECT * FROM fly_decisions WHERE mode = 'entry'").fetchone()
    assert row["reason"] == "entered" and row["accepted"] == 1
    assert row["position_id"] == live_conn.execute("SELECT position_id FROM fly_positions").fetchone()[0]


def test_refused_entry_is_journaled_with_the_engine_reason(live_conn):
    # before the entry window -> engine.evaluate_credit_spread_entry refuses with a specific reason
    snap = _snapshot(now_min=1)
    broker = FakeBroker(snapshot=snap)
    summary = live_loop.run_once(_loop_cfg(), snap, live_conn, broker, live=True, log=lambda *_: None)
    assert summary["entered"] == 0
    row = live_conn.execute("SELECT * FROM fly_decisions WHERE mode = 'entry'").fetchone()
    assert row["accepted"] == 0
    assert row["reason"] == summary["skips"][0]["entry"]
    # Regression: the refusal-path journal() call was missing `center=` entirely (plan is None on
    # refusal, so there's no plan["center"] -- book.py's own equivalent passes wanted_center
    # instead, computed the same way for the iteration journal; live_loop.py's first cut of this
    # forgot to). Without it the Decision Journal card's CENTRE/DETAIL columns render "-" even
    # though the arm's wanted centre was known the whole time.
    assert row["center_last"] is not None


def test_fresh_quote_skip_paths_are_journaled_distinctly(live_conn):
    broker = FakeBroker(fresh={})
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    row = live_conn.execute("SELECT * FROM fly_decisions WHERE mode = 'entry'").fetchone()
    assert row["reason"] == "fresh_quote_unavailable" and row["accepted"] == 0


def test_repeated_identical_skip_reason_collapses_into_one_run(live_conn):
    """record_decision's own contract (db.py): an unchanged reason bumps occurrences rather than
    inserting a new row -- confirm run_once's journal() calls actually get this behavior, not just
    the isolated db.py unit that already covers it."""
    snap = _snapshot(now_min=1)  # before the entry window, same refusal reason every tick
    for _ in range(3):
        broker = FakeBroker(snapshot=snap)
        live_loop.run_once(_loop_cfg(), snap, live_conn, broker, live=True, log=lambda *_: None)
    rows = live_conn.execute("SELECT * FROM fly_decisions WHERE mode = 'entry'").fetchall()
    assert len(rows) == 1 and rows[0]["occurrences"] == 3


def test_position_id_is_unique_per_attempt_not_just_per_day_arm_centre(live_conn):
    """Regression: position_id used to be f"live-{day}-{arm}-{center}" -- day+arm+centre only, no
    per-attempt uniqueness. A later successful entry at the SAME centre as an earlier rejected one
    collided on that id, and the UPSERT silently overwrote the rejected attempt's row, erasing it
    from the ledger (2026-07-30: the orphan sweep kept re-flagging an order the ledger no longer
    had any record of). The id must now carry a per-attempt timestamp component."""
    broker = FakeBroker()
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    pid = live_conn.execute("SELECT position_id FROM fly_positions").fetchone()[0]
    assert pid.startswith("live-gex-")
    # arm-centre-YYYYMMDDHHMMSSffffff: the trailing component alone is a 20-digit microsecond
    # timestamp -- far more entropy than the old day-only id ever carried.
    assert pid.rsplit("-", 1)[-1].isdigit() and len(pid.rsplit("-", 1)[-1]) == 20


def test_dry_run_still_journals_snapshot_and_iteration_but_not_a_position(live_conn):
    broker = FakeBroker()
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=False, log=lambda *_: None)
    assert live_conn.execute("SELECT COUNT(*) FROM fly_snapshots").fetchone()[0] == 1
    assert live_conn.execute("SELECT COUNT(*) FROM fly_iterations").fetchone()[0] == 1
    assert live_conn.execute("SELECT COUNT(*) FROM fly_positions").fetchone()[0] == 0


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
            # floor = 1.05*100 - 3.44 fees - $20 worst-case exercise fee (4 contracts) = $81.56 >= 0
            # -> risk-free
            "net": 1.05,
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
    # Regression (2026-07-30): the moment a spread actually becomes a fly was never journaled at
    # all -- neither of _confirm_completion_fill's two call sites (run_once's own fill-confirmation
    # pass, and the burst watcher, which usually wins the race in practice) wrote to fly_decisions,
    # so the live dashboard's Decision Journal card showed zero completion rows despite real
    # completions having happened.
    decision = live_conn.execute("SELECT * FROM fly_decisions WHERE mode = 'completion'").fetchone()
    assert decision["reason"] == "completed" and decision["accepted"] == 1
    assert decision["position_id"] == "E1"
    # Regression (2026-07-30): live never recorded completion_latency_min/spot_at_completion the
    # way paper's book.py always has, so a live session's Performance card showed blank median
    # latency, latency range, and median spot move despite a real completion having happened.
    assert row["completion_latency_min"] is not None and row["completion_latency_min"] >= 0
    assert row["spot_at_completion"] == pytest.approx(7500.0)  # _snapshot()'s underlying_price


def test_completion_terminal_unfilled_is_journaled_and_reverts_to_short_vertical(live_conn):
    # A rejected/cancelled completion order reverts the position to a short vertical, and since
    # nothing else changed about eligibility, the same tick immediately retries — place_resting_
    # completion() claims the freed slot and rests a new order before run_once returns. So the
    # end state carries a *new* completion_order_id, not None; what proves the revert happened is
    # the journal, not a lingering empty slot.
    dbmod.save_position(
        live_conn,
        {
            **_open_entry_row(entry_fill_status="filled"),
            "completion_order_id": "ORD-C1",
            "completion_fill_status": "pending",
        },
    )
    broker = FakeBroker(order_statuses={"ORD-C1": {"status": "Rejected", "price": None, "filled": False}})
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    row = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert row["kind"] == "short_vertical"
    assert row["completion_order_id"] not in (None, "ORD-C1")
    decisions = live_conn.execute(
        "SELECT * FROM fly_decisions WHERE mode = 'completion' ORDER BY id"
    ).fetchall()
    assert [d["reason"] for d in decisions] == ["completion_rejected", "placed"]
    assert decisions[0]["accepted"] == 0
    assert decisions[1]["accepted"] == 1
    assert all(d["position_id"] == "E1" for d in decisions)


# --------------------------------------------------------------------------- pre-close ITM exit (live)


# --------------------------------------------------------------------------- pre-close ITM exit: verticals


# --------------------------------------------------------------------------- resting completion pricing
def test_max_safe_completion_debit_is_min_of_both_gates():
    # SPX entry: credit 1.05, fees 3.44 recorded; completion adds another 3.44, PLUS the resulting
    # fly's $15 worst-case exercise-assignment reserve (2026-08-01, when the pre-close ITM exit
    # that used to bound that cost was removed) -- this bound must reserve exactly what
    # fly.position_floor reserves, or a resting order could fill into a fly the floor gate refuses.
    # buffer gate: 1.05 - 0.10 = 0.95
    # floor bound at min_floor=10: 1.05 - (10 + 3.44 + 3.4433 + 15)/100 = 1.05 - 0.318833 = 0.731167
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
    assert bound == pytest.approx(0.731167, abs=1e-4)
    # Even at min_floor=0 the floor gate binds rather than the 0.95 buffer gate, and no credit
    # can change that: both gates sit a FIXED distance below the credit, so which one binds
    # depends only on (fees + reserve) vs fee_buffer*100. At $6.88 of fees plus the $15 reserve
    # against a $10 buffer, the floor gate wins outright -- one practical consequence of the
    # 2026-08-01 reserve is that the default fee_buffer no longer binds on SPX flies at all.
    assert live_orders.max_safe_completion_debit(pos, 0.0, 0.10) == pytest.approx(0.831167, abs=1e-4)
    # A buffer wide enough to clear fees + reserve does still bind, so the min() is real.
    assert live_orders.max_safe_completion_debit(pos, 0.0, 0.50) == pytest.approx(0.55)


def test_max_safe_completion_debit_reserves_exactly_what_position_floor_does():
    """The invariant the bound exists to hold: a completion filling AT the bound must produce a fly
    whose fly.position_floor is exactly min_floor_dollars -- never a cent below. If position_floor's
    assignment reserve and this bound's ever diverge, a resting order could fill into a fly the
    floor gate would have refused, which is the one thing a resting limit must not be able to do."""
    pos = {
        "side": PUT, "center": 7495.0, "wing_width": 5, "quantity": 1,
        "net": 1.05, "fees": 3.44, "symbol": "SPX",
    }
    min_floor = 10.0
    bound = live_orders.max_safe_completion_debit(pos, min_floor, 0.10)
    completed = {
        **pos,
        "kind": "fly",
        "net": pos["net"] - bound,
        "fees": pos["fees"] + fly.vertical_open_fee(pos["symbol"], 1),
    }
    assert fly.position_floor(completed) == pytest.approx(min_floor, abs=1e-6)


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
    assert spec["price"] == 0.70  # 0.731167 tick-floored
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
    # Regression (2026-07-30): the watcher is the PRIMARY completion-placement path in practice
    # (it polls far more often than the main tick), but its call to place_resting_completion was
    # never journaled -- only run_once's own fallback copy was, and it usually loses the race.
    decision = live_conn.execute("SELECT * FROM fly_decisions WHERE mode = 'completion'").fetchone()
    assert decision["reason"] == "placed" and decision["accepted"] == 1
    assert decision["position_id"] == "E1"


def test_watcher_confirms_a_completion_fill_and_records_latency_and_spot(live_conn, monkeypatch):
    """The watcher's own copy of the completion-fill-confirmation call site -- the one that
    usually wins the race in practice (polls every ~10s vs. the main tick's 1 minute) -- must
    record completion_latency_min/spot_at_completion too, not just run_once's copy."""
    dbmod.save_position(
        live_conn,
        {
            **_open_entry_row(entry_fill_status="filled"),
            "completion_order_id": "ORD-C1",
            "completion_fill_status": "pending",
        },
    )
    _fake_cache(monkeypatch, _snapshot())
    broker = FakeBroker(order_statuses={"ORD-C1": {"status": "Filled", "price": "0.80", "filled": True}})
    live_loop.run_watch(
        _watch_cfg(),
        live_conn,
        broker,
        cache_path="unused",
        live=True,
        log=lambda *_: None,
        sleep=lambda s: None,
    )
    row = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert row["kind"] == "fly" and row["completion_fill_status"] == "filled"
    assert row["completion_latency_min"] is not None and row["completion_latency_min"] >= 0
    assert row["spot_at_completion"] == pytest.approx(7500.0)  # _snapshot()'s underlying_price


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


def test_watcher_confirms_from_the_daemon_inbox_without_opening_a_websocket(live_conn, monkeypatch):
    """Daemon mode: a row the alert daemon already wrote to the WAL inbox is treated exactly like
    a cache touch -- the watcher must confirm from it WITHOUT opening its own websocket, and (as
    always) still confirm through the ordinary .status() call, never a second write path."""
    import alerts_db

    far = _open_entry_row()  # market far from the limit: cache-gating alone would never poll
    far["net"] = 5.00
    far["credit"] = 5.00
    dbmod.save_position(live_conn, far)
    _fake_cache(monkeypatch, _snapshot())

    inbox = alerts_db.connect()
    alerts_db.record_alert(
        inbox,
        {"order_id": "ORD-E1", "status": "Filled", "price": "1.10", "filled": True, "cancellable": False},
        "2026-07-31T10:00:00-04:00",
    )
    inbox.close()

    broker = FakeBroker(order_statuses={"ORD-E1": {"status": "Filled", "price": "1.10", "filled": True}})
    cfg = _watch_cfg()
    cfg["live"]["fill_heartbeat_seconds"] = 10_000
    cfg["live"]["use_order_alert_daemon"] = True
    ticks = iter(range(0, 1000))
    live_loop.run_watch(
        cfg,
        live_conn,
        broker,
        cache_path="unused",
        live=True,
        log=lambda *_: None,
        sleep=lambda s: None,
        clock_fn=lambda: next(ticks),
    )
    assert broker.alert_calls == []  # the daemon owns the websocket; the watcher never opens one
    assert broker.status_calls  # but the fill is still confirmed through the broker, as always
    row = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert row["entry_fill_status"] == "filled"


def test_watcher_with_an_empty_inbox_falls_back_to_the_heartbeat_poll(live_conn, monkeypatch):
    """A daemon that died, stalled, or was never started must cost latency and nothing else --
    the ordinary heartbeat poll still confirms the fill."""
    far = _open_entry_row()
    far["net"] = 5.00
    far["credit"] = 5.00
    dbmod.save_position(live_conn, far)
    _fake_cache(monkeypatch, _snapshot())

    broker = FakeBroker(order_statuses={"ORD-E1": {"status": "Filled", "price": "1.10", "filled": True}})
    cfg = _watch_cfg()
    cfg["live"]["fill_heartbeat_seconds"] = 3  # elapses during the run
    cfg["live"]["use_order_alert_daemon"] = True
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
    assert broker.alert_calls == []
    assert broker.status_calls  # the heartbeat carried it despite an empty inbox
    row = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert row["entry_fill_status"] == "filled"


def test_watcher_alert_stream_off_by_default_never_calls_it(live_conn, monkeypatch):
    """Regression: use_order_alert_stream defaults to False, so an unmodified config must never
    touch the new call at all -- byte-for-byte the pre-2026-07-31 polling loop."""
    dbmod.save_position(live_conn, _open_entry_row())
    _fake_cache(monkeypatch, _snapshot())
    broker = FakeBroker(order_statuses={"ORD-E1": {"status": "Filled", "price": "1.10", "filled": True}})
    ticks = iter(range(0, 1000))
    live_loop.run_watch(
        _watch_cfg(),
        live_conn,
        broker,
        cache_path="unused",
        live=True,
        log=lambda *_: None,
        sleep=lambda s: None,
        clock_fn=lambda: next(ticks),
    )
    assert broker.alert_calls == []


def test_watcher_confirms_faster_via_a_push_alert_than_cache_gating_alone_would(live_conn, monkeypatch):
    """With the market far from the cached limit and a huge heartbeat, cache-gating alone would
    never poll (see test_watcher_cache_gates_status_polls) -- but a push alert must still confirm
    the fill THIS cycle regardless of what the cache says."""
    far = _open_entry_row()
    far["net"] = 5.00
    far["credit"] = 5.00
    dbmod.save_position(live_conn, far)
    _fake_cache(monkeypatch, _snapshot())
    broker = FakeBroker(
        order_statuses={"ORD-E1": {"status": "Filled", "price": "1.10", "filled": True}},
        alerts=[[{"order_id": "ORD-E1", "status": "Filled", "cancellable": False, "price": "1.10", "filled": True}]],
    )
    cfg = _watch_cfg()
    cfg["live"]["fill_heartbeat_seconds"] = 10_000
    cfg["live"]["use_order_alert_stream"] = True
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
    assert broker.alert_calls and broker.alert_calls[0][0] == {"ORD-E1"}
    row = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert row["entry_fill_status"] == "filled"


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


def test_settle_auto_fetches_official_price_and_skips_provisional_entirely(live_conn, monkeypatch):
    """When a broker is given and it can answer, the settlement goes straight to 'official' --
    the provisional (last-trade) path is never even consulted."""
    import provider as providermod

    dbmod.save_position(
        live_conn,
        {**_open_entry_row(entry_fill_status="filled"), "kind": "fly", "net": 0.25, "debit": 0.80},
    )

    def boom(*a, **kw):
        raise AssertionError("must not read the stream cache when the broker already answered")

    monkeypatch.setattr(providermod, "read_spot", boom)
    broker = FakeBroker(official_price=(7495.5, "tastytrade_close"))
    out = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", broker=broker)
    # The stored source keeps WHICH source answered, rather than flattening to a bare "official"
    # -- it still counts as official (see live_loop._OFFICIAL_SOURCES), but the provenance is
    # recoverable from the ledger afterwards.
    assert out["ok"] and out["source"] == "tastytrade_close"
    assert live_loop._is_official_source(out["source"])
    assert broker.official_settlement_calls == ["SPX"]
    pos = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert pos["settlement_source"] == "tastytrade_close"


def test_settle_keeps_retrying_when_only_an_intraday_tick_is_available(live_conn, monkeypatch):
    """The 2026-07-31 case: tastytrade's `close` never posted, so the fetch could only offer an
    intraday `last`. That settles the book (better than nothing) but must NOT be called official,
    and must leave `session_officially_settled` False so the next tick tries again."""
    import provider as providermod

    dbmod.save_position(
        live_conn,
        {**_open_entry_row(entry_fill_status="filled"), "kind": "fly", "net": 0.25, "debit": 0.80},
    )
    monkeypatch.setattr(providermod, "read_spot", lambda *a, **kw: None)
    broker = FakeBroker(official_price=(7504.6, "tastytrade_last_provisional"))
    out = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", broker=broker)
    assert out["ok"] and out["source"] == "tastytrade_last_provisional"
    assert not live_loop._is_official_source(out["source"])
    assert not live_loop.session_officially_settled(live_conn, DAY)


def test_settle_falls_back_to_provisional_when_broker_fetch_is_unavailable(live_conn, monkeypatch):
    import provider as providermod

    dbmod.save_position(
        live_conn,
        {**_open_entry_row(entry_fill_status="filled"), "kind": "fly", "net": 0.25, "debit": 0.80},
    )
    monkeypatch.setattr(providermod, "read_spot", lambda *a, **k: 7495.5)
    broker = FakeBroker()  # default: no source available
    out = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", broker=broker)
    assert out["ok"] and out["source"] == "last_trade_provisional"
    assert broker.official_settlement_calls == ["SPX"]  # it was tried, just came up empty


def test_settle_retries_broker_fetch_on_a_still_provisional_book_and_upgrades(live_conn, monkeypatch):
    """A settle call with no broker (or one that can't answer yet) leaves the book provisional;
    a LATER call with a broker that now has an answer upgrades it to official -- the mechanism
    the tick relies on to keep retrying every minute until the print is out."""
    import provider as providermod

    dbmod.save_position(
        live_conn,
        {**_open_entry_row(entry_fill_status="filled"), "kind": "fly", "net": 0.25, "debit": 0.80},
    )
    monkeypatch.setattr(providermod, "read_spot", lambda *a, **k: 7495.5)
    out1 = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", broker=FakeBroker())
    assert out1["source"] == "last_trade_provisional"

    broker2 = FakeBroker(official_price=(7494.0, "yahoo"))
    out2 = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", broker=broker2)
    # Yahoo's post-close quote IS the closing print, so it upgrades the book to official -- and
    # the stored source records that it was yahoo, not a generic "official".
    assert out2["ok"] and out2["source"] == "yahoo"
    assert live_loop._is_official_source(out2["source"])
    assert broker2.official_settlement_calls == ["SPX"]
    pos = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert pos["settlement_source"] == "yahoo"


def test_settle_never_calls_broker_once_already_official(live_conn, monkeypatch):
    """Once a book is officially settled, a subsequent settle attempt must short-circuit on the
    already-official guard before ever touching the broker -- no repeated network calls on a tick
    that keeps firing after the print already landed."""
    import provider as providermod

    dbmod.save_position(
        live_conn,
        {**_open_entry_row(entry_fill_status="filled"), "kind": "fly", "net": 0.25, "debit": 0.80},
    )
    monkeypatch.setattr(providermod, "read_spot", lambda *a, **k: 7495.5)
    live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", price=7495.5)  # official

    def boom(symbol):
        raise AssertionError("must not call the broker once already officially settled")

    broker = FakeBroker()
    broker.official_settlement_price = boom
    out = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", broker=broker)
    assert not out["ok"] and "official" in out["reason"]


def test_session_officially_settled_requires_the_official_print_not_just_settled(live_conn, monkeypatch):
    import provider as providermod

    dbmod.save_position(
        live_conn,
        {**_open_entry_row(entry_fill_status="filled"), "kind": "fly", "net": 0.25, "debit": 0.80},
    )
    monkeypatch.setattr(providermod, "read_spot", lambda *a, **k: 7495.5)
    assert not live_loop.session_officially_settled(live_conn, DAY)

    live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused")  # provisional
    assert live_loop.session_already_settled(live_conn, DAY)
    assert not live_loop.session_officially_settled(live_conn, DAY)  # still not official

    live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused", price=7495.5)  # official
    assert live_loop.session_officially_settled(live_conn, DAY)


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


# --------------------------------------------------------------------------- per-day structure cap
def test_max_structures_per_day_blocks_even_after_risk_free_completion(live_conn):
    import clock

    # A completed, RISK-FREE fly frees the concurrency slot -- but with the day cap at 1 it
    # still spends the day's whole structure budget, so no second entry may follow.
    dbmod.save_position(
        live_conn,
        {
            "position_id": "DONE1",
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
            "net": 0.30,
            "fees": 3.44,
            "status": "open",
            "entry_time": clock.now_iso(),
            "completed_at": clock.now_iso(),
        },
    )
    cfg = _loop_cfg()
    cfg["live"]["max_structures_per_day"] = 1
    broker = FakeBroker()
    summary = live_loop.run_once(cfg, _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    assert summary["entered"] == 0
    assert any("max_structures_per_day reached (1/1)" in s.get("entry", "") for s in summary["skips"])
    # Without the cap the same state permits a new entry (the risk-free fly doesn't block).
    cfg["live"]["max_structures_per_day"] = None
    summary2 = live_loop.run_once(cfg, _snapshot(), live_conn, FakeBroker(), live=True, log=lambda *_: None)
    assert summary2["entered"] == 1


def test_cancelled_entries_do_not_consume_the_day_budget(live_conn):
    row = _open_entry_row()
    row["status"] = "cancelled"
    row["entry_fill_status"] = "cancelled"
    dbmod.save_position(live_conn, row)
    cfg = _loop_cfg()
    cfg["live"]["max_structures_per_day"] = 1
    summary = live_loop.run_once(cfg, _snapshot(), live_conn, FakeBroker(), live=True, log=lambda *_: None)
    assert summary["entered"] == 1  # the unfilled/cancelled attempt never held risk


# --------------------------------------------------------------------------- live vs paper
def _paper_conn():
    return dbmod.connect(dbmod.default_db_path())


def _legged_row(conn, pid, *, kind, latency=None, credit=1.0, debit=None, day=None):
    import clock

    dbmod.save_position(
        conn,
        {
            "position_id": pid,
            "book_id": f"{day or DAY}:gex:SPX",
            "trade_date": day or DAY,
            "arm": "gex",
            "entry_mode": "legged",
            "symbol": "SPX",
            "kind": kind,
            "side": PUT,
            "center": 7495.0,
            "wing_width": 5,
            "quantity": 1,
            "net": credit,
            "credit": credit,
            "debit": debit,
            "fees": 3.44,
            "status": "open",
            "entry_time": clock.now_iso(),
            "completion_latency_min": latency,
            "completed_at": clock.now_iso() if kind == "fly" else None,
        },
    )


def test_live_vs_paper_restricts_paper_to_live_sessions(live_conn):
    import analytics

    paper = _paper_conn()
    # Live traded only DAY; paper has DAY plus another session that must NOT count.
    _legged_row(live_conn, "L1", kind="fly", latency=8.0, debit=0.5)
    _legged_row(live_conn, "L2", kind="short_vertical")
    _legged_row(paper, "P1", kind="fly", latency=4.0, debit=0.4)
    _legged_row(paper, "P2", kind="fly", latency=6.0, debit=0.3, day="2020-01-02")
    out = analytics.live_vs_paper(live_conn, paper, "gex")
    assert out["sessions"] == [DAY]
    assert out["live"]["entries"] == 2 and out["live"]["completed"] == 1
    assert out["paper"]["entries"] == 1  # the other-session paper row was excluded
    assert out["completion_gap"] == pytest.approx(1.0 - 0.5)
    assert out["abort_rule"]["armed"] is False and out["abort_rule"]["triggered"] is False
    paper.close()


def test_abort_rule_arms_and_triggers(live_conn):
    import analytics

    paper = _paper_conn()
    # 30 live entries, 10 completed (33%); paper 10 entries, 9 completed (90%) -> gap 57pts.
    for i in range(30):
        _legged_row(live_conn, f"L{i}", kind="fly" if i < 10 else "short_vertical", debit=0.5)
    for i in range(10):
        _legged_row(paper, f"P{i}", kind="fly" if i < 9 else "short_vertical", debit=0.4)
    out = analytics.live_vs_paper(live_conn, paper, "gex")
    assert out["abort_rule"]["armed"] is True
    assert out["abort_rule"]["triggered"] is True
    paper.close()


def test_live_eod_report_written_on_settle(live_conn, monkeypatch):
    import provider as providermod

    dbmod.save_position(
        live_conn,
        {**_open_entry_row(entry_fill_status="filled"), "kind": "fly", "net": 0.25, "debit": 0.80},
    )
    monkeypatch.setattr(providermod, "read_spot", lambda *a, **k: 7495.5)
    out = live_loop.run_settle_live(_settle_cfg(), live_conn, cache_path="unused")
    assert out["ok"] and out["report"] is not None
    text = Path(out["report"]["live_eod"]).read_text(encoding="utf-8")
    assert "Flies LIVE EOD" in text
    assert "last_trade_provisional" in text and "PROVISIONAL" in text
    assert "Live vs contemporaneous paper" in text
    assert "Abort rule not yet armed" in text


# --------------------------------------------------------------------------- orphan sweep
def test_orphan_sweep_flags_unknown_working_orders(live_conn):
    dbmod.save_position(live_conn, _open_entry_row(order_id="ORD-KNOWN"))
    broker = FakeBroker(
        working=[
            {"order_id": "ORD-KNOWN", "status": "Live", "underlying_symbol": "SPX"},
            {"order_id": "ORD-MYSTERY", "status": "Live", "underlying_symbol": "SPX"},
        ]
    )
    logs = []
    summary = live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=logs.append)
    assert summary["orphaned_orders"] == 1
    assert any("ORPHANED" in m and "ORD-MYSTERY" in m for m in logs)
    assert live_loop.read_orphans()[0]["order_id"] == "ORD-MYSTERY"  # persisted for --status


def test_orphan_sweep_clean_when_all_accounted_for(live_conn):
    dbmod.save_position(live_conn, _open_entry_row(order_id="ORD-KNOWN"))
    broker = FakeBroker(working=[{"order_id": "ORD-KNOWN", "status": "Live", "underlying_symbol": "SPX"}])
    summary = live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=lambda *_: None)
    assert summary["orphaned_orders"] == 0
    assert live_loop.read_orphans() == []


def test_orphan_sweep_ignores_working_orders_in_other_symbols(live_conn):
    """The account isn't exclusive to this loop -- a resting order in a symbol this arm doesn't
    trade (manual trading sharing the account, another module) is real and none of this sweep's
    business. Confirmed 2026-07-30: a shared account's own manual trading across a dozen other
    symbols was firing the CRITICAL orphan alert every tick."""
    broker = FakeBroker(
        working=[
            {"order_id": "ORD-MANUAL-1", "status": "Live", "underlying_symbol": "AAPL"},
            {"order_id": "ORD-MANUAL-2", "status": "Live", "underlying_symbol": "SOFI"},
        ]
    )
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
