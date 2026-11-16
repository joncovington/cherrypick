"""The strike rules' match modes, the rules that place a strike off the open, the expected move, a
standard deviation, the opening range, the entry's level, a premium or a GEX measure, and the `at_least`
and `between` expiry rules. Each rule is shown to move the strike it picks, and each refusal to fire."""

from __future__ import annotations

import copy

import pytest
from market import spx_snapshot

from cherrypick.automations import arms, engine, strikes
from cherrypick.automations.model import Chain

A = arms.load_arms()
MON = "2026-11-16"


def chain_of(quotes: dict, expiry: str = MON) -> Chain:
    """{(strike, right): (bid, ask) or (bid, ask, delta)} as a chain."""
    rows = []
    for (k, right), q in quotes.items():
        row = {"strike": float(k), "right": right, "bid": q[0], "ask": q[1]}
        if len(q) > 2:
            row["delta"] = q[2]
        rows.append(row)
    return Chain(expiry, rows)


def pick(rule, right="P", snap=None, chain=None, chosen=None, fired=None):
    snap = snap or spx_snapshot()
    chain = chain or snap.chains[MON]
    return strikes.pick(rule, right, snap, chain, chosen or {}, fired=fired)


# --- match modes -------------------------------------------------------------------------------------
def test_each_match_mode_places_a_price_target_differently():
    snap = spx_snapshot(spot=6002.0)
    assert pick({"rule": "spot", "match": "nearest"}, snap=snap) == 6000.0
    assert pick({"rule": "spot", "match": "above"}, snap=snap) == 6005.0
    assert pick({"rule": "spot", "match": "below"}, snap=snap) == 6000.0
    assert pick({"rule": "spot", "match": "exact"}, snap=snap) is None  # 6002 is not listed
    assert pick({"rule": "spot", "match": "exact", "points": 3}, snap=snap) == 6005.0
    # The old spelling resolves: up is above, down is below; the default is unchanged.
    assert pick({"rule": "spot", "round": "up"}, snap=snap) == 6005.0
    assert pick({"rule": "spot", "round": "down", "points": 2}, snap=snap) == 6000.0
    assert pick({"rule": "spot"}, snap=snap) == 6000.0


def test_value_rules_match_on_the_value():
    c = chain_of(
        {(5980, "P"): (0.9, 1.1, -0.18), (5990, "P"): (1.4, 1.6, -0.24), (6000, "P"): (2.9, 3.1, -0.33)}
    )
    assert pick({"rule": "delta", "target": 0.25}, chain=c) == 5990.0
    assert pick({"rule": "delta", "target": 0.25, "match": "below"}, chain=c) == 5990.0
    assert pick({"rule": "delta", "target": 0.25, "match": "above"}, chain=c) == 6000.0
    assert pick({"rule": "delta", "target": 0.25, "at_most": True}, chain=c) == 5990.0
    assert pick({"rule": "delta", "target": 0.40, "match": "above"}, chain=c) is None
    # premium: mid 1.0, 1.5, 3.0; bid 0.9, 1.4, 2.9
    assert pick({"rule": "premium", "target": 1.20, "price": "mid", "match": "nearest"}, chain=c) == 5980.0
    assert pick({"rule": "premium", "target": 1.20, "price": "mid", "match": "above"}, chain=c) == 5990.0
    assert pick({"rule": "premium", "target": 1.45, "price": "bid", "match": "below"}, chain=c) == 5990.0
    assert pick({"rule": "premium", "target": 1.45, "price": "ask", "match": "below"}, chain=c) == 5980.0
    assert pick({"rule": "premium", "target": 0.50, "price": "mid", "match": "below"}, chain=c) is None


# --- price references --------------------------------------------------------------------------------
def test_the_open_is_a_reference_beside_spot_and_the_previous_close():
    snap = spx_snapshot(spot=6000.0, prev_close=5990.0, day_open=6011.0)
    assert pick({"rule": "open"}, snap=snap) == 6010.0
    assert pick({"rule": "open", "match": "above"}, snap=snap) == 6015.0
    assert pick({"rule": "prev_close"}, snap=snap) == 5990.0


def test_the_expected_move_and_a_standard_deviation_come_from_the_atm_straddle():
    snap = spx_snapshot(spot=6001.0)
    c = chain_of(
        {
            (6000, "C"): (9.9, 10.1),
            (6000, "P"): (9.9, 10.1),
            **{(k, r): (1.0, 1.2) for k in range(5950, 6060, 5) for r in "CP" if k != 6000},
        }
    )
    assert strikes.expected_move(snap, c) == pytest.approx(20.0)
    assert pick({"rule": "expected_move", "times": -1.0, "match": "nearest"}, snap=snap, chain=c) == 5980.0
    assert pick({"rule": "expected_move", "times": 1.0, "match": "above"}, snap=snap, chain=c) == 6025.0
    one_sd = 20.0 / strikes.STRADDLE_PER_SD  # 25.07 points
    assert pick({"rule": "std_dev", "sd": 1.0, "match": "below"}, snap=snap, chain=c) == 6025.0
    assert pick({"rule": "std_dev", "sd": 1.0, "match": "above"}, snap=snap, chain=c) == 6030.0
    assert 6001.0 + one_sd == pytest.approx(6026.07, abs=0.01)
    # No strike quoted on both rights: no expected move, no trade.
    calls_only = chain_of({(6000, "C"): (9.9, 10.1)})
    assert strikes.expected_move(snap, calls_only) is None
    assert pick({"rule": "std_dev", "sd": 1.0, "match": "nearest"}, snap=snap, chain=calls_only) is None


def test_the_opening_range_places_a_strike_once_it_has_closed():
    tr = [(m, 6000.0 + (12 if m == 575 else 0) - (5 if m == 590 else 0)) for m in range(570, 611)]
    snap = spx_snapshot(minute=610, tr=tr)
    hi = {"rule": "opening_range", "minutes": 30, "level": "high", "match": "above"}
    assert pick(hi, snap=snap) == 6015.0  # high 6012
    assert pick({**hi, "level": "low", "match": "below"}, snap=snap) == 5995.0
    assert pick({**hi, "level": "mid", "match": "nearest"}, snap=snap) == 6005.0  # 6003.5
    assert pick({**hi, "points": 5}, snap=snap) == 6020.0
    early = spx_snapshot(minute=590, tr=[p for p in tr if p[0] <= 590])
    assert pick(hi, snap=early) is None


def test_the_entry_level_is_the_spot_when_the_entry_fired():
    tr = [(m, 6008.0 if m >= 640 else 6000.0) for m in range(570, 661)]
    snap = spx_snapshot(minute=660, spot=6030.0, tr=tr)
    rule = {"rule": "entry_level", "points": -3, "match": "nearest"}
    assert pick(rule, snap=snap, fired=640) == 6005.0  # the trail's 6008 then
    assert pick(rule, snap=snap, fired=660) == 6025.0  # this minute: the snapshot's spot
    assert pick(rule, snap=snap, fired=None) is None
    assert pick(rule, snap=snap, fired=500) is None  # before the trail began

    # Through decide: an opening-range breakout that may enter after the break carries its minute.
    arm = copy.deepcopy(A["orb-put-spread"])
    arm["entry"]["single_shot"] = False
    arm["gates"] = []
    arm["legs"][0]["strike"] = rule
    d = engine.decide(arm, snap)
    assert d.enter, d.checks
    assert d.entry.legs[0].strike == 6005.0 and d.entry.legs[1].strike == 5985.0


def test_gex_measures_each_pick_their_strike():
    snap = spx_snapshot()
    snap.gex[MON] = [
        {
            "strike": 5990.0,
            "net_gex": -50,
            "abs_gex": 50,
            "call_gex": 10,
            "put_gex": -60,
            "call_oi": 5,
            "put_oi": 900,
        },
        {
            "strike": 5995.0,
            "net_gex": -80,
            "abs_gex": 80,
            "call_gex": 5,
            "put_gex": -85,
            "call_oi": 1,
            "put_oi": 300,
        },
        {
            "strike": 6000.0,
            "net_gex": 30,
            "abs_gex": 30,
            "call_gex": 40,
            "put_gex": -10,
            "call_oi": 700,
            "put_oi": 2,
        },
        {
            "strike": 6005.0,
            "net_gex": 70,
            "abs_gex": 70,
            "call_gex": 90,
            "put_gex": -20,
            "call_oi": 400,
            "put_oi": 1,
        },
        {
            "strike": 6050.0,
            "net_gex": 900,
            "abs_gex": 900,
            "call_gex": 950,
            "put_gex": -50,
            "call_oi": 9000,
            "put_oi": 1,
        },
    ]
    g = {"rule": "gex_max", "strikes": 4}
    assert pick({**g, "measure": "net_positive"}, snap=snap) == 6005.0
    assert pick({**g, "measure": "net_negative"}, snap=snap) == 5995.0
    assert pick({**g, "measure": "call_gex"}, snap=snap) == 6005.0
    assert pick({**g, "measure": "put_gex"}, snap=snap) == 5995.0
    assert pick({**g, "measure": "call_oi"}, snap=snap) == 6000.0
    assert pick({**g, "measure": "put_oi"}, snap=snap) == 5990.0
    assert pick({**g, "strikes": 5, "measure": "net_positive"}, snap=snap) == 6050.0  # the window widens
    assert pick({"rule": "max_abs_gex", "strikes": 4}, snap=snap) == 5995.0
    snap.gex[MON] = [r for r in snap.gex[MON] if r["net_gex"] < 0]
    assert pick({**g, "measure": "net_positive"}, snap=snap) is None


# --- expiry rules ------------------------------------------------------------------------------------
def expiry_snap():
    days = {"2026-11-16": 0, "2026-11-20": 4, "2026-11-27": 11, "2026-12-18": 32, "2027-01-15": 60}
    return spx_snapshot(expiries={e: max(n, 0.25) / 365 for e, n in days.items()})


def test_at_least_and_between_name_their_expiry():
    snap = expiry_snap()
    resolve = lambda rule: engine.resolve_expiry({"expiry": rule}, snap)  # noqa: E731
    assert resolve({"kind": "at_least", "days": 5}) == "2026-11-27"
    assert resolve({"kind": "at_least", "days": 4}) == "2026-11-20"
    assert resolve({"kind": "at_least", "days": 61}) is None
    assert resolve({"kind": "between", "min": 10, "max": 40}) == "2026-11-27"
    assert resolve({"kind": "between", "min": 10, "max": 40, "prefer": "last"}) == "2026-12-18"
    assert resolve({"kind": "between", "min": 12, "max": 31}) is None
    # The old aim-at-a-date rules are unchanged.
    assert resolve({"kind": "calendar", "days": 30, "tolerance": 3}) == "2026-12-18"


def test_the_feed_asks_for_the_window_before_expirations_are_listed():
    from datetime import date

    from cherrypick.automations import feed

    today = date(2026, 11, 16)
    got = feed.target_dates({"expiry": {"kind": "between", "min": 2, "max": 4}}, today)
    assert [d.isoformat() for d in got] == ["2026-11-18", "2026-11-19", "2026-11-20"]
    assert (
        len(feed.target_dates({"expiry": {"kind": "at_least", "days": 30}}, today)) == feed.AT_LEAST_REACH + 1
    )


# --- validation --------------------------------------------------------------------------------------
def with_leg(rule, i=0, name="put-spread-1300", **top):
    a = copy.deepcopy(A[name])
    a["legs"][i]["strike"] = rule
    a.update(top)
    return arms.validate(a)


def test_validation_refuses_what_would_silently_change_a_strike():
    assert with_leg({"rule": "std_dev", "sd": -1.0, "match": "below"}) == []
    assert with_leg({"rule": "premium", "target": 1.0, "price": "mid", "match": "above"}) == []
    assert with_leg({"rule": "gex_max", "measure": "put_oi", "strikes": 10}) == []
    # A newer rule states its match; an unknown key, a bad match or a mode a rule cannot take is refused.
    assert any("match: required" in p for p in with_leg({"rule": "expected_move", "times": 1.0}))
    assert any("unknown key 'pts'" in p for p in with_leg({"rule": "spot", "pts": -5}))
    assert any("match" in p for p in with_leg({"rule": "spot", "match": "closest"}))
    assert any("match" in p for p in with_leg({"rule": "delta", "target": 0.3, "match": "exact"}))
    assert any(
        "no match mode" in p for p in with_leg({"rule": "max_abs_gex", "strikes": 6, "match": "nearest"})
    )
    assert any("not both" in p for p in with_leg({"rule": "spot", "points": 1, "pct": 0.1}))
    assert any(
        "price" in p
        for p in with_leg({"rule": "premium", "target": 1.0, "price": "last", "match": "nearest"})
    )
    assert any("measure" in p for p in with_leg({"rule": "gex_max", "measure": "vanna", "strikes": 4}))
    assert any(
        "level" in p
        for p in with_leg({"rule": "opening_range", "minutes": 30, "level": "top", "match": "above"})
    )
    assert any("missing 'sd'" in p for p in with_leg({"rule": "std_dev", "match": "below"}))
    daily = {"kind": "trigger", "trigger": {"daily": True, "signal": {"kind": "gap", "min_pct": 1.0}}}
    assert any(
        "daily trigger" in p for p in with_leg({"rule": "entry_level", "match": "nearest"}, entry=daily)
    )
    bad_width = copy.deepcopy(A["iron-fly-1dte-1330"])
    bad_width["legs"][3]["strike"]["width_of"] = ["short_put", "long_call"]
    assert any("before it is placed" in p for p in arms.validate(bad_width))


def test_validation_refuses_a_malformed_expiry():
    def expiry(rule):
        a = copy.deepcopy(A["gld-call-spread-20d"])
        a["expiry"] = rule
        return arms.validate(a)

    assert expiry({"kind": "between", "min": 30, "max": 45}) == []
    assert expiry({"kind": "at_least", "days": 30}) == []
    assert any("min is after max" in p for p in expiry({"kind": "between", "min": 45, "max": 30}))
    assert any("missing 'max'" in p for p in expiry({"kind": "between", "min": 45}))
    assert any(
        "unknown key 'tolerance'" in p for p in expiry({"kind": "at_least", "days": 3, "tolerance": 2})
    )
    assert any("prefer" in p for p in expiry({"kind": "between", "min": 1, "max": 3, "prefer": "mid"}))
    assert any("whole number" in p for p in expiry({"kind": "dte", "days": 1.5}))
    assert any("kind" in p for p in expiry({"kind": "weeks", "days": 1}))


def test_the_feed_reads_what_a_rule_needs():
    from cherrypick.automations import feed

    base = copy.deepcopy(A["put-spread-1300"])
    base["gates"] = []
    assert not feed.reads(base)["trail"] and not feed.reads(base)["gex"]
    base["legs"][0]["strike"] = {"rule": "opening_range", "minutes": 30, "level": "low", "match": "below"}
    assert feed.reads(base)["trail"]
    base["legs"][0]["strike"] = {"rule": "gex_max", "measure": "put_oi", "strikes": 10}
    assert feed.reads(base)["gex"]
