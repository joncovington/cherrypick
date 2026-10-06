import pytest

from cherrypick.contango import engine

SVXY = {"bid": 49.99, "ask": 50.01, "mid": 50.00}
SHV = {"bid": 110.40, "ask": 110.41, "mid": 110.405}


def test_slippage_is_half_the_spread_floored_at_two_bps():
    assert engine.slippage_per_share({"bid": 49.0, "ask": 51.0, "mid": 50.0}) == pytest.approx(1.0)
    assert engine.slippage_per_share(SVXY) == pytest.approx(0.01)  # 2 bps of 50 is the half-spread too
    assert engine.slippage_per_share(SHV) == pytest.approx(110.405 * 2 / 10_000)  # the floor beats 0.005


def test_a_buy_is_whole_shares_and_leaves_the_remainder_in_cash():
    plan = engine.plan_switch(cash=10_000, holding=None, target_symbol="SVXY", quotes={"SVXY": SVXY})
    assert plan["ok"] and plan["sell"] is None
    assert plan["buy"]["shares"] == 199  # 10_000 / 50.01
    assert plan["cash_after"] == pytest.approx(10_000 - 199 * 50.0 - 199 * 0.01, abs=0.01)
    assert plan["buy"]["fees"] == 0.0


def test_both_legs_are_checked_before_either_is_planned():
    wide = {"bid": 49.0, "ask": 51.0, "mid": 50.0}
    held = {"symbol": "SVXY", "shares": 100}
    plan = engine.plan_switch(cash=0, holding=held, target_symbol="SHV", quotes={"SVXY": wide, "SHV": SHV})
    assert plan == {"ok": False, "reason": "spread_too_wide_SVXY"}
    plan = engine.plan_switch(cash=0, holding=held, target_symbol="SHV", quotes={"SVXY": SVXY})
    assert plan["reason"] == "no_quote_SHV"


def test_a_sell_pays_the_pass_through_fees():
    fill = engine.sell_fill("SVXY", 200, SVXY)
    assert fill["fees"] > 0
    assert fill["cash_delta"] == pytest.approx(200 * 50.0 - fill["slippage"] - fill["fees"], abs=0.01)


def test_a_closed_stint_adds_up():
    pos = {"shares": 100, "entry_mid": 48.0, "entry_fees": 0.0, "entry_slippage": 0.96, "distributions": 3.5}
    r = engine.stint_result(pos, engine.sell_fill("SVXY", 100, SVXY))
    assert r["entry_value"] + r["exit_value"] + r["distributions"] == pytest.approx(r["gross_pnl"])
    assert r["gross_pnl"] - r["fees"] - r["slippage"] == pytest.approx(r["net_pnl"])
    assert r["entry_value"] < 0 < r["exit_value"]


def _stint(entry, exit_=None, pid="control:SHV:x"):
    return {
        "position_id": pid,
        "arm": "control",
        "symbol": "SHV",
        "shares": 90,
        "entry_session": entry,
        "exit_session": exit_,
    }


def test_a_distribution_is_owed_only_to_a_stint_holding_at_the_ex_date_open():
    div = [{"symbol": "SHV", "ex_date": "2026-11-02", "amount": 0.34}]
    assert engine.dividend_credits([_stint("2026-11-02")], div, set()) == []  # bought on the ex-date
    assert len(engine.dividend_credits([_stint("2026-10-30", "2026-11-02")], div, set())) == 1  # sold on it
    assert engine.dividend_credits([_stint("2026-10-01", "2026-10-30")], div, set()) == []  # gone before it
    paid = engine.dividend_credits([_stint("2026-10-01")], div, set())
    assert paid[0]["amount"] == pytest.approx(30.6)
    assert engine.dividend_credits([_stint("2026-10-01")], div, {("control:SHV:x", "2026-11-02")}) == []


def test_nav_refuses_to_mark_a_holding_without_a_price():
    assert engine.nav(100.0, None, None) == 100.0
    assert engine.nav(100.0, {"shares": 10}, None) is None
    assert engine.nav(100.0, {"shares": 10}, 5.0) == 150.0
