"""Item 35's study (scripts/bwb_item35.py) and its request builder (scripts/fetch_addon_weeklies.py): the
entry, the module's triggers over a minute series, the add-on variants, the gate, fills and stresses,
settlement, the bootstrap, Holm, the beat-control pairing, the paper checks and the report.

Invented chains only (Black-Scholes quotes on a flat or driven index), no store, no network. Each rule
is shown firing and then failing by breaking its invariant, because a guard that cannot fire reads as
coverage it is not.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest
from cherrypick.core import bs, fees

from cherrypick.bwb import engine

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bwb_item35 as b35  # noqa: E402
import fetch_addon_weeklies as faw  # noqa: E402

EXP = "2024-01-12"  # a Friday
ENTRY = "2024-01-08"  # the Monday: 4 days to expiry
DAYS = ["2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12"]
WIDE = tuple(float(k) for k in range(4800, 5105, 5))


def flat(_ts):
    return 5000.0


def build(spot_fn=flat, vol=0.18, half=0.05, strikes=WIDE, calls=True, days=DAYS) -> b35.ExpiryData:
    """An expiry's minutes, 09:30 to 15:59 on each day, priced by Black-Scholes at `spot_fn(ts)`."""
    end = b35.minute_ts(EXP, 16, 0)
    minutes = []
    for day in days:
        for mod in range(9 * 60 + 30, 16 * 60):
            ts = b35.minute_ts(day, mod // 60, mod % 60)
            spot = spot_fn(ts)
            years = max((end - ts) / (365 * 86400), 1e-7)
            puts = {}
            for k in strikes:
                mid = bs.price(spot, k, years, vol, "P", 0.0)
                puts[k] = (
                    max(round(mid - half, 2), 0.0),
                    round(mid + half, 2),
                    bs.delta(spot, k, years, vol, "P", 0.0),
                    vol,
                )
            minutes.append(b35.Minute(ts, day, spot, puts))
    strip = {}
    if calls:
        ts, years = b35.minute_ts(ENTRY, 10, 0), (end - b35.minute_ts(ENTRY, 10, 0)) / (365 * 86400)
        for k in (4985.0, 4990.0, 4995.0, 5000.0, 5005.0, 5010.0, 5015.0):
            mid = bs.price(5000.0, k, years, vol, "C", 0.0)
            strip[k] = (max(round(mid - half, 2), 0.0), round(mid + half, 2))
    return b35.ExpiryData(EXP, minutes, {b35.minute_ts(ENTRY, 10, 0): strip} if calls else {})


def at(day, hh, mm):
    return b35.minute_ts(day, hh, mm)


def entry_plan(data):
    out = b35.plan_at_entry(data, ENTRY, 4)
    assert out["status"] == "entered", out
    return out["plan"]


# ------------------------------------------------------------------------------------------------
# The request builder.


def test_the_holiday_thursdays_and_their_8_day_entries_are_what_the_store_lacks():
    pairs = faw.entry_pairs(date(2025, 4, 7), date(2025, 4, 30), date(2025, 4, 30))
    life = faw.lives(pairs)
    assert date(2025, 4, 17) in life  # Good Friday 04-18: the weekly is the Thursday
    friday_held = {d.isoformat() for d in life[date(2025, 4, 25)] if d != date(2025, 4, 17)}
    held = {"2025-04-25": friday_held}  # the store has the Friday from the 18th (a holiday) on
    spans, counts = faw.needed_spans(life, held, set())
    by = {(s["expiration"], s["first"], s["last"]) for s in spans}
    assert ("2025-04-17", "2025-04-11", "2025-04-17") in by  # the whole Thursday weekly
    assert ("2025-04-25", "2025-04-17", "2025-04-17") in by  # the 8-day entry, a day only
    assert counts["expiries_topped_up"] >= 1 and counts["expiries_new"] >= 1


def test_the_seven_holiday_thursdays_are_the_declared_ones():
    pairs = faw.entry_pairs(date(2022, 5, 13), date(2026, 10, 16), date(2026, 10, 16))
    thursdays = sorted(e.isoformat() for e in faw.lives(pairs) if e.weekday() == 3)
    assert thursdays == [
        "2023-04-06",
        "2024-03-28",
        "2025-04-17",
        "2025-07-03",
        "2026-04-02",
        "2026-06-18",
        "2026-07-02",
    ]


def test_a_refused_expiry_is_not_asked_for_again_and_a_held_one_asks_nothing():
    pairs = faw.entry_pairs(date(2024, 6, 14), date(2024, 6, 28), date(2024, 6, 28))
    life = faw.lives(pairs)
    spans, counts = faw.needed_spans(life, {}, {"2024-06-21"})
    assert counts["refused_expiries"] == 1 and all(s["expiration"] != "2024-06-21" for s in spans)
    held = {e.isoformat(): {d.isoformat() for d in days} for e, days in life.items()}
    assert faw.needed_spans(life, held, set()) == (
        [],
        {"expiries_new": 0, "expiries_topped_up": 0, "sessions": 0, "refused_expiries": 0},
    )


def test_missing_sessions_split_into_runs_of_consecutive_sessions():
    days = [date(2024, 1, d) for d in (8, 9, 10, 11, 12)]
    assert faw.runs(days, {days[0], days[1], days[3]}) == [(days[0], days[1]), (days[3], days[3])]


# ------------------------------------------------------------------------------------------------
# A quote, and a minute of the store as the module's snapshot.


def test_a_quote_is_usable_by_the_suites_one_definition():
    assert b35.quote_of(1.0, 1.2)["mid"] == pytest.approx(1.1)
    assert b35.quote_of(0.0, 0.05) is not None  # a zero bid with an ask is the cache's quote too
    for bad in ((None, 1.0), (1.0, None), (1.0, 0.0), (1.2, 1.0), (-0.1, 1.0)):
        assert b35.quote_of(*bad) is None, bad


def test_the_chain_reader_round_trips_the_stores_rows():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE q (expiration TEXT, day TEXT, ts INTEGER, right TEXT, strike REAL, bid REAL, ask REAL, delta REAL, iv REAL, spot REAL)"
    )
    ts = at(ENTRY, 10, 0)
    conn.executemany(
        "INSERT INTO q VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (EXP, ENTRY, ts, "P", 4930.0, 1.0, 1.2, -0.2, 0.18, None),
            (EXP, ENTRY, ts, "P", 4935.0, 1.1, 1.3, -0.22, 0.18, 5000.5),
            (EXP, ENTRY, ts, "C", 5000.0, 20.0, 21.0, 0.5, 0.18, 5000.5),
            (EXP, ENTRY, ts + 60, "P", 4930.0, 1.0, 1.2, -0.2, 0.18, 5001.0),
        ],
    )
    data = b35.load_expiry(conn, EXP)
    m = data.by_ts[ts]
    assert m.spot == 5000.5 and m.puts[4935.0] == (1.1, 1.3, -0.22, 0.18)  # the first spot seen at the minute
    assert data.calls[ts] == {5000.0: (20.0, 21.0)} and data.ts_list == [ts, ts + 60]
    assert b35.load_expiry(conn, "2030-01-01") is None


# ------------------------------------------------------------------------------------------------
# The entry.


def test_the_entry_is_the_modules_plan_at_the_ten_oclock_minute():
    data = build()
    plan = entry_plan(data)
    assert (plan["near_strike"], plan["far_strike"]) == (plan["body_strike"] + 5, plan["body_strike"] - 10)
    assert plan["body_strike"] == pytest.approx(plan["spot"] - plan["expected_move"], abs=2.6)
    assert plan["credit"] > 0
    # exactly the module's function over the same snapshot: the replay replaces only the chain reader
    minute = data.by_ts[at(ENTRY, 10, 0)]
    direct = engine.plan_entry(data.snapshot(minute, 4, with_calls=True), {})
    assert direct["plan"]["credit"] == plan["credit"]
    # the body is sold twice: four legs, the body's quote repeated
    assert [leg["leg_role"] for leg in plan["legs"]] == [
        "near_long",
        "body_short_1",
        "body_short_2",
        "far_long",
    ]


def test_each_way_the_entry_is_dropped_or_refused_is_counted_by_its_own_reason():
    assert b35.plan_at_entry(None, ENTRY, 4)["reason"] == "not_in_store"
    d = build()
    del d.by_ts[at(ENTRY, 10, 0)]
    assert b35.plan_at_entry(d, ENTRY, 4)["reason"] == "no_entry_minute"
    d = build()
    d.by_ts[at(ENTRY, 10, 0)].spot = None
    assert b35.plan_at_entry(d, ENTRY, 4)["reason"] == "no_index_price"
    assert b35.plan_at_entry(build(calls=False), ENTRY, 4)["reason"] == "no_atm_quote"
    d = build()
    d.calls[at(ENTRY, 10, 0)][5000.0] = (None, None)  # the ATM call has no quote
    assert b35.plan_at_entry(d, ENTRY, 4)["reason"] == "no_atm_quote"


def test_a_body_target_past_the_stored_band_is_dropped_not_snapped_to_the_edge():
    narrow = tuple(float(k) for k in range(4985, 5105, 5))  # the body belongs near 4935
    out = b35.plan_at_entry(build(strikes=narrow), ENTRY, 4)
    assert (out["status"], out["reason"]) == ("dropped", "leg_unquoted")
    assert b35.plan_at_entry(build(), ENTRY, 4)["status"] == "entered"  # the same chain with the band whole


def test_a_missing_wing_quote_that_shifts_a_leg_drops_the_entry():
    d = build()
    plan = entry_plan(d)
    m = d.by_ts[at(ENTRY, 10, 0)]
    m.puts[plan["near_strike"]] = (None, None, None, None)
    d._snap.clear()  # a snapshot is cached per minute
    out = b35.plan_at_entry(d, ENTRY, 4)
    assert (out["status"], out["reason"]) == ("dropped", "leg_unquoted")


def test_a_wide_leg_spread_and_a_credit_that_is_not_one_are_the_modules_refusals():
    d = build()
    plan = entry_plan(d)
    m = d.by_ts[at(ENTRY, 10, 0)]
    bid, ask, delta, iv = m.puts[plan["body_strike"]]
    m.puts[plan["body_strike"]] = (bid, bid * 1.5 + 0.2, delta, iv)  # over 25% of the mid
    d._snap.clear()
    assert b35.plan_at_entry(d, ENTRY, 4) == {"status": "refused", "reason": "spread_too_wide"}
    d = build()
    m = d.by_ts[at(ENTRY, 10, 0)]
    mid = (m.puts[plan["body_strike"]][0] + m.puts[plan["body_strike"]][1]) / 2
    m.puts[plan["body_strike"]] = (round(mid / 4, 2), round(mid / 4, 2) + 0.01, -0.1, 0.18)  # a cheap body
    assert b35.plan_at_entry(d, ENTRY, 4) == {"status": "refused", "reason": "no_credit"}


# ------------------------------------------------------------------------------------------------
# The measured minute, and the triggers the module defines on it.


def test_a_minute_is_unmeasured_for_a_missing_quote_a_zero_vol_or_a_boundary_delta():
    near = 5000.0
    m = b35.Minute(0, ENTRY, 5000.0, {near: (1.0, 1.2, -0.3, 0.2)})
    assert b35.near_abs_delta(m, near) == 0.3
    for row in ((None, None, -0.3, 0.2), (1.0, 1.2, -0.3, 0.0), (1.0, 1.2, -0.3, None), (1.0, 1.2, None, 0.2),
                (1.0, 1.2, -1.0, 0.2), (1.0, 1.2, -0.99999, 0.2)):  # fmt: skip
        m.puts[near] = row
        assert b35.near_abs_delta(m, near) is None, row
    assert b35.near_abs_delta(b35.Minute(0, ENTRY, 5000.0, {}), near) is None


def falling_then_back(low=4930.0, t_down=(2, 11, 0), t_up=(2, 12, 0), back=5000.0):
    """The index flat, a fall to `low` over 30 minutes from the given day/hour, held, then back."""
    d0, h0, m0 = t_down
    start = at(DAYS[d0 - 1], h0, m0)
    up = at(DAYS[t_up[0] - 1], t_up[1], t_up[2])

    def spot(ts):
        if ts < start:
            return 5000.0
        if ts < start + 1800:
            return 5000.0 + (low - 5000.0) * (ts - start) / 1800
        if ts < up:
            return low
        return min(back, low + (back - low) * (ts - up) / 1800)

    return spot


def first_index(ticks, cond):
    return next(i for i, t in enumerate(ticks) if cond(t))


def test_delta_arms_when_the_near_wing_reaches_fifty_and_fires_on_the_next_minute_that_prices():
    data = build(falling_then_back())
    plan = entry_plan(data)
    ticks = b35.ticks_for(data, ENTRY, plan["near_strike"])
    assert ticks[0].ts == at(ENTRY, 10, 0) and ticks[-1].ts == at(EXP, 15, 59)
    i = first_index(ticks, lambda t: t.abs_delta is not None and t.abs_delta >= 0.5)
    fire = b35.run_trigger("delta", ticks, data, plan, 4)
    assert fire["ts"] == ticks[i + 1].ts  # armed at i, fired at the next credit minute
    assert (fire["short"], fire["long"]) == (plan["far_strike"] + 5, plan["far_strike"] - 5)
    assert fire["credit"] > 0 and len(fire["quotes"]) == 2


def test_nothing_fires_while_the_index_stays_far_from_the_near_wing():
    data = build()
    plan = entry_plan(data)
    ticks = b35.ticks_for(data, ENTRY, plan["near_strike"])
    assert all(b35.run_trigger(a, ticks, data, plan, 4) is None for a in ("delta", "bounce"))


def test_an_unmeasured_minute_advances_no_latch_and_arms_nothing():
    data = build(falling_then_back())
    plan = entry_plan(data)
    ticks = b35.ticks_for(data, ENTRY, plan["near_strike"])
    i = first_index(ticks, lambda t: t.abs_delta is not None and t.abs_delta >= 0.5)
    honest = b35.run_trigger("delta", ticks, data, plan, 4)
    ticks[i].abs_delta = None  # the minute it would have armed on did not measure
    j = first_index(ticks[i + 1 :], lambda t: t.abs_delta is not None and t.abs_delta >= 0.5) + i + 1
    late = b35.run_trigger("delta", ticks, data, plan, 4)
    assert late["ts"] == ticks[j + 1].ts and late["ts"] > honest["ts"]


def test_bounce_needs_the_peak_and_then_a_pullback_to_forty_five():
    data = build(falling_then_back())
    plan = entry_plan(data)
    ticks = b35.ticks_for(data, ENTRY, plan["near_strike"])
    peak, armed = 0.0, None
    for i, t in enumerate(ticks):
        if t.abs_delta is not None:
            peak = max(peak, t.abs_delta)
            if peak >= 0.5 and t.abs_delta <= 0.45:
                armed = i
                break
    assert armed is not None
    fire = b35.run_trigger("bounce", ticks, data, plan, 4)
    assert fire["ts"] == ticks[armed + 1].ts
    # break the pullback: the index stays down, delta never retreats, bounce never arms; delta does
    stuck = build(falling_then_back(back=4930.0))
    sticks = b35.ticks_for(stuck, ENTRY, entry_plan(stuck)["near_strike"])
    assert b35.run_trigger("bounce", sticks, stuck, entry_plan(stuck), 4) is None
    assert b35.run_trigger("delta", sticks, stuck, entry_plan(stuck), 4) is not None


def test_flip_latches_below_the_flip_then_arms_on_the_reclaim_with_the_buffer():
    data = build()
    plan = entry_plan(data)
    path = {}
    for m in data.minutes:
        k = (m.ts - at(DAYS[1], 10, 0)) // 60
        m.spot = 5000.0 if k < 0 else 4980.0 if k < 30 else 4993.0 if k < 60 else 4996.0
        path[m.ts] = 4990.0
    data._flip = path  # the recomputed flip, here fixed at 4990
    ticks = b35.ticks_for(data, ENTRY, plan["near_strike"])
    i = first_index(ticks, lambda t: t.ts >= at(DAYS[1], 10, 0) + 60 * 60)  # 4996 >= 4990 * 1.001 = 4994.99
    fire = b35.run_trigger("flip", ticks, data, plan, 4)
    assert fire["ts"] == ticks[i + 1].ts
    # at 4993 (above the flip, inside the buffer) it does not arm
    for m in data.minutes:
        if m.ts >= at(DAYS[1], 10, 0) + 60 * 60:
            m.spot = 4993.0
    ticks = b35.ticks_for(data, ENTRY, plan["near_strike"])
    assert b35.run_trigger("flip", ticks, data, plan, 4) is None
    # and never having traded below it, it does not arm either
    for m in data.minutes:
        m.spot = 5000.0
    assert b35.run_trigger("flip", b35.ticks_for(data, ENTRY, plan["near_strike"]), data, plan, 4) is None


def test_the_trigger_read_a_minute_late_fires_a_minute_later():
    data = build(falling_then_back())
    plan = entry_plan(data)
    ticks = b35.ticks_for(data, ENTRY, plan["near_strike"])
    now, late = (b35.run_trigger("delta", ticks, data, plan, 4, lag=lag) for lag in (0, 1))
    assert late["ts"] == now["ts"] + 60


def test_an_armed_addon_waits_for_a_credit_and_for_the_quoted_bracket():
    data = build(falling_then_back())
    plan = entry_plan(data)
    ticks = b35.ticks_for(data, ENTRY, plan["near_strike"])
    honest = b35.run_trigger("delta", ticks, data, plan, 4)
    short, long_ = plan["far_strike"] + 5, plan["far_strike"] - 5
    # the minutes up to a later one price the spread as no credit: the fire waits
    wait_until = honest["ts"] + 5 * 60
    for m in data.minutes:
        if honest["ts"] <= m.ts < wait_until:
            lo = m.puts[long_]
            m.puts[short] = (lo[0], lo[1], -0.3, 0.2)
    data._addon.clear()
    data._snap.clear()
    waited = b35.run_trigger("delta", ticks, data, plan, 4)
    assert waited["ts"] == wait_until
    # a bracket that is not exactly one increment either side of the far wing is no bracket
    for m in data.minutes:
        m.puts.pop(long_, None)
    data._addon.clear()
    data._snap.clear()
    assert b35.run_trigger("delta", ticks, data, plan, 4) is None


def test_the_execution_gate_holds_a_fire_while_a_fly_leg_is_too_wide():
    data = build(falling_then_back())
    plan = entry_plan(data)
    ticks = b35.ticks_for(data, ENTRY, plan["near_strike"])
    honest = b35.run_trigger("delta", ticks, data, plan, 4)
    far = plan["far_strike"]
    until = honest["ts"] + 3 * 60
    for m in data.minutes:
        if honest["ts"] <= m.ts < until:
            bid, ask, d, iv = m.puts[far]
            m.puts[far] = (bid, bid * 1.5 + 0.2, d, iv)  # more than 25% of the mid
    assert b35.run_trigger("delta", ticks, data, plan, 4)["ts"] == until


def test_the_dte3_addon_opens_only_with_three_days_left():
    # fall on the first day: armed with 4 days left, fired at once: dte3 keeps it
    early = build(falling_then_back(t_down=(1, 11, 0), t_up=(1, 13, 0)))
    plan = entry_plan(early)
    rec = b35.simulate_entry(early, ENTRY, EXP, 4)
    assert rec["arms"]["delta"][0] is not None and rec["arms"]["delta-dte3"][0] == rec["arms"]["delta"][0]
    assert b35.days_left(rec["arms"]["delta"][0]["day"], EXP) >= 3
    # the same fall on Thursday (one day left): delta fires, dte3 opens no add-on and the fly rides alone
    late = build(falling_then_back(t_down=(4, 11, 0), t_up=(4, 13, 0)))
    rec = b35.simulate_entry(late, ENTRY, EXP, 4)
    assert rec["arms"]["delta"][0] is not None and rec["arms"]["delta-dte3"][0] is None
    assert plan["credit"] > 0


def test_the_shortstop_buys_the_addon_back_at_the_first_minute_spot_is_at_its_short_strike():
    low = 4900.0  # through the whole structure
    data = build(falling_then_back(low=low, back=low))
    plan = entry_plan(data)
    rec = b35.simulate_entry(data, ENTRY, EXP, 4)
    stop = rec["arms"]["delta-shortstop"][0]
    fire, exit_ = stop["fire"], stop["exit"]
    ticks = b35.ticks_for(data, ENTRY, plan["near_strike"])
    hit = first_index(ticks, lambda t: t.ts > fire["ts"] and t.spot is not None and t.spot <= fire["short"])
    assert exit_["ts"] == ticks[hit].ts
    row = ticks[hit].minute.puts
    assert exit_["cost"] == pytest.approx(
        (row[fire["short"]][0] + row[fire["short"]][1]) / 2
        - (row[fire["long"]][0] + row[fire["long"]][1]) / 2
    )
    assert stop["exit_late"]["ts"] == exit_["ts"] + 60  # the stress fills one minute later
    # a minute without a two-sided quote on both legs waits for the next
    row[fire["long"]] = (None, None, None, None)
    waited = b35.shortstop_exit(ticks, fire)
    assert waited["ts"] == exit_["ts"] + 60
    # spot never reaching the short strike: held to settlement
    assert b35.shortstop_exit(ticks[:hit], fire) is None


# ------------------------------------------------------------------------------------------------
# What a position is worth: fills, stresses, settlement.


def quotes(*pairs):
    return [{"bid": b, "ask": a} for b, a in pairs]


def hand_rec(fire=None, arm="delta"):
    fly = quotes((3.0, 3.2), (1.6, 1.8), (1.6, 1.8), (0.9, 1.1))  # near, body x2, far
    plan = {"spot": 5000.0, "expected_move": 65.0, "body_strike": 4935.0, "near_strike": 4940.0, "far_strike": 4925.0,
            "credit": 0.8, "quotes": fly}  # fmt: skip
    return {"plan": plan, "arms": {"control": {0: None}, arm: {0: fire}}}


ADDON = {
    "ts": 1,
    "day": ENTRY,
    "short": 4930.0,
    "long": 4920.0,
    "credit": 0.5,
    "quotes": quotes((0.7, 0.9), (0.2, 0.3)),
}


def test_the_bare_fly_nets_its_credit_less_fees_slippage_and_the_settlement_fee():
    parts = b35.trade_parts(hand_rec(), "control", 0, settle=5000.0)  # everything expires worthless
    assert parts["fly"] == pytest.approx(80.0) and parts["itm"] == 0
    fee = fees.ic_open_fee("SPX", 1, legs=4, sell_legs=2, ndigits=2)
    slip = sum(0.125 * (a - b) for b, a in ((3.0, 3.2), (1.6, 1.8), (1.6, 1.8), (0.9, 1.1))) * 100
    assert b35.net(parts, "verdict") == pytest.approx(80.0 - fee - slip, abs=0.02)


def test_settlement_pays_intrinsic_per_leg_and_charges_five_dollars_per_distinct_itm_symbol():
    # through the near and body but not the far wing: near 4940 and body 4935 ITM at 4932; the body is one symbol
    parts = b35.trade_parts(hand_rec(), "control", 0, settle=4932.0)
    assert parts["itm"] == 2
    owed = 2 * 3.0 - 8.0  # body x2 owes 3 each; the near wing pays 8
    assert parts["fly"] == pytest.approx((0.8 - owed) * 100)
    deep = b35.trade_parts(hand_rec(), "control", 0, settle=4800.0)  # through every strike, max loss
    assert deep["itm"] == 3 and deep["fly"] == pytest.approx((0.8 - (2 * 135 - 140 - 125)) * 100)


def test_an_addon_adds_its_credit_less_its_own_fees_and_slippage_and_its_itm_legs():
    parts = b35.trade_parts(hand_rec(ADDON), "delta", 0, settle=4925.0)  # the short ITM, the long not
    bare = b35.trade_parts(hand_rec(), "control", 0, settle=4925.0)
    assert parts["addon"] == pytest.approx((0.5 - 5.0) * 100)
    assert (bare["itm"], parts["itm"]) == (2, 3)  # the add-on's short is one more ITM symbol
    add_fee = fees.ic_open_fee("SPX", 1, legs=2, sell_legs=1, ndigits=2)
    add_slip = (0.125 * 0.2 + 0.125 * 0.1) * 100
    gap = b35.net(parts, "verdict") - b35.net(bare, "verdict")
    assert gap == pytest.approx((0.5 - 5.0) * 100 - add_fee - add_slip - 5.0, abs=0.02)


def test_the_stresses_double_the_slippage_to_halfway_and_scale_the_fees_by_one_and_a_half():
    parts = b35.trade_parts(hand_rec(ADDON), "delta", 0, settle=5000.0)
    base = b35.net(parts, "verdict")
    fee = fees.ic_open_fee("SPX", 1, legs=4, sell_legs=2, ndigits=2) + fees.ic_open_fee(
        "SPX", 1, legs=2, sell_legs=1, ndigits=2
    )
    slip = 100 * 0.125 * (0.2 + 0.2 + 0.2 + 0.2 + 0.2 + 0.1)
    assert b35.net(parts, "fees15") == pytest.approx(base - 0.5 * fee, abs=0.02)
    assert b35.net(parts, "halfway") == pytest.approx(
        base - slip, abs=0.02
    )  # 25% of the spread is twice 12.5%
    assert b35.net(parts, "natural") == pytest.approx(base - 3 * slip, abs=0.02)  # 50% is four times
    live = b35.net(parts, "live")
    assert live == pytest.approx(parts["fly"] + parts["addon"] - 10.0 - fee, abs=0.02)  # mid less 0.05, twice


def test_the_slippage_cap_binds_on_a_cheap_wide_leg_in_the_verdict_basis_only():
    cheap = hand_rec()
    cheap["plan"]["quotes"] = quotes(
        (3.0, 3.2), (1.6, 1.8), (1.6, 1.8), (0.0, 0.3)
    )  # far leg: spread 0.3, mid 0.15
    parts = b35.trade_parts(cheap, "control", 0, settle=5000.0)
    capped = b35.net(parts, "verdict")
    normal = b35.net(b35.trade_parts(hand_rec(), "control", 0, settle=5000.0), "verdict")
    assert (
        capped > normal - 100 * 0.125 * 0.3
    )  # 0.125 * 0.3 = 0.0375 would exceed the cap of 0.15 * 0.15 = 0.0225
    assert b35.net(parts, "halfway") < b35.net(parts, "verdict")


def test_a_shortstop_exit_pays_its_close_cost_and_fee_and_leaves_no_settlement_for_its_legs():
    exit_ = {"ts": 2, "cost": 4.0, "quotes": quotes((3.9, 4.1), (0.0, 0.2))}
    rec = hand_rec({"fire": ADDON, "exit": exit_, "exit_late": exit_}, arm="delta-shortstop")
    parts = b35.trade_parts(rec, "delta-shortstop", 0, settle=4900.0)
    assert parts["addon"] == pytest.approx((0.5 - 4.0) * 100)
    assert parts["itm"] == 3  # the closed add-on legs are not settled
    held = hand_rec({"fire": ADDON, "exit": None, "exit_late": None}, arm="delta-shortstop")
    held_parts = b35.trade_parts(held, "delta-shortstop", 0, settle=4900.0)
    assert held_parts["itm"] == 5 and held_parts["exit_quotes"] is None
    entry_fees = fees.ic_open_fee("SPX", 1, legs=4, sell_legs=2, ndigits=2) + fees.ic_open_fee(
        "SPX", 1, legs=2, sell_legs=1, ndigits=2
    )
    close_fee = fees.ic_close_fee("SPX", 1, legs=2, sell_legs=1, ndigits=2)
    # the fly, the add-on's two legs, the exit's two (the cheap long leg's slippage capped at 15% of its mid)
    slip = 100 * (0.125 * (0.8 + 0.3 + 0.2) + 0.15 * 0.1)
    expected = parts["fly"] + parts["addon"] - slip - entry_fees - close_fee - 3 * 5.0
    assert b35.net(parts, "verdict") == pytest.approx(expected, abs=0.02)


# ------------------------------------------------------------------------------------------------
# The gate.


def test_the_gate_passes_below_one_and_refuses_at_one_or_a_missing_print():
    assert b35.gate_state(14.0, 15.0) == (True, None)
    assert b35.gate_state(15.0, 15.0) == (False, "regime_inverted")  # at the bar refuses
    assert b35.gate_state(16.0, 15.0) == (False, "regime_inverted")
    assert b35.gate_state(None, 15.0) == (False, "regime_unmeasured")
    assert b35.gate_state(14.0, None) == (False, "regime_unmeasured")
    assert b35.gate_state(0.0, 15.0) == (False, "regime_unmeasured")


def test_a_cboe_file_reads_its_open_column_by_name():
    text = "DATE,OPEN,HIGH,LOW,CLOSE\n01/03/2024,13.0,14.0,12.5,13.5\n01/04/2024,14.0,15.0,13.0,14.5\n"
    assert b35.parse_cboe(text, "OPEN") == {"2024-01-03": 13.0, "2024-01-04": 14.0}
    assert b35.parse_cboe(text, "CLOSE")["2024-01-04"] == 14.5
    assert b35.parse_cboe("DATE,SPX\n01/03/2024,4700.5\n", "CLOSE") == {
        "2024-01-03": 4700.5
    }  # a one-value file
    assert b35.parse_cboe("nothing", "OPEN") == {}


# ------------------------------------------------------------------------------------------------
# The flip.


def test_the_recomputed_flip_sits_between_the_put_and_call_open_interest_and_moves_with_it():
    m = build().by_ts[at(DAYS[1], 10, 0)]
    near = [k for k in WIDE if 4900 <= k <= 5100]
    balanced = {("P", k): 5000 for k in near if k < 5000} | {("C", k): 5000 for k in near if k >= 5000}
    flip = b35.recompute_flip(m, EXP, balanced)
    assert flip is not None and 4900 < flip < 5100
    calls_heavy = {("P", k): 5000 for k in near if k < 5000} | {("C", k): 30000 for k in near if k >= 5000}
    assert b35.recompute_flip(m, EXP, calls_heavy) < flip  # more call open interest: the flip comes down
    puts_heavy = {("P", k): 30000 for k in near if k < 5000} | {("C", k): 5000 for k in near if k >= 5000}
    assert b35.recompute_flip(m, EXP, puts_heavy) is None or b35.recompute_flip(m, EXP, puts_heavy) > flip
    assert b35.recompute_flip(m, EXP, {}) is None  # no open interest, no flip
    m.spot = None
    assert b35.recompute_flip(m, EXP, balanced) is None


# ------------------------------------------------------------------------------------------------
# The statistics.


def week_rows(values, *, per_week=5, start=date(2022, 5, 16), arm_fired=False, gate=True, tue=False):
    """One row per entry: `values` are net per trade, `per_week` entries to a settlement week."""
    from datetime import timedelta

    rows = []
    for i, v in enumerate(values):
        week, k = divmod(i, per_week)
        d = start + timedelta(weeks=week, days=k)
        exp = (start + timedelta(weeks=week, days=4)).isoformat()
        net = {b: v for b in (*b35.BASES, "exit_late")}
        rows.append({"entry": d.isoformat(), "exp": exp, "fired": arm_fired, "gate": gate, "gate_prior": gate,
                     "tue": tue, "quarter_end": False, "net": net, "net_lag1": v if arm_fired else None})  # fmt: skip
    return rows


def test_the_bootstrap_resamples_weeks_is_seeded_and_is_one_sided():
    cl = b35.clusters([("w1", 10.0), ("w1", 12.0), ("w2", 8.0), ("w3", 11.0), ("w4", 9.0), ("w5", 10.0)])
    assert cl == [(22.0, 2), (8.0, 1), (11.0, 1), (9.0, 1), (10.0, 1)]
    a, b = b35.boot(cl), b35.boot(cl)
    assert a == b and a["p"] < 0.01 and a["lo"] > 0  # all weeks positive: no resample is at or below zero
    split = b35.boot(b35.clusters([(f"w{i}", 5.0 if i % 2 else -5.0) for i in range(40)]))
    assert 0.2 < split["p"] < 0.8  # no edge: p is nowhere near a rejection
    assert b35.boot([])["p"] == 1.0


def test_the_five_worst_weeks_are_removed_by_their_total_and_the_tail_share_is_the_worst_five_percent():
    cl = [(100.0, 5)] * 20 + [(-400.0, 5)] * 5
    assert b35.mean_without_worst(cl) == pytest.approx(100.0 / 5)
    assert b35.mean_of([("a", 1.0), ("b", 3.0)]) == 2.0
    tail = b35.tail_share([(100.0, 5)] * 18 + [(-50.0, 5), (-150.0, 5)])
    assert tail["weeks"] == 1 and tail["net"] == -150.0 and tail["share_of_losses"] == pytest.approx(0.75)


def good_design(arm, *, bump=0.0, fired=None):
    """About 400 design entries a clearly positive 60 a trade (plus a flat `bump`) with spread, 80 weeks."""
    fired = (arm != "control") if fired is None else fired
    vals = [60.0 + bump + (15.0 if i % 2 else -15.0) for i in range(400)]
    return week_rows(vals, per_week=5, arm_fired=fired)


def holdout_rows(value, n=200, per_week=5):
    return week_rows(
        [value + (10.0 if i % 2 else -10.0) for i in range(n)], per_week=per_week, start=date(2025, 1, 6)
    )


def full_rows(bump_for=None, holdout=50.0, holdout_for=None):
    rows = {}
    for arm in b35.ARMS:
        bump = (bump_for or {}).get(arm, 0.0)
        # firing lets the arm differ from control: fire on every other entry
        design = good_design(arm, bump=bump)
        for i, r in enumerate(design):
            r["fired"] = arm != "control" and i % 10 < 5
        h = holdout_rows((holdout_for or {}).get(arm, holdout))
        for r in h:
            r["fired"] = arm != "control"
        rows[arm] = design + h
    return rows


def test_a_clear_edge_passes_design_and_holdout_and_an_addon_must_also_beat_control():
    rows = full_rows(bump_for={"delta": 25.0}, holdout_for={"delta": 90.0})
    result = b35.build_result(rows)
    assert result["verdict"]["control/ungated"] == "PASS"
    assert result["verdict"]["delta/ungated"] == "PASS"  # positive, above control, holdout above control's
    # an add-on arm exactly as good as control is positive but "pays, not for the add-on"
    assert result["verdict"]["bounce/ungated"] == "PAYS, NOT FOR THE ADD-ON"
    assert result["cells"]["delta/ungated"]["conditions"]["above_control"] is True
    assert result["cells"]["bounce/ungated"]["conditions"]["above_control"] is False
    assert "bounce: pays, not for the add-on" in result["reading"]


def test_the_holdout_runs_whatever_the_design_shows_and_counts_only_after_a_design_pass():
    rows = full_rows(holdout=-40.0)
    result = b35.build_result(rows)
    assert result["cells"]["control/ungated"]["holdout"]["mean"] < 0  # computed
    assert result["verdict"]["control/ungated"] == "FAIL"  # a design pass cannot survive a negative holdout
    bad_design = {a: week_rows([-5.0] * 400) + holdout_rows(80.0) for a in b35.ARMS}
    r2 = b35.build_result(bad_design)
    assert r2["cells"]["control/ungated"]["holdout"]["mean"] > 0
    assert r2["verdict"]["control/ungated"] == "FAIL"  # a design fail stays a fail


def test_thin_samples_are_no_verdict_and_stay_in_the_family_of_twelve():
    rows = full_rows()
    rows["bounce"] = [r for r in rows["bounce"] if r["entry"] >= "2024-06-01"]  # few design entries
    rows["flip"] = [dict(r, fired=False) for r in rows["flip"]]  # no fires
    result = b35.build_result(rows)
    assert result["verdict"]["bounce/ungated"] == "NO VERDICT"
    assert result["verdict"]["flip/ungated"] == "NO VERDICT"  # fewer than 40 fires in distinct weeks
    # the thin cells still hold their place: Holm sees twelve p-values, the thin ones at 1
    assert result["cells"]["bounce/ungated"]["p"] == 1.0 and result["cells"]["flip/ungated"]["p"] == 1.0
    assert result["cells"]["control/ungated"]["holm_adjusted_p"] >= result["cells"]["control/ungated"]["p"]
    assert len(result["verdict"]) == 12
    # the gated arm needs 250 entries, not 300
    gated = b35.build_result({a: good_design(a)[:260] + holdout_rows(50.0) for a in b35.ARMS})
    assert gated["verdict"]["control/gated"] != "NO VERDICT"
    assert gated["verdict"]["control/ungated"] == "NO VERDICT"


def test_flip_is_no_verdict_input_unless_the_recomputed_flip_matched_paper():
    rows = full_rows()
    assert b35.build_result(rows, flip_valid=True)["verdict"]["flip/ungated"] != "NO VERDICT (INPUT)"
    invalid = b35.build_result(rows, flip_valid=False)
    assert invalid["verdict"]["flip/ungated"] == invalid["verdict"]["flip/gated"] == "NO VERDICT (INPUT)"
    assert len(invalid["verdict"]) == 12 and invalid["verdict"]["control/ungated"] == "PASS"


def fig(**kw):
    base = {
        "n": 400,
        "fire_weeks": 60,
        "boot": {"mean": 40.0, "p": 0.001},
        "halves": [30.0, 50.0],
        "stress": {"halfway": 20.0, "fees15": 30.0, "exit_late": 25.0},
        "without_worst": 10.0,
        "vs_control": {"mean": 20.0, "p": 0.01},
    }
    base.update(kw)
    return base


HOLD = {"n": 200, "mean": 30.0, "control_mean": 10.0, "vs_control": 20.0}


@pytest.mark.parametrize(
    ("broken", "condition"),
    [
        ({"halves": [30.0, -1.0]}, "each_half_positive"),
        ({"halves": [-1.0, 30.0]}, "each_half_positive"),
        ({"stress": {"halfway": -1.0, "fees15": 30.0, "exit_late": 25.0}}, "positive_halfway_fills"),
        ({"stress": {"halfway": 20.0, "fees15": -1.0, "exit_late": 25.0}}, "positive_1_5x_fees"),
        ({"stress": {"halfway": 20.0, "fees15": 30.0, "exit_late": -1.0}}, "positive_exit_a_minute_later"),
        ({"without_worst": -2.0}, "positive_worst_weeks_removed"),
    ],
)
def test_each_design_condition_fails_the_arm_when_its_invariant_is_broken(broken, condition):
    word, cond = b35.decide("delta-shortstop", "ungated", fig(**broken), HOLD, True, True)
    assert word == "FAIL" and cond[condition] is False
    assert b35.decide("delta-shortstop", "ungated", fig(), HOLD, True, True)[0] == "PASS"


def test_holm_and_the_beat_control_pairing_each_veto_a_positive_mean():
    assert b35.decide("delta", "ungated", fig(), HOLD, False, True)[0] == "FAIL"  # not rejected by Holm
    assert b35.decide("delta", "ungated", fig(vs_control={"mean": 20.0, "p": 0.2}), HOLD, True, True)[
        0
    ].startswith("PAYS")
    assert b35.decide("delta", "ungated", fig(vs_control={"mean": -3.0, "p": 0.001}), HOLD, True, True)[
        0
    ].startswith("PAYS")
    assert (
        b35.decide("delta", "ungated", fig(), {**HOLD, "vs_control": -1.0}, True, True)[0] == "FAIL"
    )  # holdout under control
    assert (
        b35.decide(
            "control", "ungated", {k: v for k, v in fig().items() if k != "vs_control"}, HOLD, True, True
        )[0]
        == "PASS"
    )
    assert (
        b35.decide("delta", "ungated", fig(), {**HOLD, "n": 100}, True, True)[0] == "NO VERDICT"
    )  # holdout thin (150)
    assert b35.decide("delta", "gated", fig(n=249), HOLD, True, True)[0] == "NO VERDICT"
    assert b35.decide("delta", "ungated", fig(fire_weeks=39), HOLD, True, True)[0] == "NO VERDICT"


def test_the_holm_bar_for_twelve_tests_is_one_twelfth_of_five_percent():
    from cherrypick.core import stats

    p = {f"t{i}": 1.0 for i in range(11)}
    assert stats.holm({**p, "x": 0.004}, 0.05)["x"] is True
    assert stats.holm({**p, "x": 0.0045}, 0.05)["x"] is False  # 0.05 / 12 = 0.00417


def test_the_week_clustered_pairing_is_unconditional_over_every_entry_not_just_fired_ones():
    control = week_rows([10.0] * 100, arm_fired=False)
    arm = week_rows([10.0] * 100, arm_fired=True)
    for r in arm[:5]:
        r["net"] = {k: 60.0 for k in r["net"]}  # five fires that gain 50 each, the other 95 entries identical
    diff = b35.paired(arm, control)
    assert len(diff) == 100 and b35.mean_of(diff) == pytest.approx(2.5)  # diluted over every entry, not 50
    assert sum(1 for _, x in diff if x != 0) == 5


def test_year_and_removal_cuts_and_the_minimum_detectable_effect_are_reported():
    rows = week_rows([20.0 if i % 2 else -10.0 for i in range(200)])
    d = b35.describe(rows)
    assert (
        d["n"] == 200
        and d["win_rate"] == 0.5
        and d["worst_trade"] == -10.0
        and len(d["five_largest_losses"]) == 5
    )
    assert set(d["by_year"]) == {"2022", "2023"}
    later = [r["net"]["verdict"] for r in rows if r["entry"][:4] != "2022"]
    assert d["mean_without_2022"] == pytest.approx(sum(later) / len(later))
    assert d["mean_without_2025"] == pytest.approx(d["mean"], abs=0.01)  # no 2025 rows: nothing removed
    mde = b35.minimum_detectable(rows)
    assert mde["weeks"] == 40 and mde["mde_per_trade"] > 0


# ------------------------------------------------------------------------------------------------
# The paper checks.


def paper_row(day, credit, *, arm="control", body=4935.0, exp=EXP, fired=None):
    return {"entry_session": day, "arm": arm, "expiration": exp, "body_strike": body, "near_strike": body + 5,
            "far_strike": body - 10, "entry_credit": credit, "addon_fired_at": fired, "addon_missed_at": None,
            "entry_vix9d": 14.0, "entry_vix": 15.0}  # fmt: skip


def replay_rec(day, credit, *, body=4935.0, status="entered", reason=None):
    plan = {"body_strike": body, "near_strike": body + 5, "far_strike": body - 10, "credit": credit}
    rec = {"entry": day, "exp": EXP, "status": status, "reason": reason}
    return rec | ({"plan": plan, "arms": {a: {0: None} for a in b35.ARMS}} if status == "entered" else {})


def test_the_entry_check_needs_ninety_percent_of_paper_entries_to_match_within_a_tick():
    days = [f"2024-01-{d:02d}" for d in range(2, 12)]
    records = {d: replay_rec(d, 0.80) for d in days}
    paper = [paper_row(d, 0.83) for d in days]
    ok = b35.validate_entries(records, paper)
    assert ok["passed"] and ok["share"] == 1.0
    records[days[0]] = replay_rec(days[0], 0.80, body=4930.0)  # one strike differs: 9/10 still passes
    assert b35.validate_entries(records, paper)["passed"]
    records[days[1]] = replay_rec(days[1], 0.95)  # a credit 12 cents off: 8/10 fails
    bad = b35.validate_entries(records, paper)
    assert not bad["passed"] and bad["matched"] == 8 and len(bad["misses"]) == 2
    records[days[2]] = replay_rec(days[2], None, status="dropped", reason="no_atm_quote")
    assert b35.validate_entries(records, paper)["matched"] == 7  # a drop is a miss, not an excuse


def test_paper_dates_the_store_does_not_cover_are_left_out_of_the_entry_check():
    records = {
        "2024-01-02": replay_rec("2024-01-02", 0.8),
        "2024-01-03": replay_rec("2024-01-03", None, status="dropped", reason="not_in_store"),
    }
    paper = [paper_row("2024-01-02", 0.8), paper_row("2024-01-03", 0.8), paper_row("2024-01-04", 0.8)]
    out = b35.validate_entries(records, paper)
    assert out["overlapping"] == 1 and out["passed"]
    assert not b35.validate_entries({}, paper)["passed"]  # nothing to compare is not a pass


def test_fire_agreement_is_reported_by_arm_and_skips_a_missed_addon():
    rec = replay_rec("2024-01-02", 0.8)
    rec["arms"]["delta"][0] = {"day": "2024-01-03"}
    paper = [
        paper_row("2024-01-02", 0.8, arm="delta", fired="2024-01-03T11:00:00-05:00"),
        paper_row("2024-01-02", 0.8, arm="bounce"),
        paper_row("2024-01-02", 0.8, arm="flip", fired="2024-01-04T11:00:00-05:00")
        | {"addon_missed_at": "x"},
    ]
    out = b35.fire_agreement({"2024-01-02": rec}, paper)
    assert out["delta"] == {"n": 1, "agree": 1, "both": 1, "same_day": 1}
    assert out["bounce"]["agree"] == 1 and "flip" not in out


def test_the_flip_check_needs_enough_ticks_within_five_points_on_eighty_percent():
    good = [(5000.0 + (i % 3), 5001.0) for i in range(600)]
    assert b35.validate_flip(good)["passed"]
    off = good[:400] + [(5000.0, 5020.0)] * 200  # a third wrong: 67% < 80%
    assert not b35.validate_flip(off)["passed"]
    missing = good[:400] + [(5000.0, None)] * 200  # no recomputed flip counts against it
    assert not b35.validate_flip(missing)["passed"]
    assert not b35.validate_flip(good[:100])["passed"]  # too few ticks to say


def test_the_gate_agreement_compares_the_open_reading_with_the_ticks():
    out = b35.gate_agreement([(14.0, 15.0, 14.5, 15.0), (16.0, 15.0, 14.0, 15.0), (14.0, 15.0, None, None)])
    assert out == {"n": 2, "agree": 1, "share": 0.5}


# ------------------------------------------------------------------------------------------------
# The rows, the counts and the report.


class FakeInputs:
    def __init__(self, opens, closes):
        self.opens, self.closes = opens, closes

    gate = b35.Inputs.gate
    prior_close = b35.Inputs.prior_close


def test_gated_arms_count_a_refused_day_once_as_the_gate_and_never_attempt_the_entry():
    ctl = {a: {0: None, 1: None} for a in b35.ARMS}
    ok = replay_rec("2024-01-08", 0.8) | {"exp": EXP}
    ok["plan"] |= {
        "quotes": quotes((3.0, 3.2), (1.6, 1.8), (1.6, 1.8), (0.9, 1.1)),
        "spot": 5000.0,
        "expected_move": 65.0,
    }
    ok["arms"] = ctl
    refused = replay_rec("2024-01-09", None, status="dropped", reason="leg_unquoted")
    inp = FakeInputs(
        {"VIX9D": {"2024-01-08": 14.0, "2024-01-09": 16.0}, "VIX": {"2024-01-08": 15.0, "2024-01-09": 15.0}},
        {"SPX": {EXP: 5000.0}, "VIX9D": {}, "VIX": {}},
    )
    rows, counts = b35.rows_from_records(inp, {"2024-01-08": ok, "2024-01-09": refused})
    assert len(rows["control"]) == 1 and rows["control"][0]["gate"] is True
    assert counts["ungated"]["delta"] == {"entered": {"2024": 1}, "leg_unquoted": {"2024": 1}}
    assert counts["gated"]["delta"] == {
        "entered": {"2024": 1},
        "regime_inverted": {"2024": 1},
    }  # the drop is not counted
    # a position with no settlement print is dropped for every arm
    inp.closes["SPX"] = {}
    rows, counts = b35.rows_from_records(inp, {"2024-01-08": ok})
    assert rows["control"] == [] and counts["ungated"]["control"] == {"no_settlement": {"2024": 1}}


def test_the_report_and_the_verdict_file_read_the_way_the_studies_board_reads_them():
    result = b35.build_result(full_rows(bump_for={"delta": 25.0}, holdout_for={"delta": 90.0}))
    result["window"] = "design 2022-05-13..2024-12-31; holdout 2025-01-02..2026-08-21"
    text = b35.report(result)
    assert text.startswith("# Item 35") and text.count("\n| ") > 30
    assert "pays, not for the add-on" in text and "| delta-shortstop |" in text
    orch = pytest.importorskip("cherrypick.orchestrator.hypotheses")
    read = orch.verdicts(result)
    assert len(read) == 12 and {r["arm"] for r in read} == {f"{a}/{g}" for a in b35.ARMS for g in b35.GATES}
    assert {r["arm"]: r["verdict"] for r in read}["delta/ungated"] == "pass"


def test_a_whole_expiry_runs_from_the_store_rows_to_a_settled_position(tmp_path):
    data = build(falling_then_back(low=4930.0, back=4930.0))
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE q (expiration TEXT, day TEXT, ts INTEGER, right TEXT, strike REAL, bid REAL, ask REAL, delta REAL, iv REAL, spot REAL)"
    )
    for m in data.minutes:
        for k, (bid, ask, delta, iv) in m.puts.items():
            conn.execute(
                "INSERT INTO q VALUES (?,?,?,?,?,?,?,?,?,?)",
                (EXP, m.day, m.ts, "P", k, bid, ask, delta, iv, m.spot),
            )
    for ts, strip in data.calls.items():
        for k, (bid, ask) in strip.items():
            conn.execute(
                "INSERT INTO q VALUES (?,?,?,?,?,?,?,?,?,?)",
                (EXP, ENTRY, ts, "C", k, bid, ask, None, None, 5000.0),
            )
    loaded = b35.load_expiry(conn, EXP)
    rec = b35.simulate_entry(loaded, ENTRY, EXP, 4)
    assert rec["status"] == "entered" and rec["arms"]["delta"][0] and rec["arms"]["bounce"][0] is None
    nets = b35.trade_nets(rec, "delta-shortstop", 0, 4930.0)
    assert set(nets) >= {"verdict", "halfway", "fees15", "natural", "live", "exit_late"}
    assert b35.trade_nets(rec, "control", 0, 4930.0)["verdict"] != nets["verdict"]


def test_the_live_pilot_slice_is_bounce_gated_tuesday_and_not_quarter_end_and_the_prior_close_gate_is_beside_it():
    rows = full_rows()
    for i, r in enumerate(rows["bounce"]):
        r["tue"] = i % 5 == 1
        r["quarter_end"] = i % 25 == 1
        r["gate"] = i % 2 == 0
        r["gate_prior"] = i % 4 == 0
    rep = b35.build_result(rows)["reported"]
    design = [r for r in rows["bounce"] if r["entry"] < "2025-01-01"]
    want = [r for r in design if r["tue"] and not r["quarter_end"] and r["gate"]]
    assert rep["live_pilot"]["design"]["n"] == len(want) > 0
    assert rep["prior_close_gate"]["bounce"]["design"]["n"] == sum(1 for r in design if r["gate_prior"])
    assert set(rep["versus_delta"]) >= {"delta-dte3/ungated/design", "delta-shortstop/gated/holdout"}


def test_the_study_stops_before_any_outcome_when_the_replay_does_not_match_paper(tmp_path, monkeypatch):
    out = tmp_path / "item35"
    out.mkdir()
    monkeypatch.setattr(b35, "open_inputs", lambda: object())
    monkeypatch.setattr(b35, "out_dir", lambda: out)
    monkeypatch.setattr(
        b35, "run_validation", lambda inp, log=print: {"entries": {"passed": False}, "flip": {"passed": True}}
    )
    monkeypatch.setattr(
        b35, "simulate_all", lambda *a, **k: pytest.fail("an outcome was computed before validation passed")
    )
    args = type("A", (), {"json": None})()
    assert b35.cmd_study(args) == 2
    assert (out / "validation.json").exists() and not (out / "results.json").exists()


def test_the_plan_writes_the_two_request_files_and_counts_them(tmp_path, capsys):
    pytest.importorskip("cherrypick.backtest")
    assert b35.main(["plan", "--out", str(tmp_path)]) == 0
    import json

    printed = json.loads(capsys.readouterr().out)
    assert printed["oi_sessions"] > 1000 and printed["entries_by_window"]["design"] > 600
    requests = json.loads((tmp_path / "addon_requests.json").read_text())["requests"]
    assert {"expiration", "first", "last"} == set(requests[0])  # an empty store: every expiry is asked for
    plan = json.loads((tmp_path / "oi_plan.json").read_text())["sessions"]
    assert plan["2022-05-13"] == ["SPXW"] and "2026-10-16" in plan
