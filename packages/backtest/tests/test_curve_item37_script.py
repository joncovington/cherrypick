"""Hypothesis 37's study (scripts/curve_item37.py): curve's own rule run on invented end-of-day chains.

Entry gating, each refusal, each arm's exits, the split, the costs and settlement, the pass rule and the
files it writes. Invented chains only, no store. Each rule is shown firing and failing, and each guard is
shown to fail by breaking its invariant on purpose, because a check that cannot fire reads as coverage
it is not.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cherrypick.curve")

from cherrypick.core import bs  # noqa: E402
from cherrypick.core import calendar as cal  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("curve_item37", ROOT / "scripts" / "curve_item37.py")
s37 = importlib.util.module_from_spec(_spec)
sys.modules["curve_item37"] = s37
_spec.loader.exec_module(s37)

JAN2 = date(2024, 1, 2)
FEB16 = date(2024, 2, 16)  # the monthly the 2024-01-02 entry targets (45 days)


def chain(spot=20.0, dte=45, vol=0.8, half=0.02, lo=10, hi=40, half_at=None, drop=()):
    """Black-Scholes calls at one strike a dollar, bid and ask `half` either side of the model price."""
    rows = []
    for k in range(lo, hi + 1):
        if k in drop:
            continue
        px = bs.price(spot, float(k), dte / 365, vol, "C", 0.0)
        h = half_at(k) if half_at else half
        rows.append((float(k), "C", round(max(px - h, 0.0), 2), round(px + h, 2)))
    return rows


class World:
    """Sessions plus a chain per (session, expiry), all overridable day by day."""

    def __init__(self, start, end, spot=20.0, ratio=0.9, **chain_kw):
        self.days = s37.trading_days(start, end)
        self.spot = spot if callable(spot) else (lambda d: spot)
        self.ratio = ratio if callable(ratio) else (lambda d: ratio)
        self.kw = chain_kw
        self.override = {}  # (day, expiry) -> rows
        self.kw_by_day = {}  # day -> chain kwargs
        self.opens = {}
        self.sessions = [
            s37.Session(d, self.spot(d), self.opens.get(d, self.spot(d)), self.ratio(d)) for d in self.days
        ]

    def rebuild(self):
        self.sessions = [
            s37.Session(d, self.spot(d), self.opens.get(d, self.spot(d)), self.ratio(d)) for d in self.days
        ]

    def chain_at(self, d, e):
        if (d, e) in self.override:
            return self.override[(d, e)]
        if d > e:
            return ()
        return tuple(chain(spot=self.spot(d), dte=(e - d).days, **{**self.kw, **self.kw_by_day.get(d, {})}))

    def run(self, arm, entry_from=None, entry_to=None):
        snap = s37.make_snapshot_at(self.chain_at)
        return s37.simulate(
            arm,
            self.sessions,
            self.chain_at,
            snap,
            entry_from=entry_from or self.days[0],
            entry_to=entry_to or self.days[0],
        )


def one_trade(world, arm="control", **kw):
    out = world.run(arm, **kw)
    assert len(out["trades"]) == 1, out
    return out["trades"][0], out


# ------------------------------------------------------------------------------------------------
# What curve is told, and what the study reads from the deployed config.


def test_the_study_runs_the_deployed_config_and_its_arms_differ_by_one_change():
    ex = json.loads((ROOT / "packages" / "curve" / "config.example.json").read_text(encoding="utf-8"))
    declared = ex["defaults"]
    assert mismatches(declared) == []
    p = {a: s37.arm_params(a) for a in s37.ARMS}
    assert p["near"]["short_delta_target"] == ex["books"]["near"]["short_delta_target"] == 0.40
    assert p["wide5"]["spread_width"] == 5.0 and p["control"]["spread_width"] == declared["spread_width"]
    assert p["hold-expiry"]["close_dte"] < 0 and p["control"]["close_dte"] == declared["close_dte"]
    for arm in s37.ARMS:
        differ = {k for k in p[arm] if p[arm][k] != p["control"].get(k)} - {"arm", "base_book"}
        assert len(differ) <= 1, (arm, differ)


def mismatches(declared):
    return [k for k, v in s37.DEFAULTS.items() if k in declared and declared[k] != v]


def test_the_config_pin_fails_when_a_number_drifts(monkeypatch):
    ex = json.loads((ROOT / "packages" / "curve" / "config.example.json").read_text(encoding="utf-8"))
    monkeypatch.setitem(s37.DEFAULTS, "contango_max", 0.99)
    assert mismatches(ex["defaults"]) == ["contango_max"]


# ------------------------------------------------------------------------------------------------
# plan: one path per expiry the rule can target, minus what is stored.


def test_one_path_per_target_expiry_from_its_first_session_to_its_expiry():
    days = s37.trading_days(date(2024, 1, 2), date(2024, 6, 28))
    reqs = s37.path_requests(days, through=date(2024, 6, 28))
    assert [r["expiration"] for r in reqs] == [
        "2024-02-16",
        "2024-03-15",
        "2024-04-19",
        "2024-05-17",
        "2024-06-21",
        "2024-07-19",
        "2024-08-16",
    ][: len(reqs)]
    params = s37.arm_params("control")
    for r in reqs:
        e, start = date.fromisoformat(r["expiration"]), date.fromisoformat(r["start"])
        got = s37.curve_clock.target_expiration(start, params)
        assert got["expiration"] == r["expiration"]  # the first session is one that targets it
        prev = cal.previous_trading_day(start)
        if prev in days:
            prior = s37.curve_clock.target_expiration(prev, params)
            assert prior is None or prior["expiration"] != r["expiration"]
        assert r["through"] == min(e, date(2024, 6, 28)).isoformat()
        assert r["symbol"] == "VXX"
    assert reqs[-1]["through"] == "2024-06-28"  # a later expiry is cut at the window's end


def test_the_real_window_plans_about_ninety_paths():
    reqs = s37.path_requests(s37.trading_days(s37.START, s37.END))
    assert 85 <= len(reqs) <= 95
    assert reqs[0]["start"] >= "2019-05-13" and reqs[-1]["through"] == "2026-10-28"
    assert len({r["expiration"] for r in reqs}) == len(reqs)


def _store(tmp_path):
    from cherrypick.backtest import chainfetch

    conn = sqlite3.connect(tmp_path / "eod.db")
    conn.executescript(chainfetch.SCHEMA)
    return conn


def test_a_path_the_store_answers_is_not_asked_again(tmp_path):
    conn = _store(tmp_path)
    req = {"symbol": "VXX", "expiration": "2024-02-16", "start": "2024-01-02", "through": "2024-01-12"}
    partial = {"symbol": "VXX", "expiration": "2024-03-15", "start": "2024-01-08", "through": "2024-01-19"}
    empty = {"symbol": "VXX", "expiration": "2024-04-19", "start": "2024-02-20", "through": "2024-03-01"}
    conn.execute(
        "INSERT INTO eod_chain_paths "
        "VALUES ('VXX','2024-02-16','2024-01-02','2024-02-16','complete',30,900,NULL,0)"
    )
    conn.execute(
        "INSERT INTO eod_chain_paths VALUES ('VXX','2024-04-19','2024-02-20','2024-03-01','empty',0,0,NULL,0)"
    )
    for d in s37.trading_days(date(2024, 1, 8), date(2024, 1, 12)):  # 5 of 10 sessions held
        conn.execute(
            "INSERT INTO eod_chain_quotes VALUES ('VXX',?,'2024-03-15',20,'C',1,2,1,0,0,0)", (d.isoformat(),)
        )
    conn.commit()
    covered = s37.store_coverage(conn)
    assert covered(req) and covered(empty) and not covered(partial)
    todo, stored = s37.drop_stored([req, partial, empty], covered)
    assert todo == [partial] and stored == 2
    for d in s37.trading_days(date(2024, 1, 15), date(2024, 1, 19)):  # now all held
        conn.execute(
            "INSERT INTO eod_chain_quotes VALUES ('VXX',?,'2024-03-15',20,'C',1,2,1,0,0,0)", (d.isoformat(),)
        )
    assert s37.store_coverage(conn)(partial)


def test_the_coverage_guard_fails_when_the_threshold_is_dropped(tmp_path, monkeypatch):
    conn = _store(tmp_path)
    conn.execute("INSERT INTO eod_chain_quotes VALUES ('VXX','2024-01-08','2024-03-15',20,'C',1,2,1,0,0,0)")
    conn.commit()
    req = {"symbol": "VXX", "expiration": "2024-03-15", "start": "2024-01-08", "through": "2024-01-19"}
    assert not s37.store_coverage(conn)(req)
    monkeypatch.setattr(s37, "COVERAGE", 0.0)
    assert s37.store_coverage(conn)(req)


def test_the_morning_sample_is_one_gate_open_session_in_25():
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(60)]
    got = s37.sample_sessions(days)
    assert got == [days[24], days[49]]
    assert s37.sample_sessions(days[:24]) == []


# ------------------------------------------------------------------------------------------------
# Entry: the gate, then the module's own plan.


def entered(ratio, **kw):
    w = World(JAN2, FEB16 + timedelta(days=4), ratio=lambda d: ratio if d == JAN2 else 0.9, half=0.02, **kw)
    return w.run("control")


def test_the_gate_admits_a_ratio_under_the_line_and_shuts_at_it():
    assert len(entered(0.96)["trades"]) == 1
    shut = entered(0.97)
    assert shut["trades"] == [] and shut["gated"] >= 1 and shut["refusals"] == []  # a shut gate is no refusal
    assert shut["attempts"] == 0


def test_a_missing_ratio_is_a_refusal_never_an_entry():
    out = entered(None)
    assert out["refusals"][0] == ("2024-01-02", "regime_unmeasured")


def test_the_gate_guard_fails_when_the_line_moves(monkeypatch):
    monkeypatch.setattr(s37, "GATE", 1.0)
    assert len(entered(0.97)["trades"]) == 1  # a shut gate would have refused it
    monkeypatch.setattr(s37, "GATE", 0.97)
    assert entered(0.97)["trades"] == []


def refusal_of(world, day=JAN2):
    out = world.run("control", entry_from=day, entry_to=day)
    assert out["trades"] == [], out
    return out["refusals"][0][1]


def test_no_expiry_in_the_band_is_a_refusal(monkeypatch):
    # the deployed 25-60 band leaves no gap in 2019-2026; a narrower one does, and the refusal is the same
    monkeypatch.setitem(s37.CONFIG["defaults"], "dte_max", 30)
    p = s37.arm_params("control")
    day = next(
        d
        for d in s37.trading_days(date(2024, 2, 20), date(2024, 3, 31))
        if s37.curve_clock.target_expiration(d, p) is None
    )
    w = World(day, day + timedelta(days=3))
    assert refusal_of(w, day) == "no_target_expiration"
    monkeypatch.setitem(s37.CONFIG["defaults"], "dte_max", 60)
    assert s37.curve_clock.target_expiration(day, s37.arm_params("control")) is not None


def test_a_session_with_no_stored_chain_is_a_refusal():
    w = World(JAN2, JAN2 + timedelta(days=3))
    w.override[(JAN2, FEB16)] = ()
    assert refusal_of(w) == "no_chain"


def test_the_adjusted_series_is_turned_away():
    w = World(JAN2, JAN2 + timedelta(days=3))
    w.override[(JAN2, FEB16)] = tuple(chain(spot=20.0, dte=45, lo=40, hi=80))  # every strike above the close
    assert refusal_of(w) == "chain_not_raw_basis"


def test_no_strike_above_the_close_leaves_no_short():
    w = World(JAN2, JAN2 + timedelta(days=3))
    w.override[(JAN2, FEB16)] = tuple(chain(spot=20.0, dte=45, lo=10, hi=20))
    assert refusal_of(w) == "no_delta_for_selection"


def test_a_short_wider_than_thirty_percent_of_mid_is_refused():
    w = World(JAN2, JAN2 + timedelta(days=3), half=0.5)
    assert refusal_of(w) == "spread_too_wide_short"


def test_a_wing_is_refused_only_when_wide_in_percent_and_in_money():
    wide = World(JAN2, JAN2 + timedelta(days=3), half_at=lambda k: 0.02 if k <= 25 else 0.4)
    assert refusal_of(wide) == "spread_too_wide_long"
    # 100% of mid but 20 cents: under the 25 cent floor, so the wing stands
    cheap = World(JAN2, JAN2 + timedelta(days=3), half_at=lambda k: 0.02 if k <= 25 else 0.1)
    out = cheap.run("control")
    assert out["refusals"] == [] or out["refusals"][0][1] != "spread_too_wide_long"


def test_the_wing_guard_fails_when_the_money_floor_goes(monkeypatch):
    cheap = World(JAN2, JAN2 + timedelta(days=3), half_at=lambda k: 0.02 if k <= 25 else 0.1)
    assert cheap.run("control")["refusals"] == []
    monkeypatch.setitem(s37.CONFIG["defaults"], "max_wing_spread_abs", 0.0)
    assert refusal_of(cheap) == "spread_too_wide_long"


def test_a_credit_under_a_tenth_of_max_loss_net_of_entry_costs_is_refused():
    w = World(JAN2, JAN2 + timedelta(days=3), vol=0.2)
    assert refusal_of(w) == "net_credit_below_floor"


def test_the_net_floor_guard_fails_when_the_floor_is_removed(monkeypatch):
    w = World(JAN2, JAN2 + timedelta(days=3), vol=0.2)
    monkeypatch.setitem(s37.CONFIG["defaults"], "min_net_credit_to_max_loss", -1.0)
    out = w.run("control")
    assert out["refusals"] == []  # nothing stops it: a fee-eaten credit enters


def test_a_chain_with_no_wing_strike_is_a_refusal():
    w = World(JAN2, JAN2 + timedelta(days=3), hi=24)
    assert refusal_of(w) == "no_wing_strike"


def test_an_entry_the_session_before_a_split_is_refused(monkeypatch):
    day = date(2024, 7, 23)
    w = World(day, date(2024, 7, 30))
    got = s37.curve_clock.target_expiration(day, s37.arm_params("control"))
    assert date.fromisoformat(got["expiration"]) > date(2024, 7, 24)
    assert refusal_of(w, day) == "split_next_session"
    monkeypatch.setattr(s37, "SPLIT_DATES", ())
    assert w.run("control", entry_from=day, entry_to=day)["refusals"] == []


def test_the_short_is_the_call_nearest_thirty_delta_and_near_goes_to_forty():
    w = World(JAN2, JAN2 + timedelta(days=3))
    snap = s37.make_snapshot_at(w.chain_at)(JAN2, FEB16, 20.0)
    deltas = {
        e["strike_price"]: s37.engine.bs_call_delta(
            20.0, e["strike_price"], 45, snap["greeks"][e["streamer_symbol"]]["iv"]
        )
        for e in snap["chain"]
        if e["strike_price"] > 20.0 and e["streamer_symbol"] in snap["greeks"]
    }
    for arm, target in (("control", 0.30), ("near", 0.40)):
        t, _ = one_trade(World(JAN2, FEB16 + timedelta(days=4), half=0.02), arm)
        assert t["short"] == min(deltas, key=lambda k: abs(deltas[k] - target))
        assert t["selected_by"] == "delta_computed"
    near, _ = one_trade(World(JAN2, FEB16 + timedelta(days=4), half=0.02), "near")
    base, _ = one_trade(World(JAN2, FEB16 + timedelta(days=4), half=0.02), "control")
    assert near["short"] < base["short"]


def test_the_wing_is_the_lowest_listed_strike_at_or_above_the_width():
    base, _ = one_trade(World(JAN2, FEB16 + timedelta(days=4)), "control")
    assert base["long"] - base["short"] == 2.0
    gap, _ = one_trade(World(JAN2, FEB16 + timedelta(days=4), drop=(26, 27)), "control")
    assert gap["short"] == base["short"] and gap["long"] == 28.0  # 26 and 27 are not listed
    wide, _ = one_trade(World(JAN2, FEB16 + timedelta(days=4)), "wide5")
    assert wide["long"] - wide["short"] == 5.0 and wide["width"] == 5.0


# ------------------------------------------------------------------------------------------------
# Management: each arm's exits, in the module's order, and the slot.


def fall_after_entry(d):
    return 20.0 if d <= JAN2 else 15.0


@pytest.fixture
def no_take(monkeypatch):
    """A flat world decays a spread past its profit target within weeks; this isolates the other exits."""
    for spec in s37.ARM_SPECS.values():
        monkeypatch.setitem(spec, "profit_take", False)


def test_a_profit_take_closes_at_half_the_credit_and_the_next_session_is_first_to_manage():
    w = World(JAN2, FEB16 + timedelta(days=4), spot=fall_after_entry)
    t, out = one_trade(w, "control")
    assert t["entry"] == "2024-01-02" and t["close"] == "2024-01-03" and t["how"] == "profit_take"
    assert out["busy"] == 1  # entered on D, managed on D + 1, not before


def test_a_close_frees_the_slot_for_the_next_session_and_one_position_at_a_time():
    w = World(JAN2, FEB16 + timedelta(days=4), spot=fall_after_entry)
    out = w.run("control", entry_to=date(2024, 1, 12))
    first, second = out["trades"][0], out["trades"][1]
    sessions = [s.date.isoformat() for s in w.sessions]
    assert second["entry"] == sessions[sessions.index(first["close"]) + 1]
    for a, b in zip(out["trades"], out["trades"][1:], strict=False):
        assert b["entry"] > a["close"]


def test_the_profit_take_guard_fails_when_the_target_is_removed(monkeypatch):
    w = World(JAN2, FEB16 + timedelta(days=4), spot=fall_after_entry)
    monkeypatch.setitem(s37.ARM_SPECS["control"], "profit_take", False)
    t, _ = one_trade(w, "control")
    assert t["how"] != "profit_take"


def test_hold_expiry_holds_to_expiry_with_no_profit_take_where_control_takes_it():
    fall = World(JAN2, FEB16 + timedelta(days=4), spot=fall_after_entry)
    h, _ = one_trade(fall, "hold-expiry")
    assert h["how"] == "expiry" and h["close"] == "2024-02-16"  # worth under half the credit for weeks
    c, _ = one_trade(fall, "control")
    assert c["how"] == "profit_take"


def test_close_dte_closes_at_seven_days(no_take):
    t, _ = one_trade(World(JAN2, FEB16 + timedelta(days=4)), "control")
    assert t["close"] == "2024-02-09" and t["how"] == "close_dte"
    # the same trade, six days out, has not reached the line
    assert (FEB16 - date(2024, 2, 9)).days == 7


def test_the_close_dte_guard_fails_when_the_line_is_moved(no_take, monkeypatch):
    monkeypatch.setitem(s37.CONFIG["defaults"], "close_dte", 3)
    t, _ = one_trade(World(JAN2, FEB16 + timedelta(days=4)), "control")
    assert t["close"] != "2024-02-09"


def flip_on_jan9(d):
    return 0.99 if d == date(2024, 1, 9) else 0.9


def test_the_flip_closes_control_near_wide5_and_hold_expiry_but_not_noflip(no_take):
    w = World(JAN2, FEB16 + timedelta(days=4), ratio=flip_on_jan9)
    for arm in ("control", "near", "wide5", "hold-expiry"):
        t, _ = one_trade(w, arm)
        assert (t["close"], t["how"]) == ("2024-01-09", "regime_flip"), arm
    t, _ = one_trade(w, "noflip")
    assert t["how"] == "close_dte" and t["close"] == "2024-02-09"


def test_the_flip_fires_at_the_gate_not_at_one():
    for ratio, fires in ((0.97, True), (0.969, False), (1.0, True)):
        w = World(
            JAN2, FEB16 + timedelta(days=4), ratio=lambda d, r=ratio: r if d == date(2024, 1, 9) else 0.9
        )
        t, _ = one_trade(w, "control")
        assert (t["how"] == "regime_flip") is fires, ratio


def test_the_flip_guard_fails_when_the_flip_books_are_emptied(monkeypatch):
    w = World(JAN2, FEB16 + timedelta(days=4), ratio=flip_on_jan9)
    monkeypatch.setattr(s37.management, "FLIP_BOOKS", ())
    t, _ = one_trade(w, "control")
    assert t["how"] != "regime_flip"


def test_an_unmeasured_ratio_never_forces_an_exit(no_take):
    w = World(JAN2, FEB16 + timedelta(days=4), ratio=lambda d: None if d == date(2024, 1, 9) else 0.9)
    t, _ = one_trade(w, "control")
    assert t["how"] == "close_dte"


def test_an_exit_on_a_wide_leg_waits_for_the_next_session(no_take):
    w = World(JAN2, FEB16 + timedelta(days=4))
    w.kw_by_day[date(2024, 2, 9)] = {"half": 0.2}  # wide in percent and in money on the day it would close
    t, out = one_trade(w, "control")
    assert t["close"] == "2024-02-12" and t["how"] == "close_dte" and out["exit_blocked"] == 1


def test_the_exit_gate_guard_fails_when_the_gate_is_removed(no_take, monkeypatch):
    w = World(JAN2, FEB16 + timedelta(days=4))
    w.kw_by_day[date(2024, 2, 9)] = {"half": 0.2}
    monkeypatch.setattr(s37, "exit_spread_blocks", lambda snap, params: False)
    t, out = one_trade(w, "control")
    assert t["close"] == "2024-02-09" and out["exit_blocked"] == 0


def test_an_exit_with_no_quotes_is_tried_again_and_counted(no_take):
    w = World(JAN2, FEB16 + timedelta(days=4))
    w.override[(date(2024, 2, 9), FEB16)] = ()
    t, out = one_trade(w, "control")
    assert t["close"] == "2024-02-12" and out["exit_unpriced"] == 1


def test_an_arm_that_never_clears_the_gate_reaches_expiry(no_take):
    w = World(JAN2, FEB16 + timedelta(days=4), half=0.02)
    for d in w.days:
        if d >= date(2024, 2, 8):
            w.kw_by_day[d] = {"half": 0.25}
    t, out = one_trade(w, "control")
    assert t["how"] == "expiry" and out["exit_blocked"] >= 3


def test_exposure_is_counted_on_held_sessions_with_the_shorts_extrinsic_under_five_cents(monkeypatch):
    def deep(d):
        return 20.0 if d <= JAN2 else 30.0  # the short goes deep in the money

    w = World(JAN2, FEB16 + timedelta(days=4), spot=deep)
    t, out = one_trade(w, "hold-expiry")
    assert t["exposed_sessions"] > 0 and out["exposed_sessions"] == t["exposed_sessions"]
    assert t["alt_share"] is not None and t["alt_share"] != t["share"]
    monkeypatch.setitem(s37.CONFIG["defaults"], "assignment_exposure_tv", 0.0)  # nothing can be under zero
    none, _ = one_trade(w, "hold-expiry")
    assert none["exposed_sessions"] == 0 and none["alt_share"] is None


# ------------------------------------------------------------------------------------------------
# The split.


SPLIT_DAY = date(2024, 7, 24)


def split_world(**kw):
    w = World(date(2024, 7, 2), date(2024, 8, 23), **kw)
    return w


def test_a_split_inside_a_trade_ends_it_the_session_before_at_that_sessions_quotes():
    w = split_world(spot=lambda d: 20.0 if d < SPLIT_DAY else 80.0)
    t, _ = one_trade(w, "control", entry_from=date(2024, 7, 2), entry_to=date(2024, 7, 2))
    assert (t["close"], t["how"], t["split_ended"]) == ("2024-07-23", "split", True)
    exp = date.fromisoformat(t["expiry"])
    rows = w.chain_at(date(2024, 7, 23), exp)
    sq = s37.call_quote(rows, t["short"])
    lq = s37.call_quote(rows, t["long"])
    assert t["exit"] == round(-(sq["mid"] - lq["mid"]) * 200, 2)


def test_the_split_guard_fails_when_the_ex_dates_are_forgotten(monkeypatch):
    w = split_world(spot=lambda d: 20.0 if d < SPLIT_DAY else 80.0)
    monkeypatch.setattr(s37, "SPLIT_DATES", ())
    t, _ = one_trade(w, "control", entry_from=date(2024, 7, 2), entry_to=date(2024, 7, 2))
    assert t["how"] != "split" and t["close"] != "2024-07-23"


def test_a_split_ended_trade_with_no_quotes_that_day_closes_on_the_last_priced_marks():
    w = split_world(spot=lambda d: 20.0 if d < SPLIT_DAY else 80.0)
    exp = date.fromisoformat(
        s37.curve_clock.target_expiration(date(2024, 7, 2), s37.arm_params("control"))["expiration"]
    )
    w.override[(date(2024, 7, 23), exp)] = ()
    t, _ = one_trade(w, "control", entry_from=date(2024, 7, 2), entry_to=date(2024, 7, 2))
    assert t["how"] == "split" and t["stale_close"] is True and t["close"] == "2024-07-23"


def test_the_summary_keeps_split_ended_trades_and_reports_the_rest_without_them():
    a = {
        "net": 10.0,
        "share": 0.1,
        "max_loss": 100.0,
        "split_ended": False,
        "entry": "2024-01-02",
        "close": "2024-01-03",
    }
    b = {**a, "net": -20.0, "share": -0.2, "split_ended": True}
    trades = [{**t, **FILL} for t in (a, b)]
    s = s37.summarize(trades)
    assert s["n"] == 2 and s["split_ended"] == 1 and s["n_without_split_ended"] == 1
    assert s["mean_share"] == pytest.approx(-0.05) and s["mean_share_without_split_ended"] == pytest.approx(
        0.1
    )


def test_shares_delivered_the_session_before_a_split_are_disposed_on_the_pre_split_basis():
    sessions = [s37.Session(date(2024, 7, 23), 20.0, 20.0, 0.9), s37.Session(SPLIT_DAY, 80.0, 84.0, 0.9)]
    assert s37.dispose_price(sessions, 0) == pytest.approx(21.0)  # 84 x 0.25
    assert s37.dispose_price(sessions, 1) == 80.0  # nothing after it: the close


# ------------------------------------------------------------------------------------------------
# Costs, fills and settlement.

FILL = {
    "stress_share": 0.0, "natural_share": 0.0, "nocost_share": 0.0, "exposed_sessions": 0, "alt_share": None,
    "credit": 0.0, "exit": 0.0, "shares": 0.0, "gross": 0.0, "fees": 0.0, "slippage": 0.0, "settlement": 0.0,
    "how": "profit_take",
}  # fmt: skip


def test_a_traded_trade_is_charged_the_suites_fees_and_slippage_on_top_of_mid(no_take):
    w = World(JAN2, FEB16 + timedelta(days=4))
    t, _ = one_trade(w, "control")
    rows_in, rows_out = w.chain_at(JAN2, FEB16), w.chain_at(date(2024, 2, 9), FEB16)
    s_in, l_in = s37.call_quote(rows_in, t["short"]), s37.call_quote(rows_in, t["long"])
    s_out, l_out = s37.call_quote(rows_out, t["short"]), s37.call_quote(rows_out, t["long"])

    def slip(*qs):  # 12.5% of each leg's spread, capped at 15% of mid, x100 x 2 contracts
        return sum(min(0.125 * (q["ask"] - q["bid"]), 0.15 * q["mid"]) for q in qs) * 200

    open_fee = (
        2 * 2 * (1.00 + 0.10 + 0.02) + 1 * 2 * 0.00329
    )  # $1 + clearing + ORF a contract, TAF on the sell
    close_fee = 2 * 2 * (0.10 + 0.02) + 1 * 2 * 0.00329  # nothing to close but the pass-throughs
    assert t["fees"] == pytest.approx(round(open_fee, 2) + round(close_fee, 2), abs=0.011)
    assert t["slippage"] == pytest.approx(slip(s_in, l_in) + slip(s_out, l_out), abs=0.011)
    assert t["credit"] == round((s_in["mid"] - l_in["mid"]) * 200, 2)
    assert t["exit"] == round(-(s_out["mid"] - l_out["mid"]) * 200, 2)
    assert t["settlement"] == 0.0 and t["shares"] == 0.0


def test_the_rows_add_up():
    t, _ = one_trade(World(JAN2, FEB16 + timedelta(days=4)), "control")
    assert t["gross"] == pytest.approx(t["credit"] + t["exit"] + t["shares"])
    assert t["net"] == pytest.approx(t["gross"] - t["fees"] - t["slippage"] - t["settlement"])
    assert t["max_loss"] == pytest.approx((t["width"] - t["credit"] / 200) * 200)
    assert t["share"] == pytest.approx(t["net"] / t["max_loss"])
    assert t["nocost_share"] == pytest.approx(t["gross"] / t["max_loss"])


def test_the_stress_is_one_and_a_half_times_every_cost_and_the_natural_fill_is_the_touch(
    no_take, monkeypatch
):
    w = World(JAN2, FEB16 + timedelta(days=4))
    t, _ = one_trade(w, "control")
    costs = t["fees"] + t["slippage"] + t["settlement"]
    assert t["stress_share"] * t["max_loss"] == pytest.approx(t["gross"] - 1.5 * costs, abs=0.011)
    rows_in, rows_out = w.chain_at(JAN2, FEB16), w.chain_at(date(2024, 2, 9), FEB16)
    s_in, l_in = s37.call_quote(rows_in, t["short"]), s37.call_quote(rows_in, t["long"])
    s_out, l_out = s37.call_quote(rows_out, t["short"]), s37.call_quote(rows_out, t["long"])
    nat = ((s_in["bid"] - l_in["ask"]) - (s_out["ask"] - l_out["bid"])) * 200 - t["fees"] - t["settlement"]
    assert t["natural_share"] * t["max_loss"] == pytest.approx(nat, abs=0.011)
    monkeypatch.setattr(s37, "STRESS", 1.0)
    flat, _ = one_trade(w, "control")
    assert flat["stress_share"] * flat["max_loss"] == pytest.approx(flat["gross"] - costs, abs=0.011)


def at_expiry(close, nxt_open):
    """A hold-expiry trade whose raw close on expiry day is `close` and next session's open `nxt_open`."""
    w = World(JAN2, FEB16 + timedelta(days=4), spot=lambda d: close if d == FEB16 else 20.0)
    w.opens[cal.next_trading_day(FEB16)] = nxt_open
    w.rebuild()
    t, _ = one_trade(w, "hold-expiry")
    return t


def test_a_spread_that_expires_worthless_costs_only_its_entry():
    t = at_expiry(20.0, 20.0)
    assert (t["how"], t["exit"], t["shares"], t["settlement"]) == ("expiry", 0.0, 0.0, 0.0)
    assert t["net"] == pytest.approx(t["credit"] - t["fees"] - t["slippage"])


def test_a_short_that_finishes_in_the_money_pays_intrinsic_delivers_shares_and_one_event():
    t = at_expiry(25.0, 26.0)  # short 24 in by 1, wing 26 out
    assert t["exit"] == -200.0
    assert t["shares"] == -200.0  # short 200 at 25, bought back at 26
    assert t["settlement"] == pytest.approx(
        5.0 + 200 * 25.0 * 27.8e-6 + 0.0332, abs=0.006
    )  # $5 + SEC + TAF on the sale
    assert t["net"] == pytest.approx(t["gross"] - t["fees"] - t["slippage"] - t["settlement"])


def test_both_legs_in_the_money_are_two_events_and_the_shares_net_out():
    t = at_expiry(30.0, 26.0)
    assert t["exit"] == -(6.0 - 4.0) * 200
    assert t["shares"] == 0.0  # short 200 at 30 and long 200 at 30, both closed at 26
    assert t["settlement"] > 10.0


def test_settlement_guard_fails_when_the_events_are_not_charged(monkeypatch):
    monkeypatch.setattr(s37.engine, "assignment_fee", lambda assignment, price: 0.0)
    assert at_expiry(25.0, 26.0)["settlement"] == 0.0


# ------------------------------------------------------------------------------------------------
# The numbers: drawdown, episodes, bootstrap, Holm, the pass rule.


def test_drawdown_is_the_deepest_fall_from_a_peak_of_cumulative_net():
    assert s37.max_drawdown([10, -30, 5, -5]) == 30
    assert s37.max_drawdown([-5, -5]) == 10  # the start counts as a peak
    assert s37.max_drawdown([5, 5]) == 0


def test_summary_figures():
    trades = [
        {
            **FILL,
            "entry": "2024-01-02",
            "close": "2024-01-10",
            "net": n,
            "share": n / 100,
            "max_loss": 100.0,
            "split_ended": False,
        }
        for n in (10.0, -30.0, 5.0, -5.0)
    ]
    s = s37.summarize(trades)
    assert s["n"] == 4 and s["mean_net"] == -5.0 and s["median_net"] == 0.0 and s["worst"] == -30.0
    assert s["max_drawdown"] == 30.0 and s["drawdown_units"] == pytest.approx(0.3)
    assert s["by_year"] == {"2024": {"n": 4, "net": -20.0}}


def test_episodes_are_listed_and_the_mean_is_reported_without_them():
    base = {**FILL, "how": "profit_take"}
    inside = {
        **base,
        "entry": "2020-03-02",
        "close": "2020-03-04",
        "net": -300.0,
        "share": -1.0,
        "max_loss": 300.0,
    }
    out = {**base, "entry": "2020-06-02", "close": "2020-06-04", "net": 30.0, "share": 0.1, "max_loss": 300.0}
    got = s37.episodes([inside, out], date(2019, 5, 13), date(2022, 12, 30))
    assert list(got["episodes"]) == ["2020-03 spike"]  # the 2024 and 2025 episodes are in the other window
    assert got["episodes"]["2020-03 spike"][0]["net"] == -300.0
    assert got["n_without"] == 1 and got["mean_share_without"] == 0.1


def test_refusals_are_counted_by_reason_and_year():
    got = s37.refusal_counts([("2020-03-02", "a"), ("2020-03-03", "a"), ("2021-01-04", "b")])
    assert got["by_reason"] == {"a": 2, "b": 1}
    assert got["by_year"] == {"2020": {"a": 2}, "2021": {"b": 1}}


def test_the_bootstrap_p_is_small_for_a_steady_gain_and_large_for_noise():
    win = s37.bootstrap([0.1, 0.12, 0.09, 0.11] * 10, resamples=500)
    assert win["p"] <= 0.01 and win["ci"][0] > 0
    noise = s37.bootstrap([0.3, -0.3, 0.2, -0.2] * 10, resamples=500)
    assert noise["p"] > 0.2
    assert (
        s37.bootstrap([0.1, -0.2, 0.3] * 10, resamples=300)["p"]
        == s37.bootstrap([0.1, -0.2, 0.3] * 10, resamples=300)["p"]
    )


def trades_with(shares_by_half, stress=None):
    """Closed trades whose entries fall in the two design halves."""
    out = []
    for half, shares in enumerate(shares_by_half):
        start = [date(2019, 6, 3), date(2021, 4, 5)][half]
        for i, sh in enumerate(shares):
            out.append(
                {
                    "entry": (start + timedelta(days=i)).isoformat(),
                    "share": sh,
                    "stress_share": sh - 0.01 if stress is None else stress,
                }
            )
    return out


STEADY = [
    0.10,
    0.12,
    0.09,
    0.11,
    0.10,
    0.13,
    0.08,
    0.12,
    0.10,
    0.11,
    0.09,
    0.12,
    0.10,
    0.11,
    0.10,
    0.09,
    0.12,
    0.11,
    0.10,
    0.10,
]


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(s37, "RESAMPLES", 400)
    monkeypatch.setattr(s37.bootstrap, "__defaults__", (400, s37.BLOCK, s37.SEED))


def judged(arms):
    return s37.judge(arms)


def test_an_arm_passes_the_design_when_all_four_checks_hold(fast):
    d = judged({"a": trades_with([STEADY, STEADY])})["a"]
    assert d["enough"] and all(d["conditions"].values())
    assert s37.arm_verdict(d, {"n": 40, "mean": 0.05, "ci": [0, 0.1]})["verdict"] == "pass"


def test_fewer_than_thirty_trades_is_no_verdict_not_a_fail(fast):
    d = judged({"a": trades_with([STEADY[:10], STEADY[:10]])})["a"]
    assert not d["enough"] and d["conditions"] is None
    v = s37.arm_verdict(d, None)
    assert v["verdict"] == "no verdict" and "under 30" in v["why"]


def test_the_minimum_guard_fails_when_it_is_lowered(fast, monkeypatch):
    monkeypatch.setattr(s37, "MIN_TRADES", 5)
    assert judged({"a": trades_with([STEADY[:10], STEADY[:10]])})["a"]["enough"]


def test_a_negative_mean_a_negative_half_or_a_negative_stress_each_fail(fast):
    losing = judged({"a": trades_with([[-x for x in STEADY], [-x for x in STEADY]])})["a"]
    assert losing["conditions"]["mean_positive"] is False
    bad_half = judged({"a": trades_with([STEADY, [-0.02] * 20 + []])})["a"]
    assert bad_half["conditions"]["halves_positive"] is False
    stressed = judged({"a": trades_with([STEADY, STEADY], stress=-0.01)})["a"]
    assert stressed["conditions"]["stress_positive"] is False
    assert (
        s37.arm_verdict(stressed, {"n": 99, "mean": 0.5, "ci": None})["verdict"] == "fail"
    )  # a good holdout cannot rescue it


def test_a_half_under_ten_trades_is_not_read(fast, monkeypatch):
    thin = trades_with([STEADY + STEADY[:10], [-0.5] * 5])
    d = judged({"a": thin})["a"]
    assert d["halves"][1]["read"] is False and d["conditions"]["halves_positive"] is True
    monkeypatch.setattr(s37, "MIN_HALF", 1)
    assert judged({"a": thin})["a"]["conditions"]["halves_positive"] is False


def test_holm_across_five_arms_is_stricter_than_p_under_five_percent(monkeypatch, fast):
    table = {0.11: 0.004, 0.12: 0.03, 0.13: 0.5, 0.14: 0.6, 0.15: 0.7}
    monkeypatch.setattr(
        s37, "bootstrap", lambda v, **k: {"p": table[round(sum(v) / len(v), 2)], "ci": [0, 0]}
    )
    arms = {n: trades_with([[m] * 20, [m] * 20]) for n, m in zip("abcde", table, strict=True)}
    d = judged(arms)
    assert d["a"]["conditions"]["holm"] is True
    assert d["b"]["conditions"]["holm"] is False  # 0.03 < 0.05 but not under 0.05 / 4
    monkeypatch.setattr(s37.core_stats, "holm", lambda p, alpha: {k: v <= alpha for k, v in p.items()})
    assert judged(arms)["b"]["conditions"]["holm"] is True  # the guard fails when the correction goes


def test_an_arm_without_enough_trades_still_counts_in_the_family(monkeypatch, fast):
    monkeypatch.setattr(s37, "bootstrap", lambda v, **k: {"p": 0.012, "ci": [0, 0]})
    arms = {"a": trades_with([[0.1] * 20, [0.1] * 20])}
    arms.update({n: trades_with([[0.1] * 3, [0.1] * 3]) for n in "bcde"})
    d = judged(arms)
    assert d["a"]["conditions"]["holm"] is False  # 0.012 against 0.05 / 5 with four no-verdict arms at p = 1
    assert d["b"]["holm_p"] == 1.0


def test_the_holdout_is_read_only_for_a_design_pass_and_must_clear_zero(fast):
    ok = judged({"a": trades_with([STEADY, STEADY])})["a"]
    assert s37.arm_verdict(ok, None)["verdict"] == "no verdict"
    assert s37.arm_verdict(ok, {"n": 12, "mean": 0.2, "ci": None})["verdict"] == "no verdict"
    assert s37.arm_verdict(ok, {"n": 40, "mean": 0.0, "ci": [-0.1, 0.1]})["verdict"] == "fail"
    assert s37.arm_verdict(ok, {"n": 40, "mean": 0.01, "ci": [-0.1, 0.1]})["verdict"] == "pass"


def test_the_minimum_detectable_mean_is_the_standard_error_to_clear_zero(fast):
    d = judged({"a": trades_with([STEADY, STEADY])})["a"]
    assert d["mde_mean"] == pytest.approx(1.645 * d["se"])


def test_flip_divergence_counts_the_control_flips_noflip_held_past():
    c = [
        {"entry": "2020-01-02", "how": "regime_flip"},
        {"entry": "2020-03-02", "how": "regime_flip"},
        {"entry": "2020-05-01", "how": "close_dte"},
    ]
    n = [{"entry": "2020-01-02", "how": "close_dte"}, {"entry": "2020-03-02", "how": "regime_flip"}]
    assert s37.flip_divergence(c, n) == {"flip_divergence_count": 1, "control_flip_exits": 2}


# ------------------------------------------------------------------------------------------------
# The share reference.


def test_the_share_reference_shorts_the_close_after_a_gate_open_ratio_on_adjusted_closes():
    down = [s37.Session(date(2024, 7, 17) + timedelta(days=i), 20.0 * 0.95**i, None, 0.9) for i in range(4)]
    got = s37.share_reference(down, date(2024, 7, 17), date(2024, 7, 31))
    assert (
        got["sessions"] == 3
        and got["final"] > 1.1
        and got["worst_day"] == pytest.approx(-0.0005 + 0.0, abs=1e-9)
        or got["worst_day"] <= 0
    )
    shut = [s37.Session(s.date, s.close, None, 1.1) for s in down]
    assert s37.share_reference(shut, date(2024, 7, 17), date(2024, 7, 31))["final"] == 1.0


def test_the_share_reference_does_not_read_a_split_as_a_move():
    ss = [
        s37.Session(date(2024, 7, 22), 20.0, None, 0.9),
        s37.Session(date(2024, 7, 23), 20.0, None, 0.9),
        s37.Session(SPLIT_DAY, 80.0, None, 0.9),  # the reverse split: four times the raw price
        s37.Session(date(2024, 7, 25), 80.0, None, 0.9),
    ]
    got = s37.share_reference(ss, date(2024, 7, 22), date(2024, 7, 31))
    assert got["worst_day"] > -0.01 and got["final"] == pytest.approx(1 - 0.0005, abs=1e-9)


def test_the_split_adjustment_guard_fails_when_the_ex_dates_are_forgotten(monkeypatch):
    ss = [s37.Session(date(2024, 7, 23), 20.0, None, 0.9), s37.Session(SPLIT_DAY, 80.0, None, 0.9)]
    monkeypatch.setattr(s37, "SPLIT_DATES", ())
    assert s37.share_reference(ss, date(2024, 7, 23), date(2024, 7, 31))["worst_day"] < -2.0


# ------------------------------------------------------------------------------------------------
# The morning sample.


def test_the_morning_chain_is_planned_by_the_same_function_and_compared():
    rows = [(k, r, b, a, 0.3) for k, r, b, a in chain(spot=20.0, dte=45)]
    snap = s37.build_snapshot([(k, r, b, a) for k, r, b, a, _ in rows], 20.0, JAN2, FEB16)
    close = s37.engine.plan_entry(snap, s37.arm_params("control"), s37.CONFIG)
    got = s37.morning_check(rows, 20.0, JAN2, FEB16, close, s37.arm_params("control"))
    assert got["morning_ok"] and got["close_ok"] and got["same_short"] and got["same_long"]
    assert got["vendor_delta"] == 0.3 and 0.2 < got["our_delta"] < 0.4
    wide = [(k, r, max(b - 0.5, 0), a + 0.5, 0.3) for k, r, b, a, _ in rows]
    refused = s37.morning_check(wide, 20.0, JAN2, FEB16, close, s37.arm_params("control"))
    assert refused["morning_ok"] is False and refused["morning_reason"].startswith("spread_too_wide")
    s = s37.morning_summary([got, refused])
    assert (s["sessions"], s["morning_refused"], s["close_refused"], s["refusal_differs"]) == (2, 1, 0, 1)


# ------------------------------------------------------------------------------------------------
# The whole run and the files it writes.


@pytest.fixture
def small(monkeypatch):
    monkeypatch.setattr(
        s37,
        "WINDOWS",
        {"design": (date(2024, 1, 2), date(2024, 6, 28)), "holdout": (date(2024, 7, 1), date(2024, 12, 20))},
    )
    monkeypatch.setattr(
        s37, "HALVES", ((date(2024, 1, 2), date(2024, 3, 29)), (date(2024, 4, 1), date(2024, 6, 28)))
    )
    monkeypatch.setattr(s37, "MIN_TRADES", 2)
    monkeypatch.setattr(s37, "MIN_HALF", 1)
    monkeypatch.setattr(s37, "RESAMPLES", 300)
    monkeypatch.setattr(s37.bootstrap, "__defaults__", (300, s37.BLOCK, s37.SEED))
    monkeypatch.setattr(s37, "EPISODES", (("test episode", date(2024, 2, 1), date(2024, 2, 29)),))
    monkeypatch.setattr(s37, "SPLIT_DATES", ())


def test_a_run_writes_a_verdict_per_arm_reads_the_holdout_only_for_a_pass_and_renders(small, tmp_path):
    w = World(date(2024, 1, 2), date(2025, 1, 31), half=0.02)
    snap = s37.make_snapshot_at(w.chain_at)
    res = s37.run_all(w.sessions, w.chain_at, snap)
    assert set(res["verdict"]) == set(s37.ARMS)
    for arm, v in res["verdict"].items():
        read = res["arms"][arm]["holdout"].get("read")
        assert read == (
            v["verdict"] in ("pass", "fail")
            and res["arms"][arm]["design"]["judged"]["conditions"] is not None
            and all(res["arms"][arm]["design"]["judged"]["conditions"].values())
        )
    text = s37.report(json.loads(json.dumps(res, default=str)))
    for heading in (
        "## The judged checks",
        "## What it says",
        "## How to read the tables",
        "Refusals by reason",
        "## Reading choices",
    ):
        assert heading in text
    path = tmp_path / "results.json"
    path.write_text(json.dumps(res, default=str), encoding="utf-8")
    orch = pytest.importorskip("cherrypick.orchestrator.hypotheses")
    read = orch.verdicts(json.loads(path.read_text(encoding="utf-8")))
    assert {r["arm"] for r in read} == set(s37.ARMS)
    assert isinstance(res["window"], str) and res["window"].startswith("2024-01-02..2024-06-28")


def test_an_arm_with_too_few_trades_has_no_verdict_and_its_holdout_is_not_read(small, monkeypatch):
    monkeypatch.setattr(s37, "MIN_TRADES", 50)
    w = World(date(2024, 1, 2), date(2025, 1, 31), half=0.02)
    res = s37.run_all(w.sessions, w.chain_at, s37.make_snapshot_at(w.chain_at))
    assert {v["verdict"] for v in res["verdict"].values()} == {"no verdict"}
    assert all(res["arms"][a]["holdout"] == {"read": False} for a in s37.ARMS)
    assert "not read" in s37.report(json.loads(json.dumps(res, default=str)))


def test_a_window_starts_flat_and_a_position_open_at_the_end_is_left_out_and_counted(no_take):
    w = World(date(2024, 1, 2), date(2024, 3, 5), half=0.02)
    snap = s37.make_snapshot_at(w.chain_at)
    out = s37.simulate(
        "control", w.sessions, w.chain_at, snap, entry_from=date(2024, 2, 1), entry_to=date(2024, 3, 4)
    )
    assert out["trades"] == [] and out["left_open"] == 1  # entered 2024-02-01, 7 days short of closing
    assert out["gated"] == 0 and out["attempts"] == 1  # nothing carried in from January's position


def test_plan_writes_the_request_file_and_counts_it(tmp_path, capsys):
    out = tmp_path / "requests.json"
    assert s37.main(["plan", "--out", str(out)]) == 0
    said = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert said["requests"] == len(doc["requests"]) >= 85 and said["already_stored"] == 0
    assert {r["symbol"] for r in doc["requests"]} == {"VXX"}
    from cherrypick.backtest import chainfetch

    key = chainfetch.read_path_requests(out)  # the fetcher reads it as written
    assert len(key) == said["requests"]


def test_the_report_command_rebuilds_results_md_from_results_json(small, tmp_path):
    w = World(date(2024, 1, 2), date(2025, 1, 31), half=0.02)
    res = s37.run_all(w.sessions, w.chain_at, s37.make_snapshot_at(w.chain_at))
    path = tmp_path / "results.json"
    path.write_text(json.dumps(res, default=str), encoding="utf-8")
    assert s37.main(["report", "--json", str(path)]) == 0
    assert "## What it says" in path.with_suffix(".md").read_text(encoding="utf-8")
