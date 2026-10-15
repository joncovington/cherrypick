"""Live fill realism end to end: what the live loop records about each order, the backfill from
broker transactions and the gex spot trail, and the paper live-like completion shadow."""

import json
from datetime import datetime, timedelta

import pytest
from test_book import one_arm_config
from test_engine import q, snapshot
from test_live_scaffold import (
    DAY,
    FakeBroker,
    _fake_cache,
    _loop_cfg,
    _open_entry_row,
    _snapshot,
    _watch_cfg,
)

from cherrypick.flies import analytics, clock, fill_facts, live_loop, live_orders
from cherrypick.flies import book as bookmod
from cherrypick.flies import db as dbmod
from cherrypick.flies import fill_model as fm


@pytest.fixture
def live_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    return dbmod.connect(dbmod.live_db_path())


def _quiet(*_):
    return None


def _pending_completion(order_id="ORD-C1"):
    return {
        **_open_entry_row(entry_fill_status="filled"),
        "completion_order_id": order_id,
        "completion_fill_status": "pending",
    }


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# --------------------------------------------------------------------------- placement + path
def test_a_live_entry_records_its_order_and_each_tick_observes_it(live_conn):
    """Shown to fail without the recorder: the entry order gets a row at placement (its limit, the
    mid it was asked from, spot), and the next tick, finding it still working, puts one look at
    its market on the path."""
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, FakeBroker(), live=True, log=_quiet)
    pos = live_conn.execute("SELECT * FROM fly_positions").fetchone()
    order = dbmod.live_order(live_conn, pos["entry_order_id"])
    assert order["leg"] == fm.ENTRY and order["outcome"] == "working" and order["source"] == "live"
    assert order["limit_price"] == pytest.approx(pos["credit"])
    assert order["mid_at_submit"] == pytest.approx(pos["entry_mid_at_submit"])
    assert order["spot_at_submit"] == pytest.approx(7500.0)

    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, FakeBroker(), live=True, log=_quiet)
    path = dbmod.order_path(live_conn, pos["entry_order_id"])
    assert len(path) == 1 and path[0]["source"] == "tick"
    # Entry buys the wing and sells the centre: the snapshot's quotes at exactly those strikes.
    puts = _snapshot()["puts"]
    wing, center = pos["center"] - pos["wing_width"], pos["center"]
    assert path[0]["buy_bid"] == pytest.approx(puts[wing]["bid"])
    assert path[0]["sell_ask"] == pytest.approx(puts[center]["ask"])


def test_the_resting_completion_is_recorded_at_its_submitted_limit(live_conn):
    dbmod.save_position(live_conn, _open_entry_row(entry_fill_status="filled"))
    broker = FakeBroker()
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=_quiet)
    order = dbmod.live_order(live_conn, "ORD1")
    assert order["leg"] == fm.COMPLETION
    assert order["limit_price"] == pytest.approx(broker.placed[0]["spec"]["price"])
    # Its mid at placement: buy 7500 (mid 2.6) less sell 7495 (mid 1.4).
    assert order["mid_at_submit"] == pytest.approx(1.2)


def test_the_watcher_observes_working_orders_every_cycle(live_conn, monkeypatch):
    dbmod.save_position(live_conn, _pending_completion())
    _fake_cache(monkeypatch, _snapshot())
    ticks = iter(range(0, 1000, 5))
    live_loop.run_watch(
        {**_watch_cfg()},
        live_conn,
        FakeBroker(),
        cache_path="unused",
        live=True,
        seconds=12,
        poll=1,
        heartbeat=1000,
        log=_quiet,
        sleep=lambda s: None,
        clock_fn=lambda: next(ticks),
    )
    path = dbmod.order_path(live_conn, "ORD-C1")
    assert path and {r["source"] for r in path} == {"watch"}
    assert path[0]["leg"] == fm.COMPLETION


# --------------------------------------------------------------------------- the fill
def test_a_completion_fill_records_the_broker_time_price_and_all_three_distances(live_conn):
    """The broker's leg fills give the real fill time and price (2.20 against a 2.25 limit is price
    improvement the ledger's `debit` -- the order's own price field, i.e. its limit -- can never
    show), and the measures come from the last look BEFORE that time, not the one that noticed."""
    dbmod.save_position(live_conn, _pending_completion())
    fill_facts.placed(
        live_conn,
        dict(live_conn.execute("SELECT * FROM fly_positions").fetchone()),
        leg=fm.COMPLETION,
        order_id="ORD-C1",
        limit_price=1.10,
        snapshot=_snapshot(),
    )
    now = clock.now_et()
    filled_at = now - timedelta(seconds=20)
    # One look 10s before the broker filled: spot 7503 (8 points past the 7495 centre, put side),
    # completing spread at mid 1.20 / natural 1.40 against the 1.10 limit.
    dbmod.record_order_path(
        live_conn,
        observed_at=_iso(filled_at - timedelta(seconds=10)),
        trade_date=DAY,
        position_id="E1",
        order_id="ORD-C1",
        leg=fm.COMPLETION,
        source="watch",
        limit_price=1.10,
        buy_bid=2.5,
        buy_ask=2.7,
        sell_bid=1.3,
        sell_ask=1.5,
        spot=7503.0,
    )
    status = {
        "status": "Filled",
        "price": "1.10",
        "filled": True,
        "fills": [
            {"action": "Buy to Open", "quantity": 1, "fill_price": "1.45", "filled_at": _iso(filled_at)},
            {"action": "Sell to Open", "quantity": 1, "fill_price": "0.40", "filled_at": _iso(filled_at)},
        ],
    }
    live_loop.run_once(
        _loop_cfg(),
        _snapshot(),
        live_conn,
        FakeBroker(order_statuses={"ORD-C1": status}),
        live=True,
        log=_quiet,
    )
    order = dbmod.live_order(live_conn, "ORD-C1")
    assert order["outcome"] == "filled" and order["fill_time_source"] == "broker_order"
    assert fm.parse_ts(order["broker_filled_at"]) == fm.parse_ts(_iso(filled_at))
    assert order["broker_fill_price"] == pytest.approx(1.05)  # 0.05 better than the limit
    assert order["fill_spot"] == pytest.approx(7503.0) and order["fill_spot_source"] == "path"
    assert order["fill_dist_center"] == pytest.approx(8.0)
    assert order["fill_dist_long"] == pytest.approx(3.0)
    assert order["fill_dist_widths"] == pytest.approx(1.6)
    assert order["fill_mid_gap"] == pytest.approx(0.10)
    assert order["fill_natural_gap"] == pytest.approx(0.30)
    assert order["fill_obs_age_s"] == pytest.approx(10.0)
    # The position books the broker's price, not the status's limit (2026-10-12).
    pos = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert pos["kind"] == "fly" and pos["debit"] == pytest.approx(1.05)


def _path_look(observed_at: str, *, buy, sell, spot: float) -> dict:
    return {
        "observed_at": observed_at,
        "source": "tick",
        "limit_price": 1.90,
        "buy_bid": buy[0],
        "buy_ask": buy[1],
        "sell_bid": sell[0],
        "sell_ask": sell[1],
        "spot": spot,
    }


def _filled_completion(broker_filled_at: str) -> dict:
    return {
        "order_id": "512454758",
        "leg": fm.COMPLETION,
        "side": "put",
        "center": 7770.0,
        "wing_width": 5.0,
        "limit_price": 1.90,
        "broker_filled_at": broker_filled_at,
        "resolved_at": "2026-10-07T11:13:57-04:00",
    }


def test_a_price_gap_is_never_taken_from_a_look_long_before_the_fill():
    """Shown to fail on the old bound (the price gap shared spot's 60s): order 512454758 on
    2026-10-07 filled at 1.90 at 11:13:49 ET, in one of the burst watcher's minute-long holes in
    the path. The last look before it was the 11:12:56 tick, 53s earlier, with the spread at mid
    2.45 -- recorded as the market sitting 0.55 above the limit it filled at. Spot moved 6.5 points
    in that minute; the first look after the fill had the mid at 2.00. A look that old says nothing
    about the price at the fill, so the gap is NULL; spot, which has the trail behind it, keeps its
    own minute."""
    order = _filled_completion("2026-10-07T15:13:49.356000+00:00")
    path = [_path_look("2026-10-07T11:12:56-04:00", buy=(11.5, 11.6), sell=(9.0, 9.2), spot=7772.83)]
    out = fill_facts.measures(order, None, path)
    assert out["fill_mid_gap"] is None and out["fill_natural_gap"] is None
    assert out["fill_mid"] is None and out["fill_obs_at"] is None and out["fill_obs_age_s"] is None
    assert out["fill_spot"] == pytest.approx(7772.83) and out["fill_spot_source"] == "path"


def test_a_fresh_look_gives_the_gap_and_says_how_old_it_was():
    order = _filled_completion("2026-10-07T15:13:49.356000+00:00")
    path = [
        _path_look("2026-10-07T11:12:56-04:00", buy=(11.5, 11.6), sell=(9.0, 9.2), spot=7772.83),
        _path_look("2026-10-07T11:13:41-04:00", buy=(8.6, 8.7), sell=(6.6, 6.7), spot=7778.9),
    ]
    out = fill_facts.measures(order, None, path)
    assert out["fill_obs_at"] == "2026-10-07T11:13:41-04:00"
    assert out["fill_obs_age_s"] == pytest.approx(8.4)
    assert out["fill_mid"] == pytest.approx(2.0) and out["fill_mid_gap"] == pytest.approx(0.10)


def test_a_ledger_from_before_fill_obs_age_is_migrated_on_connect(tmp_path):
    """The deployed ledgers' `fly_live_orders` predates the age column; `CREATE TABLE IF NOT
    EXISTS` never adds it, so without the migration every at-fill write would fail (inside
    `_telemetry`, silently)."""
    import sqlite3

    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE fly_live_orders (order_id TEXT PRIMARY KEY, trade_date TEXT, fill_obs_at TEXT)")
    raw.commit()
    raw.close()
    conn = dbmod.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(fly_live_orders)")}
    assert "fill_obs_age_s" in cols


def test_without_broker_fills_the_row_says_it_used_the_time_we_noticed(live_conn):
    """Before the order status carried leg fills, the only fill moment known is the look that
    noticed it. The row records that and says so, and its measures come from that look."""
    dbmod.save_position(live_conn, _pending_completion())
    broker = FakeBroker(order_statuses={"ORD-C1": {"status": "Filled", "price": "1.10", "filled": True}})
    live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=_quiet)
    order = dbmod.live_order(live_conn, "ORD-C1")
    assert order["fill_time_source"] == "noticed" and order["broker_filled_at"] is None
    assert order["broker_fill_price"] is None, "the limit is never passed off as a fill price"
    assert order["fill_spot_source"] == "path" and order["fill_dist_center"] == pytest.approx(5.0)
    assert order["fill_mid_gap"] == pytest.approx(0.10)


def test_a_cutoff_cancelled_completion_is_recorded_as_an_unfilled_order(live_conn):
    dbmod.save_position(live_conn, _pending_completion())
    live_loop.run_once(
        _loop_cfg(), _snapshot(now_min=15 * 60 + 31), live_conn, FakeBroker(), live=True, log=_quiet
    )
    order = dbmod.live_order(live_conn, "ORD-C1")
    assert order["outcome"] == "cutoff_cancelled" and order["resolved_at"] is not None


def test_a_telemetry_failure_never_stops_a_fill_being_confirmed(live_conn, monkeypatch):
    """Shown to fail without `_telemetry`: a recorder that raises inside the fill confirmation
    would abort the tick with a filled order unrecorded in the ledger."""
    dbmod.save_position(live_conn, _pending_completion())

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(fill_facts, "filled", boom)
    monkeypatch.setattr(fill_facts, "observe", boom)
    logged = []
    broker = FakeBroker(order_statuses={"ORD-C1": {"status": "Filled", "price": "1.10", "filled": True}})
    try:
        live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, broker, live=True, log=logged.append)
        escaped = None
    except RuntimeError as exc:  # caught so the guard fails on its assertion, not on the raise
        escaped = exc
    assert escaped is None, f"a telemetry failure escaped into the fill confirmation: {escaped}"
    pos = live_conn.execute("SELECT * FROM fly_positions WHERE position_id = 'E1'").fetchone()
    assert pos["kind"] == "fly" and pos["completion_fill_status"] == "filled"
    assert any("fill telemetry" in str(m) and "disk full" in str(m) for m in logged)


# --------------------------------------------------------------------------- backfill
def _seed_history(conn):
    """One filled completion and one cut off at 15:30, as the ledger held them before 10-02."""
    base = {
        "book_id": f"{DAY}:control:SPX",
        "trade_date": DAY,
        "arm": "control",
        "entry_mode": "legged",
        "symbol": "SPX",
        "side": "call",
        "wing_width": 5,
        "quantity": 1,
        "fees": 6.88,
        "entry_fill_status": "filled",
    }
    dbmod.save_position(
        conn,
        {
            **base,
            "position_id": "H1",
            "kind": "fly",
            "center": 7715.0,
            "credit": 2.50,
            "debit": 2.25,
            "net": 0.25,
            "status": "settled",
            "entry_time": f"{DAY}T11:10:47-04:00",
            "completed_at": f"{DAY}T11:24:52-04:00",
            "entry_order_id": "100",
            "completion_order_id": "101",
            "completion_fill_status": "filled",
            "entry_vol_value": 0.004,
            "underlying_at_entry": 7715.0,
        },
    )
    dbmod.save_position(
        conn,
        {
            **base,
            "position_id": "H2",
            "kind": "short_vertical",
            "center": 7740.0,
            "credit": 2.40,
            "net": 2.40,
            "status": "settled",
            "entry_time": f"{DAY}T13:00:00-04:00",
            "entry_order_id": "200",
        },
    )
    for pid, reason, when in (
        ("H1", "placed", f"{DAY}T11:10:55-04:00"),
        ("H2", "placed", f"{DAY}T13:00:10-04:00"),
        ("H2", "cutoff_cancelled", f"{DAY}T15:30:03-04:00"),
    ):
        dbmod.record_decision(
            conn,
            trade_date=DAY,
            arm="control",
            symbol="SPX",
            mode="completion",
            reason=reason,
            accepted=True,
            position_id=pid,
            when=when,
        )


def _trail(points):
    """A trail_path over in-memory (iso, spot) points."""

    def trail_path(start, end):
        lo, hi = fm.parse_ts(start) - timedelta(seconds=60), fm.parse_ts(end)
        return [(ts, s) for ts, s in points if lo <= fm.parse_ts(ts) <= hi]

    return trail_path


def test_backfill_recovers_broker_times_prices_and_spot_distances(live_conn):
    _seed_history(live_conn)
    fills = fill_facts.transaction_fills(
        [
            {
                "transaction_type": "Trade",
                "order_id": 100,
                "value": "-252.0",
                "executed_at": f"{DAY}T15:10:50+00:00",
            },
            {
                "transaction_type": "Trade",
                "order_id": 100,
                "value": "502.0",
                "executed_at": f"{DAY}T15:10:50+00:00",
            },
            {
                "transaction_type": "Trade",
                "order_id": 101,
                "value": "-410.0",
                "executed_at": f"{DAY}T15:24:40+00:00",
            },
            {
                "transaction_type": "Trade",
                "order_id": 101,
                "value": "190.0",
                "executed_at": f"{DAY}T15:24:41+00:00",
            },
            {"transaction_type": "Receive Deliver", "order_id": None, "value": "0"},
        ]
    )
    trail = _trail(
        [
            (f"{DAY}T11:15:00-04:00", 7712.0),
            (f"{DAY}T11:24:30-04:00", 7704.43),  # 11s before the broker's 11:24:41 ET fill
            (f"{DAY}T11:24:45-04:00", 7690.0),  # after the fill: never used
            (f"{DAY}T13:30:00-04:00", 7745.0),
            (f"{DAY}T14:00:00-04:00", 7736.0),
        ]
    )
    plan = fill_facts.backfill_rows(live_conn, fills=fills, trail_path=trail)
    by_id = {o["order_id"]: o for o in plan["orders"]}

    comp = by_id["101"]
    assert comp["outcome"] == "filled" and comp["fill_time_source"] == "transactions"
    assert comp["broker_filled_at"] == f"{DAY}T11:24:41-04:00", "re-stamped in ET, the ledger's convention"
    assert comp["broker_fill_price"] == pytest.approx(2.20)  # 2.25 limit: 0.05 improvement
    assert comp["placed_at"] == f"{DAY}T11:10:55-04:00"
    assert comp["fill_spot"] == pytest.approx(7704.43) and comp["fill_spot_source"] == "trail"
    assert comp["fill_dist_center"] == pytest.approx(10.57)
    assert comp["fill_dist_long"] == pytest.approx(5.57)
    assert comp["fill_dist_moves"] == pytest.approx(10.57 / (0.004 * 7715.0), abs=1e-4)
    assert comp["fill_mid_gap"] is None, "no quote history: a price gap is never estimated"

    assert by_id["100"]["broker_fill_price"] == pytest.approx(2.50) and by_id["100"]["leg"] == fm.ENTRY

    # The unfilled completion is keyed by the external identifier its order carried.
    miss = by_id["H2-completion"]
    assert miss["outcome"] == "cutoff_cancelled" and miss["resolved_at"] == f"{DAY}T15:30:03-04:00"
    assert [s for _, s in [(r["observed_at"], r["spot"]) for r in plan["paths"]["H2-completion"]]] == [
        7745.0,
        7736.0,
    ]

    # Dry run writes nothing; --write does, and a live-written row is never overwritten.
    assert live_conn.execute("SELECT COUNT(*) FROM fly_live_orders").fetchone()[0] == 0
    fill_facts.write_backfill(live_conn, plan)
    assert dbmod.live_order(live_conn, "101")["source"] == "backfill"
    dbmod.save_live_order(live_conn, {"order_id": "101", "source": "live"})
    again = fill_facts.backfill_rows(live_conn, fills=fills, trail_path=trail)
    assert "101" not in {o["order_id"] for o in again["orders"]}


def test_backfilled_orders_feed_the_distance_rule_fit(live_conn):
    _seed_history(live_conn)
    trail = _trail(
        [
            (f"{DAY}T11:15:00-04:00", 7712.0),
            (f"{DAY}T11:24:30-04:00", 7704.43),
            (f"{DAY}T13:30:00-04:00", 7745.0),
        ]
    )
    fill_facts.write_backfill(live_conn, fill_facts.backfill_rows(live_conn, fills={}, trail_path=trail))
    out = analytics.fill_realism(live_conn)
    assert out["legs"][fm.COMPLETION]["outcomes"] == {"filled": 1, "cutoff_cancelled": 1}
    assert out["rule_fit"]["price_orders"] == 0 and out["rule_fit"]["distance_orders"] == 2
    two_widths = next(r for r in out["rule_fit"]["dist"] if r["value"] == 2.0)
    # The fill reached 10.57/5 = 2.1 widths; the miss never got past the centre.
    assert (two_widths["hit"], two_widths["true_no_fill"]) == (1, 1)


# --------------------------------------------------------------------------- the paper shadow
def test_paper_legged_entries_stamp_the_live_limit_and_track_first_touches(tmp_path):
    """Shown to fail without the shadow: the limit is exactly what a live completion order would
    have rested at, and later ticks fold the completing spread into a first-touch record -- even
    after paper's own rule has completed the position."""
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    config = one_arm_config(entry_modes=["legged"])
    first = bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control")
    pid = next(a for a in first["actions"] if a["action"] == "credit_spread_opened")["position_id"]
    row = dict(conn.execute("SELECT * FROM fly_positions WHERE position_id = ?", (pid,)).fetchone())
    params = config["defaults"]
    expected = live_orders.tick_floor(
        live_orders.max_safe_completion_debit(
            {**row, "net": row["credit"]},
            params.get("min_floor_dollars", 0.0),
            params.get("fee_buffer", 0.10),
        )
    )
    assert row["shadow_completion_limit"] == pytest.approx(expected)
    assert row["shadow_touches"] is None

    later = snapshot(underlying_price=6004.0, puts={6000: q(1.0, 1.2), 6005: q(2.4, 2.6)})
    bookmod.process_snapshot(later, config, conn, "control")
    row = dict(conn.execute("SELECT * FROM fly_positions WHERE position_id = ?", (pid,)).fetchone())
    assert row["kind"] == "fly", "paper's own rule completed it on this tick"
    touches = json.loads(row["shadow_touches"])
    gap = 1.4 - row["shadow_completion_limit"]  # mid of buy 6005 / sell 6000, against the limit
    assert touches["best_gap"][0] == pytest.approx(gap)
    assert touches["max_dist"][0] == pytest.approx((6004.0 - row["center"]) / row["wing_width"])


def test_paper_legged_entries_stamp_the_live_entry_limit_and_watch_the_first_minute(tmp_path):
    """Shown to fail without the entry shadow: the limit is what `live_orders.entry_spec` would
    ask, the entry tick is the path's origin, a look 30 s later is kept with spot's move toward
    completion, and a look past the minute is not."""
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    config = one_arm_config(entry_modes=["legged"])
    t0 = datetime(2026, 7, 20, 12, 0, 0, tzinfo=clock.ET)
    first = bookmod.process_snapshot(snapshot(underlying_price=5998.0), config, conn, "control", now=t0)
    pid = next(a for a in first["actions"] if a["action"] == "credit_spread_opened")["position_id"]

    def row():
        return dict(conn.execute("SELECT * FROM fly_positions WHERE position_id = ?", (pid,)).fetchone())

    r = row()
    assert r["side"] == "put", "spot under the centre sells the put spread"
    assert r["shadow_entry_limit"] == pytest.approx(live_orders.tick_floor(r["credit"]))
    (origin,) = json.loads(r["shadow_entry_path"])
    assert (origin["s"], origin["spot"], origin["move"]) == (0, 5998.0, pytest.approx(0.0))

    bookmod.process_snapshot(
        snapshot(underlying_price=5999.5), config, conn, "control", now=t0 + timedelta(seconds=30)
    )
    bookmod.process_snapshot(
        snapshot(underlying_price=6003.0), config, conn, "control", now=t0 + timedelta(seconds=75)
    )
    path = json.loads(row()["shadow_entry_path"])
    assert [o["s"] for o in path] == [0, 30], "the 75 s look is past the window"
    assert path[1]["move"] == pytest.approx(1.5), "a put spread completes on a rise"
    assert path[1]["mid_gap"] is not None


def test_shadow_entry_splits_paper_net_by_what_live_would_have_filled(tmp_path):
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    ran = json.dumps([{"s": 0, "move": 0.0}, {"s": 15, "move": 1.2}])
    still = json.dumps([{"s": 0, "move": 0.0}, {"s": 15, "move": 0.1}])
    for pid, path, kind, pnl in (
        ("P1", ran, "fly", 30.0),
        ("P2", still, "short_vertical", -250.0),
        ("P3", None, "fly", 5.0),
    ):
        dbmod.save_position(
            conn,
            {
                "position_id": pid,
                "book_id": f"{DAY}:control:SPX",
                "trade_date": DAY,
                "arm": "control",
                "entry_mode": "legged",
                "symbol": "SPX",
                "kind": kind,
                "side": "put",
                "center": 7495.0,
                "wing_width": 5,
                "quantity": 1,
                "credit": 1.20,
                "net": 1.20,
                "fees": 6.88,
                "pnl": pnl,
                "status": "settled",
                "entry_time": f"{DAY}T10:30:00-04:00",
                "shadow_entry_limit": 1.20,
                "shadow_entry_path": path,
            },
        )
    out = analytics.shadow_entry(conn, values=[1.0])
    (cell,) = out["arms"]["control"]["shadow"]
    assert (cell["would_fill"], cell["would_miss"], cell["unjudged"]) == (1, 1, 1)
    assert (cell["filled_net"], cell["missed_net"], cell["missed_completed"]) == (-250.0, 30.0, 1)


def test_live_entry_paths_come_from_the_spot_trail_for_fills_and_misses_alike(live_conn):
    """The order path stops at the fill, so the fit reads the trail: a filled order's first minute
    is there as fully as a missed one's."""
    for oid, outcome, side in (("E1", "filled", "put"), ("E2", "replaced", "call"), ("E3", "working", "put")):
        dbmod.save_live_order(
            live_conn,
            {
                "order_id": oid,
                "trade_date": DAY,
                "position_id": f"P-{oid}",
                "leg": fm.ENTRY,
                "side": side,
                "placed_at": f"{DAY}T11:00:00-04:00",
                "spot_at_submit": 7500.0,
                "outcome": outcome,
                "source": "live",
            },
        )
    trail = _trail(
        [
            (f"{DAY}T10:59:50-04:00", 7499.0),  # before placement: never a look
            (f"{DAY}T11:00:15-04:00", 7500.6),
            (f"{DAY}T11:00:30-04:00", 7501.2),
            (f"{DAY}T11:01:10-04:00", 7505.0),  # past the minute
        ]
    )
    orders = {o["order_id"]: o for o in fill_facts.entry_spot_paths(live_conn, trail)}
    assert set(orders) == {"E1", "E2"}, "a working order is not judged"
    assert orders["E1"]["filled"] and not orders["E2"]["filled"]
    assert [(o["s"], o["move"]) for o in orders["E1"]["path"]] == [
        (0, 0.0),
        (15, pytest.approx(0.6)),
        (30, pytest.approx(1.2)),
    ]
    assert orders["E2"]["path"][-1]["move"] == pytest.approx(-1.2), "a call spread completes on a fall"
    fit = analytics.entry_rule_fit(list(orders.values()), values=[1.0])
    assert fit["always_fill"] == 0.5 and fit["rules"][0]["false_miss"] == 1


def test_shadow_completion_reads_settled_rows_per_arm(tmp_path):
    conn = dbmod.connect(str(tmp_path / "paper_trades.db"))
    k = fm.grid_key(0.10)
    for pid, kind, touched in (("P1", "fly", f"{DAY}T11:00:00-04:00"), ("P2", "short_vertical", None)):
        dbmod.save_position(
            conn,
            {
                "position_id": pid,
                "book_id": f"{DAY}:control:SPX",
                "trade_date": DAY,
                "arm": "control",
                "entry_mode": "legged",
                "symbol": "SPX",
                "kind": kind,
                "side": "put",
                "center": 7495.0,
                "wing_width": 5,
                "quantity": 1,
                "credit": 1.20,
                "net": 0.20 if kind == "fly" else 1.20,
                "fees": 6.88,
                "pnl": 10.0,
                "status": "settled",
                "settlement_price": 7495.0,
                "entry_time": f"{DAY}T10:30:00-04:00",
                "shadow_completion_limit": 1.00,
                "shadow_touches": json.dumps({"mid": {k: touched}} if touched else {"mid": {}}),
            },
        )
    out = analytics.shadow_completion(conn, values=[0.0, 0.10])
    arm = out["arms"]["control"]
    assert arm["paper"] == {"completed": 1, "completion_rate": 0.5, "net": 20.0}
    by_value = {s["value"]: s for s in arm["shadow"]}
    assert by_value[0.10]["completed"] == 1 and by_value[0.0]["completed"] == 0


def test_a_failed_live_submit_is_not_counted_as_entered(live_conn):
    # 2026-10-08: `entered` counted the attempt, so a failed submit spawned a fill watcher for an
    # order that never existed. The dry run's would-be entry still counts.
    class Refusing(FakeBroker):
        def place(self, spec, live):
            self.placed.append({"spec": spec, "live": live})
            return {"ok": False, "error": "ConnectError: no route to host"}

    out = live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, Refusing(), live=True, log=_quiet)
    assert out["entered"] == 0 and live_conn.execute("SELECT COUNT(*) FROM fly_positions").fetchone()[0] == 0
    out = live_loop.run_once(_loop_cfg(), _snapshot(), live_conn, FakeBroker(), live=False, log=_quiet)
    assert out["entered"] == 1
