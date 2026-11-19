"""Named expiries (#1318): a calendar's or a diagonal's far leg sits on `far_expiry`, is placed, priced,
held and closed off its own chain, and a one-expiry automation reads exactly as it did."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from cherrypick.core import bs
from market import spx_snapshot

from cherrypick.automations import (
    arms,
    builderform,
    db,
    driver,
    engine,
    enrolment,
    feed,
    pricing,
    record,
    sketch,
)
from cherrypick.automations.model import Entry, Leg

MON = "2026-11-16"
NEAR, WEEKLY, FAR = "2026-12-18", "2027-01-08", "2027-01-15"
YEAR = 365.0

CAL = {
    "name": "cal",
    "symbol": "SPX",
    "expiry": {"kind": "at_least", "days": 25},
    "far_expiry": {"kind": "after_near", "days": 21, "monthly": True},
    "entry": {"kind": "time", "at": "10:00", "window": 5},
    "weekdays": [0, 1, 2, 3, 4],
    "legs": [
        {
            "name": "L1",
            "action": "STO",
            "qty": 1,
            "side": "put",
            "strike": {"rule": "spot", "points": 0, "match": "nearest"},
        },
        {
            "name": "L2",
            "action": "BTO",
            "qty": 1,
            "side": "put",
            "expiry": "far",
            "strike": {"rule": "same_as", "leg": "L1"},
        },
    ],
    "size": {"contracts": 1},
    "limits": {"max_open": 1, "per_day": 1},
    "exit": {"close_before_expiry_min": 15},
}


def cal(**changes) -> dict:
    a = copy.deepcopy(CAL)
    a.update(changes)
    return a


def loaded(arm: dict) -> dict:
    return arms.load_arms({arm["name"]: arm})[arm["name"]]


def snap(session=MON, minute=600, expiries=None, **kw):
    days = expiries or {NEAR: 32, WEEKLY: 53, FAR: 60}
    return spx_snapshot(session=session, minute=minute, expiries={e: d / YEAR for e, d in days.items()}, **kw)


# --- the check ----------------------------------------------------------------------------------------
def test_a_calendar_is_a_well_formed_automation_and_its_far_leg_keeps_its_expiry():
    assert arms.validate(CAL) == []
    legs = loaded(CAL)["legs"]
    assert "expiry" not in legs[0] and legs[1]["expiry"] == "far"
    assert arms.written_leg(legs[1])["expiry"] == "far" and "expiry" not in arms.written_leg(legs[0])
    near = dict(CAL["legs"][0], expiry="near")
    assert "expiry" not in arms.internal_leg(near)[0]  # near is the default, never stored


@pytest.mark.parametrize(
    "arm,word",
    [
        (cal(far_expiry=None), "far_expiry: required"),
        (cal(exit={}), "close_before_expiry_min"),
        (cal(far_expiry={"kind": "after_near", "days": 0}), "far_expiry.days"),
        (cal(far_expiry={"kind": "after_near", "days": 21, "extra": 1}), "unknown key 'extra'"),
        (cal(far_expiry={"kind": "nope"}), "far_expiry.kind"),
    ],
)
def test_the_check_names_what_a_far_leg_needs(arm, word):
    if arm.get("far_expiry") is None:
        arm.pop("far_expiry")
    assert any(word in p for p in arms.validate(arm)), arms.validate(arm)


def test_a_far_leg_is_bought_one_leg_stays_near_and_far_expiry_needs_a_far_leg():
    sold = cal()
    sold["legs"][1]["action"] = "STO"
    assert any("sold on the far expiry" in p for p in arms.validate(sold))
    all_far = cal()
    all_far["legs"][0]["expiry"] = "far"
    assert any("at least one leg on the near expiry" in p for p in arms.validate(all_far))
    no_leg = cal()
    no_leg["legs"][1].pop("expiry")
    assert any("no leg is on the far expiry" in p for p in arms.validate(no_leg))
    bad = cal()
    bad["legs"][1]["expiry"] = "later"
    assert any("legs[1].expiry: near or far" in p for p in arms.validate(bad))


# --- resolving -------------------------------------------------------------------------------------------
def test_after_near_takes_the_first_listed_expiry_far_enough_after_near():
    s = snap()
    assert engine.resolve_expiry(loaded(CAL), s) == NEAR
    assert engine.resolve_far_expiry(loaded(CAL), s, NEAR) == FAR  # the weekly is not a monthly
    weekly = loaded(cal(far_expiry={"kind": "after_near", "days": 21}))
    assert engine.resolve_far_expiry(weekly, s, NEAR) == WEEKLY
    too_far = loaded(cal(far_expiry={"kind": "after_near", "days": 90}))
    assert engine.resolve_far_expiry(too_far, s, NEAR) is None
    by_days = loaded(cal(far_expiry={"kind": "at_least", "days": 10}))
    assert engine.resolve_far_expiry(by_days, s, NEAR) == WEEKLY  # counted from the session, after near


def test_feed_asks_for_both_expiries():
    from datetime import date

    a = loaded(CAL)
    assert feed.expiries_for(a, date(2026, 11, 16), [NEAR, WEEKLY, FAR]) == [NEAR, FAR]
    unlisted = feed.expiries_for(a, date(2026, 11, 16), [])
    assert NEAR in unlisted and FAR in unlisted


# --- entering -------------------------------------------------------------------------------------------
def test_a_calendar_enters_with_each_leg_priced_off_its_own_chain():
    s = snap()
    d = engine.decide(loaded(CAL), s)
    assert d.enter, d.checks
    e = d.entry
    assert e.expiry == NEAR and [g.expiry for g in e.legs] == [None, FAR]
    assert e.legs[0].strike == e.legs[1].strike == 6000.0
    near_q, far_q = s.chains[NEAR].get(6000.0, "P"), s.chains[FAR].get(6000.0, "P")
    mid = lambda q: (q["bid"] + q["ask"]) / 2  # noqa: E731
    assert e.prices["mid"] == pytest.approx(mid(near_q) - mid(far_q))  # a debit: the far put costs more
    assert e.prices["mid"] < 0
    assert e.max_loss == pytest.approx(-e.prices["mid"]) and e.max_profit is None
    assert ("far_expiry", True, FAR) in d.checks


def test_no_far_chain_is_no_entry_and_says_why():
    d = engine.decide(loaded(CAL), snap(expiries={NEAR: 32}))
    assert not d.enter and d.reason == "no_far_expiry"


def test_a_one_expiry_automation_is_untouched():
    one = cal(far_expiry=None, exit={})
    one.pop("far_expiry")
    one["legs"][1].pop("expiry")
    one["legs"][1]["strike"] = {"rule": "from_leg", "leg": "L1", "points": -10, "match": "nearest"}
    d = engine.decide(loaded(one), snap())
    assert d.enter and all(g.expiry is None for g in d.entry.legs)
    assert all("expiry" not in g for g in d.entry.to_dict()["legs"])
    assert Entry.from_dict(json.loads(json.dumps(d.entry.to_dict()))) == d.entry


# --- holding and closing -----------------------------------------------------------------------------------
def held():
    a = loaded(CAL)
    e = engine.decide(a, snap()).entry
    return a, engine.Position(Entry.from_dict(json.loads(json.dumps(e.to_dict()))), a)


def test_it_closes_before_the_near_expiry_at_both_chains_mids():
    a, pos = held()
    on_expiry = snap(session=NEAR, minute=945, expiries={NEAR: 0.25 / 24, FAR: 28})
    assert engine.manage(pos, on_expiry) == ("time", 945)
    trade = engine.close_position(pos, "time", on_expiry)
    n, f = on_expiry.chains[NEAR].get(6000.0, "P"), on_expiry.chains[FAR].get(6000.0, "P")
    assert trade["exit_mid"] == pytest.approx(
        round(-(n["bid"] + n["ask"]) / 2 + (f["bid"] + f["ask"]) / 2, 4)
    )
    assert trade["legs"][1]["expiry"] == FAR and "expiry" not in trade["legs"][0]
    assert engine.manage(pos, snap(session=NEAR, minute=600, expiries={NEAR: 0.25, FAR: 28})) is None


def test_a_far_leg_with_no_quote_is_no_reading_never_another_expirys_price():
    a, pos = held()
    only_near = snap(session=NEAR, minute=945, expiries={NEAR: 0.25 / 24})
    assert engine.close_position(pos, "time", only_near) is None
    assert engine.held_chain(pos.entry, only_near).for_leg(pos.entry.legs[1]) is None


def test_unclosed_it_settles_at_near_with_the_far_leg_at_intrinsic_and_no_far_fee():
    a, pos = held()
    t = engine.settle_position(pos, 5900.0)  # 100 in the money: the near put's loss, the far put's intrinsic
    assert t["exit_mid"] == pytest.approx(0.0)
    assert t["settlement"] == 5.0  # the near leg only


def test_a_held_positions_rows_sit_on_their_own_expiries():
    pos = {"expiration": NEAR}
    legs = [{"expiration": None}, {"expiration": FAR}]
    rows = [
        {"strike": 6000.0, "right": "P", "bid": 1, "ask": 2},
        {"strike": 6000.0, "right": "P", "bid": 5, "ask": 6},
    ]
    assert feed.by_expiry(pos, legs, rows) == {NEAR: [rows[0]], FAR: [rows[1]]}
    assert feed.by_expiry(pos, [{"expiration": None}] * 2, rows) == rows


# --- pricing ----------------------------------------------------------------------------------------------
def test_bounds_with_far_legs():
    calendar = [Leg("L1", "P", -1, 6000.0), Leg("L2", "P", 1, 6000.0, FAR)]
    assert pricing.payoff_extremes(calendar, -12.0) == (None, 12.0)
    assert pricing.value_bounds(calendar) == (0.0, None)
    diagonal = [Leg("L1", "P", -1, 6000.0), Leg("L2", "P", 1, 5990.0, FAR)]
    assert pricing.payoff_extremes(diagonal, -8.0) == (None, 18.0)  # the debit and the width
    reverse = [Leg("L1", "P", 1, 6000.0), Leg("L2", "P", -1, 6000.0, FAR)]
    assert pricing.payoff_extremes(reverse, 12.0) == (None, None)
    assert pricing.value_bounds(reverse) == (None, None)


# --- the ledger --------------------------------------------------------------------------------------------
def test_the_ledger_keeps_a_far_legs_expiry_and_streams_it(tmp_path):
    conn = db.connect(str(tmp_path / "p.db"))
    pid = "cal:" + MON
    row = {
        "position_id": pid,
        "arm": "cal",
        "symbol": "SPX",
        "entry_session": MON,
        "expiration": NEAR,
        "entry_value": -1200.0,
        "entry_json": "{}",
    }
    legs = [
        {
            "position_id": pid,
            "leg_role": "L1",
            "right": "P",
            "side": -1,
            "strike": 6000.0,
            "expiration": None,
        },
        {"position_id": pid, "leg_role": "L2", "right": "P", "side": 1, "strike": 6000.0, "expiration": FAR},
    ]
    db.open_new_position(
        conn,
        position_row=row,
        leg_rows=legs,
        entry_row={"arm": "cal", "symbol": "SPX", "entry_session": MON, "outcome": "entered"},
    )
    assert db.open_expirations(conn) == {"SPX": [NEAR, FAR]}
    assert [g["expiration"] for g in db.legs(conn, pid)] == [None, FAR]


def test_a_held_calendar_is_managed_off_its_grouped_rows():
    a, pos = held()
    on_expiry = snap(session=NEAR, minute=945, expiries={NEAR: 0.25 / 24, FAR: 28})
    rows = [on_expiry.chains[NEAR].get(6000.0, "P"), on_expiry.chains[FAR].get(6000.0, "P")]
    p = {"expiration": NEAR, "symbol": "SPX"}
    legs = [{"expiration": None}, {"expiration": FAR}]
    when = datetime(2026, 12, 18, 15, 45, tzinfo=ZoneInfo("America/New_York"))
    s = feed.position_snapshot(p, feed.by_expiry(p, legs, rows), 6000.0, when, exit_rules=a["exit"])
    assert engine.manage(pos, s) == ("time", 945)
    assert engine.close_position(pos, "time", s) is not None


def test_the_replay_record_carries_a_far_legs_expiry_only():
    d = engine.decide(loaded(CAL), snap())
    legs = record.decision_summary(d)["entry"]["legs"]
    assert legs[0] == ["L1", "P", -1, 6000.0] and legs[1] == ["L2", "P", 1, 6000.0, FAR]


# --- the modeled payoff -------------------------------------------------------------------------------
def test_the_sketch_draws_a_calendar_as_a_tent_with_its_peak_modeled():
    sk = sketch.sketch(loaded(CAL))
    assert sk["modeled"] is True and sk["top"] == "peak, modeled" and sk["bottom"] == "max loss"
    assert sk["legs"][1]["expiry"] == "far" and "expiry" not in sk["legs"][0]
    ys = [y for _, y in sk["curve"]]
    peak = max(sk["curve"], key=lambda p: p[1])
    assert peak[0] == 0.0 and ys[0] < 0 and ys[-1] < 0  # highest at the shared strike, a debit at the wings


def test_a_one_expiry_sketch_is_not_modeled():
    one = {**loaded(CAL), "legs": [loaded(CAL)["legs"][0]]}
    one.pop("far_expiry")
    assert "modeled" not in sketch.sketch(one)


def placed_calendar():
    a = loaded(CAL)
    s = snap()
    got = engine.structure(a, s)
    return builderform._placed({"ok": True}, got, s, {"kind": "live", "at": "2026-11-16T10:00"}), got, s


def test_the_placed_calendar_values_its_far_leg_at_its_entry_vol():
    out, got, s = placed_calendar()
    assert out["far_expiry"] == FAR and out["legs"][1]["expiry"] == "far"
    leg = out["modeled"]["legs"][0]
    assert leg["name"] == "L2" and leg["days"] == 28 and leg["iv"] > 0
    model = builderform.far_model(got["legs"], got, s)
    at = 6000.0
    far = bs.price(at, 6000.0, 28 / 365, model["L2"]["iv"], "P", 0.0)
    want = got["prices"]["mid"] - max(6000.0 - at, 0) + far
    y = dict((x, v) for x, v in out["curve"])[at]
    assert y == pytest.approx(round(want * 100, 2), abs=0.02)
    assert out["modeled"]["peak"] == pytest.approx(max(v for _, v in out["curve"]))


def test_the_placed_calendar_is_drawn_wide_enough_to_hold_both_breakevens():
    out, _got, _s = placed_calendar()
    low, high = out["breakevens"]
    assert out["curve"][0][1] < 0 < out["modeled"]["peak"] and out["curve"][-1][1] < 0
    assert low < 6000.0 < high


def test_the_modeled_curve_never_goes_below_the_engines_loss_bound():
    out, _got, _s = placed_calendar()
    assert out["max_profit"] is None and out["max_loss"] > 0
    assert min(v for _, v in out["curve"]) >= -out["max_loss"] - 0.01


# --- backtest coverage and replay ---------------------------------------------------------------------
def test_a_far_leg_asks_for_the_far_chain_too():
    got = enrolment.needs(loaded(CAL))
    chains = [n for n in got if n["field"] == "chain"]
    assert chains == [
        {"field": "chain", "expiry": "all", "cadence": "5m"},
        {"field": "chain", "expiry": "monthly", "cadence": "5m", "far": True},
    ]
    assert enrolment.run_chain(loaded(CAL))["far"] is True


def test_a_same_day_calendar_names_the_intraday_store_for_its_run():
    zero = loaded(cal(expiry={"kind": "dte", "days": 0}))
    near, far = (n for n in enrolment.needs(zero) if n["field"] == "chain")
    assert near == {"field": "chain", "expiry": "0dte", "cadence": "1m"} and far["cadence"] == "5m"
    assert enrolment.run_chain(zero) is not None and enrolment.run_chain(zero)["far"] is True


def test_the_far_rule_is_in_the_structure_block_and_one_expiry_hashes_are_unchanged():
    one = {k: v for k, v in CAL.items() if k != "far_expiry"}
    part = {k: one[k] for k in ("symbol", "expiry", "legs")}  # the block's keys before far expiries
    text = json.dumps(part, sort_keys=True, separators=(",", ":"), default=str)
    assert enrolment.block_hash(one, "structure") == hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    assert enrolment.block_hash(CAL, "structure") != enrolment.block_hash(
        cal(far_expiry={"kind": "after_near", "days": 35, "monthly": True}), "structure"
    )


def test_a_held_calendar_is_replayed_off_the_far_chain_it_entered():
    _a, pos = held()
    assert driver.held_expiries(pos.entry) == [NEAR, FAR]
