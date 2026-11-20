"""The live tick against a fake broker: reconcile before plan, the ledger, dry-run, the gates, disarm,
orphans, the persisted hold and several funds. No real broker is ever called."""

import json
import pathlib
from datetime import datetime

import pytest
from cherrypick.core import broker as core_broker
from cherrypick.core import fees as core_fees
from cherrypick.core import home as _home
from cherrypick.core import live as _live
from cherrypick.core.clock import ET

from cherrypick.ladder import live_db, live_loop, live_tick, orders

DAY = "2026-11-23"  # a Monday, a trading day
ACCOUNT = "12345670000"
EXAMPLE = pathlib.Path(__file__).resolve().parents[1] / "config.example.json"
FILLED_AT = "2026-11-23T10:30:00-05:00"


def at(hhmm: str, day: str = DAY) -> datetime:
    h, m = hhmm.split(":")
    return datetime.fromisoformat(day).replace(hour=int(h), minute=int(m), tzinfo=ET)


def live_config(**live) -> dict:
    cfg = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    cfg["live"].update({"enabled": True, "gate0_confirmed": "owner 2026-11-20", "dry_run": False})
    cfg["live"].update(live)
    return cfg


def two_fund_config(**live) -> dict:
    cfg = live_config(
        **{"symbols": ["TQQQ", "SOXL"], "max_open_lots_total": 2, "correlated_groups": {}, **live}
    )
    cfg["names"]["SOXL"] = {}
    return cfg


# --------------------------------------------------------------------------- the fake broker
class FakeBroker:
    """The `core.execution.Broker` interface the tick uses, over an in-memory book."""

    def __init__(self):
        self.book: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.next_id = 1000
        self.working_error: Exception | None = None
        self.orders_error: Exception | None = None
        self.unreadable: set[str] = set()
        self.transactions: list[dict] = []
        self.shares: dict[str, float] = {}  # equity shares held, moved by every fill
        self.extra: list[dict] = []  # positions the ladder did not make (options, hand-held shares)
        self.positions_error = False
        self.positions_calls = 0

    # --- what the tick calls
    def positions(self):
        self.positions_calls += 1
        if self.positions_error:
            return None
        rows = [
            {"symbol": s, "instrument_type": "Equity", "underlying_symbol": s, "quantity": q}
            for s, q in self.shares.items()
            if q
        ]
        return rows + [dict(r) for r in self.extra]

    def place(self, spec, live):
        eid = spec["external_identifier"]
        self.calls.append(("place", eid, live))
        oid = str(self.next_id)
        self.next_id += 1
        leg = spec["legs"][0]
        self.book[oid] = {
            "order_id": oid,
            "external_identifier": eid,
            "status": "Live",
            "underlying_symbol": leg["symbol"],
            "price": spec["price"],
            "fills": [],
            "terminal": False,
        }
        return {"ok": True, "order_id": oid, "external_identifier": eid}

    def cancel(self, order_id):
        self.calls.append(("cancel", self.book[order_id]["external_identifier"]))
        row = self.book[order_id]
        row.update(status="Cancelled", terminal=True)
        return {"ok": True, "order_id": order_id}

    def status(self, order_id):
        if order_id in self.unreadable:
            return {"order_id": order_id, "status": None, "error": "boom"}
        row = self.book[order_id]
        return {
            "order_id": order_id,
            "status": row["status"],
            "price": str(row["price"]),
            "external_identifier": row["external_identifier"],
            "fills": list(row["fills"]),
        }

    def working_orders(self):
        if self.working_error:
            raise self.working_error
        return [self._row(r) for r in self.book.values() if not r["terminal"]]

    def orders_today(self):
        if self.orders_error:
            raise self.orders_error
        return [self._row(r) for r in self.book.values()]

    def history(self, trade_date, symbol=None):
        return list(self.transactions), None

    @staticmethod
    def _row(r):
        return {k: r[k] for k in ("order_id", "external_identifier", "status", "underlying_symbol")}

    # --- what a test does to the world
    def fill(self, order_id, price, qty=None, at=FILLED_AT):
        row = self.book[order_id]
        spec_qty = qty if qty is not None else int(orders.parse_id(row["external_identifier"])["shares"])
        row["fills"] = [{"quantity": str(spec_qty), "fill_price": str(price), "filled_at": at, "symbol": "x"}]
        row.update(status="Filled", terminal=True)
        parsed = orders.parse_id(row["external_identifier"]) or {}
        symbol = row["underlying_symbol"]
        sign = 1 if parsed.get("side") == "buy" else -1
        self.shares[symbol] = self.shares.get(symbol, 0) + sign * spec_qty

    def hand_order(self, eid, symbol="TQQQ"):
        oid = str(self.next_id)
        self.next_id += 1
        self.book[oid] = {
            "order_id": oid, "external_identifier": eid, "status": "Live", "underlying_symbol": symbol,
            "price": 1.0, "fills": [], "terminal": False,
        }  # fmt: skip
        return oid

    def order_id_of(self, client_prefix):
        return next(
            o for o, r in self.book.items() if (r["external_identifier"] or "").startswith(client_prefix)
        )

    def placed(self, side=None):
        out = [c[1] for c in self.calls if c[0] == "place"]
        return [e for e in out if side is None or orders.parse_id(e)["side"] == side]

    def cancelled(self):
        return [c[1] for c in self.calls if c[0] == "cancel"]


# --------------------------------------------------------------------------- the world
@pytest.fixture(autouse=True)
def seams(monkeypatch):
    monkeypatch.setattr(_live, "supervisor_heartbeat_fresh", lambda max_age_seconds=90: True)
    monkeypatch.setattr(live_loop, "_designated", lambda: ACCOUNT)


@pytest.fixture
def world(managed_home, cache, tmp_path):
    managed_home.mkdir(parents=True, exist_ok=True)
    (managed_home / "config.json").write_text(
        json.dumps({"modules": {"ladder": {"enabled": True}}}), encoding="utf-8"
    )
    cache.summary("TQQQ", DAY, prev_day_close=100.0)
    cache.quote("TQQQ", 95.0, 95.1)
    conn = live_db.connect(str(tmp_path / "live_trades.db"))
    arm(DAY)

    class World:
        pass

    w = World()
    w.cache, w.conn, w.broker = cache, conn, FakeBroker()
    return w


def arm(day: str = DAY) -> None:
    _live.write_arm_record("ladder", date=day, at="t", armed_by="test")


def disarm() -> None:
    _live.disarm("ladder")


def tick(w, cfg, hhmm, *, broker=None, day=DAY):
    return live_tick.run_once(
        cfg,
        broker=broker if broker is not None else w.broker,
        conn=w.conn,
        cache_path=w.cache.path,
        when=at(hhmm, day),
        designated=ACCOUNT,
    )


def lots(w, status=None):
    sql = "SELECT * FROM ladder_positions" + (" WHERE status = ?" if status else "")
    return [dict(r) for r in w.conn.execute(sql, (status,) if status else ())]


def decisions(w, mode):
    return [dict(r) for r in w.conn.execute("SELECT * FROM ladder_decisions WHERE mode = ?", (mode,))]


BUY1 = "ldr.b.TQQQ.20261123.1.10x9300"


# --------------------------------------------------------------------------- reconcile before plan
def test_a_buy_that_filled_unseen_becomes_a_lot_and_its_sell_rests_with_no_cancel(world):
    cfg = live_config()
    tick(world, cfg, "09:00")
    assert world.broker.placed() == [BUY1]
    world.broker.fill(world.broker.order_id_of("ldr.b"), 93.0)  # filled; the ledger has not seen it
    out = tick(world, cfg, "09:01")
    assert world.broker.cancelled() == []
    assert world.broker.placed("buy") == [BUY1]  # not placed a second time
    [lot] = lots(world)
    assert (lot["symbol"], lot["quantity"], lot["buy_price"], lot["status"]) == ("TQQQ", 10, 93.0, "open")
    assert lot["position_id"] == "session:TQQQ:2026-11-23:1"
    assert world.broker.placed("sell") == [orders.sell_id(lot["position_id"], 10, 96.72)]
    assert out["new_lots"] == [lot["position_id"]]


def test_a_filled_buy_is_one_lot_however_many_ticks_read_it(world):
    cfg = live_config()
    tick(world, cfg, "09:00")
    world.broker.fill(world.broker.order_id_of("ldr.b"), 93.0)
    for hhmm in ("09:01", "09:02", "09:03"):
        tick(world, cfg, hhmm)
    assert len(lots(world)) == 1
    assert len(world.broker.placed("sell")) == 1


def test_the_new_lot_carries_the_brokers_fill_and_ids(world):
    cfg = live_config()
    tick(world, cfg, "09:00")
    oid = world.broker.order_id_of("ldr.b")
    world.broker.fill(oid, 92.5)  # better than the limit: the fill is what is recorded
    tick(world, cfg, "09:01")
    [lot] = lots(world)
    assert lot["buy_price"] == 92.5 and lot["buy_level"] == 93.0 and lot["entry_value"] == -925.0
    assert lot["buy_order_id"] == oid and lot["buy_client_id"] == BUY1
    assert lot["buy_fees"] == core_fees.stock_trade_fee(10, 92.5, side="buy", ndigits=4)
    assert lot["target_price"] == 96.2  # worked from the fill: ceil(92.5 * 1.04)
    row = live_db.order(world.conn, BUY1)
    assert row["final"] == 1 and row["state"] == "filled" and row["fill_source"] == "fills"


def test_a_sell_fill_closes_the_lot_with_realized_money_that_adds_up(world):
    cfg = live_config()
    tick(world, cfg, "09:00")
    world.broker.fill(world.broker.order_id_of("ldr.b"), 93.0)
    tick(world, cfg, "09:01")
    world.broker.fill(world.broker.order_id_of("ldr.s"), 96.72, at="2026-11-23T11:30:00-05:00")
    out = tick(world, cfg, "09:02")
    [lot] = lots(world)
    assert out["closed_lots"] == [lot["position_id"]] and lot["status"] == "closed"
    buy_fee = core_fees.stock_trade_fee(10, 93.0, side="buy", ndigits=4)
    sell_fee = core_fees.stock_trade_fee(10, 96.72, side="sell", ndigits=4)
    assert lot["entry_value"] == -930.0 and lot["exit_value"] == 967.2
    assert lot["gross_pnl"] == round(lot["entry_value"] + lot["exit_value"], 2) == 37.2
    assert lot["fees"] == round(buy_fee + sell_fee, 2) and lot["settlement"] == 0.0 and lot["slippage"] == 0.0
    assert lot["net_pnl"] == round(lot["gross_pnl"] - lot["fees"], 2)
    assert (lot["buy_fees"], lot["sell_fees"]) == (buy_fee, sell_fee)
    assert lot["exit_reason"] == "target_gain" and lot["hold_days"] == 0
    assert lot["sell_client_id"].startswith("ldr.s.") and lot["fees_source"] == "modeled"


def test_the_brokers_own_fee_rows_replace_the_modeled_fee(world):
    cfg = live_config()
    tick(world, cfg, "09:00")
    buy = world.broker.order_id_of("ldr.b")
    world.broker.fill(buy, 93.0)
    tick(world, cfg, "09:01")
    sell = world.broker.order_id_of("ldr.s")
    world.broker.fill(sell, 96.72)
    world.broker.transactions = [
        {"transaction_type": "Trade", "order_id": int(buy), "clearing_fees": "0.10", "regulatory_fees": "0"},
        {
            "transaction_type": "Trade",
            "order_id": int(sell),
            "clearing_fees": "0.10",
            "regulatory_fees": "0.32",
        },
        {"transaction_type": "Trade", "order_id": 5, "clearing_fees": "9.99"},
    ]
    tick(world, cfg, "09:02")
    [lot] = lots(world)
    assert (lot["buy_fees"], lot["sell_fees"], lot["fees"]) == (0.1, 0.42, 0.52)
    assert lot["net_pnl"] == round(37.2 - 0.52, 2) and lot["fees_source"] == "broker"


def test_a_partly_sold_lot_stays_open_alerts_and_gets_no_second_sell(world):
    cfg = live_config()
    tick(world, cfg, "09:00")
    world.broker.fill(world.broker.order_id_of("ldr.b"), 93.0)
    tick(world, cfg, "09:01")
    sell = world.broker.order_id_of("ldr.s")
    world.broker.fill(sell, 96.72, qty=4)
    world.broker.book[sell]["status"] = "Cancelled"  # ended with 4 of 10 sold
    world.broker.calls.clear()
    out = tick(world, cfg, "09:02")
    assert lots(world)[0]["status"] == "open" and out["reconcile"]["alerts"][0]["kind"] == "partial_sell"
    assert any(d["reason"] == "partial_sell" for d in decisions(world, "alert"))
    tick(world, cfg, "09:03")
    assert world.broker.placed("sell") == []  # never a second sell for shares that are partly gone
    assert any(d["reason"].startswith("partial_sell_review") for d in decisions(world, "alert"))


def test_an_order_the_broker_ended_unfilled_is_not_re_placed_the_same_session(world):
    cfg = live_config()
    tick(world, cfg, "09:00")
    world.broker.cancel(world.broker.order_id_of("ldr.b"))  # ended by hand at the broker
    world.broker.calls.clear()
    tick(world, cfg, "09:01")
    assert world.broker.calls == []
    assert live_db.order(world.conn, BUY1)["ended_session"] == DAY


def test_an_unreadable_order_status_stops_the_plan(world):
    cfg = live_config()
    tick(world, cfg, "09:00")
    world.broker.unreadable = {world.broker.order_id_of("ldr.b")}
    world.broker.calls.clear()
    out = tick(world, cfg, "09:01")
    assert out["skipped"] == "reconcile_incomplete" and world.broker.calls == []


def test_an_unreadable_order_list_stops_the_plan(world):
    cfg = live_config()
    world.broker.orders_error = RuntimeError("down")
    out = tick(world, cfg, "09:00")
    assert out["skipped"] == "reconcile_incomplete" and world.broker.calls == []


# --------------------------------------------------------------------------- unarmed and outside the window
def test_unarmed_reconciles_a_fill_and_sends_nothing(world):
    cfg = live_config()
    tick(world, cfg, "09:00")
    world.broker.fill(world.broker.order_id_of("ldr.b"), 93.0)
    disarm()
    world.broker.calls.clear()
    out = tick(world, cfg, "09:01")
    assert world.broker.calls == []
    assert len(lots(world)) == 1 and out["new_lots"]  # read and recorded
    assert out["planned"] is False


def test_unarmed_cancels_nothing_even_with_a_resting_buy(world):
    cfg = live_config()
    tick(world, cfg, "09:00")
    disarm()
    world.broker.calls.clear()
    tick(world, cfg, "21:00")
    tick(world, cfg, "09:30")
    assert world.broker.calls == []


def test_before_session_start_only_reconciles(world):
    cfg = live_config()
    out = tick(world, cfg, "07:30")
    assert world.broker.calls == [] and out["planned"] is False


def test_a_stale_arm_record_is_removed_before_any_broker_read(world):
    arm("2026-11-20")
    out = tick(world, live_config(), "09:00")
    assert not live_loop.arm_stamp_date()
    assert world.broker.calls == [] and out["planned"] is False


def test_gates_unmet_means_the_broker_is_not_even_built(world, monkeypatch):
    built = []
    monkeypatch.setattr(live_tick, "BrokerAdapter", lambda config: built.append(1))
    cfg = live_config(enabled=False)
    out = live_tick.run_once(
        cfg, conn=world.conn, cache_path=world.cache.path, when=at("09:00"), designated=ACCOUNT
    )
    assert out["skipped"] == "gates_unmet" and built == []


# --------------------------------------------------------------------------- dry-run
def test_dry_run_sends_nothing_and_journals_the_intended_orders(world):
    cfg = live_config(dry_run=True)
    out = tick(world, cfg, "09:00")
    assert world.broker.calls == [] and out["dry_run"] is True and out["placed"] == 1
    [row] = decisions(world, "dry_run")
    assert BUY1 in row["reason"] and row["accepted"] == 1
    detail = json.loads(row["detail"])
    assert detail["shares"] == 10 and detail["limit"] == 93.0 and detail["tif"] == "Ext"
    assert live_db.orders(world.conn) == []  # nothing placed, so no order row
    tick(world, cfg, "09:01")
    [row] = decisions(world, "dry_run")  # the same intent is not journaled again ...
    assert row["occurrences"] == 1
    assert [i["what"] for i in live_tick.read_checks()["intents"]] == [
        f"place buy TQQQ {BUY1}"
    ]  # ... and is listed


@pytest.mark.parametrize("value", [True, "false", None, 0, "no"])
def test_only_a_literal_false_leaves_dry_run(value):
    assert live_tick.dry_run({"live": {"dry_run": value}}) is True
    assert live_tick.dry_run({"live": {}}) is True
    assert live_tick.dry_run({"live": {"dry_run": False}}) is False


def test_the_shipped_example_is_dry():
    assert json.loads(EXAMPLE.read_text(encoding="utf-8"))["live"]["dry_run"] is True


def test_live_submission_goes_out_marked_live_when_dry_run_is_off(world):
    tick(world, live_config(dry_run=False), "09:00")
    assert world.broker.calls == [("place", BUY1, True)]
    row = live_db.order(world.conn, BUY1)
    assert row["state"] == "working" and row["order_id"] and row["account"] == "****0000"
    assert decisions(world, "order") and not decisions(world, "dry_run")


def test_a_halted_day_cancels_the_resting_buy_and_still_places_a_sell(world):
    cfg = live_config()
    tick(world, cfg, "09:00")
    world.broker.fill(world.broker.order_id_of("ldr.b"), 93.0)
    flag = _home.halt_flag_path()
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("", encoding="utf-8")
    world.broker.calls.clear()
    tick(world, cfg, "09:01")
    assert world.broker.placed("buy") == [] and len(world.broker.placed("sell")) == 1


# --------------------------------------------------------------------------- disarm_at
def test_at_disarm_at_the_buys_are_cancelled_while_armed_then_the_loop_disarms(world, monkeypatch):
    cfg = live_config()
    tick(world, cfg, "09:00")  # a buy rests
    events = world.broker.calls
    real = live_loop.uninstall_task
    monkeypatch.setattr(live_loop, "uninstall_task", lambda: (events.append(("disarm",)), real())[1])
    events.clear()
    out = tick(world, cfg, "20:00")
    assert events[0] == ("cancel", BUY1) and events[-1] == ("disarm",)
    assert out["disarmed"] is True and live_loop.arm_stamp_date() is None
    assert out["passes"] == 2 and live_db.order(world.conn, BUY1)["state"] == "cancelled"


def test_disarm_never_cancels_a_sell(world):
    cfg = live_config()
    tick(world, cfg, "09:00")
    world.broker.fill(world.broker.order_id_of("ldr.b"), 93.0)
    tick(world, cfg, "09:01")
    sell = world.broker.placed("sell")
    world.broker.calls.clear()
    tick(world, cfg, "20:00")
    assert all(orders.parse_id(e)["side"] == "buy" for e in world.broker.cancelled())
    open_sell = [r for r in live_db.open_orders(world.conn) if r["side"] == "sell"]
    assert [r["client_id"] for r in open_sell] == sell


def test_a_buy_that_fills_as_it_is_cancelled_still_gets_its_sell_before_disarm(world):
    cfg = live_config()
    tick(world, cfg, "09:00")

    def cancel_loses_the_race(order_id):
        world.broker.fill(order_id, 93.0)  # it filled first
        return {"ok": False, "order_id": order_id, "error": "already filled"}

    world.broker.cancel = cancel_loses_the_race
    out = tick(world, cfg, "20:00")
    assert out["passes"] == 2 and len(lots(world)) == 1 and len(world.broker.placed("sell")) == 1
    assert live_loop.arm_stamp_date() is None


def test_after_the_disarm_the_next_tick_only_reads(world):
    cfg = live_config()
    tick(world, cfg, "09:00")
    tick(world, cfg, "20:00")
    world.broker.calls.clear()
    out = tick(world, cfg, "20:01")
    assert world.broker.calls == [] and out["planned"] is False


# --------------------------------------------------------------------------- orphans
def test_other_modules_orders_in_the_account_are_not_orphans(world):
    b = world.broker
    b.hand_order("flies.2026-11-23.abc")
    b.hand_order("meic-1")
    b.hand_order(None)
    b.hand_order("cp-deadbeef", symbol="SPX")
    assert live_tick.sweep_orphans(world.conn, b) == 0
    assert live_tick.read_orphans() == []


def test_a_ladder_order_the_ledger_never_recorded_is_an_orphan_and_is_adopted(world):
    cfg = live_config(dry_run=True)
    oid = world.broker.hand_order(BUY1)  # placed, then the tick died before the ledger write
    assert live_tick.sweep_orphans(world.conn, world.broker) == 1
    assert [o["order_id"] for o in live_tick.read_orphans()] == [oid]
    out = tick(world, cfg, "09:00")
    assert out["orphans"] == 1 and out["reconcile"]["adopted"] == [BUY1]
    assert live_db.order(world.conn, BUY1)["adopted"] == 1
    world.broker.fill(oid, 93.0)
    tick(world, cfg, "09:01")
    assert len(lots(world)) == 1 and live_tick.sweep_orphans(world.conn, world.broker) == 0


def test_an_orphan_sweep_that_cannot_look_is_unknown_and_stops_the_tick(world):
    world.broker.working_error = RuntimeError("no answer")
    out = tick(world, live_config(), "09:00")
    assert out["orphans"] is None and out["skipped"] == "orphan_sweep_unreadable"
    assert world.broker.calls == []


def test_our_order_ids_are_published_for_the_other_loops(world):
    tick(world, live_config(), "09:00")
    tick(world, live_config(), "09:01")
    published = json.loads(
        (_home.state_dir() / "live_known_orders" / "ladder.json").read_text(encoding="utf-8")
    )
    assert published["order_ids"] == [world.broker.order_id_of("ldr.b")]


# --------------------------------------------------------------------------- several funds
def test_each_fund_has_its_own_orders_lots_and_ledger_rows(world):
    cfg = two_fund_config()
    world.cache.summary("SOXL", DAY, prev_day_close=50.0)
    world.cache.quote("SOXL", 47.0, 47.1)
    tick(world, cfg, "09:00")
    placed = world.broker.placed("buy")
    assert sorted(orders.parse_id(e)["symbol"] for e in placed) == ["SOXL", "TQQQ"]
    for oid, row in world.broker.book.items():
        world.broker.fill(oid, orders.parse_id(row["external_identifier"])["price"])
    tick(world, cfg, "09:01")
    assert sorted((lot["symbol"], lot["quantity"]) for lot in lots(world)) == [("SOXL", 21), ("TQQQ", 10)]
    sells = {orders.parse_id(e)["lot"] for e in world.broker.placed("sell")}
    assert sells == {"session-TQQQ-2026-11-23-1", "session-SOXL-2026-11-23-1"}
    assert live_db.bought_in_session(world.conn, "session", "SOXL", DAY) == 1


def test_the_total_cap_holds_across_funds(world):
    cfg = two_fund_config(max_open_lots_total=1)
    world.cache.summary("SOXL", DAY, prev_day_close=50.0)
    tick(world, cfg, "09:00")
    assert len(world.broker.placed("buy")) == 1


# --------------------------------------------------------------------------- the ledger
def test_no_account_number_is_stored_anywhere(world):
    cfg = live_config()
    tick(world, cfg, "09:00")
    world.broker.fill(world.broker.order_id_of("ldr.b"), 93.0)
    tick(world, cfg, "09:01")
    world.conn.commit()
    raw = pathlib.Path(world.conn.execute("PRAGMA database_list").fetchone()[2])
    blob = b"".join(p.read_bytes() for p in raw.parent.glob(raw.name + "*"))
    assert ACCOUNT.encode() not in blob and b"****0000" in blob
    log = live_tick.log_file().read_text(encoding="utf-8")
    assert ACCOUNT not in log


def test_the_live_file_is_not_the_paper_file():
    assert live_db.live_db_path().endswith("live_trades.db")
    assert "paper_trades" not in live_db.live_db_path()


def test_the_live_file_reads_with_the_paper_readers(world):
    from cherrypick.ladder import db as paper_db

    cfg = live_config()
    tick(world, cfg, "09:00")
    world.broker.fill(world.broker.order_id_of("ldr.b"), 93.0)
    tick(world, cfg, "09:01")
    assert [lot["position_id"] for lot in paper_db.open_lots(world.conn)] == ["session:TQQQ:2026-11-23:1"]


def test_every_tick_leaves_an_iteration_row(world):
    tick(world, live_config(dry_run=True), "09:00")
    tick(world, live_config(), "07:00")
    phases = [r[0] for r in world.conn.execute("SELECT phase FROM ladder_loop_iterations ORDER BY id")]
    assert phases == ["dry_run", "reconcile"]


# --------------------------------------------------------------------------- the adapter
class Sdk:
    """What the fake SDK shows: the submissions it took, and the switch for an unreadable order list."""

    def __init__(self):
        self.submitted: list[str] = []
        self.orders_error: Exception | None = None
        self.hook = None


@pytest.fixture
def adapter(world, monkeypatch):
    """The real `BrokerAdapter` over a fake SDK (`_ensure`, `build_order` and the seam calls replaced)."""
    sdk = Sdk()

    async def place_order(account, session, order, *, live, serialize=None, deploy_limit_pct=None):
        sdk.submitted.append(order["external_identifier"])
        if sdk.hook:
            sdk.hook(order)
        return {"ok": True, "dry_run": not live, "response": {"order": {"id": 5000 + len(sdk.submitted)}}}

    async def orders_today(account, session):
        if sdk.orders_error:
            raise sdk.orders_error
        return []

    async def working_orders(account, session):
        return []

    async def positions(account, session):
        return []

    def ensure(self):
        self._account, self._session, self._resolved_for = "acct", "sess", ACCOUNT

    monkeypatch.setattr(core_broker, "place_order", place_order)
    monkeypatch.setattr(core_broker, "orders_today", orders_today)
    monkeypatch.setattr(core_broker, "working_orders", working_orders)
    monkeypatch.setattr(core_broker, "positions", positions)
    monkeypatch.setattr(core_broker, "build_order", lambda spec: spec)
    monkeypatch.setattr(live_tick.BrokerAdapter, "_ensure", ensure)
    sdk.place_order = place_order
    return sdk


def make_adapter(cfg):
    broker = live_tick.BrokerAdapter(cfg)
    broker.gate_when = at("09:00")
    return broker


def buy_spec(symbol="TQQQ", eid="ldr.b.TQQQ.20261123.1.10x9300"):
    return orders.buy_spec(symbol, 10, 93.0, eid)


SELL = "ldr.s.lot-1.10x9672"


def test_the_adapter_refuses_a_live_placement_while_dry_run_is_on(world, adapter):
    res = make_adapter(live_config(dry_run=True)).place(buy_spec(), True)
    assert res["ok"] is False and "live.dry_run" in " ".join(res["unmet_gates"]) and adapter.submitted == []


def test_the_adapter_places_when_dry_run_is_off_and_every_gate_holds(world, adapter):
    res = make_adapter(live_config()).place(buy_spec(), True)
    assert (
        res["ok"] and res["order_id"] == "5001" and adapter.submitted == [buy_spec()["external_identifier"]]
    )


def test_a_gate_flipped_mid_tick_blocks_the_next_placement(world, adapter):
    cfg = two_fund_config()
    world.cache.summary("SOXL", DAY, prev_day_close=50.0)
    adapter.hook = lambda order: disarm()  # the arm record goes right after the first order is taken
    out = live_tick.run_once(
        cfg, broker_factory=live_tick.BrokerAdapter, conn=world.conn, cache_path=world.cache.path,
        when=at("09:00"), designated=ACCOUNT,
    )  # fmt: skip
    assert len(adapter.submitted) == 1
    [refused] = decisions(world, "refused")
    assert out["failed"] == 1 and "arm.record" in refused["detail"]


def test_a_sell_passes_the_halt_flag_a_buy_does_not(world, adapter):
    flag = _home.halt_flag_path()
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("", encoding="utf-8")
    broker = make_adapter(live_config())
    assert broker.place(buy_spec(), True)["ok"] is False
    assert broker.place(orders.sell_spec("TQQQ", 10, 96.72, SELL), True)["ok"] is True


def test_a_sell_is_refused_once_the_arm_is_gone(world, adapter):
    broker = make_adapter(live_config())
    disarm()
    assert broker.place(orders.sell_spec("TQQQ", 10, 96.72, SELL), True)["ok"] is False
    assert adapter.submitted == []


def test_an_uncertain_submit_is_held_across_processes(world, adapter, monkeypatch):
    cfg = two_fund_config()
    world.cache.summary("SOXL", DAY, prev_day_close=50.0)

    async def raises(*a, **k):
        adapter.orders_error = TimeoutError("the read-back fails too")
        raise TimeoutError("no answer")

    monkeypatch.setattr(core_broker, "place_order", raises)
    out = live_tick.run_once(
        cfg, broker_factory=live_tick.BrokerAdapter, conn=world.conn, cache_path=world.cache.path,
        when=at("09:00"), designated=ACCOUNT,
    )  # fmt: skip
    # the first placement was uncertain, so the second was not even tried
    assert out["failed"] == 2 and out["placed"] == 0
    held = live_tick.read_held()
    assert len(held["unresolved"]) == 1 and pathlib.Path(live_tick.hold_path()).exists()
    status = live_loop.status(cfg, designated=ACCOUNT, when=at("09:01"))
    assert status["held"]["unresolved"] == held["unresolved"]

    # the next minute is a new process: a new adapter reads the hold from disk and submits nothing
    monkeypatch.setattr(core_broker, "place_order", adapter.place_order)
    res = make_adapter(cfg).place(buy_spec(), True)
    assert res["ok"] is False and res.get("uncertain") and adapter.submitted == []


# --------------------------------------------------------------------------- the commands
def test_status_says_the_order_path_exists_and_whether_it_is_dry(world):
    status = live_loop.status(live_config(dry_run=True), designated=ACCOUNT, when=at("09:00"))
    assert status["order_path"] is True and status["dry_run"] is True and status["mode"] == "dry-run"
    assert "dry-run" in status["note"] and status["held"] == {"unresolved": [], "unrecorded": {}}
    status = live_loop.status(live_config(dry_run=False), designated=ACCOUNT, when=at("09:00"))
    assert status["mode"] == "live" and status["dry_run"] is False
    assert status["ready"] is True  # ready still reflects the gates


def test_ready_still_reflects_the_gates_in_dry_run(world):
    disarm()
    status = live_loop.status(live_config(dry_run=True), designated=ACCOUNT, when=at("09:00"))
    assert status["ready"] is False and status["unmet_gates"] == ["arm.record"]


def test_once_through_the_command_runs_the_tick(world, capsys, monkeypatch, tmp_path):
    cfg = live_config(dry_run=True)
    path = tmp_path / "ladder.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    seen = {}

    def fake_run_once(config, **kw):
        seen["config"] = config
        return {"ok": True, "order_path": True}

    monkeypatch.setattr(live_tick, "run_once", fake_run_once)
    assert live_loop.main(["--config", str(path), "--once"]) == 0
    assert seen["config"]["live"]["dry_run"] is True
    assert json.loads(capsys.readouterr().out)["order_path"] is True
