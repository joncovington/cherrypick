"""Retiring an automation, a group's suggested budget, and a budget that changes on a date.

- **Retired** (`arms`: `retired`): off whatever `enabled` says, `retired` in the Groups view, refused by the
  Groups check when switched on, kept by a Builder save, and set or cleared only by `drafts.retire`, which
  never switches one on.
- **Suggested** (`budgets`): the most the members not retired can have at risk at once, from their own
  sizes and strikes, or None with each member that has no bound and why.
- **Dated** (`config.groups`, `groupedit.check`, `groupread`): once a group has run, a budget change starts
  at the next session and the sessions before it keep the budget they ran on.
"""

from __future__ import annotations

from datetime import date

import pytest

from cherrypick.automations import arms, budgets, drafts, groupedit, groupread
from cherrypick.automations import config as config_mod

SAT = date(2026, 11, 21)  # the next session is Monday 2026-11-23
NEXT = "2026-11-23"


# --------------------------------------------------------------------------- retired


def test_a_retired_automation_is_off_whatever_its_switch_says():
    loaded = arms.load_arms({"iron-fly-1000": {"retired": "2026-11-21", "enabled": True}})
    assert loaded["iron-fly-1000"]["enabled"] is False
    assert loaded["iron-condor-1345"]["enabled"] is True


def test_retired_takes_false_or_a_date_and_nothing_else():
    assert arms.load_arms({"iron-fly-1000": {"retired": False}})["iron-fly-1000"]["enabled"] is True
    with pytest.raises(ValueError, match=r"iron-fly-1000\.retired: false, or the ISO date"):
        arms.load_arms({"iron-fly-1000": {"retired": True}})


def test_the_view_names_a_retired_automation_retired_and_counts_it():
    cfg = {"automations": {"iron-fly-1000": {"retired": "2026-11-21", "enabled": False}}}
    v = groupedit.view(cfg, SAT)
    rows = {m["name"]: m for g in v["groups"] for m in g["members"]}
    assert rows["iron-fly-1000"]["state"] == "retired" and rows["iron-fly-1000"]["retired"] == "2026-11-21"
    assert rows["iron-condor-1345"]["retired"] is None
    assert v["counts"]["retired"] == 1


def test_the_groups_check_refuses_to_switch_a_retired_one_on():
    cfg = {"automations": {"iron-fly-1000": {"retired": "2026-11-21", "enabled": False}}}
    r = groupedit.check(
        cfg, {"groups": {"ensemble": {"enabled": True}}, "switches": {"iron-fly-1000": True}}, SAT
    )
    assert r["problems"] == ["switches.iron-fly-1000: retired; reinstate it on its own page first"]
    assert r["block"] is None


def test_retiring_writes_the_date_and_switches_it_off():
    r = drafts.retire({}, "iron-fly-1000", True, SAT)
    assert r["problems"] == [] and r["entry"] == {"retired": "2026-11-21", "enabled": False}


def test_reinstating_clears_it_and_leaves_it_off():
    cfg = {"automations": {"iron-fly-1000": {"retired": "2026-11-21", "enabled": False}}}
    r = drafts.retire(cfg, "iron-fly-1000", False, SAT)
    assert r["problems"] == [] and r["entry"] == {"retired": False, "enabled": False}


def test_retiring_twice_or_an_unknown_one_is_refused():
    cfg = {"automations": {"iron-fly-1000": {"retired": "2026-11-21", "enabled": False}}}
    assert drafts.retire(cfg, "iron-fly-1000", True, SAT)["problems"] == ["iron-fly-1000: already retired"]
    assert drafts.retire({}, "nope", True, SAT)["problems"] == ["nope: no such automation"]


def test_a_builder_save_keeps_retired_as_stored_whatever_the_editor_sends():
    cfg = {"automations": {"iron-fly-1000": {"retired": "2026-11-21", "enabled": False}}}
    spec = drafts.editable(cfg, "iron-fly-1000")
    r = drafts.draft(cfg, "iron-fly-1000", {**spec, "retired": False}, SAT)
    assert r["problems"] == [] and r["entry"]["retired"] == "2026-11-21"


# --------------------------------------------------------------------------- suggested budget


def _arm(size, legs, max_open=1):
    return {"name": "x", "size": size, "limits": {"max_open": max_open, "per_day": 1}, "legs": legs}


def _leg(name, right, side, **strike):
    return {"name": name, "right": right, "side": side, "strike": strike}


def test_a_dollar_cap_is_its_bound_for_each_structure_it_may_hold():
    assert budgets.risk_bound(_arm({"max_risk": 250}, [], max_open=2)) == (500.0, None)


def test_a_spread_placed_in_points_risks_its_width_for_each_contract():
    legs = [
        _leg("s", "P", -1, rule="spot", points=-5),
        _leg("l", "P", 1, rule="from_leg", leg="s", points=-10),
    ]
    assert budgets.risk_bound(_arm({"contracts": 2}, legs)) == (2000.0, None)


def test_a_width_set_from_a_leg_placed_by_delta_still_counts():
    legs = [
        _leg("l", "C", 1, rule="delta", target=0.3),
        _leg("s", "C", -1, rule="from_leg", leg="l", points=15),
    ]
    assert budgets.risk_bound(_arm({"contracts": 1}, legs)) == (1500.0, None)


def test_a_wing_placed_by_percent_has_no_bound():
    legs = [_leg("s", "P", -1, rule="spot", points=0), _leg("l", "P", 1, rule="from_leg", leg="s", pct=-0.5)]
    risk, why = budgets.risk_bound(_arm({"contracts": 1}, legs))
    assert risk is None and "width is set by the session" in why


def test_a_long_option_alone_risks_its_premium_and_has_no_bound():
    risk, why = budgets.risk_bound(_arm({"contracts": 1}, [_leg("l", "P", 1, rule="delta", target=0.1)]))
    assert risk is None and "premium" in why


def test_the_suggestion_adds_the_members_not_retired_and_is_none_when_one_has_no_bound():
    shipped = arms.load_arms(
        {"iron-fly-1000": {"retired": "2026-11-21"}, "iron-fly-1dte-1330": {"retired": "2026-11-21"}}
    )
    s = budgets.suggested(
        {n: shipped[n] for n in ("iron-condor-1345", "iron-fly-1000", "iron-fly-1dte-1330")}
    )
    assert s == {"allocation": 1000.0, "members": [{"name": "iron-condor-1345", "risk": 1000.0, "why": None}]}
    s = budgets.suggested({n: shipped[n] for n in ("iron-condor-1345", "long-put-1030")})
    assert s["allocation"] == 1300.0
    unknown = arms.load_arms({})
    s = budgets.suggested({n: unknown[n] for n in ("iron-condor-1345", "iron-fly-1dte-1330")})
    assert s["allocation"] is None and [m["risk"] for m in s["members"]] == [1000.0, None]


def test_the_view_gives_each_group_its_suggestion_and_each_member_its_shape():
    (g,) = groupedit.view({}, SAT)["groups"]
    assert "allocation" in g["suggested"] and len(g["suggested"]["members"]) == len(arms.ARMS)
    m = next(m for m in g["members"] if m["name"] == "iron-condor-1345")
    assert m["shape"]["exact"] is True and len(m["shape"]["curve"]) >= 4


# --------------------------------------------------------------------------- dated budget


def _ran(**budget):
    return {"groups": {"ensemble": {"enabled": True, "start": "2026-11-16", **budget}}}


def test_a_change_takes_its_budget_from_its_date():
    cfg = _ran(allocation=10000, budget_changes=[{"from": NEXT, "allocation": 20000}])
    assert config_mod.groups(cfg, date(2026, 11, 20))["ensemble"]["allocation"] == 10000
    assert config_mod.groups(cfg, date(2026, 11, 23))["ensemble"]["allocation"] == 20000


def test_changes_must_rise_and_carry_only_a_budget():
    with pytest.raises(ValueError, match="after the change before it"):
        config_mod.groups(_ran(budget_changes=[{"from": NEXT}, {"from": NEXT}]))
    with pytest.raises(ValueError, match="unknown key 'enabled'"):
        config_mod.groups(_ran(budget_changes=[{"from": NEXT, "enabled": False}]))


def test_a_group_that_has_run_gets_a_dated_change_never_a_rewrite():
    cfg = _ran(allocation=10000)
    r = groupedit.check(
        cfg, {"groups": {"ensemble": {"enabled": True, "start": "2026-11-16", "allocation": 20000}}}, SAT
    )
    assert r["problems"] == []
    assert r["block"]["ensemble"]["allocation"] == 10000
    assert r["block"]["ensemble"]["budget_changes"] == [{"from": NEXT, "allocation": 20000.0}]
    assert any("changes from 2026-11-23" in n for n in r["notes"])


def test_sending_the_budget_back_drops_the_change_waiting():
    cfg = _ran(allocation=10000, budget_changes=[{"from": NEXT, "allocation": 20000}])
    r = groupedit.check(
        cfg, {"groups": {"ensemble": {"enabled": True, "start": "2026-11-16", "allocation": 10000}}}, SAT
    )
    assert "budget_changes" not in r["block"]["ensemble"]


def test_an_unrelated_save_keeps_the_change_waiting():
    cfg = _ran(allocation=10000, budget_changes=[{"from": NEXT, "allocation": 20000}])
    v = groupedit.view(cfg, SAT)
    (g,) = v["groups"]
    # The page edits from the budget the next session carries, and says what is waiting.
    assert g["allocation"] == 20000 and g["budget"]["today"] == {"allocation": 10000.0}
    assert g["budget"]["pending"] == {"from": NEXT, "allocation": 20000.0} and g["budget"]["dated"] is True
    r = groupedit.check(
        cfg, {"groups": {"ensemble": {"enabled": True, "start": "2026-11-16", "allocation": 20000}}}, SAT
    )
    assert r["block"]["ensemble"]["budget_changes"] == [{"from": NEXT, "allocation": 20000.0}]


def test_a_group_that_has_not_run_takes_the_budget_as_given():
    cfg = {"groups": {"ensemble": {"enabled": False}}}
    r = groupedit.check(cfg, {"groups": {"ensemble": {"enabled": False, "allocation": 5000}}}, SAT)
    assert r["block"]["ensemble"] == {"enabled": False, "allocation": 5000}


def _closed(arm, session, net):
    return {
        "arm": arm,
        "entry_session": session,
        "exit_session": session,
        "status": "closed",
        "capital": 100.0,
        "net_pnl": net,
        "gross_pnl": net,
        "fees": 0.0,
        "settlement": 0.0,
        "slippage": 0.0,
        "net_stress": net,
        "net_mid": net,
    }


def test_the_record_reads_each_trade_against_the_allocation_its_session_carried():
    spec = config_mod.groups(
        _ran(allocation=10000, budget_changes=[{"from": "2026-11-03", "allocation": 20000}])
    )
    pos = [_closed("a", "2026-11-02", 300), _closed("a", "2026-11-03", -500)]
    g = groupread.read({"a": {"group": "ensemble"}}, spec, pos, [])["groups"][0]
    # 300 of 10,000, then -500 of 20,000: never the whole net over either.
    assert g["against_allocation"] == {
        "net": 0.005,
        "drawdown": 0.025,
        "since": "2026-11-02",
        "changes": ["2026-11-03"],
    }
