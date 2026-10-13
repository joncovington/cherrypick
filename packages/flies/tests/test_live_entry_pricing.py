"""`live.entry_pricing` (2026-10-12): the opening credit at mid first, conceding once.

The default ("concession", or the key absent) must stay byte-identical to the pilot's rule: the
fresh credit, mid less `slippage_frac` of the legs' spreads, floored to the tick. "mid_first" asks
the mid (floored to the tick, so never past it) on a structure's first order of the day and, if that
order is still resting a tick later, cancels it and re-places at the concession through the ordinary
entry stage, so the fresh-quote check and every gate apply to the replacement.

The fixture snapshot's ATM put spread (7500/7495) has a 1.20 mid and a 1.15 concession price.
"""

import pytest
from test_live_scaffold import DAY, FakeBroker, _loop_cfg, _quote_table, _snapshot

from cherrypick.flies import db as dbmod
from cherrypick.flies import fly, live_loop, live_orders


@pytest.fixture
def live_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return dbmod.connect(dbmod.live_db_path())


def _cfg(pricing=None):
    cfg = _loop_cfg()
    if pricing is not None:
        cfg["live"]["entry_pricing"] = pricing
    return cfg


def _tick(conn, cfg, broker, snap=None):
    return live_loop.run_once(cfg, snap or _snapshot(), conn, broker, live=True, log=lambda *_: None)


def _rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM fly_positions ORDER BY id").fetchall()]


def _entry_details(conn):
    return [
        r["detail"]
        for r in conn.execute(
            "SELECT detail FROM fly_decisions WHERE mode = 'entry' AND reason = 'entered' ORDER BY id"
        ).fetchall()
    ]


def _expected_prices(snap=None):
    """(concession, mid) for the entry the fixture produces, from the same quotes the loop prices."""
    table = _quote_table(snap or _snapshot())
    short_q, long_q = table["SPXW  260729P07500000"], table["SPXW  260729P07495000"]
    return (
        live_orders.tick_floor(fly.vertical_credit(short_q, long_q)),
        fly.vertical_credit(short_q, long_q, 0.0),
    )


# --------------------------------------------------------------------------- the default
@pytest.mark.parametrize("pricing", [None, "concession", "something-else"])
def test_the_default_prices_exactly_as_the_pilot_always_has(live_conn, pricing):
    concession, _mid = _expected_prices()
    broker = FakeBroker()
    _tick(live_conn, _cfg(pricing), broker)
    assert [p["spec"]["price"] for p in broker.placed] == [concession] == [1.15]
    (row,) = _rows(live_conn)
    assert row["net"] == row["credit"] == concession
    # The decision journal's detail is the pre-option string, character for character.
    assert _entry_details(live_conn) == ["credit 1.15, order ORD1"]


def test_the_default_leaves_an_unfilled_order_resting_tick_after_tick(live_conn):
    broker = FakeBroker()
    for _ in range(3):
        _tick(live_conn, _cfg(), broker)
    assert broker.cancelled == []
    assert [p["spec"]["price"] for p in broker.placed] == [1.15]


def test_the_default_and_an_explicit_concession_place_identical_orders_and_rows(tmp_path, monkeypatch):
    def run(pricing, home):
        monkeypatch.setenv("CHERRYPICK_HOME", str(home))
        conn = dbmod.connect(dbmod.live_db_path())
        broker = FakeBroker()
        for _ in range(2):
            _tick(conn, _cfg(pricing), broker)
        keep = ("kind", "side", "center", "net", "credit", "entry_mid_at_submit", "fees", "entry_fill_status")
        return [p["spec"] for p in broker.placed], [{k: r[k] for k in keep} for r in _rows(conn)]

    absent = run(None, tmp_path / "a")
    explicit = run("concession", tmp_path / "b")
    for spec in absent[0] + explicit[0]:
        spec.pop("external_identifier", None)  # minted from the clock
    assert absent == explicit


# --------------------------------------------------------------------------- mid first
def test_mid_first_asks_the_mid_then_concedes_once_on_the_next_tick(live_conn):
    concession, mid = _expected_prices()
    broker = FakeBroker()  # every status() answers "Live": the order never fills

    _tick(live_conn, _cfg("mid_first"), broker)
    assert [p["spec"]["price"] for p in broker.placed] == [live_orders.tick_floor(mid)] == [1.20]
    (first,) = _rows(live_conn)
    assert first["net"] == 1.20 and first["entry_fill_status"] == "pending"
    assert first["entry_mid_at_submit"] == pytest.approx(mid, abs=1e-4)

    _tick(live_conn, _cfg("mid_first"), broker)
    assert broker.cancelled == ["ORD1"]
    assert [p["spec"]["price"] for p in broker.placed] == [1.20, concession]
    first, second = _rows(live_conn)
    assert first["status"] == "cancelled"
    assert second["net"] == concession and second["entry_fill_status"] == "pending"

    # Conceded once: the concession order rests like any default order, no further walk.
    _tick(live_conn, _cfg("mid_first"), broker)
    assert broker.cancelled == ["ORD1"]
    assert len(broker.placed) == 2
    assert _entry_details(live_conn) == [
        "credit 1.20, order ORD1, pricing mid_first",
        "credit 1.15, order ORD2, pricing conceded",
    ]


def test_a_mid_first_order_that_fills_is_never_conceded(live_conn):
    broker = FakeBroker()
    _tick(live_conn, _cfg("mid_first"), broker)
    broker._statuses["ORD1"] = {"status": "Filled", "price": "1.20", "filled": True}
    _tick(live_conn, _cfg("mid_first"), broker)
    assert broker.cancelled == []
    row = _rows(live_conn)[0]
    assert row["entry_fill_status"] == "filled" and row["net"] == pytest.approx(1.20)


def test_mid_first_never_prices_beyond_mid_and_floors_to_the_tick(live_conn):
    """A 1.23 mid asks 1.20, never 1.25 -- flooring a credit asks for less."""
    snap = _snapshot()
    table = _quote_table(snap)
    short = "SPXW  260729P07500000"
    richer = {**table, short: {**table[short], "mid": table[short]["mid"] + 0.03}}
    broker = FakeBroker(snapshot=snap, fresh=richer)
    _tick(live_conn, _cfg("mid_first"), broker, snap)
    fresh_mid = fly.vertical_credit(richer[short], richer["SPXW  260729P07495000"], 0.0)
    assert fresh_mid == pytest.approx(1.23)
    (placed,) = broker.placed
    assert placed["spec"]["price"] == 1.20
    assert placed["spec"]["price"] <= fresh_mid
    assert round(placed["spec"]["price"] * 100) % 5 == 0


def test_entry_mid_price_is_the_floored_mid_and_never_below_the_concession():
    assert live_orders.entry_mid_price(1.23, 1.15) == 1.20
    assert live_orders.entry_mid_price(1.20, 1.15) == 1.20
    assert live_orders.entry_mid_price(1.17, 1.15) == 1.15
    assert live_orders.entry_mid_price(1.249, 1.20) == 1.20


def test_when_the_mid_floors_to_the_concession_there_is_nothing_to_concede(live_conn):
    """A 1.17 mid floors to 1.15, the concession itself: one order at 1.15, left resting."""
    snap = _snapshot()
    table = _quote_table(snap)
    short = "SPXW  260729P07500000"
    tight = {
        **table,
        short: {"bid": 2.58, "ask": 2.62, "mid": 2.60},
        "SPXW  260729P07495000": {"bid": 1.42, "ask": 1.44, "mid": 1.43},
    }
    broker = FakeBroker(snapshot=snap, fresh=tight)
    for _ in range(2):
        _tick(live_conn, _cfg("mid_first"), broker, snap)
    assert [p["spec"]["price"] for p in broker.placed] == [1.15]
    assert broker.cancelled == []


def test_the_fresh_quote_check_still_refuses_a_mid_first_entry(live_conn):
    snap = _snapshot()
    table = _quote_table(snap)
    short = "SPXW  260729P07500000"
    worse = {**table, short: {**table[short], "mid": table[short]["mid"] - 5.0}}
    broker = FakeBroker(snapshot=snap, fresh=worse)
    summary = _tick(live_conn, _cfg("mid_first"), broker, snap)
    assert broker.placed == []
    assert any("fresh quote diverged" in s.get("entry", "") for s in summary["skips"])


def test_the_buying_power_cap_still_refuses_a_mid_first_entry(live_conn):
    cfg = _cfg("mid_first")
    cfg["live"]["max_open_margin_dollars"] = 100  # one 5-wide spread commits ~$380
    broker = FakeBroker()
    _tick(live_conn, cfg, broker)
    assert broker.placed == []


def test_a_structure_already_tried_today_is_priced_at_the_concession(live_conn):
    """The ledger, not process state, remembers the first try: a structure whose order ended
    unfilled earlier today (here a rejection) goes straight to the concession."""
    dbmod.save_position(
        live_conn,
        {
            "position_id": "EARLIER",
            "book_id": f"{DAY}:gex:SPX",
            "trade_date": DAY,
            "arm": "gex",
            "entry_mode": "legged",
            "symbol": "SPX",
            "kind": "short_vertical",
            "side": "put",
            "center": 7500.0,
            "wing_width": 5,
            "quantity": 1,
            "net": 1.20,
            "credit": 1.20,
            "fees": 3.44,
            "status": "cancelled",
            "entry_order_id": "OLD",
            "entry_fill_status": "rejected",
        },
    )
    broker = FakeBroker()
    _tick(live_conn, _cfg("mid_first"), broker)
    assert [p["spec"]["price"] for p in broker.placed] == [1.15]


def test_the_shipped_example_leaves_mid_first_off():
    import json
    from pathlib import Path

    example = json.loads(
        (Path(__file__).resolve().parents[1] / "config.example.json").read_text(encoding="utf-8")
    )
    assert live_orders.entry_pricing_mode(example["live"]) == live_orders.ENTRY_PRICING_CONCESSION
