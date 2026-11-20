"""The order layer against a fake broker. Nothing here touches a real broker or the ledger."""

from __future__ import annotations

import ast
import json
import pathlib
from datetime import datetime

import pytest
from cherrypick.core.broker import build_order
from cherrypick.core.clock import ET

from cherrypick.ladder import orders, rules

SRC = pathlib.Path(orders.__file__).parent
EXAMPLE = json.loads((SRC.parent.parent.parent / "config.example.json").read_text(encoding="utf-8"))

DAY = "2026-11-23"  # a Monday
RULE = {"entry": "session", "target": "fixed", "drop": 0.07, "gain": 0.04, "min_shares": 3}


def at(hhmm: str, day: str = DAY) -> datetime:
    h, m = hhmm.split(":")
    return datetime.fromisoformat(day).replace(hour=int(h), minute=int(m), tzinfo=ET)


def live_block(**over) -> dict:
    block = {k: v for k, v in EXAMPLE["live"].items() if not k.startswith("_")}
    block.update(
        symbols=["TQQQ"],
        max_open_lots=3,
        max_open_lots_total=6,
        max_open_lots_per_group=6,
        max_open_dollars=10000,
        correlated_groups={"nasdaq-semis": ["TQQQ", "SOXL"]},
    )
    block.update(over)
    return block


class FakeBroker:
    """A book of working orders: place adds one, cancel removes it, the test fills by hand."""

    def __init__(self, fail=None):
        self.working: list[dict] = []
        self.calls: list[tuple] = []
        self.fail = fail
        self._n = 0

    def place(self, spec, live):
        self.calls.append(("place", spec["external_identifier"], live))
        if self.fail:
            return dict(self.fail)
        self._n += 1
        oid = f"ORD{self._n}"
        self.working.append(
            {"order_id": oid, "external_identifier": spec["external_identifier"], "spec": spec}
        )
        return {
            "ok": True,
            "response": {"order": {"id": oid}},
            "external_identifier": spec["external_identifier"],
        }

    def cancel(self, order_id):
        self.calls.append(("cancel", order_id))
        self.working = [w for w in self.working if w["order_id"] != order_id]
        return {"ok": True}

    def working_orders(self):
        return [dict(w) for w in self.working]

    def status(self, order_id):
        self.calls.append(("status", order_id))
        return {"status": "Live"}


def run(live=None, rule=None, broker=None, dry_run=False, **state):
    """One tick: plan against the broker's working list, apply, return (plan, results)."""
    broker = broker if broker is not None else FakeBroker()
    state.setdefault("when", at("09:00"))
    state.setdefault("armed", True)
    state.setdefault("ref_close", {"TQQQ": 100.0})
    state.setdefault("lots", [])
    state.setdefault("marks", {"TQQQ": 100.0, "SOXL": 50.0, "SPXL": 80.0, "TECL": 50.0})
    p = orders.plan(live or live_block(), rule or RULE, working=broker.working_orders(), **state)
    return p, orders.Executor(broker, dry_run=dry_run, log=lambda m: None).apply(p)


def lot(i=1, symbol="TQQQ", shares=10, cost=93.0, buy_date="2026-11-20"):
    return {
        "id": f"session:{symbol}:{buy_date}:{i}",
        "symbol": symbol,
        "shares": shares,
        "cost": cost,
        "buy_date": buy_date,
    }


def prices(plan, side="buy"):
    return [a.spec["price"] for a in plan.places if a.side == side]


# --------------------------------------------------------------------------- buys
def test_buys_rest_at_the_session_levels_paper_uses():
    b = FakeBroker()
    p, _ = run(broker=b, live=live_block(max_open_lots=3))
    expect = [rules.buy_level("session", ref_close=100.0, last_buy=None, bought_today=n) for n in range(3)]
    assert prices(p) == expect == [93.0, 86.49, 80.43]
    assert [w["spec"]["time_in_force"] for w in b.working] == ["Ext"] * 3


def test_bought_today_advances_the_levels():
    p, _ = run(live=live_block(max_open_lots=1), buys_today={"TQQQ": 2})
    assert prices(p) == [rules.buy_level("session", ref_close=100.0, last_buy=None, bought_today=2)]


@pytest.mark.parametrize("entry,filled,expected", [("once", 0, 1), ("once", 1, 0), ("chain", 0, 1)])
def test_once_and_chain_place_one_level_at_most(entry, filled, expected):
    p, _ = run(rule={**RULE, "entry": entry}, buys_today={"TQQQ": filled})
    assert len(p.places) == expected


def test_chain_prices_from_the_anchor():
    p, _ = run(rule={**RULE, "entry": "chain"}, anchors={"TQQQ": 90.0}, lots=[lot()])
    assert prices(p, "buy") == [rules.floor_cent(90.0 * 0.93)]


def test_whole_shares_sized_at_the_level_never_over_the_lot():
    p, _ = run()
    for a in p.places:
        leg = a.spec["legs"][0]
        assert isinstance(leg["quantity"], int) and leg["quantity"] * a.spec["price"] <= 1000
        assert leg["quantity"] == rules.lot_shares(1000, a.spec["price"])


def test_a_lot_under_min_shares_is_refused():
    p, _ = run(ref_close={"TQQQ": 400.0})  # 372 -> 2 shares
    assert p.places == [] and any("lot_too_small" in r for r in p.buys_blocked)


def test_no_reference_close_no_buy_and_resting_buys_are_left():
    b = FakeBroker()
    run(broker=b, live=live_block(max_open_lots=1))
    p, _ = run(broker=b, live=live_block(max_open_lots=1), ref_close={"TQQQ": None})
    assert p.places == [] and p.cancels == [] and len(p.keeps) == 1


def test_nothing_before_session_start_and_a_buy_after_it():
    p, _ = run(when=at("08:14"))
    assert p.places == [] and "before_session_start" in p.buys_blocked
    p, _ = run(when=at("08:15"))
    assert p.places


def test_order_specs_are_the_shape_build_order_takes():
    p, _ = run(live=live_block(max_open_lots=2), lots=[lot(shares=10, cost=93.0)])
    buy = next(a for a in p.places if a.side == "buy").spec
    sell = next(a for a in p.places if a.side == "sell").spec
    b, s = build_order(buy), build_order(sell)
    assert str(b.time_in_force) == "Ext" and b.price < 0
    assert str(s.time_in_force) == "GTC Ext" and s.price > 0
    assert b.legs[0].action.name == "BUY_TO_OPEN" and s.legs[0].action.name == "SELL_TO_CLOSE"
    assert str(b.external_identifier) == buy["external_identifier"]


# --------------------------------------------------------------------------- caps
def test_per_fund_cap_counts_open_lots_and_resting_buys():
    b = FakeBroker()
    p, _ = run(broker=b, live=live_block(max_open_lots=2), lots=[lot()])
    assert len(p.places) == 1 + 1  # one buy, one sell for the held lot
    assert len([w for w in b.working if w["spec"]["legs"][0]["action"] == "buy to open"]) == 1


def test_total_cap_across_funds():
    p, _ = run(
        live=live_block(
            symbols=["TQQQ", "TECL"], max_open_lots=2, max_open_lots_total=2, correlated_groups={}
        ),
        ref_close={"TQQQ": 100.0, "TECL": 50.0},
    )
    assert [(a.symbol) for a in p.places] == ["TQQQ", "TQQQ"]
    assert any("TECL: total_cap_full" in r for r in p.buys_blocked)


def test_group_cap_holds_across_funds_in_a_group_but_not_outside_it():
    live = live_block(symbols=["TQQQ", "SOXL", "SPXL"], max_open_lots=2, max_open_lots_per_group=1)
    p, _ = run(live=live, ref_close={"TQQQ": 100.0, "SOXL": 50.0, "SPXL": 80.0})
    assert sorted({a.symbol for a in p.places}) == ["SPXL", "TQQQ"]
    assert [a.symbol for a in p.places].count("TQQQ") == 1
    assert any("SOXL: group_cap_full" in r for r in p.buys_blocked)


def test_group_cap_counts_an_open_lot_in_the_group():
    live = live_block(symbols=["SOXL"], max_open_lots=2, max_open_lots_per_group=1)
    p, _ = run(live=live, ref_close={"SOXL": 50.0}, lots=[lot(symbol="TQQQ", cost=93.0)])
    assert [a for a in p.places if a.side == "buy"] == []


def test_dollar_ceiling_counts_resting_buys_as_if_filled():
    live = live_block(max_open_lots=3, max_open_dollars=1500)
    p, _ = run(live=live)
    assert len(p.places) == 1  # 930 resting + a second 946 would pass 1500
    assert any("dollar_ceiling" in r for r in p.buys_blocked)


def test_dollar_ceiling_counts_open_lots():
    live = live_block(max_open_lots=3, max_open_dollars=1500)
    p, _ = run(live=live, lots=[lot(shares=10, cost=93.0)])
    assert [a for a in p.places if a.side == "buy"] == []


def test_resting_buys_hold_their_slots_on_the_next_run():
    b = FakeBroker()
    live = live_block(max_open_lots=1)
    run(broker=b, live=live)
    p, _ = run(broker=b, live=live)
    assert p.places == [] and p.cancels == [] and len(b.working) == 1


def test_invalid_limits_plan_nothing():
    p, _ = run(live=live_block(max_open_dollars=10, lot_dollars=1000))
    assert p.places == [] and any("limits_invalid" in r for r in p.buys_blocked)


# --------------------------------------------------------------------------- breaker and halt
def test_the_breaker_cancels_resting_buys_places_none_and_keeps_sells():
    live = live_block(max_open_lots=2, max_open_dollars=1000, drawdown_breaker_frac=0.3, lot_dollars=500)
    held = [lot(shares=10, cost=50.0)]
    b = FakeBroker()
    run(broker=b, live=live, lots=held, marks={"TQQQ": 50.0})  # sell + one buy rest
    assert len(b.working) == 2
    p, _ = run(broker=b, live=live, lots=held, marks={"TQQQ": 19.0})  # down 310 of the 300 line
    assert p.breaker["tripped"] and p.places == []
    assert [c.side for c in p.cancels] == ["buy"] and [k.side for k in p.keeps] == ["sell"]
    assert [w["spec"]["legs"][0]["action"] for w in b.working] == ["sell to close"]


def test_the_breaker_holds_just_inside_its_line():
    live = live_block(max_open_dollars=1000, drawdown_breaker_frac=0.5, lot_dollars=500)
    p, _ = run(live=live, lots=[lot(shares=10, cost=100.0)], marks={"TQQQ": 50.1})
    assert not p.breaker["tripped"]


def test_a_missing_mark_trips_the_breaker():
    p, _ = run(lots=[lot()], marks={})
    assert p.breaker["tripped"] and p.breaker["reason"] == "no_mark"
    assert [a for a in p.places if a.side == "buy"] == []


def test_halt_blocks_buys_cancels_resting_buys_and_still_places_sells():
    b = FakeBroker()
    run(broker=b, live=live_block(max_open_lots=1))
    assert len(b.working) == 1
    p, _ = run(broker=b, halted=True, lots=[lot(shares=10, cost=93.0)], marks={"TQQQ": 93.0})
    assert [a.side for a in p.places] == ["sell"]
    assert [a.side for a in p.cancels] == ["buy"]
    assert [w["spec"]["legs"][0]["action"] for w in b.working] == ["sell to close"]


def test_halt_does_not_stop_a_sell_reprice():
    old = orders.sell_id(lot()["id"], 10, rules.target("fixed", cost=93.0, shares=10, age=0))
    b = FakeBroker()
    b.working.append({"order_id": "X1", "external_identifier": old})
    p, _ = run(broker=b, halted=True, lots=[lot(cost=90.0)])
    assert [c.reason for c in p.cancels] == ["terms_changed"]


# --------------------------------------------------------------------------- armed, idempotent, disarm
def test_unarmed_plans_nothing_and_the_executor_sends_nothing():
    b = FakeBroker()
    p, results = run(broker=b, armed=False, lots=[lot()])
    assert p.actions == [] and results == [] and b.calls == []


def test_the_executor_refuses_an_unarmed_plan_even_with_actions():
    b = FakeBroker()
    armed_plan, _ = run(broker=FakeBroker())
    armed_plan.armed = False
    assert orders.Executor(b, log=lambda m: None).apply(armed_plan) == [] and b.calls == []


def test_a_second_run_places_and_cancels_nothing():
    b = FakeBroker()
    live = live_block(max_open_lots=2)
    state = {"lots": [lot()], "marks": {"TQQQ": 93.0}}
    first, _ = run(broker=b, live=live, **state)
    assert first.places
    n = len(b.calls)
    second, _ = run(broker=b, live=live, **state)
    assert second.places == [] and second.cancels == [] and len(b.calls) == n


def test_the_same_state_plans_the_same_ids():
    a, _ = run(broker=FakeBroker(), lots=[lot()])
    c, _ = run(broker=FakeBroker(), lots=[lot()])
    assert [x.external_identifier for x in a.places] == [x.external_identifier for x in c.places]


def test_resting_buys_are_cancelled_at_disarm_at_and_not_before():
    b = FakeBroker()
    run(broker=b, live=live_block(max_open_lots=1), when=at("19:00"))
    p, _ = run(broker=b, live=live_block(max_open_lots=1), when=at("19:59"))
    assert p.cancels == [] and len(b.working) == 1
    p, _ = run(broker=b, live=live_block(max_open_lots=1), when=at("20:00"))
    assert len(p.cancels) == 1 and p.places == [] and b.working == []


def test_disarm_cancels_buys_but_not_sells():
    b = FakeBroker()
    run(broker=b, lots=[lot()], live=live_block(max_open_lots=1))
    run(broker=b, lots=[lot()], live=live_block(max_open_lots=1), when=at("20:00"))
    assert [w["spec"]["legs"][0]["action"] for w in b.working] == ["sell to close"]


def test_a_buy_the_broker_ended_today_is_not_re_placed_the_same_session():
    live = live_block(max_open_lots=1)
    first, _ = run(live=live)
    eid = first.places[0].external_identifier
    p, _ = run(live=live, ended_ids={eid})
    assert p.places == []


# --------------------------------------------------------------------------- sells
def test_a_sell_is_placed_on_the_fill_at_the_target():
    b = FakeBroker()
    p, _ = run(
        broker=b, live=live_block(max_open_lots=1), lots=[lot(shares=10, cost=93.0)], marks={"TQQQ": 93.0}
    )
    sell = next(a for a in p.places if a.side == "sell")
    assert sell.spec["price"] == rules.ceil_cent(93.0 * 1.04) == 96.72
    assert sell.spec["time_in_force"] == "GTC Ext" and sell.spec["price_effect"] == "credit"
    assert sell.spec["legs"][0]["quantity"] == 10 and sell.spec["legs"][0]["action"] == "sell to close"


def test_a_sell_is_placed_even_before_the_session_window():
    p, _ = run(when=at("07:00"), lots=[lot()])
    assert [a.side for a in p.places] == ["sell"]


def test_decay_re_prices_by_cancel_then_place():
    rule = {**RULE, "target": "decay"}
    held = lot(shares=10, cost=93.0, buy_date="2026-10-20")  # day 34
    stale = orders.sell_id(held["id"], 10, rules.target("decay", cost=93.0, shares=10, age=30))
    b = FakeBroker()
    b.working.append({"order_id": "OLD", "external_identifier": stale})
    p, _ = run(broker=b, rule=rule, live=live_block(max_open_lots=1), lots=[held], marks={"TQQQ": 93.0})
    assert [c.order_id for c in p.cancels] == ["OLD"] and not [a for a in p.places if a.side == "sell"]
    p, _ = run(broker=b, rule=rule, live=live_block(max_open_lots=1), lots=[held], marks={"TQQQ": 93.0})
    new = next(a for a in p.places if a.side == "sell")
    assert new.spec["price"] == rules.breakeven(93.0, 10) and new.external_identifier != stale


def test_a_fixed_arm_never_re_prices_with_age():
    held = lot(buy_date="2026-09-01")
    b = FakeBroker()
    run(broker=b, lots=[held], live=live_block(max_open_lots=1))
    p, _ = run(broker=b, lots=[held], live=live_block(max_open_lots=1))
    assert p.cancels == [] and p.places == []


def test_a_sell_that_ended_unfilled_is_re_placed_with_a_suffix_then_gives_up():
    held = lot()
    base = orders.sell_id(held["id"], 10, 96.72)
    p, _ = run(lots=[held], ended_ids={base})
    assert p.places[0].external_identifier == base + ".r1"
    p, _ = run(lots=[held], ended_ids={base, base + ".r1", base + ".r2"})
    assert [a for a in p.places if a.side == "sell"] == [] and p.alerts[0]["kind"] == "sell_ended_repeatedly"


def test_hospital_lots_alert_and_nothing_else():
    held = lot(buy_date="2026-10-01")  # 53 days
    b = FakeBroker()
    run(broker=b, lots=[held], live=live_block(max_open_lots=1))
    p, _ = run(broker=b, lots=[held], live=live_block(max_open_lots=1))
    assert [a["kind"] for a in p.alerts] == ["hospital"] and p.places == [] and p.cancels == []


def test_an_orphan_sell_is_an_alert_not_a_cancel():
    b = FakeBroker()
    b.working.append({"order_id": "Z", "external_identifier": "ldr.s.gone-lot.10x9672"})
    p, _ = run(broker=b, lots=[])
    assert p.cancels == [] and p.alerts[0]["kind"] == "orphan_sell"


def test_hand_orders_are_never_touched():
    b = FakeBroker()
    b.working += [
        {"order_id": "H1", "external_identifier": None},
        {"order_id": "H2", "external_identifier": "my-own-order"},
        {"order_id": "H3", "external_identifier": "hand.b.TQQQ.20261123.1.10x9300"},
        {"order_id": "H4", "external_identifier": "hand.s.some-lot.10x9672"},
    ]
    p, _ = run(broker=b, halted=True)
    assert p.actions == [] and ("cancel", "H1") not in b.calls


# --------------------------------------------------------------------------- splits
def test_a_reverse_split_cancels_the_old_orders_then_places_at_restated_terms():
    held = lot(shares=24, cost=10.0)
    live = live_block(max_open_lots=2)
    b = FakeBroker()
    run(broker=b, live=live, lots=[held], ref_close={"TQQQ": 12.5})  # before the split
    old_ids = {w["external_identifier"] for w in b.working}
    assert len(old_ids) == 2
    state = {"lots": [held], "splits": {"TQQQ": 0.125}, "ref_close": {"TQQQ": 12.5}}
    p, _ = run(broker=b, live=live, **state)
    assert {c.external_identifier for c in p.cancels} == old_ids and p.places == []
    p, _ = run(broker=b, live=live, **state)
    sell = next(a for a in p.places if a.side == "sell")
    assert sell.spec["legs"][0]["quantity"] == 3 and sell.spec["price"] == rules.ceil_cent(80.0 * 1.04)
    buy = next(a for a in p.places if a.side == "buy")
    assert buy.external_identifier not in old_ids


def test_a_forward_split_restates_shares_and_cost():
    held = lot(shares=10, cost=90.0)
    p, _ = run(lots=[held], splits={"TQQQ": 3.0})
    sell = next(a for a in p.places if a.side == "sell")
    assert sell.spec["legs"][0]["quantity"] == 30 and sell.spec["price"] == rules.ceil_cent(30.0 * 1.04)


def test_a_reverse_split_that_leaves_no_whole_share_alerts():
    p, _ = run(lots=[lot(shares=2, cost=10.0)], splits={"TQQQ": 0.125})
    assert (
        p.alerts[0]["kind"] == "no_whole_shares_after_split"
        and [a for a in p.places if a.side == "sell"] == []
    )


# --------------------------------------------------------------------------- multi-symbol and executor
def test_each_fund_has_its_own_levels_and_ids():
    live = live_block(symbols=["TQQQ", "SPXL"], max_open_lots=1, correlated_groups={})
    p, _ = run(live=live, ref_close={"TQQQ": 100.0, "SPXL": 80.0})
    by = {a.symbol: a for a in p.places}
    assert by["TQQQ"].spec["price"] == 93.0 and by["SPXL"].spec["price"] == 74.4
    assert ".TQQQ." in by["TQQQ"].external_identifier and ".SPXL." in by["SPXL"].external_identifier


def test_dry_run_logs_the_orders_and_sends_nothing():
    b = FakeBroker()
    seen: list[str] = []
    p = orders.plan(
        live_block(), RULE, when=at("09:00"), armed=True, ref_close={"TQQQ": 100.0}, lots=[lot()], working=[]
    )
    results = orders.Executor(b, dry_run=True, log=seen.append).apply(p)
    assert b.calls == [] and b.working == []
    assert all(r["dry_run"] for r in results) and len(results) == len(p.places)
    assert any("dry-run: would place" in m for m in seen)


def test_dry_run_cancels_nothing_either():
    b = FakeBroker()
    stale = orders.sell_id(lot()["id"], 10, 90.0)
    b.working.append({"order_id": "OLD", "external_identifier": stale})
    p, results = run(broker=b, dry_run=True, lots=[lot()])
    assert [c.order_id for c in p.cancels] == ["OLD"] and results[0]["dry_run"]
    assert b.calls == [] and len(b.working) == 1


def test_live_placement_passes_live_true_and_reads_the_order_id():
    b = FakeBroker()
    _, results = run(broker=b, live=live_block(max_open_lots=1))
    assert b.calls[0][0] == "place" and b.calls[0][2] is True
    assert results[0]["ok"] and results[0]["order_id"] == "ORD1"


def test_a_failed_place_is_reported_not_raised():
    b = FakeBroker(fail={"ok": False, "error": "rejected"})
    _, results = run(broker=b, live=live_block(max_open_lots=1))
    assert results[0]["ok"] is False and results[0]["error"] == "rejected"


def test_an_uncertain_place_holds_the_rest():
    b = FakeBroker(fail={"ok": False, "uncertain": True, "error": "timeout"})
    _, results = run(broker=b, live=live_block(max_open_lots=3))
    assert results[0]["uncertain"] and all(r.get("skipped") == "broker_hold" for r in results[1:])
    assert len(b.calls) == 1


# --------------------------------------------------------------------------- the fence
READER = "cherrypick.core.execution.order_id_of"  # a pure reader of a place result: the one allowed import
FORBIDDEN = ("broker", "execution", "tastytrade", "session", "auth")


def violations(source: str) -> list[str]:
    names = imports_in(source)
    others = [n for n in names if n.startswith("cherrypick.core.execution.") and n != READER]
    if READER in names and not others:
        names = [n for n in names if n != "cherrypick.core.execution"]
    return [n for n in names if n != READER and any(w in n.lower() for w in FORBIDDEN)]


def imports_of(path: pathlib.Path) -> list[str]:
    return imports_in(path.read_text(encoding="utf-8"))


def imports_in(source: str) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names += [base] + [f"{base}.{a.name}" for a in node.names]
    return names


def test_the_order_fence_catches_a_trading_import():
    """The scan has to be able to fail: each of these is flagged."""
    for line in (
        "from cherrypick.core.execution import Broker",
        "from cherrypick.core.broker import place_order",
        "from cherrypick.core import execution",
        "import tastytrade",
        "from cherrypick.core.auth import get_session",
    ):
        assert violations(line + "\n"), line


def test_the_order_layer_imports_nothing_that_can_submit():
    bad = violations((SRC / "orders.py").read_text(encoding="utf-8"))
    assert bad == []


def test_the_order_layer_builds_no_broker_and_calls_no_submit_helper():
    called = {
        (n.id if isinstance(n, ast.Name) else n.attr)
        for n in ast.walk(ast.parse((SRC / "orders.py").read_text(encoding="utf-8")))
        if isinstance(n, ast.Name | ast.Attribute)
    }
    assert not {
        c for c in called if c in {"Broker", "build_order", "submit_order", "place_order", "get_session"}
    }


def test_nothing_in_the_live_path_imports_the_order_layer():
    for name in ("live_loop.py", "live_gates.py", "credentials.py", "paper_loop.py", "cli.py"):
        assert not [
            n
            for n in imports_of(SRC / name)
            if n.endswith("ladder.orders") or n.endswith("ladder import orders")
        ]
