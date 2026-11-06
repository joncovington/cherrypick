"""The assignment-risk guard: close a position holding a short leg its holder has reason to exercise early.

A short in the money is exercised when exercising beats selling, which is when its time value is gone:
a call with less time value than a dividend going ex before expiry, or any short with under a nickel
left. Cash-settled index roots cannot be assigned. The dividend comes in through an injected lookup, so
nothing here touches Dolt; every date is explicit, never the host clock.
"""

import json
from datetime import datetime

from cherrypick.earnings import dividends, management

ET = management.ET
TODAY = "2026-08-12"  # a Wednesday; the next trading day is 2026-08-13
EXPIRY = "2026-08-21"

CONFIG = {
    "strategies": {"iron_fly": {"profit_target_pct": 0.25, "stop_loss_credit_multiple": 1.5}},
    "management": {},
}


def at(hhmm, day=TODAY):
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00").replace(tzinfo=ET)


def position(root="AAPL", short_strike=190, wing=200, put_strike=180):
    sym = f"{root:<6}"
    legs = [
        {"symbol": f"{sym}260821C{short_strike * 1000:08d}", "action": "Sell to Open", "quantity": 1},
        {"symbol": f"{sym}260821P{short_strike * 1000:08d}", "action": "Sell to Open", "quantity": 1},
        {"symbol": f"{sym}260821C{wing * 1000:08d}", "action": "Buy to Open", "quantity": 1},
        {"symbol": f"{sym}260821P{put_strike * 1000:08d}", "action": "Buy to Open", "quantity": 1},
    ]
    trade = {
        "order_id": "T1",
        "strategy": "iron_fly",
        "symbol": root,
        "expiration": EXPIRY,
        "entry_credit": 5.0,
        "legs_json": json.dumps(legs),
        "opened_at": None,
    }
    return trade, legs


def snapshot(legs, *, spot, short_call_mid, wide=False):
    """The short call marks at `short_call_mid`; the other legs sit at a few cents."""
    mids = [short_call_mid, 0.02, 0.01, 0.01]
    quotes = {}
    for leg, mid in zip(legs, mids, strict=True):
        spread = 3.0 if (wide and mid == short_call_mid) else 0.0
        quotes[leg["symbol"]] = {
            "bid": max(0.0, mid - spread / 2),
            "ask": mid + spread / 2,
            "mid": mid,
            "delta": None,
            "iv": None,
        }
    return {"ok": True, "quotes": quotes, "spot": spot, "max_spread_pct": 0.0, "source": "stream"}


def lookup_returning(**row):
    calls = []

    def lookup(symbol, today):
        calls.append((symbol, today))
        return row

    lookup.calls = calls
    return lookup


def decide(trade, snap, lookup=None, *, now=None, config=None):
    return management.evaluate(
        trade,
        snap,
        config or CONFIG,
        now=now or at("10:00"),
        sessions_held=1,
        is_first_check_of_day=False,
        dividend_lookup=lookup,
    )


DIVIDEND = {"known": True, "ex_date": "2026-08-18", "amount": 0.50, "source": "projected"}


# --------------------------------------------------------------------------- rule (a): the dividend
def test_an_itm_short_call_with_less_time_value_than_the_dividend_closes():
    trade, legs = position()
    # spot 195: the 190 call has 5.00 intrinsic and marks 5.20 -- 0.20 of time value, under the 0.50.
    d = decide(trade, snapshot(legs, spot=195.0, short_call_mid=5.20), lookup_returning(**DIVIDEND))
    assert d.action == "close_all" and d.reason == "assignment_risk"
    assert d.detail["rule"] == "dividend"
    assert d.detail["leg"] == legs[0]["symbol"]
    assert d.detail["intrinsic"] == 5.0 and d.detail["time_value"] == 0.2
    assert d.detail["dividend"] == 0.5 and d.detail["ex_date"] == "2026-08-18"
    assert d.detail["dividend_source"] == "projected" and d.detail["dividend_known"] is True


def test_it_does_not_fire_out_of_the_money():
    trade, legs = position()
    # spot on the 190 strike: neither short has any intrinsic value
    d = decide(trade, snapshot(legs, spot=190.0, short_call_mid=4.00), lookup_returning(**DIVIDEND))
    assert d.reason != "assignment_risk"


def test_it_does_not_fire_when_the_ex_date_is_after_expiry():
    trade, legs = position()
    late = {**DIVIDEND, "ex_date": "2026-08-24"}
    d = decide(trade, snapshot(legs, spot=195.0, short_call_mid=5.20), lookup_returning(**late))
    assert d.reason != "assignment_risk"


def test_it_does_not_fire_when_the_time_value_covers_the_dividend():
    trade, legs = position()
    # 0.60 of time value against a 0.50 dividend: not worth exercising for.
    d = decide(trade, snapshot(legs, spot=195.0, short_call_mid=5.60), lookup_returning(**DIVIDEND))
    assert d.reason != "assignment_risk"


def test_a_dividend_that_has_already_gone_ex_does_not_count():
    trade, legs = position()
    past = {**DIVIDEND, "ex_date": TODAY}
    d = decide(trade, snapshot(legs, spot=195.0, short_call_mid=5.20), lookup_returning(**past))
    assert d.reason != "assignment_risk"


# --------------------------------------------------------------------------- rule (b): nothing left to sell
def test_a_deep_itm_short_with_under_a_nickel_of_time_value_closes_without_a_dividend():
    trade, legs = position()
    d = decide(trade, snapshot(legs, spot=210.0, short_call_mid=20.03))
    assert d.action == "close_all" and d.reason == "assignment_risk"
    assert d.detail["rule"] == "extrinsic_floor" and d.detail["floor"] == 0.05
    assert d.detail["time_value"] == 0.03


def test_a_slightly_itm_short_with_no_time_value_the_morning_after_does_not_close():
    trade, legs = position()
    # spot 191.9: the 190 call is 1% in the money, intrinsic 1.90, mid 1.92 -- the post-crush shape.
    d = decide(trade, snapshot(legs, spot=191.9, short_call_mid=1.92))
    assert d.reason != "assignment_risk"


def test_a_four_percent_itm_short_with_no_time_value_closes():
    trade, legs = position()
    d = decide(trade, snapshot(legs, spot=198.0, short_call_mid=8.02))
    assert d.reason == "assignment_risk" and d.detail["rule"] == "extrinsic_floor"
    assert d.detail["time_value"] == 0.02 and 0.04 <= d.detail["itm_pct"] < 0.05


def test_the_depth_threshold_is_configurable():
    trade, legs = position()
    snap = snapshot(legs, spot=191.9, short_call_mid=1.92)
    config = {**CONFIG, "management": {"assignment_deep_itm_pct": 0.005}}
    assert decide(trade, snap, config=config).reason == "assignment_risk"


def test_a_deep_itm_short_on_its_expiration_day_is_left_to_the_expiry_close():
    trade, legs = position()
    snap = snapshot(legs, spot=210.0, short_call_mid=20.02)
    d = decide(trade, snap, now=at("10:00", EXPIRY))
    assert d.reason != "assignment_risk"
    assert decide(trade, snap, now=at("15:20", EXPIRY)).reason == "expiry_close"


def test_the_floor_applies_to_a_short_put_too():
    trade, legs = position()
    # spot 170 puts the 190 put 20 in the money. The call is far out, so only the put can fire.
    snap = snapshot(legs, spot=170.0, short_call_mid=0.01)
    snap["quotes"][legs[1]["symbol"]].update({"bid": 20.03, "ask": 20.03, "mid": 20.03})
    d = decide(trade, snap)
    assert d.reason == "assignment_risk" and d.detail["right"] == "P"


def test_the_floor_is_configurable():
    trade, legs = position()
    snap = snapshot(legs, spot=210.0, short_call_mid=20.03)
    config = {**CONFIG, "management": {"assignment_extrinsic_floor": 0.02}}
    assert decide(trade, snap, config=config).reason != "assignment_risk"


def test_an_unknown_dividend_is_recorded_and_the_floor_still_applies():
    trade, legs = position()
    unknown = {"known": False, "note": "fewer_than_two_dividends_on_record"}
    d = decide(trade, snapshot(legs, spot=210.0, short_call_mid=20.03), lookup_returning(**unknown))
    assert d.reason == "assignment_risk" and d.detail["rule"] == "extrinsic_floor"
    assert d.detail["dividend_known"] is False
    assert d.detail["dividend_note"] == "fewer_than_two_dividends_on_record"
    # and with time value left, an unknown dividend alone closes nothing
    held = decide(trade, snapshot(legs, spot=195.0, short_call_mid=5.20), lookup_returning(**unknown))
    assert held.reason != "assignment_risk"


def test_the_guard_can_be_switched_off():
    trade, legs = position()
    config = {**CONFIG, "management": {"assignment_guard": False}}
    d = decide(trade, snapshot(legs, spot=210.0, short_call_mid=20.03), config=config)
    assert d.reason != "assignment_risk"


# --------------------------------------------------------------------------- index roots and long legs
def test_a_cash_settled_index_root_is_never_assigned():
    trade, legs = position(root="SPXW", short_strike=4000, wing=4010, put_strike=3990)
    d = decide(trade, snapshot(legs, spot=4100.0, short_call_mid=100.01), lookup_returning(**DIVIDEND))
    assert d.reason != "assignment_risk"


def test_a_long_leg_in_the_money_is_not_assignment_risk():
    trade, legs = position(short_strike=190, wing=200)
    # spot 205: both calls are in the money, the short with real time value; only the LONG 200 call is
    # nearly flat. A long is exercised at our discretion.
    snap = snapshot(legs, spot=205.0, short_call_mid=15.60)
    snap["quotes"][legs[2]["symbol"]].update({"bid": 5.02, "ask": 5.02, "mid": 5.02})
    d = decide(trade, snap, lookup_returning(**DIVIDEND))
    assert d.reason != "assignment_risk"


# --------------------------------------------------------------------------- precedence and the gate
def test_the_expiry_close_and_the_entry_session_both_outrank_the_guard():
    trade, legs = position()
    snap = snapshot(legs, spot=210.0, short_call_mid=20.03)
    expiry_day = at("15:20", EXPIRY)
    assert decide(trade, snap, now=expiry_day).reason == "expiry_close"
    entry = management.evaluate(
        trade, snap, CONFIG, now=at("15:37"), sessions_held=0, dividend_lookup=lookup_returning(**DIVIDEND)
    )
    assert entry.reason == "entry_session"


def test_the_guard_outranks_the_strategys_own_target():
    trade, legs = position()
    snap = snapshot(legs, spot=210.0, short_call_mid=20.03)
    # the strategy alone would close this on its stop (the debit is far past 1.5x the credit)
    assert decide(trade, snap, config={**CONFIG, "management": {"assignment_guard": False}}).reason == (
        "stop_loss"
    )
    assert decide(trade, snap).reason == "assignment_risk"


def test_an_ex_date_tomorrow_is_urgent_and_is_not_held_by_the_spread_gate():
    trade, legs = position()
    tomorrow = {**DIVIDEND, "ex_date": "2026-08-13"}
    snap = snapshot(legs, spot=195.0, short_call_mid=5.20, wide=True)
    d = decide(trade, snap, lookup_returning(**tomorrow))
    assert d.reason == "assignment_risk" and d.detail["urgent"] is True
    assert management.execution_gate(snap, CONFIG, "iron_fly", now=at("10:00"), reason=d.reason) == (
        "spread_too_wide"
    )
    assert (
        management.execution_gate(snap, CONFIG, "iron_fly", now=at("10:00"), reason=d.reason, urgent=True)
        is None
    )
    # an urgent close still waits for the execution window
    assert (
        management.execution_gate(snap, CONFIG, "iron_fly", now=at("09:30"), reason=d.reason, urgent=True)
        == "before_exec_window"
    )


def test_a_later_ex_date_and_the_floor_rule_wait_on_the_gate():
    trade, legs = position()
    d = decide(trade, snapshot(legs, spot=195.0, short_call_mid=5.20), lookup_returning(**DIVIDEND))
    assert d.detail["urgent"] is False
    floor = decide(trade, snapshot(legs, spot=210.0, short_call_mid=20.03))
    assert floor.detail["urgent"] is False


def test_the_lookup_is_not_asked_when_nothing_is_in_the_money():
    trade, legs = position()
    lookup = lookup_returning(**DIVIDEND)
    decide(trade, snapshot(legs, spot=190.0, short_call_mid=4.00), lookup)
    assert lookup.calls == []


# --------------------------------------------------------------------------- the dividend projection
def _d(iso):
    from datetime import date

    return date.fromisoformat(iso)


def test_a_declared_future_dividend_is_used_as_written():
    rows = [(_d("2026-05-15"), 0.5), (_d("2026-08-14"), 0.52)]
    out = dividends.next_dividend(rows, _d(TODAY))
    assert out == {"known": True, "ex_date": "2026-08-14", "amount": 0.52, "source": "declared"}


def test_a_regular_payer_is_projected_one_gap_on():
    rows = [(_d("2026-02-13"), 0.5), (_d("2026-05-15"), 0.5), (_d("2026-08-07"), 0.5)]
    out = dividends.next_dividend(rows, _d("2026-09-01"))
    assert out["source"] == "projected" and out["amount"] == 0.5
    assert out["ex_date"] == "2026-11-02"  # 2026-08-07 + the 87-day median gap, past the 09-01 today


def test_a_name_with_one_dividend_or_a_stopped_payer_is_unknown():
    assert dividends.next_dividend([(_d("2026-05-15"), 0.5)], _d(TODAY))["known"] is False
    stopped = [(_d("2024-02-13"), 0.5), (_d("2024-05-15"), 0.5)]
    assert dividends.next_dividend(stopped, _d(TODAY)) == {"known": False, "note": "dividend_appears_stopped"}
