"""The engine on invented markets, one arm at a time. Each blocking test is PAIRED with the same market
made passable, so a gate is shown both to fire and to be the reason (a check that cannot fire reads as
coverage it is not)."""

from __future__ import annotations

import copy
from datetime import date

import pytest
from market import ALL_TIME, CALENDAR, spx_snapshot, trail

from cherrypick.ensemble import arms, engine, pricing
from cherrypick.ensemble.events import EventCalendar
from cherrypick.ensemble.model import OpenPosition

A = arms.load_arms()
MON, TUE, WED = "2026-11-16", "2026-11-17", "2026-11-18"


def why(arm_name, snap, arm=None):
    d = engine.decide(arm or A[arm_name], snap)
    return d.reason, d


def mutate(name, **changes):
    a = copy.deepcopy(A[name])
    a.update(changes)
    return a


# --- the shipped arms are well formed ---------------------------------------------------------------
def test_every_shipped_arm_validates_and_the_validator_can_fail():
    assert set(A) == {
        "iron-fly-1000",
        "iron-condor-1345",
        "put-spread-1300",
        "iron-fly-1dte-1330",
        "gld-call-spread-20d",
        "long-put-1030",
        "gld-call-spread-1dte",
        "orb-put-spread",
    }
    assert all(arms.validate(a) == [] for a in A.values())
    bad = copy.deepcopy(A["iron-fly-1000"])
    bad["typo_key"] = 1
    bad["legs"][1]["strike"]["leg"] = "not_placed_yet"
    bad["gates"].append({"gate": "no_such_gate"})
    bad["exit"] = {"profit": 1}
    problems = arms.validate(bad)
    assert len(problems) == 4 and any("typo_key" in p for p in problems)
    with pytest.raises(ValueError):
        arms.load_arms({"x": {"name": "x", "symbol": "SPX"}})


def test_a_new_arm_is_a_config_entry():
    new = copy.deepcopy(A["long-put-1030"])
    new["name"] = "long-put-1100"
    new["entry"] = {"kind": "time", "at": "11:00", "window": 5}
    loaded = arms.load_arms({"long-put-1100": new})
    snap = spx_snapshot(session=WED, minute=660, spot=6010.0, prev_close=6000.0, vix=15.0)
    assert engine.decide(loaded["long-put-1100"], snap).enter


# --- iron-fly-1000 ----------------------------------------------------------------------------------
def fly_snap(**kw):
    return spx_snapshot(**kw)


def test_iron_fly_enters_at_the_previous_close_with_ten_point_wings():
    d = engine.decide(A["iron-fly-1000"], fly_snap())
    assert d.enter, d.checks
    e = d.entry
    strikes = {g.name: g.strike for g in e.legs}
    assert strikes == {"short_put": 6000.0, "short_call": 6000.0, "long_put": 5990.0, "long_call": 6010.0}
    assert e.quantity >= 1 and e.fees_open > 0
    assert e.prices["mid"] > 0 and e.max_loss == pytest.approx(10 - e.prices["mid"])


def test_iron_fly_gates_each_block_and_each_is_the_reason():
    ok = fly_snap()
    assert engine.decide(A["iron-fly-1000"], ok).enter
    cases = {
        "weekday": fly_snap(session=TUE),
        "entry_not_due": fly_snap(minute=590),
        "gate:sd_change": fly_snap(spot=6200.0, prev_close=6000.0),
        "gate:expected_move_pct": fly_snap(vol=0.08),
        "gate:bid_ask": fly_snap(half_spread=1.2),
    }
    for reason, snap in cases.items():
        assert why("iron-fly-1000", snap)[0] == reason, reason


def test_event_skips_and_the_unknown_pass_with_a_flag():
    on_pmi = fly_snap(session="2026-11-04")  # a Wednesday, the third trading day: services PMI
    assert why("iron-fly-1000", on_pmi)[0] == "event:PMI-Svcs"
    sentiment = EventCalendar(data={"Sentiment": [MON]}, covered={"Sentiment": ALL_TIME})
    s = fly_snap()
    s.events = sentiment
    assert why("iron-fly-1000", s)[0] == "event:Sentiment"
    s.events = EventCalendar()  # knows nothing about sentiment: the gate passes and says so
    d = engine.decide(A["iron-fly-1000"], s)
    assert d.enter
    assert ("event:Sentiment", True, "unknown") in d.checks


def test_mid_price_cap_blocks_a_credit_above_it():
    arm = mutate("iron-fly-1000", gates=[{"gate": "mid_price", "max": 0.50}])
    assert why("iron-fly-1000", fly_snap(), arm)[0] == "gate:mid_price"
    arm = mutate("iron-fly-1000", gates=[{"gate": "mid_price", "max": 9.65}])
    assert engine.decide(arm, fly_snap()).enter


def test_sizing_up_to_a_budget_with_a_minimum_of_one():
    assert engine.size({"max_risk": 250, "minimum": 1}, 3.5) == 1  # 350 > 250 but the minimum holds
    assert engine.size({"max_risk": 250}, 3.5) == 0
    assert engine.size({"max_risk": 1000}, 3.0) == 3
    assert engine.size({"contracts": 2}, 99.0) == 2


def test_one_entry_a_day_and_the_open_limit():
    assert why("iron-fly-1000", fly_snap(entries={"iron-fly-1000": [MON]}))[0] == "entered_today"
    held = [OpenPosition("iron-fly-1000", "2026-11-13", MON)]
    assert why("iron-fly-1000", fly_snap(open_positions=held))[0] == "max_open"


# --- iron-condor-1345 -------------------------------------------------------------------------------
def condor_snap(**kw):
    kw.setdefault("minute", 825)
    kw.setdefault("tr", trail(end=kw["minute"]))
    kw.setdefault("expiries", {MON: 2.25 / 8760})
    return spx_snapshot(**kw)


def test_iron_condor_geometry_and_its_three_gates():
    d = engine.decide(A["iron-condor-1345"], condor_snap())
    assert d.enter, d.checks
    s = {g.name: g.strike for g in d.entry.legs}
    assert s == {"short_put": 5995.0, "long_put": 5990.0, "short_call": 6005.0, "long_call": 6010.0}
    assert d.entry.quantity == 2
    assert why("iron-condor-1345", condor_snap(vix=25.0))[0] == "gate:vix_level"
    assert why("iron-condor-1345", condor_snap(closes=[6100.0] * 40))[0] == "gate:above_ma"
    assert why("iron-condor-1345", condor_snap(session=WED))[0] == "weekday"
    wide = mutate("iron-condor-1345", gates=[{"gate": "reward_risk", "min": 100_000}])
    assert why("iron-condor-1345", condor_snap(), wide)[0] == "gate:reward_risk"


# --- put-spread-1300 and orb-put-spread -------------------------------------------------------------
def spread_snap(**kw):
    kw.setdefault("minute", 780)
    # rising on average with wobble: RSI under 70, the close in the upper part of its range
    kw.setdefault(
        "tr", [(m, 5990.0 + (m - 570) * 0.02 + (3 if (m // 5) % 2 else 0)) for m in range(570, 781)]
    )
    kw.setdefault("expiries", {MON: 3.0 / 8760})
    kw.setdefault("oi_call", 3000)
    kw.setdefault("spot", 6000.0)
    return spx_snapshot(**kw)


def test_put_spread_1300_passes_and_each_gate_is_the_reason():
    d = engine.decide(A["put-spread-1300"], spread_snap())
    assert d.enter, d.checks
    assert [g.strike for g in d.entry.legs] == [5995.0, 5985.0]
    assert why("put-spread-1300", spread_snap(oi_call=1000, oi_put=3000))[0] == "gate:gex_ratio"
    assert why("put-spread-1300", spread_snap(session=WED))[0] == "weekday"
    ups = [(m, 5900.0 + (m - 570) * 0.5) for m in range(570, 781)]
    assert why("put-spread-1300", spread_snap(tr=ups))[0] == "gate:rsi"
    downs = [(m, 6100.0 - (m - 570) * 0.5 + (m % 2)) for m in range(570, 781)]
    r, _ = why("put-spread-1300", spread_snap(tr=downs))
    assert r == "gate:stoch"
    assert why("put-spread-1300", spread_snap(spot=6150.0, prev_close=6000.0))[0] == "gate:sd_change"


def test_put_spread_defers_to_the_opening_range_arm_only_through_the_tree_gate():
    held = spread_snap(entries={"orb-put-spread": [MON]})
    assert why("put-spread-1300", held)[0] == "gate:no_position_today"
    stated = mutate(
        "put-spread-1300",
        gates=[g for g in A["put-spread-1300"]["gates"] if g["gate"] != "no_position_today"],
    )
    assert engine.decide(stated, held).enter


def orb_trail(breakout_at=640):
    out = []
    for m in range(570, 700):
        s = 6000.0 + (4 if m == 580 else 0)  # the range's high is 6004
        if m >= breakout_at:
            s = 6008.0
        out.append((m, s))
    return out


def test_orb_enters_once_at_the_breakout_minute():
    arm = A["orb-put-spread"]
    at = spx_snapshot(minute=640, tr=[p for p in orb_trail() if p[0] <= 640], spot=6008.0, prev_close=6000.0)
    d = engine.decide(arm, at)
    assert d.enter, d.checks
    assert d.entry.legs[0].strike - d.entry.legs[1].strike == 20
    later = spx_snapshot(
        minute=641, tr=[p for p in orb_trail() if p[0] <= 641], spot=6008.0, prev_close=6000.0
    )
    assert engine.decide(arm, later).reason == "entry_not_due"  # single shot
    retry = mutate("orb-put-spread", entry={**arm["entry"], "single_shot": False})
    assert engine.decide(retry, later).enter
    before = spx_snapshot(minute=620, tr=[p for p in orb_trail() if p[0] <= 620])
    assert engine.decide(arm, before).reason == "entry_not_due"  # no break yet
    past = spx_snapshot(minute=800, tr=[(m, 6008.0 if m >= 640 else 6000.0) for m in range(570, 801)])
    assert engine.decide(arm, past).reason == "entry_not_due"  # after 13:00 nothing
    skipped = spx_snapshot(session="2026-11-06", minute=640, tr=[p for p in orb_trail() if p[0] <= 640])
    assert engine.decide(arm, skipped).reason == "event:FirstWeekly"


def test_orb_gex_gates_block():
    arm = A["orb-put-spread"]
    tr = [p for p in orb_trail() if p[0] <= 640]
    put_heavy = spx_snapshot(minute=640, tr=tr, spot=6008.0, oi_call=100, oi_put=3000)
    assert engine.decide(arm, put_heavy).reason == "gate:gex_ratio"
    assert engine.decide(arm, spx_snapshot(minute=640, tr=tr, spot=6008.0)).enter
    thin = spx_snapshot(minute=640, tr=tr, spot=6008.0, oi_put=1)  # almost no put gex above spot
    assert engine.decide(arm, thin).reason == "gate:gex_put_total"


# --- iron-fly-1dte-1330 -----------------------------------------------------------------------------
def dte1_snap(**kw):
    kw.setdefault("session", MON)
    kw.setdefault("minute", 810)
    kw.setdefault("vol", 0.12)
    kw.setdefault("expiries", {TUE: 1.15 / 365})
    kw.setdefault("oi_call", 2000)
    kw.setdefault("tr", trail(end=kw["minute"]))
    return spx_snapshot(**kw)


def test_one_day_fly_sits_on_the_gex_strike_with_a_matched_call_wing():
    d = engine.decide(A["iron-fly-1dte-1330"], dte1_snap())
    assert d.enter, d.checks
    s = {g.name: g.strike for g in d.entry.legs}
    assert s["short_put"] == s["short_call"] == 6000.0
    assert s["long_put"] <= 6000.0 * (1 - 0.005)
    assert s["long_call"] - 6000.0 == pytest.approx(6000.0 - s["long_put"])
    assert d.entry.expiry == TUE


def test_one_day_fly_gates():
    arm = A["iron-fly-1dte-1330"]
    assert (
        why("iron-fly-1dte-1330", dte1_snap(session="2026-11-03", expiries={"2026-11-04": 1.15 / 365}))[0]
        == "event:PMI-Svcs"
    )
    assert why("iron-fly-1dte-1330", dte1_snap(day_open=5900.0))[0] == "gate:gap_pct"  # opened 1.7% below
    assert why("iron-fly-1dte-1330", dte1_snap(closes=[6100.0] * 40))[0] == "gate:above_ma"
    assert engine.decide(arm, dte1_snap()).enter
    # a wall 0.67% above spot among the 20 nearest strikes (but outside the 6 that place the fly)
    wall = dte1_snap()
    row = next(r for r in wall.gex[TUE] if r["strike"] == 6040.0)
    row["abs_gex"] = 10**15
    assert engine.decide(arm, wall).reason == "gate:gex_max_otm"
    row["abs_gex"] = 0
    assert engine.decide(arm, wall).enter


# --- GLD debit spreads ------------------------------------------------------------------------------
def gld(session, expiries, minute=750, vol=0.16, spot=250.0, **kw):
    return spx_snapshot(
        session=session, minute=minute, symbol="GLD", spot=spot, prev_close=249.0, day_open=249.5,
        vol=vol, expiries=expiries, step=1.0, width=40.0, half_spread=0.02, vix=None, vix_prev_close=None,
        tr=trail(end=minute, base=spot), **kw,
    )  # fmt: skip


def test_gld_20d_picks_the_listed_expiry_within_tolerance_and_spaces_entries():
    exp = {"2026-12-04": 18 / 365, "2026-12-11": 25 / 365}
    d = engine.decide(A["gld-call-spread-20d"], gld(MON, exp))
    assert d.enter, d.checks
    assert d.entry.expiry == "2026-12-04"
    long_call, short_call = d.entry.legs
    assert short_call.strike - long_call.strike == 15 and d.entry.prices["mid"] < 0  # a debit
    assert d.entry.quantity == 2
    assert (
        why("gld-call-spread-20d", gld(MON, exp, entries={"gld-call-spread-20d": ["2026-11-10"]}))[0]
        == "every_days"
    )
    assert engine.decide(
        A["gld-call-spread-20d"], gld(MON, exp, entries={"gld-call-spread-20d": ["2026-11-01"]})
    ).enter
    near = {"2026-11-27": 11 / 365}
    assert why("gld-call-spread-20d", gld(MON, near))[0] == "no_expiry"
    held = [OpenPosition("gld-call-spread-20d", "2026-11-12", "2026-12-04")]
    assert why("gld-call-spread-20d", gld(MON, exp, open_positions=held))[0] == "one_per_expiry"


def test_gld_1dte_change_band_and_skips():
    exp = {TUE: 1.1 / 365}
    # a volatile day, so the call ten dollars up still has a bid (a zero bid cannot be sold: "unpriced")
    d = engine.decide(A["gld-call-spread-1dte"], gld(MON, exp, minute=740, vol=0.5))
    assert d.enter, d.checks
    assert engine.decide(A["gld-call-spread-1dte"], gld(MON, exp, minute=740)).reason == "unpriced"
    assert d.entry.quantity == 3 and d.entry.legs[1].strike - d.entry.legs[0].strike == 10
    down = gld(MON, exp, minute=740, vol=0.5, spot=248.0)
    assert engine.decide(A["gld-call-spread-1dte"], down).reason == "gate:change_pct"
    cpi = copy.deepcopy(CALENDAR)
    cpi.data["CPI"] = {TUE}
    s = gld(MON, exp, minute=740, vol=0.5)
    s.events = cpi
    assert engine.decide(A["gld-call-spread-1dte"], s).reason == "event:CPI"  # through the expiry
    s2 = gld("2026-12-30", {"2026-12-31": 1.1 / 365}, minute=740, vol=0.5)
    assert engine.decide(A["gld-call-spread-1dte"], s2).reason == "event:QuarterEnd"


# --- long-put-1030 ----------------------------------------------------------------------------------
def test_long_put_buys_the_ten_delta_or_less_and_sizes_to_the_budget():
    snap = spx_snapshot(
        session=WED, minute=630, spot=6010.0, prev_close=6000.0, vix=15.0, vix_prev_close=16.0
    )
    d = engine.decide(A["long-put-1030"], snap)
    assert d.enter, d.checks
    (leg,) = d.entry.legs
    q = snap.chains[WED].get(leg.strike, "P")
    assert abs(q["delta"]) <= 0.10
    assert d.entry.quantity == max(1, int(300 // (d.entry.max_loss * 100)))
    assert (
        why("long-put-1030", spx_snapshot(session=WED, minute=630, spot=5990.0, prev_close=6000.0, vix=15.0))[
            0
        ]
        == "gate:change_pct"
    )
    assert (
        why("long-put-1030", spx_snapshot(session=WED, minute=630, spot=6010.0, prev_close=6000.0, vix=17.0))[
            0
        ]
        == "gate:vix_change_pct"
    )
    cpi = EventCalendar(
        data={"CPI": [WED], "Sentiment": []}, covered={"CPI": ALL_TIME, "Sentiment": ALL_TIME}
    )
    s = spx_snapshot(session=WED, minute=630, spot=6010.0, prev_close=6000.0, vix=15.0)
    s.events = cpi
    assert engine.decide(A["long-put-1030"], s).reason == "event:CPI"


# --- holding, exits and the money -------------------------------------------------------------------
def test_orb_touch_exit_and_money_identities():
    arm = A["orb-put-spread"]
    tr = [p for p in orb_trail() if p[0] <= 640]
    entry = engine.decide(arm, spx_snapshot(minute=640, tr=tr, spot=6008.0, prev_close=6000.0)).entry
    pos = engine.Position(entry, arm)
    near = spx_snapshot(minute=700, spot=5999.0, tr=trail(end=700, base=5999.0))
    assert engine.manage(pos, near) is None
    hit = spx_snapshot(minute=700, spot=5997.9, tr=trail(end=700, base=5997.9))
    assert engine.manage(pos, hit)[0] == "touch"
    trade = engine.close_position(pos, "touch", hit)
    assert trade is not None
    mult = 100 * trade["quantity"]
    assert trade["gross"] == pytest.approx((entry.prices["mid"] + trade["exit_mid"]) * mult, abs=0.01)
    for kind in pricing.KINDS:
        assert trade["net"][kind] == pytest.approx(
            trade["gross"] - trade["fees"] - trade["settlement"] - trade["slippage"][kind], abs=0.01
        )
    assert trade["slippage"]["mid"] == 0 and trade["slippage"]["verdict"] > 0
    assert trade["net"]["mid"] >= trade["net"]["stress"] or trade["slippage"]["stress"] == 0
    assert trade["fees"] > 0 and trade["settlement"] == 0


def test_settlement_pays_intrinsic_and_charges_each_itm_leg_once():
    arm = A["orb-put-spread"]
    tr = [p for p in orb_trail() if p[0] <= 640]
    entry = engine.decide(arm, spx_snapshot(minute=640, tr=tr, spot=6008.0, prev_close=6000.0)).entry
    pos = engine.Position(entry, arm)
    short = entry.legs[0].strike
    full_loss = engine.settle_position(pos, short - 50)  # through both strikes
    assert full_loss["exit_mid"] == pytest.approx(
        -20.0
    )  # the long put's intrinsic less the short's, per share
    assert full_loss["settlement"] == 10.0  # two ITM legs at $5 each
    win = engine.settle_position(pos, short + 50)
    assert win["exit_mid"] == 0 and win["settlement"] == 0
    assert win["gross"] == pytest.approx(entry.prices["mid"] * 100 * entry.quantity, abs=0.01)
    # the long put's intrinsic comes back, the short's goes out: (short - settle) for the short, only
    assert full_loss["gross"] == pytest.approx((entry.prices["mid"] - 20.0) * 100 * entry.quantity, abs=0.01)


def test_fly_profit_target_and_touch():
    arm = A["iron-fly-1dte-1330"]
    entry = engine.decide(arm, dte1_snap()).entry
    pos = engine.Position(entry, arm)
    same = dte1_snap(minute=811)
    assert engine.manage(pos, same) is None
    tomorrow = spx_snapshot(session=TUE, minute=575, vol=0.12, expiries={TUE: 0.2 / 365}, oi_call=2000)
    kind, _ = engine.manage(pos, tomorrow)
    assert kind == "profit_target"
    crashed = dte1_snap(minute=820, spot=5960.0)
    assert engine.manage(pos, crashed)[0] == "touch"  # -0.5% is 5970


def test_time_exit_before_expiry():
    arm = A["gld-call-spread-1dte"]
    entry = engine.decide(arm, gld(MON, {TUE: 1.1 / 365}, minute=740, vol=0.5)).entry
    pos = engine.Position(entry, arm)
    late = gld(TUE, {TUE: 0.05 / 365}, minute=945, vol=0.5)
    assert engine.manage(pos, late)[0] == "time"
    early = gld(TUE, {TUE: 0.05 / 365}, minute=944, vol=0.5)
    assert engine.manage(pos, early) is None
    assert date.fromisoformat(entry.expiry) == date(2026, 11, 17)


def test_payoff_extremes():
    from cherrypick.ensemble.model import Leg

    ps = [Leg("s", "P", -1, 100.0), Leg("l", "P", 1, 90.0)]
    assert pricing.payoff_extremes(ps, 2.0) == (2.0, 8.0)
    lc = [Leg("l", "C", 1, 100.0)]
    assert pricing.payoff_extremes(lc, -3.0) == (None, 3.0)
    naked = [Leg("s", "C", -1, 100.0)]
    assert pricing.payoff_extremes(naked, 3.0) == (None, None)
    lp = [Leg("l", "P", 1, 100.0)]
    assert pricing.payoff_extremes(lp, -3.0) == (97.0, 3.0)


def test_a_store_that_holds_fewer_strikes_than_a_window_caps_it_and_says_so():
    kw = {
        "minute": 780,
        "oi_call": 3000,
        "width": 100.0,
        "tr": [(m, 5990.0 + (m - 570) * 0.02 + (3 if (m // 5) % 2 else 0)) for m in range(570, 781)],
        "expiries": {MON: 3.0 / 8760},
    }
    narrow = spx_snapshot(**kw)  # 41 strikes, the arm asks for 50
    assert engine.decide(A["put-spread-1300"], narrow).reason == "gate:gex_ratio"
    capped = spx_snapshot(**kw, gex_strike_cap=41)
    d = engine.decide(A["put-spread-1300"], capped)
    assert d.enter, d.checks
