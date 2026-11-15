"""Ensemble on core.entrychecks: every gate gives through core exactly what its own function gives, the
gates validate through core, and a `trigger` entry fires through core's entry_due."""

from __future__ import annotations

import copy

import pytest
from cherrypick.core import entrychecks as ec
from market import spx_snapshot

from cherrypick.ensemble import arms, engine
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
        (
            {
                "kind": "trigger",
                "bar_minutes": 5,
                "trigger": {
                    **SIGNAL,
                    "signal": {
                        "kind": "event",
                        "event": "fomc",
                        "timing": "on",
                        "days": 0,
                        "unit": "sessions",
                        "at": "10:00",
                    },
                },
            },
            "event dates",
        ),
    ],
)
def test_a_bad_trigger_entry_is_refused(entry, message):
    arm = copy.deepcopy(A["iron-fly-1000"])
    arm["entry"] = entry
    problems = arms.validate(arm)
    assert any(message in p for p in problems), problems
