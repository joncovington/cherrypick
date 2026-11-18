"""core.entrychecks: stage order, first failure, missing data refused by name, the registry kept apart per
module, the check record, and the trigger's window, time to enter and cadence."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from cherrypick.core import bs
from cherrypick.core import entrychecks as ec
from cherrypick.core.signals import Bar

ET = ZoneInfo("America/New_York")
DAY = "2026-01-05"
F, YEARS = 5000.0, 30 / 365


def at(hhmm: str, day: str = DAY) -> datetime:
    h, m = hhmm.split(":")
    return datetime.fromisoformat(day).replace(hour=int(h), minute=int(m), tzinfo=ET)


def bars(closes, session=DAY, size=5):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(Bar(session, 570 + i * size, 570 + (i + 1) * size, prev, max(prev, c), min(prev, c), c))
        prev = c
    return out


def chain(vol=lambda k: 0.18 + 0.6 * (F - k) / F):
    rows = []
    for k in range(4500, 5501, 25):
        for right in ("C", "P"):
            p = bs.price(F, k, YEARS, vol(k), right, 0.0)
            rows.append({"strike": float(k), "right": right, "bid": p, "ask": p, "iv": vol(k)})
    return rows


def ctx(**kw):
    base = {"bars": bars([100, 101, 102, 103]), "chains": {"entry": chain()}, "years": {"entry": YEARS}}
    return ec.Context(**{**base, **kw})


ABOVE_101 = {
    "check": "signal",
    "spec": {"kind": "cross", "a": {"name": "close"}, "b": 101, "direction": "above", "seed": True},
}
ATM_LOW = {"check": "atm_iv", "chain": "entry", "iv_from": "row", "min": 10, "max": 25}
ATM_HIGH = {"check": "atm_iv", "chain": "entry", "iv_from": "row", "min": 30}
SKEW = {"check": "skew_25d", "chain": "entry", "iv_from": "row", "delta_from": "mid", "min": 0.5}
VEGA = {"check": "net_greek", "greek": "vega", "multiplier": 100, "max": 0}


# --- evaluation ---------------------------------------------------------------------------------------


def test_stages_run_in_order_whatever_the_list_order():
    v = ec.evaluate([SKEW, ATM_LOW, ABOVE_101], ctx(), stages=["signal", "chain"])
    assert v.passed
    assert [c.name for c in v.checks] == ["signal", "skew_25d", "atm_iv"]
    assert [c.stage for c in v.checks] == ["signal", "chain", "chain"]


def test_evaluation_stops_at_the_first_failure_and_names_it():
    v = ec.evaluate([ATM_HIGH, SKEW], ctx(), stages=["chain"])
    assert not v.passed
    assert v.failed.name == "atm_iv" and v.failed.value == pytest.approx(18.0, abs=0.01)
    assert [c.name for c in v.checks] == ["atm_iv"]  # skew never ran


def test_only_the_stages_asked_for_run():
    v = ec.evaluate([ABOVE_101, VEGA], ctx(), stages=["signal", "chain"])
    assert [c.name for c in v.checks] == ["signal"]
    legs = [{"quantity": -1, "delta": -0.1, "gamma": 0.0, "theta": 1.0, "vega": 3.0}]
    v = ec.evaluate([ABOVE_101, VEGA], ctx(legs=legs), stages=["structure"])
    assert v.passed and v.checks[0].value == pytest.approx(-300.0)  # short vega: within max 0


def test_missing_data_fails_by_name_and_never_passes():
    v = ec.evaluate(
        [{"check": "iv_rank", "chain": "entry", "iv_from": "row", "min": 0}], ctx(), stages=["chain"]
    )
    assert not v.passed
    assert v.failed.reason == "missing: iv_history" and v.failed.value is None
    v = ec.evaluate([{**ATM_LOW, "chain": "30d"}], ctx(), stages=["chain"])
    assert v.failed.reason == "missing: chain:30d"
    v = ec.evaluate([VEGA], ctx(), stages=["structure"])
    assert v.failed.reason == "missing: legs"


def test_coverage_names_what_each_condition_lacks():
    checks = [
        ABOVE_101,
        ATM_LOW,
        {"check": "term_ratio", "near": "entry", "far": "30d", "iv_from": "row", "max": 1},
    ]
    assert ec.coverage(checks, {"bars", "chain:entry"}) == {2: ["chain:30d"]}
    event = {
        "check": "signal",
        "spec": {
            "kind": "event",
            "event": "fomc",
            "timing": "on",
            "days": 0,
            "unit": "sessions",
            "at": "10:00",
        },
    }
    assert ec.coverage([event], {"bars"}) == {0: ["events"]}


def test_the_chain_measures_read_their_roles():
    hist = [12.0, 15.0, 30.0]
    closes = [100 * (1.001**i) for i in range(30)]
    c = ctx(
        iv_history=hist,
        closes=closes,
        chains={"entry": chain(), "30d": chain(lambda k: 0.20)},
        years={"entry": YEARS, "30d": YEARS},
    )
    checks = [
        {"check": "iv_rank", "chain": "entry", "iv_from": "row", "min": 30, "max": 40},
        {"check": "iv_percentile", "chain": "entry", "iv_from": "row", "min": 60},
        {"check": "iv_minus_rv", "chain": "entry", "iv_from": "row", "days": 20, "min": 10},
        {"check": "term_ratio", "near": "entry", "far": "30d", "iv_from": "row", "max": 1},
        {"check": "term_spread", "near": "entry", "far": "30d", "iv_from": "row", "min": 1.5},
    ]
    v = ec.evaluate(checks, c, stages=["chain"])
    assert v.passed, v.failed
    values = [ch.value for ch in v.checks]
    assert values[0] == pytest.approx(100 * 6 / 18, abs=0.1)
    assert values[3] == pytest.approx(0.9, abs=0.001)


# --- validation and the registry -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("checks", "message"),
    [
        ([{"check": "nope"}], r"checks\[0\]\.check"),
        ([{**ATM_LOW, "color": 1}], "unknown key 'color'"),
        ([{"check": "atm_iv", "chain": "entry", "min": 1}], "needs 'iv_from'"),
        ([{"check": "atm_iv", "chain": "entry", "iv_from": "row"}], "needs a 'min' or a 'max'"),
        ([{**ATM_LOW, "iv_from": "feed"}], r"checks\[0\]\.iv_from"),
        ([{"check": "signal", "spec": {"kind": "gap"}}], r"checks\[0\]\.spec"),
    ],
)
def test_validate_refuses_with_the_path(checks, message):
    with pytest.raises(ValueError, match=message):
        ec.validate(checks)


def test_a_module_extends_its_own_copy_of_the_registry():
    gex = ec.Condition(
        "gex_ratio",
        "chain",
        lambda c, p: (c.extra["gex"] >= p["min"], c.extra["gex"]),
        lambda p: {"gex"},
        {"min": lambda v: True},
    )
    mine = ec.CORE.extended(gex)
    assert "gex_ratio" in mine.names() and "gex_ratio" not in ec.CORE.names()
    v = ec.evaluate(
        [{"check": "gex_ratio", "min": 1}], ctx(extra={"gex": 2.0}), stages=["chain"], registry=mine
    )
    assert v.passed and v.checks[0].value == 2.0
    assert (
        ec.evaluate([{"check": "gex_ratio", "min": 1}], ctx(), stages=["chain"], registry=mine).failed.reason
        == "missing: gex"
    )
    with pytest.raises(ValueError, match="registered twice"):
        mine.extended(gex)


def test_the_check_record_is_plain_data():
    v = ec.evaluate([ATM_LOW], ctx(), stages=["chain"])
    d = v.checks[0].to_dict()
    assert set(d) == {"stage", "name", "passed", "value", "reason"} and d["passed"] is True


# --- the trigger --------------------------------------------------------------------------------------


FIXED = {"at": "13:45", "within": 5}


def test_a_fixed_time_is_due_for_its_minutes_then_missed():
    assert ec.entry_due(FIXED, now=at("13:44")).reason == "not_yet"
    assert ec.entry_due(FIXED, now=at("13:45")).due
    assert ec.entry_due(FIXED, now=at("13:49")).due
    late = ec.entry_due(FIXED, now=at("13:50"))
    assert not late.due and late.reason == "missed" and late.fired == 825


def signal_trigger(within):
    spec = {"kind": "cross", "a": {"name": "close"}, "b": 101, "direction": "above", "seed": True}
    return {"signal": spec, "window": {"open": "09:40", "close": "10:30"}, "within": within}


def test_a_signal_is_due_from_its_firing_inside_the_window():
    bs_ = bars([100, 100.5, 102, 103])  # crosses 101 on the bar ending 09:45
    assert ec.entry_due(signal_trigger(10), now=at("09:46"), bars=bs_).due
    assert ec.entry_due(signal_trigger(10), now=at("09:46"), bars=bs_).fired == 585
    assert ec.entry_due(signal_trigger(10), now=at("09:55"), bars=bs_).reason == "missed"
    assert ec.entry_due(signal_trigger(None), now=at("10:30"), bars=bs_).due
    assert ec.entry_due(signal_trigger(None), now=at("10:31"), bars=bs_).reason == "missed"


def test_a_firing_outside_the_window_does_not_count():
    early = bars([100, 102, 103])  # fires on the bar ending 09:40... then stays above
    t = {**signal_trigger(None), "window": {"open": "09:45", "close": "10:30"}}
    assert ec.entry_due(t, now=at("09:50"), bars=early).reason == "not_yet"
    assert ec.entry_due(t, now=at("10:31"), bars=early).reason == "no_firing"


def test_cadence_holds_an_entry_back():
    d = ec.entry_due(FIXED, now=at("13:46"), last_fill=at("13:40"), spacing_seconds=600)
    assert not d.due and d.reason == "spacing" and d.wait_seconds == 240
    assert ec.entry_due(FIXED, now=at("13:46"), last_fill=at("13:30"), spacing_seconds=600).due


@pytest.mark.parametrize(
    ("trigger", "message"),
    [
        ({"at": "13:45"}, "within"),
        ({"at": "25:00", "within": 5}, "trigger.at"),
        ({"signal": signal_trigger(5)["signal"], "within": 5}, "trigger.window"),
        ({**signal_trigger(5), "window": {"open": "11:00", "close": "10:00"}}, "trigger.window"),
        ({**signal_trigger(5), "color": 1}, "unknown key 'color'"),
        ({"cron": "x"}, "needs 'at' or 'signal'"),
    ],
)
def test_validate_trigger_refuses_with_the_path(trigger, message):
    with pytest.raises(ValueError, match=message):
        ec.validate_trigger(trigger)


def daily_bars(closes, last="2026-01-02"):
    """Completed daily bars, one per trading day, ending on `last`."""
    from cherrypick.core import calendar as cal

    days, d = [], date.fromisoformat(last)
    while len(days) < len(closes):
        if cal.is_trading_day(d):
            days.append(d.isoformat())
        d -= timedelta(days=1)
    return [Bar(s, 570, 960, c, c, c, c) for s, c in zip(reversed(days), closes, strict=True)]


def daily_trigger(within=30):
    spec = {"kind": "cross", "a": {"name": "close"}, "b": 101, "direction": "above", "seed": True}
    return {"signal": spec, "window": {"open": "10:00", "close": "11:00"}, "within": within, "daily": True}


def test_a_daily_trigger_acts_today_on_yesterdays_completed_bar():
    # 2026-01-05 is a Monday; its previous trading day is Friday 2026-01-02, where the close crosses 101.
    crossed = daily_bars([100, 100.5, 102])
    assert ec.entry_due(daily_trigger(), now=at("09:59"), bars=crossed).reason == "not_yet"
    due = ec.entry_due(daily_trigger(), now=at("10:00"), bars=crossed)
    assert due.due and due.fired == 600
    assert ec.entry_due(daily_trigger(), now=at("10:30"), bars=crossed).reason == "missed"
    # Crossed the day before yesterday and stayed above: yesterday did not fire, so nothing today.
    earlier = daily_bars([100, 102, 103])
    assert ec.entry_due(daily_trigger(), now=at("10:59"), bars=earlier).reason == "not_yet"
    assert ec.entry_due(daily_trigger(), now=at("11:01"), bars=earlier).reason == "no_firing"


def test_a_daily_trigger_never_reads_todays_forming_bar_or_an_old_day():
    crossed = daily_bars([100, 100.5, 102])
    # A bar of today's session, crossing back down, is dropped: it has not completed.
    forming = [*crossed, Bar(DAY, 570, 600, 102, 102, 99, 99)]
    assert ec.entry_due(daily_trigger(), now=at("10:00"), bars=forming).due
    # Bars that stop before the previous trading day are stale, never an older firing acted on today.
    stale = daily_bars([100, 100.5, 102], last="2025-12-31")
    d = ec.entry_due(daily_trigger(), now=at("10:00"), bars=stale)
    assert not d.due and d.reason == "stale_bars"
    with pytest.raises(ValueError, match="daily: true or false"):
        ec.validate_trigger({**daily_trigger(), "daily": "yes"})
    unseeded = daily_trigger()
    unseeded["signal"] = {**unseeded["signal"], "seed": False}
    with pytest.raises(ValueError, match="carries its state across sessions"):
        ec.validate_trigger(unseeded)


def test_a_developing_daily_trigger_reads_todays_bar_as_it_stands_each_minute():
    # Yesterday closed under 101; today's bar crosses only once the price is above it.
    done = daily_bars([100, 100.5, 100.8])
    t = {**daily_trigger(None), "developing": True}
    under = ec.developing_bar(DAY, 100.9, [(570, 100.9), (590, 100.7)], 100.95)
    assert ec.entry_due(t, now=at("10:10"), bars=[*done, under]).reason == "not_yet"
    over = ec.developing_bar(DAY, 100.9, [(570, 100.9), (600, 101.3)], 101.2)
    due = ec.entry_due(t, now=at("10:10"), bars=[*done, over])
    assert due.due and due.fired == 610
    # Outside the window, and back under it, it is not due.
    assert not ec.entry_due(t, now=at("09:50"), bars=[*done, over]).due
    assert ec.entry_due(t, now=at("11:01"), bars=[*done, over]).reason == "no_firing"
    # A bar must stand for today, and the days before must reach yesterday.
    assert ec.entry_due(t, now=at("10:10"), bars=done).reason == "no_developing_bar"
    stale = daily_bars([100, 100.5, 100.8], last="2025-12-31")
    assert ec.entry_due(t, now=at("10:10"), bars=[*stale, over]).reason == "stale_bars"
    # The completed-bar trigger, given the same bars, still acts on yesterday only.
    assert ec.entry_due(daily_trigger(None), now=at("10:10"), bars=[*done, over]).reason == "not_yet"


def test_a_developing_bar_is_its_open_its_range_so_far_and_the_price_now():
    b = ec.developing_bar(DAY, 100.0, [(570, 100.5), (600, 99.2), (620, 101.7)], 101.0)
    assert (b.session, b.open, b.high, b.low, b.close) == (DAY, 100.0, 101.7, 99.2, 101.0)


def test_developing_needs_a_daily_trigger_and_no_within():
    with pytest.raises(ValueError, match="developing: true or false"):
        ec.validate_trigger({**daily_trigger(None), "developing": "yes"})
    with pytest.raises(ValueError, match="needs daily: true"):
        ec.validate_trigger({**daily_trigger(None), "daily": False, "developing": True})
    with pytest.raises(ValueError, match="within must be null"):
        ec.validate_trigger({**daily_trigger(30), "developing": True})


def test_the_trigger_answers_the_same_live_and_in_a_replay():
    """A loop ticking each minute on the bars completed so far and a replay over the whole day agree on
    when the entry was first due: no reading of a later bar."""
    day = bars([100, 100.5, 100.8, 101.4, 101.2, 102, 101.6, 102.3])
    t = signal_trigger(None)
    first_live = None
    for minute in range(575, 650):
        done = [b for b in day if b.end <= minute]
        now = at(f"{minute // 60:02d}:{minute % 60:02d}")
        if done and ec.entry_due(t, now=now, bars=done).due:
            first_live = minute
            break
    assert first_live == ec.entry_due(t, now=at("10:30"), bars=day).fired
