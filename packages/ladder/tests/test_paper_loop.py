import json

from conftest import at

from cherrypick.ladder import analytics, db, paper_loop, provider, stream_request

DAY = "2026-11-13"  # a Friday session
NEXT = "2026-11-16"  # the Monday after


def _tick(config, ledger, cache, day=DAY, hhmm="10:00"):
    return paper_loop.run_once(config, ledger, cache_path=cache.path, when=at(day, hhmm))


def _only(config, arm):
    return {**config, "arms": {arm: config["arms"][arm]}}


def test_reference_close_prefers_the_sessions_prev_close_then_the_prior_day_close(cache):
    assert provider.reference_close(cache.path, "TQQQ", __import__("datetime").date(2026, 11, 13)) is None
    cache.summary("TQQQ", "2026-11-12", day_close=100.0)
    assert provider.reference_close(cache.path, "TQQQ", __import__("datetime").date(2026, 11, 13)) == 100.0
    cache.summary("TQQQ", DAY, prev_day_close=101.0)
    assert provider.reference_close(cache.path, "TQQQ", __import__("datetime").date(2026, 11, 13)) == 101.0


def test_nothing_buys_above_the_level(config, ledger, cache):
    cache.summary("TQQQ", DAY, prev_day_close=100.0).quote("TQQQ", 93.00, 93.02)
    _tick(config, ledger, cache)
    assert db.open_lots(ledger) == []


def test_once_buys_one_lot_a_session_at_the_level(config, ledger, cache):
    cfg = _only(config, "once")
    cache.summary("TQQQ", DAY, prev_day_close=100.0).quote("TQQQ", 80.00, 80.02)
    _tick(cfg, ledger, cache)
    _tick(cfg, ledger, cache, hhmm="10:01")
    lots = db.open_lots(ledger)
    assert len(lots) == 1
    lot = lots[0]
    assert lot["buy_price"] == 93.0 and lot["quantity"] == 10
    assert lot["position_id"] == f"once:TQQQ:{DAY}:1"
    assert lot["entry_value"] == -930.0 and lot["target_price"] == 96.72


def test_chain_keeps_buying_down_to_the_cap_on_a_gap(config, ledger, cache):
    cfg = _only(config, "chain")
    cache.summary("TQQQ", DAY, prev_day_close=100.0).quote("TQQQ", 70.00, 70.02)
    _tick(cfg, ledger, cache)
    lots = db.open_lots(ledger)
    assert [lot["buy_price"] for lot in lots] == [93.0, 86.49, 80.43]
    assert [lot["chained_from"] for lot in lots] == [None, 93.0, 86.49]
    reasons = {r["reason"] for r in ledger.execute("SELECT reason FROM ladder_decisions")}
    assert "name_cap_full" in reasons


def test_a_lot_sells_at_its_target_and_frees_its_slot(config, ledger, cache):
    cfg = _only(config, "chain")
    cache.summary("TQQQ", DAY, prev_day_close=100.0).quote("TQQQ", 92.90, 92.95)
    _tick(cfg, ledger, cache)
    cache.quote("TQQQ", 86.40, 86.45)
    _tick(cfg, ledger, cache, hhmm="10:30")
    assert [lot["buy_price"] for lot in db.open_lots(ledger)] == [93.0, 86.49]
    # 86.49 x 1.04 = 89.9496 -> 89.95: the second lot sells, the first holds
    cache.quote("TQQQ", 89.95, 89.97)
    _tick(cfg, ledger, cache, hhmm="11:00")
    open_ = db.open_lots(ledger)
    assert [lot["buy_price"] for lot in open_] == [93.0]
    closed = db.positions(ledger, status="closed")[0]
    assert (
        closed["sell_price"] == 89.95 and closed["exit_reason"] == "target_gain" and closed["hold_days"] == 0
    )
    assert closed["gross_pnl"] == round(closed["entry_value"] + closed["exit_value"], 2)
    assert closed["net_pnl"] == round(closed["gross_pnl"] - closed["fees"], 2)
    # the anchor stays the last lot bought (86.49) while the book still holds one
    assert (
        paper_loop.chain_anchor([dict(r) for r in ledger.execute("SELECT * FROM ladder_positions")]) == 86.49
    )


def test_an_empty_book_resets_the_chain_to_the_reference(config, ledger, cache):
    cfg = _only(config, "chain")
    cache.summary("TQQQ", DAY, prev_day_close=100.0).quote("TQQQ", 92.90, 92.95)
    _tick(cfg, ledger, cache)
    cache.quote("TQQQ", 96.72, 96.75)
    _tick(cfg, ledger, cache, hhmm="12:00")
    assert db.open_lots(ledger) == []
    lots = [dict(r) for r in ledger.execute("SELECT * FROM ladder_positions")]
    assert paper_loop.chain_anchor(lots) is None
    cache.quote("TQQQ", 92.90, 92.95)
    _tick(cfg, ledger, cache, hhmm="13:00")
    assert [lot["buy_price"] for lot in db.open_lots(ledger)] == [93.0]


def test_decay_sells_at_breakeven_after_day_30(config, ledger, cache):
    cfg = _only(config, "decay")
    cache.summary("TQQQ", DAY, prev_day_close=100.0).quote("TQQQ", 92.90, 92.95)
    _tick(cfg, ledger, cache)
    lot = db.open_lots(ledger)[0]
    later = "2026-12-15"  # day 32
    cache.summary("TQQQ", later, prev_day_close=93.5).quote("TQQQ", 93.05, 93.07)
    _tick(cfg, ledger, cache, day=later)
    closed = db.position(ledger, lot["position_id"])
    assert closed["status"] == "closed" and closed["exit_reason"] == "target_breakeven"
    assert closed["net_pnl"] >= 0 and closed["hold_days"] == 32


def test_stale_quote_or_missing_close_does_nothing_and_says_so(config, ledger, cache):
    cfg = _only(config, "once")
    cache.quote("TQQQ", 50.0, 50.02)  # no summary: no reference close
    _tick(cfg, ledger, cache)
    cache.summary("TQQQ", DAY, prev_day_close=100.0).quote("TQQQ", 50.0, 50.02, age=600)
    _tick(cfg, ledger, cache, hhmm="10:05")
    assert db.open_lots(ledger) == []
    reasons = {r["reason"] for r in ledger.execute("SELECT reason FROM ladder_decisions")}
    assert {"no_reference_close", "no_fresh_quote"} <= reasons


def test_outside_hours_and_holidays_skip(config, ledger, cache):
    cache.summary("TQQQ", DAY, prev_day_close=100.0).quote("TQQQ", 50.0, 50.02)
    assert _tick(config, ledger, cache, hhmm="07:59")["skipped"] == "outside_hours"
    assert _tick(config, ledger, cache, hhmm="20:00")["skipped"] == "outside_hours"
    assert _tick(config, ledger, cache, day="2026-11-14")["skipped"] == "not_a_trading_day"
    assert db.open_lots(ledger) == []
    assert _tick(config, ledger, cache, hhmm="19:59")["actions"] > 0


def test_a_lot_too_small_is_refused(config, ledger, cache):
    cfg = _only(config, "once")
    cfg["names"] = {"TQQQ": {"lot_dollars": 150}}
    cache.summary("TQQQ", DAY, prev_day_close=100.0).quote("TQQQ", 90.0, 90.02)
    _tick(cfg, ledger, cache)
    assert db.open_lots(ledger) == []
    detail = ledger.execute("SELECT detail FROM ladder_decisions WHERE reason = 'lot_too_small'").fetchone()
    assert json.loads(detail[0])["shares"] == 1


def test_the_arm_cap_spans_funds(config, ledger, cache):
    cfg = _only(config, "chain")
    cfg["defaults"] = {**cfg["defaults"], "max_open_lots_total": 4}
    cfg["names"] = {"TQQQ": {}, "SPXL": {}}
    cache.summary("TQQQ", DAY, prev_day_close=100.0).quote("TQQQ", 70.0, 70.02)
    cache.summary("SPXL", DAY, prev_day_close=200.0).quote("SPXL", 140.0, 140.04)
    _tick(cfg, ledger, cache)
    assert db.open_count(ledger, "chain") == 4
    reasons = {r["reason"] for r in ledger.execute("SELECT reason FROM ladder_decisions")}
    assert "arm_cap_full" in reasons


def test_arms_keep_separate_books(config, ledger, cache):
    cache.summary("TQQQ", DAY, prev_day_close=100.0).quote("TQQQ", 92.0, 92.02)
    _tick(config, ledger, cache)
    assert sorted(lot["arm"] for lot in db.open_lots(ledger)) == ["chain", "decay", "once"]


def test_summary_counts_the_groups_18_day_hospital(config, ledger, cache):
    cfg = _only(config, "once")
    cache.summary("TQQQ", DAY, prev_day_close=100.0).quote("TQQQ", 92.0, 92.02)
    _tick(cfg, ledger, cache)
    out = analytics.summary(ledger, today=__import__("datetime").date(2026, 12, 2))["arms"]["once"]
    assert out["open"] == 1 and out["in_hospital"] == 1 and out["sold_within_18d"] == 0


def test_stream_request_names_each_fund_as_underlying_and_leg(config, ledger, managed_home):
    from cherrypick.core import streamrequests

    stream_request.write(config, ledger, "unused")
    data = json.loads(streamrequests.request_path("ladder").read_text())
    assert data["symbols"] == ["TQQQ"]
    assert data["legs"] == ["TQQQ"]
    assert data["nearest_window"] == {"TQQQ": False}


def test_a_held_fund_stays_requested_after_it_leaves_the_config(config, ledger, cache, managed_home):
    from cherrypick.core import streamrequests

    cfg = _only(config, "once")
    cache.summary("TQQQ", DAY, prev_day_close=100.0).quote("TQQQ", 92.0, 92.02)
    _tick(cfg, ledger, cache)
    cfg["names"] = {"TQQQ": {"enabled": False}, "SPXL": {}}
    stream_request.write(cfg, ledger, "unused")
    data = json.loads(streamrequests.request_path("ladder").read_text())
    assert data["symbols"] == ["SPXL", "TQQQ"]
    assert data["entry_symbols"] == ["SPXL"]


def test_status_shows_each_arms_next_level(config, ledger, cache):
    cache.summary("TQQQ", NEXT, prev_day_close=100.0)
    out = paper_loop.run_status(config, ledger, cache_path=cache.path)
    assert out["ok"] and set(out["arms"]) == {"chain", "once", "decay"}
