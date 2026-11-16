"""Ensemble on core.entrychecks: every gate gives through core exactly what its own function gives, the
gates validate through core, and a `trigger` entry fires through core's entry_due."""

from __future__ import annotations

import copy
import dataclasses

import pytest
from cherrypick.core import entrychecks as ec
from market import spx_snapshot

from cherrypick.ensemble import arms, engine, events
from cherrypick.ensemble import gates as gates_mod

A = arms.load_arms()


def market_ctx(snap, arm):
    expiry = engine.resolve_expiry(arm, snap) or snap.session
    return gates_mod.Ctx(snap, arm, snap.chains[expiry], expiry)


SNAPS = [
    spx_snapshot(),
    spx_snapshot(spot=6030.0, prev_close=6000.0, day_open=6010.0, vix=22.0, vix_prev_close=19.0),
    spx_snapshot(spot=5950.0, prev_close=6000.0, day_open=5990.0, vix=14.0, vix_prev_close=15.0, vol=0.12),
]


def test_every_market_gate_through_core_equals_its_own_function():
    seen = 0
    for arm in A.values():
        if arm["symbol"] != "SPX":
            continue
        for snap in SNAPS:
            ctx = market_ctx(snap, arm)
            for g in arm.get("gates", []):
                if gates_mod.stage_of(g) != "market":
                    continue
                fn = gates_mod.REGISTRY[g["gate"]][1]
                direct = fn(ctx, **{k: v for k, v in g.items() if k not in ("gate", "off")})
                assert gates_mod.run(g, ctx) == (bool(direct[0]), direct[1]), (arm["name"], g)
                seen += 1
    assert seen > 10


def test_every_gate_has_a_core_stage():
    assert set(gates_mod.CORE_STAGE) == set(gates_mod.REGISTRY)
    for name, (stage, _) in gates_mod.REGISTRY.items():
        core = gates_mod.CORE_STAGE[name]
        assert (stage == "structure") == (core == "structure"), name
        assert gates_mod.CONDITIONS.get(name).stage == core


def test_core_validation_refuses_a_misspelt_gate_parameter_at_load():
    bad = copy.deepcopy(A["iron-fly-1000"])
    bad["gates"] = [{"gate": "sd_change", "minn": -1.1}]
    problems = arms.validate(bad)
    assert any("iron-fly-1000.gates[0]: unknown key 'minn'" in p for p in problems), problems
    missing = copy.deepcopy(A["iron-fly-1000"])
    missing["gates"] = [{"gate": "reward_risk"}]
    assert any("needs 'min'" in p for p in arms.validate(missing))


def test_the_shared_registry_is_not_changed_by_ensembles():
    assert "sd_change" in gates_mod.CONDITIONS.names()
    assert "sd_change" not in ec.CORE.names()


# --- the trigger entry --------------------------------------------------------------------------------


def trigger_arm(trigger, bar_minutes=None):
    arm = copy.deepcopy(A["iron-fly-1000"])
    arm["gates"] = []
    arm["entry"] = {"kind": "trigger", "trigger": trigger}
    if bar_minutes is not None:
        arm["entry"]["bar_minutes"] = bar_minutes
    return arm


def rising(until, *, start=570, base=6000.0, cross_at=600, step=0.5):
    """Flat until `cross_at`, then rising: five-minute closes cross base + 1 once the rise is on."""
    return [(m, base + (m - cross_at) * step if m >= cross_at else base) for m in range(start, until + 1)]


SIGNAL = {
    "signal": {"kind": "cross", "a": {"name": "close"}, "b": 6001.0, "direction": "above", "seed": False},
    "window": {"open": "09:45", "close": "11:00"},
    "within": 10,
}


def test_a_signal_trigger_enters_after_the_bar_that_fires_and_not_before():
    arm = trigger_arm(SIGNAL, bar_minutes=5)
    assert not arms.validate(arm)
    # 10:02 is over 6001 inside the bar 10:00-10:04, which completes at 10:05.
    early = spx_snapshot(minute=604, tr=rising(604), spot=6002.0)
    d = engine.decide(arm, early)
    assert d.reason == "entry_not_due" and d.checks[-1][2] == "not_yet"
    due = spx_snapshot(minute=605, tr=rising(605), spot=6002.5)
    d = engine.decide(arm, due)
    assert d.enter, d.checks
    assert ("entry_due", True, 605) in d.checks
    late = spx_snapshot(minute=616, tr=rising(616), spot=6008.0)
    assert engine.decide(arm, late).checks[-1][2] == "missed"


def test_a_fixed_time_trigger_matches_the_time_entry():
    timed = A["iron-fly-1000"]
    fixed = trigger_arm({"at": "10:00", "within": 5})
    fixed["gates"] = copy.deepcopy(timed["gates"])
    for minute in (599, 600, 604, 605):
        snap = spx_snapshot(minute=minute)
        a, b = engine.decide(timed, snap), engine.decide(fixed, snap)
        assert a.enter == b.enter, minute
        if a.enter:
            assert a.entry.to_dict() == b.entry.to_dict()


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"kind": "trigger", "trigger": SIGNAL}, "bar_minutes"),
        ({"kind": "trigger", "trigger": {"at": "10:00", "within": 5}, "bar_minutes": 5}, "reads no bars"),
        ({"kind": "trigger", "trigger": {"at": "10:00"}}, "within"),
        ({"kind": "trigger", "trigger": SIGNAL, "bar_minutes": 5, "extra": 1}, "unknown key 'extra'"),
    ],
)
def test_a_bad_trigger_entry_is_refused(entry, message):
    arm = copy.deepcopy(A["iron-fly-1000"])
    arm["entry"] = entry
    problems = arms.validate(arm)
    assert any(message in p for p in problems), problems


# --- event-date entries ---------------------------------------------------------------------------------

FOMC_DAY = "2026-10-28"  # a decision day `core.calendar` holds, so the rule label answers


def event_trigger(label="FOMC", at="13:45", timing="on", days=0):
    return {
        "signal": {
            "kind": "event",
            "event": label,
            "timing": timing,
            "days": days,
            "unit": "sessions",
            "at": at,
        },
        "window": {"open": "13:45", "close": "14:00"},
        "within": 5,
    }


def with_calendar(snap, calendar):
    return dataclasses.replace(snap, events=calendar)


def every_day(arm):
    arm["weekdays"] = [0, 1, 2, 3, 4]
    return arm


def test_an_event_trigger_enters_on_the_event_day_at_its_time_and_not_on_other_days():
    arm = every_day(trigger_arm(event_trigger(), bar_minutes=1))
    assert not arms.validate(arm), arms.validate(arm)
    cal = events.EventCalendar()
    on = with_calendar(spx_snapshot(session=FOMC_DAY, minute=826, tr=rising(826, cross_at=9999)), cal)
    d = engine.decide(arm, on)
    assert ("entry_due", True, 825) in d.checks, d.checks
    before = with_calendar(spx_snapshot(session=FOMC_DAY, minute=820, tr=rising(820, cross_at=9999)), cal)
    assert engine.decide(arm, before).reason == "entry_not_due"
    other = with_calendar(spx_snapshot(session="2026-10-27", minute=826, tr=rising(826, cross_at=9999)), cal)
    assert engine.decide(arm, other).checks[-1][2] == "not_yet"
    closed = with_calendar(spx_snapshot(session="2026-10-27", minute=841, tr=rising(841, cross_at=9999)), cal)
    assert engine.decide(arm, closed).checks[-1][2] == "no_firing"
    # The day before, counted in sessions, fires on 10-27.
    eve = every_day(trigger_arm(event_trigger(timing="before", days=1), bar_minutes=1))
    assert ("entry_due", True, 825) in engine.decide(eve, other).checks


def test_an_event_the_calendar_cannot_answer_is_never_read_as_quiet():
    # CPI is a data label: with no data file every day is unknown, so the trigger is refused by name.
    arm = every_day(trigger_arm(event_trigger(label="CPI"), bar_minutes=1))
    snap = with_calendar(
        spx_snapshot(session=FOMC_DAY, minute=826, tr=rising(826, cross_at=9999)), events.EventCalendar()
    )
    d = engine.decide(arm, snap)
    assert d.reason == "entry_not_due" and d.checks[-1][2] == "event_dates_unknown"
    assert engine.decide(arm, dataclasses.replace(snap, events=None)).checks[-1][2] == "event_dates_unknown"


VWAP = {"name": "vwap", "anchor": "session", "price": "hlc3", "weight": "volume"}
OVER_VWAP = {"kind": "cross", "a": {"name": "close"}, "b": VWAP, "direction": "above", "seed": False}
VWAP_TRIG = {"signal": OVER_VWAP, "window": {"open": "09:45", "close": "15:00"}, "within": 5}


def vwap_arm(**entry):
    arm = trigger_arm(VWAP_TRIG, bar_minutes=5)
    arm["entry"].update(entry)
    return arm


def test_a_signal_that_reads_volume_names_whose_volume_weighs_the_bars():
    assert any("declare `volume`" in x for x in arms.validate(vwap_arm()))
    assert not arms.validate(vwap_arm(volume="SPY"))
    assert arms.volume_symbols(vwap_arm(volume="spy")) == {"SPY"}
    exit_arm = copy.deepcopy(A["iron-fly-1000"])
    exit_arm["exit"] = {"signal": {"trigger": VWAP_TRIG, "bar_minutes": 5}}
    assert any("exit.signal.trigger.signal: reads bar volume" in x for x in arms.validate(exit_arm))
    exit_arm["exit"]["signal"]["volume"] = "SPY"
    assert not arms.validate(exit_arm) and arms.volume_symbols(exit_arm) == {"SPY"}
    # Equal weights read no volume: the declared index stand-in passes, and takes no `volume`.
    equal = {**VWAP_TRIG, "signal": {**OVER_VWAP, "b": {**VWAP, "weight": "equal"}}}
    assert not arms.validate(trigger_arm(equal, bar_minutes=5))
    noisy = trigger_arm(equal, bar_minutes=5)
    noisy["entry"]["volume"] = "SPY"
    assert any("the signal reads no volume" in x for x in arms.validate(noisy))
    # Relative volume over more sessions than the snapshot carries could never fill.
    rv = {
        "kind": "cross",
        "a": {"name": "relative_volume", "n": 6},
        "b": 2,
        "direction": "above",
        "seed": True,
    }
    deep = trigger_arm({**VWAP_TRIG, "signal": rv}, bar_minutes=5)
    deep["entry"]["volume"] = "SPY"
    assert any("relative volume over more than" in x for x in arms.validate(deep))


def test_the_trigger_reads_the_declared_volume_and_a_gap_is_no_volume_not_a_zero():
    arm = every_day(vwap_arm(volume="SPY"))
    # Falling into 10:00 then rising through the VWAP from 10:00: fires on the 5-minute bar after.
    # Due within 5 minutes of the 10:05 firing, read at 10:06.
    tr = [(m, 6000.0 - (m - 570) * 0.5 if m < 600 else 5985.0 + (m - 600) * 2.0) for m in range(570, 607)]
    full = {"SPY": {"2026-11-16": dict.fromkeys(range(570, 607), 1000.0)}}
    snap = dataclasses.replace(spx_snapshot(minute=606, tr=tr), volumes=full)
    assert engine.trigger_due(arm["entry"], snap) == (True, 605)
    gap = {"SPY": {"2026-11-16": {m: 1000.0 for m in range(570, 607) if m != 583}}}
    assert engine.trigger_due(arm["entry"], dataclasses.replace(snap, volumes=gap)) == (False, "no_volume")
    assert engine.trigger_due(arm["entry"], dataclasses.replace(snap, volumes={})) == (False, "no_volume")


# --- signal exits ---------------------------------------------------------------------------------------

FALL = {
    "signal": {"kind": "cross", "a": {"name": "close"}, "b": 5995.0, "direction": "below", "seed": False},
    "window": {"open": "09:30", "close": "15:45"},
    "within": 30,
}


def falling(until, *, fall_at, start=570, base=6000.0, step=0.5):
    return [(m, base - (m - fall_at) * step if m >= fall_at else base) for m in range(start, until + 1)]


def held(exit_rules):
    arm = copy.deepcopy(A["iron-fly-1000"])
    arm["exit"] = exit_rules
    assert not arms.validate(arm), arms.validate(arm)
    d = engine.decide(arm, spx_snapshot(minute=600))
    assert d.enter, d.checks
    return arm, engine.Position(d.entry, arm)


def test_a_signal_exit_closes_after_its_bar_fires_and_not_before():
    _, pos = held({"signal": {"trigger": FALL, "bar_minutes": 5}})
    quiet = spx_snapshot(minute=640, tr=falling(640, fall_at=9999))
    assert engine.manage(pos, quiet) is None
    # Falls from 10:30; under 5995 from 10:41, inside the bar 10:40-10:44, complete at 10:45.
    early = spx_snapshot(minute=644, tr=falling(644, fall_at=630), spot=5993.0)
    assert engine.manage(pos, early) is None
    due = spx_snapshot(minute=645, tr=falling(645, fall_at=630), spot=5992.5)
    assert engine.manage(pos, due) == ("signal", 645)


def test_on_the_entry_day_a_signal_that_fired_before_the_entry_is_no_exit():
    _, pos = held(
        {"signal": {"trigger": {**FALL, "window": {"open": "09:30", "close": "15:45"}}, "bar_minutes": 5}}
    )
    # Under 5995 from 09:42; the bar completes and fires at 09:45, before the 10:00 entry, and is still
    # due at 10:05 (within 30): the day's first firing is before the entry, so it is no exit.
    snap = spx_snapshot(minute=605, tr=falling(605, fall_at=571), spot=5983.0)
    assert engine.manage(pos, snap) is None
    # The next session the same fall counts: it fires at 09:45, due until 10:14.
    nxt = spx_snapshot(session="2026-11-17", minute=600, tr=falling(600, fall_at=571), spot=5985.0)
    assert engine.manage(pos, nxt)[0] == "signal"


def test_a_profit_target_or_touch_still_comes_before_a_signal():
    _, pos = held({"touch": {"points": -20}, "signal": {"trigger": FALL, "bar_minutes": 5}})
    snap = spx_snapshot(minute=700, tr=falling(700, fall_at=630), spot=5960.0)
    assert engine.manage(pos, snap)[0] == "touch"


@pytest.mark.parametrize(
    ("exit_rules", "message"),
    [
        ({"signal": {"trigger": FALL}}, "bar_minutes"),
        ({"signal": {"trigger": FALL, "bar_minutes": 5, "kind": "trigger"}}, "unknown key 'kind'"),
        ({"signal": "fall"}, "a trigger is an object"),
        ({"signal": {"trigger": {"window": FALL["window"]}, "bar_minutes": 5}}, "exit.signal.trigger"),
    ],
)
def test_a_bad_signal_exit_is_refused(exit_rules, message):
    arm = copy.deepcopy(A["iron-fly-1000"])
    arm["exit"] = exit_rules
    problems = arms.validate(arm)
    assert any(message in x for x in problems), problems


# --- daily signals ----------------------------------------------------------------------------------------

from cherrypick.core import calendar as _cal  # noqa: E402

from cherrypick.ensemble import feed, model  # noqa: E402
from cherrypick.ensemble import snapshot as snapshot_mod  # noqa: E402


def daily_rows(closes, before="2026-11-16"):
    """Completed daily bars, one per trading day, ending the trading day before `before`."""
    from datetime import date

    days, d = [], _cal.previous_trading_day(date.fromisoformat(before))
    while len(days) < len(closes):
        days.append(d.isoformat())
        d = _cal.previous_trading_day(d)
    return [
        {"session": s, "open": c, "high": c, "low": c, "close": c}
        for s, c in zip(reversed(days), closes, strict=True)
    ]


DAILY_UP = {
    "signal": {"kind": "cross", "a": {"name": "close"}, "b": 6001.0, "direction": "above", "seed": True},
    "window": {"open": "10:00", "close": "11:00"},
    "within": 30,
    "daily": True,
}


def test_a_daily_trigger_enters_today_on_yesterdays_completed_close():
    arm = trigger_arm(DAILY_UP)
    assert not arms.validate(arm) and arms.reads_daily(arm)
    crossed = dataclasses.replace(spx_snapshot(minute=600), daily=daily_rows([5990.0, 5995.0, 6010.0]))
    assert ("entry_due", True, 600) in engine.decide(arm, crossed).checks
    flat = dataclasses.replace(spx_snapshot(minute=600), daily=daily_rows([5990.0, 6010.0, 6012.0]))
    assert engine.decide(arm, flat).checks[-1][2] == "not_yet"
    # No daily bars carried: stale, never a guess.
    assert engine.decide(arm, spx_snapshot(minute=600)).checks[-1][2] == "stale_bars"
    # Yesterday's bar without its open is absent, never filled in: stale, not a cross.
    partial = daily_rows([5990.0, 5995.0, 6010.0])
    partial[-1] = {**partial[-1], "open": None}
    no_open = dataclasses.replace(spx_snapshot(minute=600), daily=partial)
    assert engine.decide(arm, no_open).checks[-1][2] == "stale_bars"


def test_a_daily_signal_exit_closes_the_session_after_it_fires():
    down = {**DAILY_UP, "signal": {**DAILY_UP["signal"], "b": 5995.0, "direction": "below"}}
    _, pos = held({"signal": {"trigger": down}})
    nxt = dataclasses.replace(
        spx_snapshot(session="2026-11-17", minute=605),
        daily=daily_rows([6000.0, 6002.0, 5990.0], before="2026-11-17"),
    )
    assert engine.manage(pos, nxt) == ("signal", 600)


def test_a_daily_trigger_takes_no_bar_minutes_and_the_feed_asks_for_its_history():
    arm = trigger_arm(DAILY_UP, bar_minutes=5)
    assert any("a daily signal reads daily bars" in x for x in arms.validate(arm))
    arm = trigger_arm(DAILY_UP)
    need = feed.reads(arm)
    assert need["daily"] and need["closes"] == arms.DAILY_HISTORY
    assert not feed.reads(A["iron-fly-1000"])["daily"]


def test_the_snapshot_keeps_only_completed_days_and_round_trips():
    rows = daily_rows([5990.0, 6010.0]) + [
        {"session": "2026-11-16", "open": 1, "high": 1, "low": 1, "close": 1}
    ]
    snap = snapshot_mod.build(
        session="2026-11-16", minute=600, symbol="SPX", spot=6000.0, prev_close=6010.0, day_open=6000.0,
        quotes={}, trail=[], closes=[], daily=rows,
    )  # fmt: skip
    assert [r["session"] for r in snap.daily] == [r["session"] for r in rows[:2]]
    assert model.Snapshot.from_dict(snap.to_dict()).daily == snap.daily


# --- the stop, the delta limit and the DTE exit -----------------------------------------------------------


def test_a_credit_multiple_stop_closes_at_its_loss_and_not_before():
    _, pos = held({"stop_credit_multiple": 0.1})  # an 8.74 credit with 1.26 of risk: a 0.87 loss
    credit = pos.entry.prices["mid"]
    assert credit > 0
    assert engine.manage(pos, spx_snapshot(minute=630)) is None
    far = spx_snapshot(minute=630, spot=5940.0)
    kind, loss = engine.manage(pos, far)
    assert kind == "stop" and loss >= 0.1 * credit - 1e-9


def test_a_delta_limit_reads_the_legs_deltas_and_never_guesses():
    _, pos = held({"delta_limit": 0.02})  # the fly's delta peaks near 0.023 at 5960
    assert engine.manage(pos, spx_snapshot(minute=630)) is None
    kind, delta = engine.manage(pos, spx_snapshot(minute=630, spot=5960.0))
    assert kind == "delta" and abs(delta) >= 0.02
    # One leg with no delta is no reading: taken as zero, the long put's missing delta would push the
    # total past the limit at an unmoved spot.
    snap = spx_snapshot(minute=630)
    for rows in snap.chains.values():
        for r in rows.rows:
            if (float(r["strike"]), r["right"]) == (5990.0, "P"):
                r["delta"] = None
    assert engine.manage(pos, snap) is None


def test_a_dte_exit_closes_on_the_session_it_reaches_and_never_on_the_entry_day():
    arm, pos = held({"close_at_dte": 3})
    pos.entry = dataclasses.replace(pos.entry, expiry="2026-11-20")
    two_out = {"2026-11-20": 4.0 / 365}
    assert engine.manage(pos, spx_snapshot(minute=630, expiries=two_out)) is None  # entry day, 4 days out
    assert engine.manage(pos, spx_snapshot(session="2026-11-17", minute=571, expiries=two_out)) == ("dte", 3)


@pytest.mark.parametrize(
    ("exit_rules", "message"),
    [
        ({"stop_credit_multiple": 0}, "exit.stop_credit_multiple"),
        ({"delta_limit": 7}, "exit.delta_limit"),
        ({"close_at_dte": -1}, "exit.close_at_dte"),
        ({"close_at_dte": 2.5}, "exit.close_at_dte"),
    ],
)
def test_a_bad_exit_value_is_refused(exit_rules, message):
    arm = copy.deepcopy(A["iron-fly-1000"])
    arm["exit"] = exit_rules
    assert any(message in x for x in arms.validate(arm)), arms.validate(arm)


# --- IV gates on the underlying's vol index ------------------------------------------------------------


def iv_ctx(vol_closes, closes=None):
    snap = dataclasses.replace(spx_snapshot(minute=600), vol_closes=list(vol_closes))
    if closes is not None:
        snap = dataclasses.replace(snap, closes=list(closes))
    return gates_mod.Ctx(snap, A["iron-fly-1000"], snap.chains[snap.session], snap.session)


def test_vol_index_rank_and_percentile_rank_the_last_close_against_the_ones_before_it():
    history = [10.0 + i for i in range(10)]  # 10..19, then the last close
    ok, v = gates_mod.vol_index_rank(iv_ctx([*history, 14.5]), min=40, max=50, lookback=10)
    assert ok and v == 50.0  # (14.5 - 10) / (19 - 10)
    ok, v = gates_mod.vol_index_percentile(iv_ctx([*history, 14.5]), min=50, lookback=10)
    assert ok and v == 50.0  # 10..14 below it
    # Fewer closes than the lookback asks for: no reading, so the gate fails.
    assert gates_mod.vol_index_rank(iv_ctx(history), lookback=10) == (False, None)
    assert gates_mod.vol_index_rank(iv_ctx([]), lookback=10) == (False, None)


def test_vol_index_minus_rv_compares_the_index_with_realized_vol_in_vol_points():
    closes = [6000.0 * (1.0 + 0.001 * ((-1) ** i)) for i in range(21)]
    ok, v = gates_mod.vol_index_minus_rv(iv_ctx([20.0], closes), min=0, n=20)
    assert ok and v is not None and v < 20.0
    assert gates_mod.vol_index_minus_rv(iv_ctx([20.0], closes[:5]), n=20) == (False, None)


def test_an_iv_gate_validates_and_the_feed_asks_for_its_history():
    arm = copy.deepcopy(A["iron-fly-1000"])
    arm["gates"] = [
        {"gate": "vol_index_rank", "max": 30, "lookback": 120},
        {"gate": "vol_index_minus_rv", "min": 2, "n": 10},
    ]
    assert not arms.validate(arm), arms.validate(arm)
    need = feed.reads(arm)
    assert need["vol_closes"] == 121 and need["closes"] >= 11
    assert feed.reads(A["iron-fly-1000"])["vol_closes"] == 0
    bad = copy.deepcopy(arm)
    bad["gates"] = [{"gate": "vol_index_rank", "lookbak": 120}]
    assert any("unknown key 'lookbak'" in x for x in arms.validate(bad))


def test_the_feed_reads_the_index_closes_before_today_and_none_without_an_index(monkeypatch):
    from datetime import date

    from cherrypick.core import volindex

    monkeypatch.setattr(
        volindex,
        "closes",
        lambda index, folder=None: {"2026-11-12": 1.0, "2026-11-13": 2.0, "2026-11-16": 3.0},
    )
    assert feed.vol_history("SPX", date(2026, 11, 16), 5) == [1.0, 2.0]
    assert feed.vol_history("TSLA", date(2026, 11, 16), 5) == []
    assert feed.vol_history("SPX", date(2026, 11, 16), 0) == []


# --- the safeguards, stated --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        (None, "limits: required"),
        ({"per_day": 1}, "limits.max_open: required"),
        ({"max_open": 1}, "limits.per_day: required"),
        ({"max_open": 1, "per_day": 2}, "limits.per_day: required, and 1"),
        ({"max_open": 0, "per_day": 1}, "limits.max_open"),
        ({"max_open": 1, "per_day": 1, "max_opne": 2}, "unknown key 'max_opne'"),
    ],
)
def test_every_arm_states_its_safeguards(limits, message):
    arm = copy.deepcopy(A["iron-fly-1000"])
    if limits is None:
        arm.pop("limits")
    else:
        arm["limits"] = limits
    assert any(message in x for x in arms.validate(arm)), arms.validate(arm)
    assert all(a["limits"]["per_day"] == 1 and a["limits"]["max_open"] >= 1 for a in A.values())


def test_a_replay_record_from_before_per_day_runs_under_one_a_day():
    from cherrypick.ensemble import record

    old = {"iron-fly-1000": {**A["iron-fly-1000"], "limits": {"max_open": 2}}}
    ran = record.as_they_ran(old)
    assert ran["iron-fly-1000"]["limits"] == {"max_open": 2, "per_day": 1}
    assert record.as_they_ran({"x": {"name": "x"}})["x"]["limits"] == {"max_open": 1, "per_day": 1}
