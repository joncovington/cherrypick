# ruff: noqa: F811
"""Splits and the positions cross-check through the live tick, against the fake broker. Invented numbers
throughout: a lot of 10 shares bought at 93.00 on 2026-11-23, then a second session on 2026-11-24."""

import json
from datetime import date

import pytest
from test_live_tick import (  # noqa: F401 -- the fixtures and helpers the tick tests share
    ACCOUNT,
    DAY,
    arm,
    at,
    decisions,
    live_config,
    lots,
    seams,
    tick,
    two_fund_config,
    world,
)

from cherrypick.ladder import live_db, live_loop, live_tick, orders, rules

DAY2 = "2026-11-24"  # the next trading day
SPLIT = {"fund": "TQQQ", "ex_date": DAY2, "to": 2, "for": 1}


def cfg_with(*splits, **live):
    return live_config(splits=list(splits), correlated_groups={}, **live)


def held_lot(w, cfg):
    """Session one: the buy fills at 93.00 (10 shares) and its sell rests. Returns the lot's id."""
    tick(w, cfg, "09:00")
    w.broker.fill(w.broker.order_id_of("ldr.b"), 93.0)
    tick(w, cfg, "09:01")
    [lot] = lots(w)
    assert lot["quantity"] == 10 and len(w.broker.placed("sell")) == 1
    return lot["position_id"]


def session_two(w, *, close, bid, shares):
    """The next session: its arm record, its cache rows and the broker's share count."""
    arm(DAY2)
    w.cache.summary("TQQQ", DAY2, prev_day_close=close)
    w.cache.quote("TQQQ", bid, round(bid + 0.1, 2))
    w.broker.shares["TQQQ"] = shares
    w.broker.calls.clear()


def tick2(w, cfg, hhmm="09:00"):
    """A tick of session two, with room for a second lot so a healthy fund would take a new buy."""
    wide = {**cfg, "live": {**cfg["live"], "max_open_lots": 2, "max_open_lots_total": 3}}
    return tick(w, wide, hhmm, day=DAY2)


def split_rows(w):
    return [dict(r) for r in w.conn.execute("SELECT * FROM ladder_lot_splits")]


# --------------------------------------------------------------------------- the split source
def test_a_declared_split_restates_the_lot_once_and_never_twice(world):
    cfg = cfg_with(SPLIT)
    pid = held_lot(world, cfg)
    session_two(world, close=100.0, bid=48.0, shares=20)
    out = tick2(world, cfg)
    [lot] = lots(world)
    assert (lot["quantity"], lot["buy_price"]) == (20, 46.5)
    assert lot["entry_value"] == -930.0  # the cash that moved does not change
    [row] = split_rows(world)
    assert (row["position_id"], row["ex_date"], row["shares_before"], row["shares_after"]) == (
        pid,
        DAY2,
        10,
        20,
    )
    assert out["splits"]["applied"] == [pid]
    for hhmm in ("09:01", "09:02", "09:03"):
        tick2(world, cfg, hhmm)
    [lot] = lots(world)
    assert (lot["quantity"], lot["buy_price"]) == (20, 46.5) and len(split_rows(world)) == 1
    assert len(decisions(world, "split")) == 1


def test_the_restatement_is_journaled(world):
    cfg = cfg_with(SPLIT)
    pid = held_lot(world, cfg)
    session_two(world, close=100.0, bid=48.0, shares=20)
    tick2(world, cfg)
    [row] = decisions(world, "split")
    assert pid in row["reason"] and "2:1" in row["reason"] and "10 -> 20" in row["reason"]
    assert json.loads(row["detail"])["shares_after"] == 20


def test_a_resting_sell_is_cancelled_and_then_placed_again_at_the_new_terms(world):
    cfg = cfg_with(SPLIT)
    pid = held_lot(world, cfg)
    old_sell = world.broker.placed("sell")[0]
    session_two(world, close=100.0, bid=48.0, shares=20)
    tick2(world, cfg, "09:00")
    assert world.broker.cancelled() == [old_sell] and world.broker.placed("sell") == []
    tick2(world, cfg, "09:01")
    rule = orders.arm_rule(cfg)
    age = rules.age_days(date(2026, 11, 23), date(2026, 11, 24))
    target = rules.target(rule["target"], cost=46.5, shares=20, age=age, gain=float(rule["gain"]))
    assert world.broker.placed("sell") == [orders.sell_id(pid, 20, target)]
    assert target < 50.0  # priced on the new terms, nowhere near the old 96.72


def test_the_reference_close_across_a_split_is_restated_so_the_buy_is_not_priced_off_the_old_terms(world):
    cfg = cfg_with(SPLIT)
    held_lot(world, cfg)
    session_two(world, close=100.0, bid=48.0, shares=20)
    out = tick2(world, cfg)
    buys = [a for a in out["plan"]["actions"] if a["kind"] == "place" and a["side"] == "buy"]
    # 100.00 on the old terms is 50.00 on the new; the 7% level is 46.50, not 93.00
    assert [a["external_identifier"] for a in buys] == [orders.buy_id("TQQQ", DAY2, 1, 21, 46.5)]


def test_a_reference_close_already_on_the_new_terms_is_used_as_it_is(world):
    cfg = cfg_with(SPLIT)
    held_lot(world, cfg)
    session_two(world, close=50.0, bid=48.0, shares=20)
    out = tick2(world, cfg)
    buys = [a for a in out["plan"]["actions"] if a["kind"] == "place" and a["side"] == "buy"]
    assert [a["external_identifier"] for a in buys] == [orders.buy_id("TQQQ", DAY2, 1, 21, 46.5)]


def test_a_split_with_no_quote_to_judge_the_close_by_places_no_buy(world):
    cfg = cfg_with(SPLIT)
    session_two(world, close=100.0, bid=48.0, shares=0)
    world.cache.conn.execute("DELETE FROM stream_quotes")
    world.cache.conn.commit()
    out = tick2(world, cfg)
    assert world.broker.placed("buy") == []
    assert any("no_reference_close" in b for b in out["plan"]["buys_blocked"])


def test_a_lot_bought_on_the_ex_date_is_already_on_the_new_terms(world):
    cfg = cfg_with(SPLIT)
    session_two(world, close=100.0, bid=48.0, shares=0)
    tick2(world, cfg, "09:00")
    assert world.broker.placed("buy")
    world.broker.fill(world.broker.order_id_of("ldr.b"), 46.4)
    tick2(world, cfg, "09:01")
    [lot] = lots(world)
    assert lot["quantity"] == 21 and split_rows(world) == []


def test_a_split_not_yet_ex_changes_nothing(world):
    cfg = cfg_with({**SPLIT, "ex_date": "2026-12-01"})
    held_lot(world, cfg)
    session_two(world, close=96.0, bid=95.0, shares=10)
    tick2(world, cfg)
    assert lots(world)[0]["quantity"] == 10 and split_rows(world) == []


def test_a_reverse_split_that_leaves_less_than_a_share_alerts_and_sells_nothing(world):
    cfg = cfg_with({**SPLIT, "to": 1, "for": 20})
    pid = held_lot(world, cfg)
    session_two(world, close=5.0, bid=1900.0, shares=0)
    out = tick2(world, cfg)
    [lot] = lots(world)
    assert lot["quantity"] == 0 and lot["status"] == "open"
    alerts = [d["reason"] for d in decisions(world, "alert")]
    assert f"split_leaves_no_whole_share {pid}" in alerts
    assert any(a["kind"] == "no_whole_shares_after_split" for a in out["plan"]["alerts"])
    assert world.broker.placed("sell") == []


def test_a_reverse_split_that_leaves_whole_shares_rounds_down(world):
    cfg = cfg_with({**SPLIT, "to": 1, "for": 4})
    held_lot(world, cfg)
    session_two(world, close=372.0, bid=370.0, shares=2)
    tick2(world, cfg)
    [lot] = lots(world)
    assert (lot["quantity"], lot["buy_price"]) == (2, 372.0)
    assert not [d for d in decisions(world, "alert") if "split_leaves" in d["reason"]]


def test_a_second_split_after_the_first_restates_on_top_of_it(world):
    cfg = cfg_with(SPLIT, {**SPLIT, "ex_date": "2026-11-25", "to": 3, "for": 1})
    held_lot(world, cfg)
    session_two(world, close=100.0, bid=48.0, shares=20)
    tick2(world, cfg)
    assert lots(world)[0]["quantity"] == 20
    arm("2026-11-25")
    world.cache.summary("TQQQ", "2026-11-25", prev_day_close=48.0)
    world.cache.quote("TQQQ", 16.0, 16.1)
    world.broker.shares["TQQQ"] = 60
    tick(world, cfg, "09:00", day="2026-11-25")
    [lot] = lots(world)
    assert (lot["quantity"], lot["buy_price"]) == (60, 15.5) and len(split_rows(world)) == 2


def test_the_ledger_refuses_a_second_application_of_the_same_split_to_a_lot(world):
    pid = held_lot(world, cfg_with())
    record = {
        "symbol": "TQQQ", "ex_date": DAY2, "to_shares": 2, "for_shares": 1, "shares_before": 10,
        "shares_after": 20, "price_before": 93.0, "price_after": 46.5, "applied_at": "t",
    }  # fmt: skip
    assert live_db.apply_lot_split(world.conn, pid, {"quantity": 20}, record) is True
    assert live_db.apply_lot_split(world.conn, pid, {"quantity": 40}, record) is False
    assert lots(world)[0]["quantity"] == 20 and len(split_rows(world)) == 1


def test_a_failed_application_leaves_the_lot_and_the_record_untouched(world):
    pid = held_lot(world, cfg_with())
    before = lots(world)[0]
    with pytest.raises(Exception):  # noqa: B017 -- the record misses a required column
        live_db.apply_lot_split(
            world.conn, pid, {"quantity": 20, "buy_price": 46.5}, {"ex_date": DAY2, "symbol": "TQQQ"}
        )
    assert lots(world)[0] == before and split_rows(world) == []


def test_a_split_entry_it_cannot_read_stops_every_funds_new_buys(world):
    cfg = cfg_with({"fund": "TQQQ", "ex_date": "soon", "to": 2, "for": 1})
    out = tick(world, cfg, "09:00")
    assert world.broker.placed() == []
    assert any("splits_invalid" in b for b in out["plan"]["buys_blocked"])
    assert any("splits_invalid" in d["reason"] for d in decisions(world, "alert"))


# --------------------------------------------------------------------------- the positions cross-check
def test_matching_positions_pass_and_buys_go_on(world):
    cfg = cfg_with()
    held_lot(world, cfg)
    session_two(world, close=96.0, bid=95.0, shares=10)
    out = tick2(world, cfg)
    assert out["positions"]["readable"] and out["positions"]["blocked"] == {}
    assert world.broker.placed("buy")


def test_a_shortfall_stops_that_funds_new_buys_and_keeps_its_sell(world):
    cfg = cfg_with()
    held_lot(world, cfg)
    sell = world.broker.order_id_of("ldr.s")
    session_two(world, close=96.0, bid=95.0, shares=6)  # four of ten shares are gone at the broker
    out = tick2(world, cfg)
    assert world.broker.placed("buy") == []
    assert "TQQQ" in out["positions"]["blocked"] and "shortfall" in out["positions"]["blocked"]["TQQQ"]
    assert any(b.startswith("TQQQ: positions_shortfall") for b in out["plan"]["buys_blocked"])
    assert world.broker.book[sell]["status"] == "Live" and world.broker.cancelled() == []  # the sell rests on
    assert [d["reason"] for d in decisions(world, "alert")] == [out["positions"]["blocked"]["TQQQ"]]


def test_a_shortfall_in_one_fund_leaves_another_funds_buys_alone(world):
    cfg = two_fund_config()
    world.cache.summary("SOXL", DAY, prev_day_close=50.0)
    world.cache.quote("SOXL", 47.0, 47.1)
    tick(world, cfg, "09:00")
    world.broker.fill(world.broker.order_id_of("ldr.b.TQQQ"), 93.0)
    tick(world, cfg, "09:01")
    world.broker.shares["TQQQ"] = 6
    world.broker.calls.clear()
    out = tick(world, cfg, "09:02")
    assert "TQQQ" in out["positions"]["blocked"] and "SOXL" not in out["positions"]["blocked"]
    soxl = world.broker.order_id_of("ldr.b.SOXL")
    assert world.broker.book[soxl]["status"] == "Live"  # SOXL's resting buy stays
    assert world.broker.book[world.broker.order_id_of("ldr.s")]["status"] == "Live"  # TQQQ's sell stays
    assert all(".TQQQ." in c for c in world.broker.cancelled())  # only TQQQ's own resting buy goes


def test_a_blocked_funds_resting_buys_are_cancelled_and_a_healthy_funds_are_kept(world):
    cfg = cfg_with(max_open_lots=4, max_open_lots_total=5, max_open_dollars=9000)
    tick(world, cfg, "09:00")  # buys rest
    more = cfg_with(max_open_lots=5, max_open_lots_total=6, max_open_dollars=9000)  # room for the lot too
    resting = world.broker.placed("buy")
    assert resting
    world.conn.execute(
        "INSERT INTO ladder_positions (position_id, arm, symbol, entry_rule, target_rule, quantity, "
        "buy_level, buy_price, buy_session, buy_time, status, target_price, target_stage, entry_value) "
        "VALUES ('session:TQQQ:2026-11-22:1', 'session', 'TQQQ', 'once', 'gain', 10, 93, 93, '2026-11-22', "
        "'2026-11-22T10:00:00-05:00', 'open', 96.72, 'gain', -930)"
    )
    world.conn.commit()
    world.broker.shares["TQQQ"] = 10
    world.broker.calls.clear()
    tick(world, more, "09:01")
    assert world.broker.cancelled() == []  # the ledger and the broker agree: the buys stay
    world.broker.shares["TQQQ"] = 0  # ten shares the ledger holds are not at the broker
    tick(world, more, "09:02")
    assert sorted(world.broker.cancelled()) == sorted(resting)


def test_a_surplus_is_reported_and_never_adopted(world):
    cfg = cfg_with()
    held_lot(world, cfg)
    session_two(world, close=96.0, bid=95.0, shares=15)  # five shares are the user's own
    out = tick2(world, cfg)
    assert out["positions"]["blocked"] == {}
    assert out["positions"]["funds"]["TQQQ"]["status"] == "surplus"
    [row] = decisions(world, "positions")
    assert "surplus" in row["reason"] and "left alone" in row["reason"]
    assert lots(world)[0]["quantity"] == 10 and len(lots(world)) == 1  # not adopted
    assert world.broker.placed("buy")  # and no reason to stop


def test_an_unreadable_read_stops_every_funds_new_buys_and_keeps_the_sells(world):
    cfg = two_fund_config()
    world.cache.summary("SOXL", DAY, prev_day_close=50.0)
    world.cache.quote("SOXL", 47.0, 47.1)
    tick(world, cfg, "09:00")
    world.broker.fill(world.broker.order_id_of("ldr.b.TQQQ"), 93.0)
    tick(world, cfg, "09:01")
    sell = world.broker.order_id_of("ldr.s")
    world.broker.positions_error = True
    world.broker.calls.clear()
    out = tick(world, cfg, "09:02")
    assert out["positions"]["readable"] is False
    assert set(out["positions"]["blocked"]) == {"TQQQ", "SOXL"}
    assert world.broker.book[sell]["status"] == "Live"  # no sell was touched
    assert "ldr.s" not in " ".join(world.broker.cancelled())
    assert any(d["reason"].startswith("positions_unreadable") for d in decisions(world, "alert"))


def test_an_unreadable_read_places_no_new_buy(world):
    cfg = cfg_with()
    world.broker.positions_error = True
    out = tick(world, cfg, "09:00")
    assert world.broker.placed() == [] and out["positions"]["readable"] is False


def test_a_whole_number_ratio_is_a_suspected_split_that_blocks_and_is_not_acted_on(world):
    cfg = cfg_with()  # no split declared
    held_lot(world, cfg)
    session_two(world, close=48.0, bid=47.0, shares=20)  # the broker split the shares; nobody told the ledger
    out = tick2(world, cfg)
    assert out["positions"]["funds"]["TQQQ"]["suspected_split"] == "2:1"
    assert "suspected_split 2:1" in out["positions"]["blocked"]["TQQQ"]
    assert world.broker.placed("buy") == []
    [lot] = lots(world)
    assert (lot["quantity"], lot["buy_price"]) == (10, 93.0) and split_rows(world) == []  # not restated
    assert any("suspected_split" in d["reason"] for d in decisions(world, "alert"))


def test_declaring_the_suspected_split_restates_the_lot_and_clears_the_block(world):
    held_lot(world, cfg_with())
    session_two(world, close=100.0, bid=48.0, shares=20)
    assert "TQQQ" in tick2(world, cfg_with())["positions"]["blocked"]
    out = tick2(world, cfg_with(SPLIT), "09:01")
    assert out["positions"]["blocked"] == {} and lots(world)[0]["quantity"] == 20


def test_a_declared_split_the_broker_did_not_make_is_caught_by_the_next_check(world):
    cfg = cfg_with(SPLIT)  # a typo: the fund did not split
    held_lot(world, cfg)
    session_two(world, close=96.0, bid=95.0, shares=10)
    out = tick2(world, cfg)
    assert lots(world)[0]["quantity"] == 20  # restated as declared ...
    assert "broker holds 10, ledger 20" in out["positions"]["blocked"]["TQQQ"]  # ... and the broker disagrees
    assert world.broker.placed("buy") == []


def test_other_modules_option_positions_are_ignored(world):
    cfg = cfg_with()
    held_lot(world, cfg)
    option = {"instrument_type": "Equity Option"}
    world.broker.extra = [
        {**option, "symbol": "SPXW  261218P05000000", "underlying_symbol": "SPX", "quantity": -2.0},
        {**option, "symbol": "TQQQ  261218C00050000", "underlying_symbol": "TQQQ", "quantity": 7.0},
    ]
    session_two(world, close=96.0, bid=95.0, shares=10)
    out = tick2(world, cfg)
    assert out["positions"]["blocked"] == {} and out["positions"]["funds"]["TQQQ"]["status"] == "match"


def test_a_fund_the_ladder_holds_but_no_longer_lists_is_still_compared(world):
    held_lot(world, cfg_with())
    session_two(world, close=96.0, bid=95.0, shares=6)
    world.cache.summary("SOXL", DAY2, prev_day_close=50.0)
    world.cache.quote("SOXL", 47.0, 47.1)
    other = two_fund_config(symbols=["SOXL"])
    out = tick2(world, other)
    assert "TQQQ" in out["positions"]["funds"] and "TQQQ" in out["positions"]["blocked"]


def test_the_check_runs_in_dry_run_too_and_reads_but_never_writes(world):
    cfg = cfg_with(dry_run=True)
    world.broker.positions_error = True
    out = tick(world, cfg, "09:00")
    assert out["positions"]["readable"] is False and world.broker.calls == []


def test_an_unarmed_tick_reads_no_positions(world):
    from test_live_tick import disarm

    disarm()
    tick(world, cfg_with(), "09:00")
    assert world.broker.positions_calls == 0


# --------------------------------------------------------------------------- --status
def test_status_carries_the_reason_a_fund_is_blocked_and_the_standing_intents(world):
    cfg = cfg_with(dry_run=True)
    world.broker.shares["TQQQ"] = 0
    tick(world, cfg, "09:00")
    status = live_loop.status(cfg, designated=ACCOUNT, when=at("09:00"))
    checks = status["checks"]
    assert checks["positions"]["readable"] is True and checks["positions"]["blocked"] == {}
    assert [i["what"] for i in checks["intents"]] == [
        f"place buy TQQQ {orders.buy_id('TQQQ', DAY, 1, 10, 93.0)}"
    ]
    world.broker.positions_error = True
    tick(world, cfg, "09:01")
    status = live_loop.status(cfg, designated=ACCOUNT, when=at("09:01"))
    assert status["checks"]["positions"]["readable"] is False
    assert status["checks"]["positions"]["blocked"] == {"TQQQ": "positions_unreadable"}


def test_status_with_no_tick_yet_has_empty_checks(world):
    assert live_loop.status(cfg_with(), designated=ACCOUNT, when=at("09:00"))["checks"] == {}


# --------------------------------------------------------------------------- the dry-run journal
def test_a_standing_dry_run_intent_is_one_row_however_many_ticks_and_days_see_it(world):
    cfg = cfg_with(dry_run=True)
    for hhmm in ("09:00", "09:01", "09:02", "09:03"):
        tick(world, cfg, hhmm)
    [row] = decisions(world, "dry_run")
    assert row["occurrences"] == 1


def test_an_intent_whose_terms_changed_is_journaled_again():
    prior = {"detail": json.dumps({"shares": 10, "limit": 93.0, "tif": "Ext"})}
    same = {"shares": 10, "limit": 93.0, "tif": "Ext", "reason": "level 1"}
    assert live_tick._same_terms(prior, same) is True
    assert live_tick._same_terms(prior, {**same, "limit": 92.5}) is False
    assert live_tick._same_terms(prior, {**same, "shares": 11}) is False
    assert live_tick._same_terms(prior, {**same, "tif": "GTC Ext"}) is False
    assert live_tick._same_terms(None, same) is False
    assert live_tick._same_terms({"detail": "not json"}, same) is False


def test_a_new_dry_run_intent_is_journaled_when_the_level_moves(world):
    cfg = cfg_with(dry_run=True)
    tick(world, cfg, "09:00")
    world.cache.summary("TQQQ", DAY, prev_day_close=101.0)  # a new close: new level, new terms
    tick(world, cfg, "09:01")
    assert len(decisions(world, "dry_run")) == 2
